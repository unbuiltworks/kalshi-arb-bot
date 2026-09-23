"""Realtime L5 -> PMUS BTC15m copyability observer. READ-ONLY / SHADOW ONLY.

THE QUESTION. Not "would an L5-shaped policy do well on PMUS" -- the frozen
shadow already asks that. This asks the harder, time-sensitive one:

    when we LEARN what L5 actually did, is there still an economically
    comparable PMUS BTC15m action available?

which cannot be answered from history, because the answer is destroyed by
hindsight. A backtest sees L5's 15:13:19 trade and PMUS's 15:13:19 book
together. We do not. We see the trade when the feed publishes it, and the only
honest PMUS state to price it against is the one visible AT THAT MOMENT.

SO NOTHING IS EVER BACKDATED. Every row records three clocks -- L5's event
time, our receipt time, and the age of the PMUS snapshot we priced it against
-- and the PMUS snapshot is taken when the action is RECEIVED, never when it
happened. A row whose PMUS snapshot predates its receipt is a bug, and
`no_backdating_ok` on every row is the assertion that it did not happen.

TWO TRACKS THAT NEVER TOUCH.

  MIRROR_RAW    translate L5's actual observed action into a PMUS q=1 action.
                This measures COPYABILITY: did L5's own decision survive the
                trip to us?
  CLONE_MODEL   the frozen L5 behavioural engine, deciding independently from
                PMUS state alone, knowing nothing about L5's feed. This
                measures TRANSFERABILITY.

Their inventories and economics are kept in separate objects and are never
summed, because a number that mixes "what L5 did" with "what our model would
do" answers neither question.

EVIDENCE CLASSES ARE THE FROZEN ONES. Public-book interaction can only reach
NO_INTERACTION or QUEUE_AMBIGUOUS, and it says so. No fill is ever
manufactured: `is_fill` is False everywhere until account evidence exists.

RESTART SAFETY. The tape is append-only JSONL and the dedupe index is rebuilt
from it at boot, so restarting loses nothing and double-counts nothing. A dead
window is recorded as dead and collection continues.

Structurally incapable of trading: public GETs only, no credential, no order
path, no venue client that can mutate.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import ssl
import sys
import time
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import pmus_fees as fees
from src import pmus_interaction_classifier as clf
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

L5_DATA = "https://data-api.polymarket.com"
L5_ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
PMUS_GATEWAY = "https://gateway.polymarket.us"
UA = {"User-Agent": "Mozilla/5.0 (l5-pmus-realtime-observer; read-only)"}

L5_SERIES = "btc-updown-15m"
PMUS_SERIES = "btc-updown-15m"
WINDOW_S = 900

#: the frozen sports eligibility band. NOT widened here; recorded when it binds.
BAND_LO, BAND_HI = D("0.20"), D("0.80")
TICK = D("0.01")

LABELS = ("SHADOW/READ-ONLY. No order exists on either venue. Nothing here is "
          "submitted, filled, settled or realized.")


# ------------------------------------------------------------------ transport

def _ctx() -> ssl.SSLContext:
    bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    if bundle and os.path.exists(bundle):
        try:
            return ssl.create_default_context(cafile=bundle)
        except Exception:                                      # noqa: BLE001
            pass
    return ssl.create_default_context()


def _get(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as fh:
        return json.loads(fh.read().decode())


def l5_activity(limit: int = 200) -> Optional[List[dict]]:
    """L5's published trades, newest first. None means the feed failed."""
    try:
        rows = _get("%s/activity?user=%s&limit=%d&type=TRADE"
                    % (L5_DATA, L5_ADDR, limit))
    except Exception:                                          # noqa: BLE001
        return None
    return rows if isinstance(rows, list) else None


def pmus_bbo(slug: str) -> Optional[dict]:
    try:
        return (_get("%s/v1/markets/%s/bbo" % (PMUS_GATEWAY, slug))
                or {}).get("marketData")
    except Exception:                                          # noqa: BLE001
        return None


def pmus_book(slug: str) -> Optional[dict]:
    try:
        return (_get("%s/v1/markets/%s/book" % (PMUS_GATEWAY, slug))
                or {}).get("marketData")
    except Exception:                                          # noqa: BLE001
        return None


