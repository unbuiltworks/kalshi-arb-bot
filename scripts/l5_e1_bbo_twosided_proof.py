"""THE TWO-SIDED PMUS BBO PROOF. Read-only. One question, four readings.

WHAT IS ALREADY SETTLED, and what this closes.

A read-only capture on 2026-10-07T22:29:48Z settled the BID side with
three independent readings of `cpc-btc-updown-15m-2026-10-07-2215z` all
giving 0.9900 (REST /bbo `bestBid`, REST /book `bids[0]`, and the
authenticated websocket's own first frame), and established that the PMUS
AUTHENTICATED WEBSOCKET CARRIES NO bestBid/bestAsk AT ALL -- so E1's
first-leg price always comes from the ladder top.

That capture was ONE-SIDED (`offers: []`, `bestAsk: null`), because it
landed 842s into a 15-minute window where a BTC15m book has collapsed.
The ASK side therefore rested on the ladder-ordering proof and the
complement arithmetic, not on a direct reading. This closes that.

THE PROOF OBLIGATION, stated as the owner stated it. For one slug at one
instant, all four of these must read bid A / ask B:

    1. REST /v1/markets/{slug}/bbo   -> marketData.bestBid / bestAsk
    2. REST /v1/markets/{slug}/book  -> bids[0] / offers[0]
    3. the RAW authenticated WS frame -> its own bids[0] / offers[0]
    4. the AuthBook SNAPSHOT          -> bid / ask

and then: E1's YES maker first-leg price is A, and its NO maker first-leg
price is 1 - B in NO terms with the proven serializer emitting B as the
wire `price.value`.

READING 3 IS THE ONE THAT NEEDED WORK. AuthBook's `pre_book` only keeps
frames from BEFORE a slug's first two-sided book, so by the time there is
a two-sided snapshot those shapes are gone. So this probe WRAPS
`AuthBook.ingest` -- from the outside, without modifying AuthBook -- and
keeps the last raw book frames for the pinned slug. That makes reading 3
the venue's own bytes rather than a restatement of reading 4.

IT DOES NOT SHOP FOR A RESULT. The owner's instruction is explicit: if
WS/AuthBook disagrees with the venue's contemporaneous book, STOP. So the
FIRST two-sided state found is the one reported, agreement or not. The
only thing retried is the search for a two-sided state at all.

ORDERING IS IMPOSSIBLE BY CONSTRUCTION, NOT BY A FLAG:
  * every REST read goes through `pmus_auth.ReadOnlyTransport`, which
    refuses a non-GET in the TRANSPORT, not in a caller's flag;
  * `latch.consume_attempt` and `consume_complement` are REPLACED WITH
    RAISES before any work;
  * no order body, preview gate or submission module is imported, and
    `sys.modules` is checked afterwards to prove none appeared;
  * no order endpoint and no POST is named anywhere in this file.

Credentials are used: the only PMUS decision feed is authenticated.
Signing is not ordering.

Exit 0 = BBO PROVEN. 3 = no two-sided state found in budget.
4 = DISAGREEMENT (stop and report). 2 = no window resolvable.

Run:
    python3 -m scripts.l5_e1_bbo_twosided_proof
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Optional

ONE = D("1")

FORBIDDEN_MODULES = (
    "analysis.l5.live_exec2.orders",
    "src.l5_preview_gate",
    "src.pmus_q1_executor",
)

#: how long to hunt for a two-sided state, in total.
TOTAL_BUDGET_S = float(os.getenv("PMUS_E1_TWOSIDED_BUDGET_S") or 2400.0)
#: how long to sit on one window before rolling to the next.
PER_WINDOW_BUDGET_S = float(
    os.getenv("PMUS_E1_TWOSIDED_WINDOW_BUDGET_S") or 600.0)
POLL_S = 1.0

#: a REST reading taken more than this after the WS snapshot is not
#: "contemporaneous" and cannot adjudicate a disagreement.
MAX_SKEW_S = 10.0

OUT_DIR = (os.getenv("PMUS_E1_BBO_DIR")
           or "/state/pmus-ws/e1_bbo_semantics")


# ----------------------------------------------------------------- safety

def disable_the_latch() -> Dict[str, Any]:
    from analysis.l5.e1_cohort import latch as L

    def _refuse(*a: Any, **k: Any) -> Any:
        raise AssertionError(
            "REFUSED: a read-only BBO proof tried to consume an E1 latch "
            "slot")

    L.consume_attempt = _refuse            # type: ignore[assignment]
    L.consume_complement = _refuse         # type: ignore[assignment]
    return {"latch_consume_replaced_with_a_raise": True}


def assert_no_order_path() -> Dict[str, Any]:
    present = sorted(m for m in FORBIDDEN_MODULES if m in sys.modules)
    if present:
        raise SystemExit(
            "REFUSED: an order-capable module is loaded in a read-only "
            "proof: %r" % (present,))
    return {"forbidden_modules_loaded": [], "venue_post": "none",
            "order_constructed": False, "order_submitted": False,
            "latch_touched": False, "cohort_advanced": False}


def dec(x: Any) -> Optional[D]:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, dict):
        x = x.get("value", x.get("px"))
        if isinstance(x, dict):
            x = x.get("value")
    try:
        d = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


# --------------------------------------------------------- raw WS capture

class RawFrameTap:
    """Keeps the last raw book frames AuthBook ingested for one slug.

    Wraps `ingest` from the OUTSIDE. AuthBook is not modified: the
    wrapper records the message and then calls the original, so the fold
    sees exactly what it would have seen.
    """

    def __init__(self, book: Any, slug: str, keep: int = 6) -> None:
        self.slug = str(slug)
        self.keep = int(keep)
        self.frames: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self._original = book.ingest
        self._book = book
        book.ingest = self._tapped          # type: ignore[assignment]

    def _tapped(self, msg: Any, now: float) -> Any:
        try:
            self._record(msg, now)
        except Exception:                                   # noqa: BLE001
            pass            # a tap must never break the fold
        return self._original(msg, now)

    def _record(self, msg: Any, now: float) -> None:
        for shape in self._book.frame_shapes(msg):
            if str(shape.get("slug")) != self.slug:
                continue
            if shape.get("n_bids") is None and shape.get("n_offers") is None:
                continue                      # a trade frame, not a book
            with self.lock:
                self.frames.append({"t": now, "shape": shape,
                                    "raw": _prune(msg)})
                del self.frames[:-self.keep]

    def latest_two_sided(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            for f in reversed(self.frames):
                sh = f["shape"]
                if (sh.get("n_bids") or 0) > 0 and (sh.get("n_offers")
                                                    or 0) > 0:
                    return f
            return None

    def latest(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.frames[-1] if self.frames else None

    def restore(self) -> None:
        self._book.ingest = self._original  # type: ignore[assignment]


def _prune(msg: Any, depth: int = 0) -> Any:
    """The frame, with ladders capped so one record stays readable."""
    if depth > 6:
        return "...DEPTH"
    if isinstance(msg, dict):
        out = {}
        for k, v in msg.items():
            if k in ("bids", "offers", "asks") and isinstance(v, list):
                out[k] = [_prune(x, depth + 1) for x in v[:5]]
                out[k + "_n_levels"] = len(v)
            else:
                out[k] = _prune(v, depth + 1)
        return out
    if isinstance(msg, list):
        return [_prune(x, depth + 1) for x in msg[:5]]
    return msg


# -------------------------------------------------------------- REST reads

def rest_reads(slug: str) -> Dict[str, Any]:
    from analysis.l5.e1_cohort import pmus_identity as PI
    t = PI.read_only_transport()
    t0 = time.time()
    bbo = _tolerant(PI.bbo, slug, t)
    t1 = time.time()
    book = _tolerant(PI.book, slug, t)
    t2 = time.time()
    return {"bbo": {"t": t0, "raw": bbo},
            "book": {"t": t1, "raw": book, "top": book_top(book)},
            "elapsed_s": round(t2 - t0, 3)}


def _tolerant(fn: Any, slug: str, t: Any, tries: int = 3,
              pause: float = 3.0) -> Dict[str, Any]:
    """Read-only REST, retrying a transient refusal, NEVER inventing a
    body. `/v1/markets/{slug}/bbo` 404s at window open while
    `/v1/events/slug/{event}` already resolves the market -- measured."""
    last = None
    for i in range(tries):
        try:
            got = fn(slug, t)
            if isinstance(got, dict) and got:
                return got
            last = "EMPTY_BODY"
        except Exception as exc:                            # noqa: BLE001
            last = "%s: %s" % (type(exc).__name__, exc)
        if i < tries - 1:
            time.sleep(pause)
    return {"REST_READ_REFUSED": last, "attempts": tries}


def book_top(payload: Any) -> Dict[str, Any]:
    """`bids[0]` / `offers[0]` and whether each side is best-first."""
    md = (payload or {}).get("marketData") or payload or {}
    out: Dict[str, Any] = {}
    for name, key, want_desc in (("bid", "bids", True),
                                 ("offer", "offers", False)):
        rows = md.get(key) or []
        vals = [dec(r.get("px")) for r in rows if isinstance(r, dict)]
        vals = [v for v in vals if v is not None]
        out[name] = {
            "n_levels": len(rows),
            "level_0": str(vals[0]) if vals else None,
            "level_0_qty": (rows[0].get("qty")
                            if rows and isinstance(rows[0], dict)
                            else None),
            "first_5": [str(v) for v in vals[:5]],
            "best_by_value": (str(max(vals)) if (vals and want_desc)
                              else (str(min(vals)) if vals else None)),
            "level_0_is_the_best": (
                bool(vals) and vals[0] == (max(vals) if want_desc
                                           else min(vals))),
            "monotonic_best_first": (
                all(a >= b for a, b in zip(vals, vals[1:])) if want_desc
                else all(a <= b for a, b in zip(vals, vals[1:]))),
        }
    return out


# --------------------------------------------------------------- the proof

def adjudicate(slug: str, snap: Dict[str, Any], ws_frame: Dict[str, Any],
               rest: Dict[str, Any], ws_t: float) -> Dict[str, Any]:
    """All four readings against each other. No shopping, no retry."""
    bbo = rest["bbo"]["raw"] or {}
    md = bbo.get("marketData") or {}

    r1_bid, r1_ask = dec(md.get("bestBid")), dec(md.get("bestAsk"))
    top = rest["book"]["top"] or {}
    r2_bid = dec((top.get("bid") or {}).get("level_0"))
    r2_ask = dec((top.get("offer") or {}).get("level_0"))
    sh = (ws_frame or {}).get("shape") or {}
    r3_bid = dec((sh.get("bid0") or {}).get("px"))
    r3_ask = dec((sh.get("offer0") or {}).get("px")
                 if isinstance(sh.get("offer0"), dict) else None)
    r4_bid, r4_ask = dec(snap.get("bid")), dec(snap.get("ask"))

    skew = abs(rest["bbo"]["t"] - ws_t)
    readings = {
        "1_rest_bbo": {"bid": _s(r1_bid), "ask": _s(r1_ask),
                       "path": "marketData.bestBid/.bestAsk"},
        "2_rest_book_top": {"bid": _s(r2_bid), "ask": _s(r2_ask),
                            "path": "marketData.bids[0]/.offers[0]"},
        "3_raw_ws_frame": {"bid": _s(r3_bid), "ask": _s(r3_ask),
                           "path": "the ingested frame's own bids[0]/"
                                   "offers[0]"},
        "4_authbook_snapshot": {"bid": _s(r4_bid), "ask": _s(r4_ask),
                                "path": "AuthBook.snapshot()"},
    }

    bids = [r1_bid, r2_bid, r3_bid, r4_bid]
    asks = [r1_ask, r2_ask, r3_ask, r4_ask]
    bid_all = [b for b in bids if b is not None]
    ask_all = [a for a in asks if a is not None]
    all_four_bid = len(bid_all) == 4 and len(set(bid_all)) == 1
    all_four_ask = len(ask_all) == 4 and len(set(ask_all)) == 1

    out: Dict[str, Any] = {
        "slug": slug,
        "T_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(int(ws_t))),
        "T_unix": ws_t,
        "readings": readings,
        "skew_s": round(skew, 3), "max_skew_s": MAX_SKEW_S,
        "contemporaneous": skew <= MAX_SKEW_S,
        "all_four_agree_on_the_bid": all_four_bid,
        "all_four_agree_on_the_ask": all_four_ask,
        "A_bid": _s(bid_all[0]) if all_four_bid else None,
        "B_ask": _s(ask_all[0]) if all_four_ask else None,
        "two_sided": bool(ask_all) and bool(bid_all),
        "book_is_uncrossed": (bool(bid_all) and bool(ask_all)
                              and bid_all[0] < ask_all[0]),
        "ladder_is_best_first": {
            "bid": (top.get("bid") or {}).get("level_0_is_the_best"),
            "offer": (top.get("offer") or {}).get("level_0_is_the_best"),
        },
        "ws_carries_a_best_bid_field": bool(sh.get("bestBid") is not None),
    }

    if all_four_bid and all_four_ask:
        A, B = bid_all[0], ask_all[0]
        out["e1_decision"] = e1_prices(slug, A, B)
        out["VERDICT"] = "BBO PROVEN"
    elif not out["contemporaneous"]:
        out["VERDICT"] = ("INCONCLUSIVE: the REST cross-check was not "
                          "contemporaneous with the WS snapshot")
    else:
        out["VERDICT"] = "DISAGREEMENT: STOP"
        out["disagreement"] = {
            "bids": [_s(b) for b in bids],
            "asks": [_s(a) for a in asks],
        }
    return out


def _s(x: Any) -> Optional[str]:
    return None if x is None else str(x)


def e1_prices(slug: str, A: D, B: D) -> Dict[str, Any]:
    """What E1 would do, through the PROVEN serializer -- not restated."""
    from src import l5_order_body as OB
    from analysis.l5.e1_cohort import pmus_book as PBK
    from analysis.l5.e1_cohort import spec as SP

    comp = PBK.complement_of(str(A), str(B))
    no_bid = comp["bid"]                        # 1 - B, in NO terms
    yes_body = OB.build(slug=slug, side="UP", price=A,
                        qty=SP.FIRST_LEG_QTY, post_only=True)
    no_body = OB.build(slug=slug, side="DOWN", price=no_bid,
                       qty=SP.FIRST_LEG_QTY, post_only=True)
    return {
        "yes_maker_first_leg": {
            "outcome_terms": str(A),
            "rule": "the YES best bid, unchanged",
            "wire_price_value": yes_body["price"]["value"],
            "wire_equals_A": yes_body["price"]["value"] == str(A),
            "outcome_side": yes_body["outcomeSide"],
            "post_only": yes_body["participateDontInitiate"],
            "quantity": yes_body["quantity"],
            "would_cross_the_ask": A >= B,
        },
        "no_maker_first_leg": {
            "outcome_terms": str(no_bid),
            "rule": "1 - YES best ask (the NO best bid)",
            "equals_one_minus_B": no_bid == ONE - B,
            "wire_price_value": no_body["price"]["value"],
            "wire_equals_B": no_body["price"]["value"] == str(B),
            "why_the_wire_differs": (
                "price.value is ALWAYS the long (YES) side's price, so a "
                "NO order at X is sent as 1 - X; 1 - (1 - B) = B"),
            "outcome_side": no_body["outcomeSide"],
            "post_only": no_body["participateDontInitiate"],
        },
        "complement_arithmetic": {
            "no_bid_is_1_minus_yes_ask": str(ONE - B),
            "no_ask_is_1_minus_yes_bid": str(ONE - A),
            "complement_is_uncrossed": (ONE - B) < (ONE - A),
            "pair_cost_of_both_best_bids": str(A + (ONE - B)),
        },
        "frozen": {"qty": str(SP.FIRST_LEG_QTY),
                   "max_first_leg_price": str(SP.MAX_FIRST_LEG_PRICE),
                   "pair_ceiling": str(SP.PAIR_COST_CEILING),
                   "fingerprint": SP.E1_FINGERPRINT},
        "NOTHING_WAS_SUBMITTED": True,
    }


# ------------------------------------------------------------------- main

def main() -> int:
    out: Dict[str, Any] = {
        "proof": "E1_BBO_TWO_SIDED", "venue": "polymarket_us",
        "read_only": True, "credentials_used": True,
        "signing_is_not_ordering": True,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **disable_the_latch(),
    }
    from analysis.l5.e1_cohort import pmus_book as PBK
    from analysis.l5.e1_cohort import pmus_identity as PI
    from analysis.l5.e1_cohort import pmus_selector as SEL

    deadline = time.time() + TOTAL_BUDGET_S
    attempts: List[Dict[str, Any]] = []
    feed = PBK.PmusDecisionFeed(clock=time.time)
    feed.start()
    book = getattr(feed, "_book", None)
    out["feed_started"] = book is not None

    try:
        while time.time() < deadline:
            now = time.time()
            ws = SEL.window_start_for(now)
            slug = PI.slug_for(ws)
            att: Dict[str, Any] = {
                "slug": slug, "window_start": ws,
                "seconds_into_window": round(now - ws, 1)}
            try:
                PI.resolve(slug)
                att["resolved"] = True
            except Exception as exc:                        # noqa: BLE001
                att["resolved"] = False
                att["refused"] = "%s: %s" % (type(exc).__name__, exc)
                attempts.append(att)
                time.sleep(10.0)
                continue

            tap = RawFrameTap(book, slug) if book is not None else None
            wdl = min(deadline, time.time() + PER_WINDOW_BUDGET_S,
                      float(ws + SEL.WINDOW_S) - 20.0)
            found = None
            while time.time() < wdl:
                snap = feed._snapshot_for(slug)
                if snap and snap.get("bid") is not None \
                        and snap.get("ask") is not None:
                    frame = tap.latest_two_sided() if tap else None
                    if frame is not None:
                        found = (snap, frame, time.time())
                        break
                time.sleep(POLL_S)

            if found is None:
                h = feed.health({"slug": slug})
                att["result"] = "NO_TWO_SIDED_STATE_IN_THIS_WINDOW"
                att["health_why"] = h.get("why")
                att["book_updates"] = h.get("book_updates")
                att["last_raw_frame_shape"] = (
                    (tap.latest() or {}).get("shape") if tap else None)
                attempts.append(att)
                if tap:
                    tap.restore()
                continue

            snap, frame, ws_t = found
            rest = rest_reads(slug)
            verdict = adjudicate(slug, snap, frame, rest, ws_t)
            att["result"] = "ADJUDICATED"
            attempts.append(att)
            out["attempts"] = attempts
            out["proof_record"] = {
                "authbook_snapshot": snap,
                "raw_ws_frame": frame,
                "rest": rest,
                "feed_health": feed.health({"slug": slug}),
            }
            out["verdict"] = verdict
            if tap:
                tap.restore()
            out.update(assert_no_order_path())
            rc = 0 if verdict["VERDICT"] == "BBO PROVEN" else 4
            out["result"] = verdict["VERDICT"]
            finish(out)
            return rc

        out["attempts"] = attempts
        out["result"] = "NO_TWO_SIDED_STATE_IN_BUDGET"
        out.update(assert_no_order_path())
        finish(out)
        return 3
    finally:
        try:
            feed.stop()
        except Exception:                                   # noqa: BLE001
            pass


def finish(out: Dict[str, Any]) -> None:
    out["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime())
    out["saved_to"] = persist(out)
    summarize(out)


def persist(out: Dict[str, Any]) -> Optional[str]:
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, "twosided.%s.json"
                            % time.strftime("%Y%m%dT%H%M%SZ",
                                            time.gmtime()))
        with open(path, "w", encoding="utf8") as fh:
            json.dump(out, fh, indent=1, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        return path
    except OSError as exc:
        return "UNSAVED:%s" % type(exc).__name__


def summarize(out: Dict[str, Any]) -> None:
    """Compact, because Railway drops logs above 500/sec."""
    v = out.get("verdict") or {}
    rows: List[Any] = [
        ("result", out.get("result")),
        ("saved_to", out.get("saved_to")),
        ("VERDICT", v.get("VERDICT")),
        ("slug", v.get("slug")),
        ("T_utc", v.get("T_utc")),
        ("A_bid", v.get("A_bid")),
        ("B_ask", v.get("B_ask")),
        ("readings", v.get("readings")),
        ("all_four_bid", v.get("all_four_agree_on_the_bid")),
        ("all_four_ask", v.get("all_four_agree_on_the_ask")),
        ("skew_s", v.get("skew_s")),
        ("contemporaneous", v.get("contemporaneous")),
        ("uncrossed", v.get("book_is_uncrossed")),
        ("ladder_best_first", v.get("ladder_is_best_first")),
        ("ws_has_best_field", v.get("ws_carries_a_best_bid_field")),
        ("disagreement", v.get("disagreement")),
        ("e1_yes", (v.get("e1_decision") or {}).get(
            "yes_maker_first_leg")),
        ("e1_no", (v.get("e1_decision") or {}).get("no_maker_first_leg")),
        ("e1_complement", (v.get("e1_decision") or {}).get(
            "complement_arithmetic")),
        ("rest_book_top", ((out.get("proof_record") or {}).get("rest")
                           or {}).get("book", {}).get("top")),
        ("raw_ws_shape", ((out.get("proof_record") or {}).get(
            "raw_ws_frame") or {}).get("shape")),
        ("attempts", out.get("attempts")),
        ("safety", {"venue_post": out.get("venue_post"),
                    "order_submitted": out.get("order_submitted"),
                    "latch_touched": out.get("latch_touched"),
                    "cohort_advanced": out.get("cohort_advanced")}),
    ]
    for k, val in rows:
        s = json.dumps(val, default=str, sort_keys=True)
        if len(s) > 3500:
            s = s[:3500] + "...TRUNCATED"
        print("E1TS %s=%s" % (k, s))


if __name__ == "__main__":
    raise SystemExit(main())
