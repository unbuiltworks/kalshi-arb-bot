"""READ-ONLY frozen-L5 shadow over PMUS BTC15m, from the public book tape.

Nothing here is new strategy. It is `src/kalshi_l5_shadow.py`'s structure with
a PMUS book source: the SAME frozen persistence-v2 action model
(`src.pmus_l5_persistence_v2`), the SAME state discretization
(`src.polymarket_l5.action_model.state_key`), the SAME behavioral OPEN /
EXTEND / COMPLETE vocabulary. No parameter is tuned here and none may be
tuned against these first windows.

WHY PMUS BTC15m IS A DIFFERENT KIND OF INPUT FROM PMUS SPORTS. The sports lane
has no expiry, so 635/635 of its decisions collapsed into one TTE bucket. A
BTC15m market serves `startTime` and `endDate` exactly fifteen minutes apart,
so seconds-to-expiry is a FACT and the model keys on literal TTE -- the
fidelity the sports lane never had.

THE FILL PROBLEM, AND WHY THERE ARE TWO TRACKS.

The public gateway has no trade tape (`/trades` is 404) and its book is a ~30
second cache, so nothing here can observe a print, a queue position, or a
taker side. The owner's rule is explicit: a fill must NOT be inferred merely
because a cached book later moved through our price. So:

  * TRACK A -- DECISIONS. Inventory is held at zero and the controller is asked,
    at every decision instant, what it wants. This answers "does it OPEN, at
    what side, price and size, and why" with NO fill assumption anywhere. It
    is the primary track and it is assumption-free.

  * TRACK B -- ASSUMED-FILL COUNTERFACTUAL. Inventory cannot evolve without
    fills, and questions like "does it repeatedly EXTEND" or "can inventory
    cross zero" are unanswerable at zero inventory. Track B therefore advances
    inventory under ONE named assumption, stated on every row it produces:

        ASSUMPTION_VOLUME_AT_TOUCH: a maker quote resting AT the touch is
        assumed filled for one unit when `sharesTraded` strictly increased
        over the interval the quote was live.

    That is an assumption about volume, not about book movement, and it is
    still an assumption. Every Track B figure carries
    `fill_class: ASSUMED_FILL_SHADOW` and `fill_evidence: none`. None of it is
    a fill, none of it is realized, and the two tracks are never summed.

Read-only and keyless: it reads a JSONL tape off disk and has no venue client,
no credential, and no order path. `order_entry` is False on every row.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_fees as fees
from src import pmus_l5_persistence_v2 as pv2
from src.l5_venue_adapter import PMUS_PRODUCT_CRYPTO_UPDOWN, PMUSAdapter
from src.polymarket_l5.action_model import state_key

#: the controller's decision cadence, matched to the Kalshi lane
DECISION_INTERVAL_S = 30.0
#: never act inside the settlement margin
SETTLEMENT_CUTOFF_S = 60.0
#: the public book is a ~30 s cache, so a "fresh" snapshot means one cache age
STALE_BOOK_TTL_S = 45.0
MAX_ACTIONS_PER_WINDOW = 12
TICK = D("0.01")

LABELS = ("SHADOW/THEORETICAL frozen-L5 observer over the PMUS public book "
          "tape. No print, queue position or taker side is observable here, "
          "so nothing is FILLED and nothing is REALIZED. order_entry=False "
          "structurally.")


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _epoch(iso: Any) -> Optional[float]:
    import datetime as dt
    if iso is None:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def window_close_epoch(slug: str) -> Optional[float]:
    """The window's hard expiry, from the slug's own stamp.

    `cpc-btc-updown-15m-2026-09-23-1345z` opens 13:45Z and closes 14:00Z. The
    clock is read from the identifier rather than from a metadata call so the
    replay needs nothing but the tape.
    """
    import datetime as dt
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$", str(slug))
    if not m:
        return None
    y, mo, dd, hh, mm = (int(x) for x in m.groups())
    start = dt.datetime(y, mo, dd, hh, mm, tzinfo=dt.timezone.utc)
    return (start + dt.timedelta(minutes=15)).timestamp()


class Inventory:
    """Behavioral q=1 units, exact Decimal cost. UP == YES."""

    def __init__(self) -> None:
        self.up = 0
        self.dn = 0
        self.up_cost = D("0")
        self.dn_cost = D("0")

    @property
    def residual(self) -> int:
        return self.up - self.dn

    @property
    def matched(self) -> int:
        return min(self.up, self.dn)

    def avg(self, up_side: bool) -> Optional[D]:
        n, c = (self.up, self.up_cost) if up_side else (self.dn, self.dn_cost)
        return (c / n) if n else None

    def record(self, side_up: bool, px: D) -> None:
        if side_up:
            self.up += 1
            self.up_cost += px
        else:
            self.dn += 1
            self.dn_cost += px

    def pair_cost(self) -> Optional[D]:
        """Effective cost of the matched pairs actually held. None when no
        pair exists -- an unmatched leg has no pair cost, and reporting one
        would invent economics."""
        if self.matched == 0:
            return None
        return ((self.up_cost / self.up if self.up else D(0))
                + (self.dn_cost / self.dn if self.dn else D(0)))


def load_tape(pattern: str) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    for path in sorted(glob.glob(pattern)):
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "bbo" or not r.get("slug"):
                    continue
                by.setdefault(r["slug"], []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: r.get("t") or "")
    return by


def _book(row: Mapping[str, Any]) -> Dict[str, Any]:
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    return {
        "t": _epoch(row.get("t")),
        "yes_bid": bid, "yes_ask": ask,
        # NO side is the complement of the YES book, as on every binary venue
        "no_bid": (1.0 - ask) if ask is not None else None,
        "no_ask": (1.0 - bid) if bid is not None else None,
        "bid_shares": _f(row.get("bid_shares")),
        "ask_shares": _f(row.get("ask_shares")),
        "shares_traded": _f(row.get("shares_traded")),
        "state": row.get("state"),
    }


def replay_window(slug: str, rows: List[dict], model: Mapping[str, Any],
                  *, assume_fills: bool) -> dict:
    """One window, chronological, no hindsight."""
    adapter = PMUSAdapter(PMUS_PRODUCT_CRYPTO_UPDOWN)
    close_t = window_close_epoch(slug)
    out: Dict[str, Any] = {
        "market": slug,
        "venue": "PMUS", "product": "btc-updown-15m",
        "model_sha": model.get("_sha256_16"),
        "track": "B_ASSUMED_FILL" if assume_fills else "A_DECISIONS_ONLY",
        "fill_assumption": ("ASSUMPTION_VOLUME_AT_TOUCH: a quote at the touch "
                            "is assumed filled for one unit when sharesTraded "
                            "strictly increased while it was live. AN "
                            "ASSUMPTION, NOT EVIDENCE." if assume_fills
                            else "NONE -- inventory is held at zero"),
        "settlement": adapter.settlement(),
        "provenance": adapter.provenance(),
        "fee_schedule": fees.provenance(),
        "close_utc_epoch": close_t,
        "decisions": [], "blockers": [],
        "shadow_only": True, "order_entry": False, "realized_pnl": None,
        "labels": LABELS,
    }
    if close_t is None:
        out["blockers"].append("no_settlement_clock_in_slug")
        return out
    if not rows:
        out["blockers"].append("no_book_snapshots")
        return out

    books = [_book(r) for r in rows]
    books = [b for b in books if b["t"] is not None]
    if not books:
        out["blockers"].append("no_timestamped_snapshots")
        return out

    inv = Inventory()
    last_rel = ""
    t = books[0]["t"]
    # THE REPLAY ENDS WHERE THE TAPE ENDS. Walking past the last snapshot
    # produces a decision at every step whose "latest prior book" is the same
    # ageing one, so a window still in progress reports a wall of stale_book
    # holds that say nothing about the venue and everything about the clock.
    # A decision cannot be made on data that does not exist yet.
    stop_t = min(close_t - SETTLEMENT_CUTOFF_S, books[-1]["t"])
    n_actions = 0
    while t <= stop_t and n_actions < MAX_ACTIONS_PER_WINDOW:
        prior = [b for b in books if b["t"] < t]
        if not prior:
            t += DECISION_INTERVAL_S
            continue
        book = prior[-1]
        age = t - book["t"]
        rec: Dict[str, Any] = {
            "t_utc_epoch": round(t, 3),
            "tte_s": round(close_t - t, 1),
            "book": {"yes_bid": book["yes_bid"], "yes_ask": book["yes_ask"],
                     "no_bid": book["no_bid"], "no_ask": book["no_ask"],
                     "bid_shares": book["bid_shares"],
                     "ask_shares": book["ask_shares"],
                     "age_s": round(age, 1),
                     "spread": (round(book["yes_ask"] - book["yes_bid"], 4)
                                if book["yes_bid"] is not None
                                and book["yes_ask"] is not None else None)},
            "inventory": {"up": inv.up, "dn": inv.dn},
            "imbalance": inv.residual,
            "matched_inventory": inv.matched,
            "residual": inv.residual,
            "effective_pair_cost": (str(inv.pair_cost())
                                    if inv.pair_cost() is not None else None),
            "shadow_only": True, "order_entry": False,
        }
        if age > STALE_BOOK_TTL_S:
            rec.update({"action": "HOLD", "reason": "stale_book",
                        "detail": "snapshot older than one cache age"})
            out["decisions"].append(rec)
            out["blockers"].append("stale_book@%.0f" % t)
            t += DECISION_INTERVAL_S
            continue
        if book["yes_bid"] is None or book["yes_ask"] is None:
            rec.update({"action": "HOLD", "reason": "one_sided_book"})
            out["decisions"].append(rec)
            out["blockers"].append("one_sided_book@%.0f" % t)
            t += DECISION_INTERVAL_S
            continue

        tte = close_t - t
        st = state_key(book["yes_bid"], tte, float(inv.residual))
        rec["state"] = st
        d = pv2.v2_dist(model, st, last_rel or "open", "q<5")
        if d["backed_off"]:
            rec.update({"action": "HOLD", "reason": "model_off_distribution",
                        "detail": "state not in the frozen model's support"})
            out["decisions"].append(rec)
            out["blockers"].append("off_distribution@%.0f" % t)
            t += DECISION_INTERVAL_S
            continue

        avg_surplus = inv.avg(inv.residual > 0) if inv.residual else None
        prop = pv2.proposal(model, st, last_rel or "open", "q<5",
                            inv.residual,
                            str(avg_surplus) if avg_surplus else None)
        rel = prop["recommended_relation"]
        if inv.up == 0 and inv.dn == 0:
            rel = "open"
        if rel == "sell":
            rec.update({"action": "HOLD", "relation": "sell",
                        "reason": "sell_not_in_scope",
                        "detail": "L5's BTC15m corpus contains zero sells"})
            out["decisions"].append(rec)
            t += DECISION_INTERVAL_S
            continue

        side_up = ((rel in ("open", "extend") and inv.residual >= 0)
                   or (rel == "complete" and inv.residual < 0))
        # maker join at the touch on the side we want
        px = D(str(book["yes_bid"] if side_up
                   else book["no_bid"])).quantize(TICK)
        near_touch = True
        crosses = (side_up and float(px) >= (book["yes_ask"] or 1.0)) or \
                  ((not side_up) and float(px) >= (book["no_ask"] or 1.0))
        if crosses:
            rec.update({"action": "HOLD", "relation": rel,
                        "reason": "would_cross",
                        "detail": "a maker join at the touch would take"})
            out["decisions"].append(rec)
            t += DECISION_INTERVAL_S
            continue
        if not adapter.entry_ok(px):
            rec.update({
                "action": "HOLD", "relation": rel,
                "side": "UP" if side_up else "DOWN",
                "desired_price": str(px),
                "reason": "entry_band",
                "detail": ("the PMUS executor's band is 0.20-0.80 and this "
                           "price is outside it. THE BAND IS A SPORTS "
                           "PARAMETER and is NOT widened here; it is recorded "
                           "as binding and left for a separate review.")})
            out["decisions"].append(rec)
            t += DECISION_INTERVAL_S
            continue

        rec.update({
            "action": "QUOTE", "relation": rel,
            "side": "UP" if side_up else "DOWN",
            "desired_price": str(px), "size": 1,
            "native_l5_action": rel,
            "near_touch": near_touch,
            "tick": str(TICK),
            "fee_economics": {
                "maker_fee_charged": "0",
                "taker_fee_if_taken": str(fees.taker_fee(px)),
                "note": "maker pays nothing; the taker figure is what a "
                        "cross would have cost, for comparison only",
            },
            "maker_economics": {
                "rebate_if_filled": str(fees.maker_rebate(px)),
                "status": "ACCRUAL_ONLY -- credited at the point of trade by "
                          "the venue, but no trade exists here. Excluded from "
                          "pre-rebate P&L and from strategy selection.",
            },
            "reason": "frozen model recommends %s at this state" % rel,
            "fill_class": "ASSUMED_FILL_SHADOW" if assume_fills else "NO_FILL_MODEL",
            "fill_evidence": "none -- the public gateway serves no trade tape",
        })

        filled = False
        if assume_fills:
            until = min(t + DECISION_INTERVAL_S, stop_t)
            after = [b for b in books if t <= b["t"] <= until]
            grew = bool(after and book["shares_traded"] is not None
                        and after[-1]["shares_traded"] is not None
                        and after[-1]["shares_traded"] > book["shares_traded"])
            rec["assumed_fill_basis"] = {
                "shares_traded_before": book["shares_traded"],
                "shares_traded_after": (after[-1]["shares_traded"]
                                        if after else None),
                "grew": grew,
            }
            if grew:
                inv.record(side_up, px)
                filled = True
        rec["assumed_filled"] = filled
        rec["inventory_after"] = {"up": inv.up, "dn": inv.dn,
                                  "residual": inv.residual,
                                  "matched": inv.matched}
        last_rel = rel
        n_actions += 1
        out["decisions"].append(rec)
        t += DECISION_INTERVAL_S

    out["summary"] = summarize(out["decisions"], inv)
    return out


def summarize(decisions: List[dict], inv: Inventory) -> dict:
    quotes = [d for d in decisions if d.get("action") == "QUOTE"]
    holds = [d for d in decisions if d.get("action") == "HOLD"]
    rels: Dict[str, int] = {}
    for q in quotes:
        rels[q["relation"]] = rels.get(q["relation"], 0) + 1
    hold_reasons: Dict[str, int] = {}
    for h in holds:
        hold_reasons[h["reason"]] = hold_reasons.get(h["reason"], 0) + 1
    return {
        "decision_points": len(decisions),
        "quotes": len(quotes), "holds": len(holds),
        "relations": rels, "hold_reasons": hold_reasons,
        "terminal_inventory": {"up": inv.up, "dn": inv.dn,
                               "residual": inv.residual,
                               "matched": inv.matched},
        "terminal_pair_cost": (str(inv.pair_cost())
                               if inv.pair_cost() is not None else None),
        "realized_pnl": None,
        "labels": "every count above is SHADOW; no order existed",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default="data/pmus_btc15m/*.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--assume-fills", action="store_true",
                    help="Track B: advance inventory under the named "
                         "volume-at-touch assumption")
    a = ap.parse_args(argv)
    model = pv2.load_v2()
    by = load_tape(a.tape)
    rows = [replay_window(slug, rs, model, assume_fills=a.assume_fills)
            for slug, rs in sorted(by.items())]
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"label": "PMUS_BTC15M_L5_SHADOW_V1",
                       "windows": rows}, fh, indent=1, default=str)
    for r in rows:
        s = r.get("summary") or {}
        print("%s  track=%s" % (r["market"], r["track"]))
        print("   decisions %-4s quotes %-4s holds %-4s  relations %s"
              % (s.get("decision_points"), s.get("quotes"), s.get("holds"),
                 s.get("relations")))
        print("   hold reasons %s" % (s.get("hold_reasons"),))
        print("   terminal inventory %s  pair cost %s"
              % (s.get("terminal_inventory"), s.get("terminal_pair_cost")))
        if r["blockers"]:
            import collections
            b = collections.Counter(x.split("@")[0] for x in r["blockers"])
            print("   blockers %s" % dict(b))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