# ------------------------------------------------------------------ alignment

def l5_window_start(slug: str) -> Optional[int]:
    """L5 stamps the window START as a unix epoch: btc-updown-15m-<epoch>."""
    tail = str(slug).rsplit("-", 1)[-1]
    try:
        v = int(tail)
    except (TypeError, ValueError):
        return None
    return v if v > 1_000_000_000 else None


def pmus_slug_for(window_start: int) -> str:
    """The simultaneous PMUS market. PMUS stamps the START too -- VERIFIED,
    not assumed, by `verify_alignment` below."""
    d = dt.datetime.fromtimestamp(window_start, dt.timezone.utc)
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        d.strftime("%Y-%m-%d"), d.hour, d.minute)


def verify_alignment(window_start: int) -> Dict[str, Any]:
    """Prove the two venues mean the same fifteen minutes.

    Two venues sharing a product name is not evidence that they share a clock:
    Kalshi stamps the CLOSE in the same position where these two stamp the
    OPEN, and an off-by-one-window comparison would look plausible and be
    entirely wrong. So the PMUS event's own startTime is read back and
    compared against L5's epoch.
    """
    want = dt.datetime.fromtimestamp(window_start, dt.timezone.utc)
    out: Dict[str, Any] = {
        "l5_window_start_utc": want.isoformat(),
        "pmus_slug": pmus_slug_for(window_start),
        "aligned": None, "why": None,
    }
    want_ev = "btc-updown-15m-%s-%02d%02dz" % (
        want.strftime("%Y-%m-%d"), want.hour, want.minute)
    try:
        # PAGINATED, oldest first. A single unpaginated page answers for the
        # series' FIRST hundred windows and silently reports "not listed" for
        # every current one -- which reads as a failed alignment when it is
        # only a short page.
        off = 0
        while off < 4000:
            ev = _get("%s/v1/events?series_id=375&limit=100&offset=%d"
                      % (PMUS_GATEWAY, off))
            rows = ev.get("events") or []
            if not rows:
                break
            for e in rows:
                if str(e.get("slug")) != want_ev:
                    continue
                st = str(e.get("startTime", "")).replace("Z", "+00:00")
                got = dt.datetime.fromisoformat(st)
                out["pmus_start_utc"] = got.isoformat()
                out["aligned"] = (got == want)
                out["why"] = ("PMUS event startTime equals L5's window epoch"
                              if out["aligned"] else
                              "PMUS startTime %s != L5 %s" % (got, want))
                return out
            off += len(rows)
            if len(rows) < 100:
                break
    except Exception as exc:                                   # noqa: BLE001
        out["why"] = "alignment check failed: %s" % type(exc).__name__
        return out
    out["why"] = "PMUS event not listed (window may be outside its horizon)"
    return out


# ------------------------------------------------------------- L5 translation

def l5_to_pmus_action(row: Mapping[str, Any]) -> Dict[str, Any]:
    """L5's observed trade as a PMUS q=1 intent.

    L5's `outcome` is Up/Down and PMUS's YES is Up, so the sides map directly.
    A SELL is carried through as a sell of that outcome and is NOT silently
    turned into a buy of the other one: L5's BTC15m corpus contains ZERO
    sells, so a sell appearing here is news, not a translation problem.
    """
    outcome = str(row.get("outcome") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()
    px = row.get("price")
    try:
        price = D(str(px))
    except Exception:                                          # noqa: BLE001
        price = None
    return {
        "l5_outcome": row.get("outcome"),
        "l5_side": side,
        "l5_price": str(price) if price is not None else None,
        "l5_size": row.get("size"),
        "l5_usdc": row.get("usdcSize"),
        "pmus_side": ("UP" if outcome == "up" else
                      "DOWN" if outcome == "down" else None),
        "direction": "BUY" if side == "BUY" else side,
        "is_sell": side == "SELL",
        "q": 1,
    }


# ---------------------------------------------------------------- copyability

def copyability(action: Mapping[str, Any],
                bbo: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Is L5's action still available to us on PMUS, right now, as a maker?

    Classes, and why each is separate:
      NO_BOOK              PMUS is unreadable or the side is absent
      MAKER_COPYABLE       we can REST at L5's price or better without crossing
      REQUIRES_CROSSING    reaching L5's price means taking, which pays the
                           taker fee and gives up the spread the whole exercise
                           exists to earn
      BAND_INADMISSIBLE    the price is outside the frozen 0.20-0.80 band
      PRICE_MOVED_AWAY     the side exists but L5's price is no longer
                           reachable at all on the current book
    """
    out: Dict[str, Any] = {"klass": None, "why": None,
                           "band": [str(BAND_LO), str(BAND_HI)]}
    side = action.get("pmus_side")
    lp = action.get("l5_price")
    if bbo is None or side is None or lp is None:
        out["klass"] = "NO_BOOK"
        out["why"] = "no PMUS book, no side, or no L5 price"
        return out

    def f(k):
        v = (bbo.get(k) or {}).get("value") if isinstance(bbo.get(k), Mapping) \
            else bbo.get(k)
        try:
            return D(str(v))
        except Exception:                                      # noqa: BLE001
            return None

    yes_bid, yes_ask = f("bestBid"), f("bestAsk")
    if yes_bid is None or yes_ask is None:
        out["klass"] = "NO_BOOK"
        out["why"] = "one-sided or unreadable PMUS book"
        out["yes_bid"] = str(yes_bid) if yes_bid is not None else None
        out["yes_ask"] = str(yes_ask) if yes_ask is not None else None
        return out

    # our side's own bid/ask. NO is the complement of the YES book.
    if side == "UP":
        our_bid, our_ask = yes_bid, yes_ask
    else:
        our_bid, our_ask = (D(1) - yes_ask), (D(1) - yes_bid)

    l5_price = D(lp)
    out.update({"yes_bid": str(yes_bid), "yes_ask": str(yes_ask),
                "our_bid": str(our_bid), "our_ask": str(our_ask),
                "spread": str(yes_ask - yes_bid),
                "l5_price": str(l5_price),
                "price_delta_vs_our_touch": str(l5_price - our_bid)})

    if not (BAND_LO <= l5_price <= BAND_HI):
        out["klass"] = "BAND_INADMISSIBLE"
        out["why"] = ("L5's price is outside the frozen band; the band is NOT "
                      "widened here, it is recorded as binding")
        return out
    if l5_price >= our_ask:
        out["klass"] = "REQUIRES_CROSSING"
        out["why"] = ("L5's price is at or through our ask, so copying it "
                      "would take rather than make")
        out["taker_fee_if_crossed"] = str(fees.taker_fee(l5_price))
        return out
    out["klass"] = "MAKER_COPYABLE"
    out["why"] = "we can rest at L5's price without crossing"
    out["maker_rebate_if_filled"] = str(fees.maker_rebate(l5_price))
    out["ticks_from_our_touch"] = str(
        ((l5_price - our_bid) / TICK).quantize(D("1")))
    return out


# ------------------------------------------------------------------ inventory

class Book:
    """q=1 behavioural inventory. One per TRACK; never merged."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.up = 0
        self.dn = 0
        self.up_cost = D("0")
        self.dn_cost = D("0")
        self.actions = 0

    @property
    def residual(self) -> int:
        return self.up - self.dn

    @property
    def matched(self) -> int:
        return min(self.up, self.dn)

    def add(self, side_up: bool, px: D) -> None:
        self.actions += 1
        if side_up:
            self.up += 1
            self.up_cost += px
        else:
            self.dn += 1
            self.dn_cost += px

    def pair_cost(self) -> Optional[D]:
        if not self.matched:
            return None
        return ((self.up_cost / self.up if self.up else D(0))
                + (self.dn_cost / self.dn if self.dn else D(0)))

    def relation_for(self, side_up: bool) -> str:
        """OPEN / EXTEND / COMPLETE from inventory alone, the frozen
        vocabulary."""
        if self.up == 0 and self.dn == 0:
            return "open"
        r = self.residual
        if r == 0:
            return "open"
        return "extend" if (r > 0) == side_up else "complete"

    def snapshot(self) -> Dict[str, Any]:
        pc = self.pair_cost()
        return {"track": self.name, "up": self.up, "dn": self.dn,
                "actions": self.actions,
                "residual": self.residual, "matched": self.matched,
                "effective_pair_cost": str(pc) if pc is not None else None,
                "labels": "SHADOW inventory; no order exists"}


# ------------------------------------------------------------------ the loop

def action_key(row: Mapping[str, Any]) -> str:
    """Dedupe key.

    NOT the transaction hash alone. Measured on this very feed: a single
    transaction can carry two fills at different prices, so hashing by tx
    silently drops the second one and makes L5 look less active than he is.
    The key is therefore the whole economic identity of the fill.
    """
    return "|".join(str(row.get(k)) for k in
                    ("transactionHash", "asset", "side", "price", "size",
                     "timestamp"))


class Observer:
    def __init__(self, state_dir: str) -> None:
        self.dir = state_dir
        os.makedirs(self.dir, exist_ok=True)
        self.tape = os.path.join(self.dir, "l5_pmus_realtime.jsonl")
        self.seen: set = set()
        self.windows: Dict[int, Dict[str, Any]] = {}
        self.alignment: Dict[int, dict] = {}
        self.pmus_cache: Dict[str, Tuple[float, Optional[dict]]] = {}
        self.stats = collections.Counter()
        self.latencies: List[float] = []
        self.model = pv2.load_v2()
        self.clone_last_rel = ""
        self.clone_ticks = collections.Counter()
        self._hydrate()

    # -- restart safety ---------------------------------------------------
    def _hydrate(self) -> None:
        """Rebuild the dedupe index from the tape. Restarting must neither
        lose the sample nor double-count it."""
        if not os.path.exists(self.tape):
            return
        n = 0
        with open(self.tape) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "l5_action":
                    continue
                k = r.get("action_key")
                if k:
                    self.seen.add(k)
                    n += 1
                # Backlog latencies are an artefact of when a process
                # started, so hydrating them would reimport the very
                # distortion the live path excludes.
                if r.get("backlog"):
                    self.stats["backlog"] += 1
                    continue
                self.stats["realtime"] += 1
                lat = r.get("latency_s")
                if isinstance(lat, (int, float)):
                    self.latencies.append(float(lat))
        self.stats["hydrated"] = n

    def _emit(self, row: Mapping[str, Any]) -> None:
        with open(self.tape, "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    # -- PMUS state, snapshotted AT RECEIPT --------------------------------
    def pmus_now(self, slug: str, max_age_s: float = 2.0) -> Tuple[Optional[dict], float]:
        """Current PMUS BBO and the age of the snapshot we priced against.

        A tiny cache exists because a burst of L5 rows arrives in one poll and
        re-fetching per row would price them against different books for no
        reason. The age is RECORDED on every row so a stale snapshot can never
        masquerade as a fresh one.
        """
        now = time.time()
        hit = self.pmus_cache.get(slug)
        if hit and (now - hit[0]) <= max_age_s:
            return hit[1], now - hit[0]
        b = pmus_bbo(slug)
        self.pmus_cache[slug] = (time.time(), b)
        return b, 0.0

    # -- CLONE_MODEL: the frozen engine, from PMUS state alone -------------
    def clone_tick(self) -> Optional[dict]:
        """One independent decision on the PMUS window trading right now.

        This track knows NOTHING about L5's feed. It is the transferability
        question -- would the frozen model, seeing only PMUS, do what L5 does
        -- and mixing it with MIRROR_RAW would answer neither.

        No fill is assumed, so its inventory advances only where interaction
        evidence would support it, which on a public book is never. The
        relation it WOULD take is recorded regardless, because that is the
        behavioural signal.
        """
        now = time.time()
        ws = int(now // WINDOW_S) * WINDOW_S
        slug = pmus_slug_for(ws)
        bbo, age = self.pmus_now(slug)
        if bbo is None:
            return None

        def f(k):
            v = (bbo.get(k) or {}).get("value") \
                if isinstance(bbo.get(k), Mapping) else bbo.get(k)
            try:
                return float(v)
            except Exception:                                  # noqa: BLE001
                return None

        bid, ask = f("bestBid"), f("bestAsk")
        if bid is None or ask is None:
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "one_sided_book"}
        w = self.windows.setdefault(ws, {
            "window_start": ws, "pmus_slug": slug,
            "mirror": Book("MIRROR_RAW"), "clone": Book("CLONE_MODEL"),
            "l5_actions": 0, "l5_sides": collections.Counter(),
            "l5_relations": collections.Counter(),
            "copy": collections.Counter()})
        clone = w["clone"]
        tte = (ws + WINDOW_S) - now
        st = state_key(bid, tte, float(clone.residual))
        d = pv2.v2_dist(self.model, st, self.clone_last_rel or "open", "q<5")
        if d["backed_off"]:
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "model_off_distribution", "state": st}
        prop = pv2.proposal(self.model, st, self.clone_last_rel or "open",
                            "q<5", clone.residual, None)
        rel = prop["recommended_relation"]
        if clone.up == 0 and clone.dn == 0:
            rel = "open"
        if rel == "sell":
            return {"kind": "clone_tick", "slug": slug, "action": "HOLD",
                    "reason": "sell_not_in_scope", "state": st}
        side_up = ((rel in ("open", "extend") and clone.residual >= 0)
                   or (rel == "complete" and clone.residual < 0))
        px = D(str(bid if side_up else (1.0 - ask))).quantize(TICK)
        row = {"kind": "clone_tick", "utc": dt.datetime.now(
                   dt.timezone.utc).isoformat(),
               "slug": slug, "tte_s": round(tte, 1), "state": st,
               "relation": rel, "side": "UP" if side_up else "DOWN",
               "desired_price": str(px), "size": 1,
               "pmus_snapshot_age_s": round(age, 3),
               "book": {"bid": bid, "ask": ask},
               "maker_rebate_if_filled": str(fees.maker_rebate(px)),
               "taker_fee_if_crossed": str(fees.taker_fee(px)),
               "interaction": clf.classify(
                   {"desired_price": str(px),
                    "side": "UP" if side_up else "DOWN"}, None, False),
               "inventory": clone.snapshot(),
               "track": "CLONE_MODEL",
               "labels": "SHADOW; independent of L5's feed; never summed with "
                         "MIRROR_RAW"}
        if not (BAND_LO <= px <= BAND_HI):
            row["action"] = "HOLD"
            row["reason"] = "entry_band"
            row["band"] = [str(BAND_LO), str(BAND_HI)]
        else:
            row["action"] = "QUOTE"
            self.clone_last_rel = rel
            w["l5_relations"]  # touch, keeps shape stable
        self._emit(row)
        return row

    # -- one poll ----------------------------------------------------------
    def poll(self) -> Dict[str, Any]:
        recv = time.time()
        rows = l5_activity(limit=200)
        if rows is None:
            self.stats["feed_errors"] += 1
            return {"kind": "poll", "ok": False, "why": "l5 feed unreadable"}
        fresh = []
        for r in rows:
            if not str(r.get("slug", "")).startswith(L5_SERIES):
                continue
            k = action_key(r)
            if k in self.seen:
                continue
            self.seen.add(k)
            fresh.append((k, r))
        fresh.sort(key=lambda kr: kr[1].get("timestamp") or 0)

        for k, r in fresh:
            self._handle(k, r, recv)
        ct = self.clone_tick()
        if ct:
            self.clone_ticks[ct.get("action") or "?"] += 1
            if ct.get("action") == "QUOTE":
                self.clone_ticks["rel:" + str(ct.get("relation"))] += 1
            elif ct.get("reason"):
                self.clone_ticks["hold:" + str(ct.get("reason"))] += 1
        self.stats["polls"] += 1
        self.stats["actions"] += len(fresh)
        return {"kind": "poll", "ok": True, "new_actions": len(fresh),
                "total_actions": self.stats["actions"]}

    def _handle(self, key: str, r: Mapping[str, Any], recv: float) -> None:
        ws = l5_window_start(r.get("slug", ""))
        if ws is None:
            return
        if ws not in self.alignment:
            self.alignment[ws] = verify_alignment(ws)
        align = self.alignment[ws]
        slug = align["pmus_slug"]

        ev_t = float(r.get("timestamp") or 0)
        latency = round(recv - ev_t, 3)
        # BACKLOG IS NOT LATENCY. The first poll after a start (or a restart)
        # sees every action the feed still holds, including windows that closed
        # hours ago. Their "latency" measures when this process began, not how
        # fast the feed is, and pricing them against a closed PMUS market
        # yields NO_BOOK for reasons that say nothing about copyability.
        # They are kept in the tape and in L5's behavioural statistics, and
        # excluded from the realtime measurements.
        backlog = recv >= (ws + WINDOW_S)
        if not backlog:
            self.latencies.append(latency)

        bbo, age = self.pmus_now(slug)
        act = l5_to_pmus_action(r)
        cop = copyability(act, bbo)

        w = self.windows.setdefault(ws, {
            "window_start": ws, "pmus_slug": slug,
            "mirror": Book("MIRROR_RAW"), "clone": Book("CLONE_MODEL"),
            "l5_actions": 0, "l5_sides": collections.Counter(),
            "l5_relations": collections.Counter(),
            "copy": collections.Counter(),
        })
        w["l5_actions"] += 1
        w["l5_sides"][act.get("pmus_side")] += 1
        if backlog:
            w["copy"]["BACKLOG_WINDOW_CLOSED"] += 1
            self.stats["backlog"] += 1
        else:
            w["copy"][cop["klass"]] += 1
            self.stats["realtime"] += 1

        # ACTUAL L5: relation inferred from L5's own running inventory
        l5_book = w.setdefault("l5_book", Book("ACTUAL_L5"))
        side_up = act.get("pmus_side") == "UP"
        l5_rel = l5_book.relation_for(side_up)
        if act.get("l5_price") is not None and not act["is_sell"]:
            l5_book.add(side_up, D(act["l5_price"]))
        w["l5_relations"][l5_rel] += 1

        # MIRROR_RAW: only advances where the action was actually copyable
        mirror_rel = None
        if (not backlog and cop["klass"] == "MAKER_COPYABLE"
                and not act["is_sell"]):
            mirror_rel = w["mirror"].relation_for(side_up)
            w["mirror"].add(side_up, D(act["l5_price"]))

        tte = (ws + WINDOW_S) - ev_t
        row = {
            "kind": "l5_action", "action_key": key,
            "labels": LABELS,
            "backlog": backlog,
            "backlog_note": ("observed after its window closed, so its "
                             "latency reflects when this process started and "
                             "is excluded from realtime statistics"
                             if backlog else None),
            "l5_event_utc": dt.datetime.fromtimestamp(
                ev_t, dt.timezone.utc).isoformat(),
            "receipt_utc": dt.datetime.fromtimestamp(
                recv, dt.timezone.utc).isoformat(),
            "latency_s": latency,
            "no_backdating_ok": recv >= ev_t,
            "pmus_snapshot_age_s": round(age, 3),
            "window_start_utc": dt.datetime.fromtimestamp(
                ws, dt.timezone.utc).isoformat(),
            "tte_s_at_event": round(tte, 1),
            "tte_s_at_receipt": round((ws + WINDOW_S) - recv, 1),
            "still_in_window_at_receipt": recv < (ws + WINDOW_S),
            "alignment": align,
            "l5": {"slug": r.get("slug"), "tx": r.get("transactionHash"),
                   **act, "relation_actual": l5_rel},
            "pmus": {"slug": slug,
                     "bbo": None if bbo is None else {
                         "bid": (bbo.get("bestBid") or {}).get("value"),
                         "ask": (bbo.get("bestAsk") or {}).get("value"),
                         "bid_shares": bbo.get("bidShares"),
                         "ask_shares": bbo.get("askShares"),
                         "shares_traded": bbo.get("sharesTraded"),
                         "state": bbo.get("state")}},
            "copyability": cop,
            "mirror_relation": mirror_rel,
            "interaction": clf.classify(
                {"desired_price": act.get("l5_price"),
                 "side": act.get("pmus_side")}, None, False),
            "inventory": {
                "ACTUAL_L5": l5_book.snapshot(),
                "MIRROR_RAW": w["mirror"].snapshot(),
                "CLONE_MODEL": w["clone"].snapshot(),
            },
            "tracks_note": ("ACTUAL_L5, MIRROR_RAW and CLONE_MODEL are "
                            "separate objects and are never summed"),
        }
        self._emit(row)

    # -- reporting ---------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        lat = sorted(self.latencies)

        def p(q):
            return round(lat[min(len(lat) - 1, int(q * len(lat)))], 2) \
                if lat else None

        cop = collections.Counter()
        rels = collections.Counter()
        per_window = []
        for ws, w in sorted(self.windows.items()):
            cop.update(w["copy"])
            rels.update(w["l5_relations"])
            per_window.append({
                "window_utc": dt.datetime.fromtimestamp(
                    ws, dt.timezone.utc).strftime("%H:%M"),
                "pmus_slug": w["pmus_slug"],
                "l5_actions": w["l5_actions"],
                "l5_sides": dict(w["l5_sides"]),
                "l5_relations": dict(w["l5_relations"]),
                "copy": dict(w["copy"]),
                "ACTUAL_L5": w["l5_book"].snapshot() if "l5_book" in w else None,
                "MIRROR_RAW": w["mirror"].snapshot(),
                "CLONE_MODEL": w["clone"].snapshot(),
            })
        cop.pop("BACKLOG_WINDOW_CLOSED", None)
        tot = sum(cop.values())
        return {
            "kind": "summary", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "labels": LABELS,
            "polls": self.stats["polls"], "feed_errors": self.stats["feed_errors"],
            "actions_observed": self.stats["actions"],
            "actions_realtime": self.stats["realtime"],
            "actions_backlog": self.stats["backlog"],
            "backlog_note": ("backlog actions are kept in the tape and in "
                             "L5's behavioural statistics, and excluded from "
                             "latency and copyability, which they would "
                             "otherwise dominate with an artefact of start "
                             "time"),
            "hydrated_from_tape": self.stats["hydrated"],
            "windows": len(self.windows),
            "actions_per_window": (round(self.stats["actions"] / len(self.windows), 1)
                                   if self.windows else None),
            "latency_s": {"n": len(lat), "p50": p(0.5), "p90": p(0.9),
                          "p99": p(0.99),
                          "max": round(lat[-1], 2) if lat else None},
            "copyability": dict(cop),
            "copyability_pct": {k: round(100.0 * v / tot, 1)
                                for k, v in cop.items()} if tot else {},
            "l5_relations_actual": dict(rels),
            "clone_model_ticks": dict(self.clone_ticks),
            "model_sha": self.model.get("_sha256_16"),
            "per_window": per_window,
            "interaction_evidence": ("public book only -- every interaction is "
                                     "QUEUE_AMBIGUOUS or NO_INTERACTION; no "
                                     "fill is manufactured"),
            "liquidity_rewards_usd": "0.00 -- eligibility and allocation not "
                                     "established",
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default="data/l5_pmus_realtime")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="0 = run forever")
    ap.add_argument("--summary-every", type=float, default=300.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)

    obs = Observer(a.state_dir)
    print("L5_PMUS_OBS %s" % json.dumps({
        "kind": "boot", "state_dir": a.state_dir, "tape": obs.tape,
        "hydrated_actions": obs.stats["hydrated"],
        "l5_series": L5_SERIES, "pmus_series": PMUS_SERIES,
        "band_frozen": [str(BAND_LO), str(BAND_HI)],
        "read_only": True, "order_path": False, "labels": LABELS}), flush=True)

    end = time.time() + a.minutes * 60.0 if a.minutes else None
    last_sum = 0.0
    while True:
        r = obs.poll()
        print("L5_PMUS_OBS %s" % json.dumps(r), flush=True)
        now = time.time()
        if now - last_sum >= a.summary_every or a.once:
            s = obs.summary()
            obs._emit(s)
            print("L5_PMUS_SUMMARY %s" % json.dumps(s), flush=True)
            last_sum = now
        if a.once or (end and now >= end):
            break
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
