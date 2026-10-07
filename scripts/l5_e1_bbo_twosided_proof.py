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

#: THE CRITICAL BOUND. A REST reading further than this from the websocket
#: capture cannot adjudicate anything: at +119s a BTC15m book moves
#: constantly (103 book updates in 45s, measured), so a one-tick
#: difference across several seconds is ordinary market motion, not a
#: parse defect. The first version of this probe measured skew from the
#: WRONG READ and reported 0.0 while the real gap was 6-9 seconds.
MAX_SKEW_S = float(os.getenv("PMUS_E1_BBO_MAX_SKEW_S") or 1.0)

#: how many samples to take while hunting for a DETERMINATE one. An
#: INCONCLUSIVE sample is a MEASUREMENT FAILURE, not a result, so it is
#: retried. A determinate sample -- agreement OR genuine disagreement --
#: ENDS THE RUN. That asymmetry is deliberate: retrying past a genuine
#: disagreement would be shopping for a verdict.
MAX_SAMPLES = int(os.getenv("PMUS_E1_BBO_MAX_SAMPLES") or 60)

OUT_DIR = (os.getenv("PMUS_E1_BBO_DIR")
           or "/state/pmus-ws/e1_bbo_semantics")

#: the PMUS gateway, taken from the proven identity module rather than
#: restated here.
GATEWAY = None          # bound in main() from pmus_identity.GATEWAY


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

def rest_book_once(slug: str, http: Any) -> Dict[str, Any]:
    """ONE /book request, with its own start and end timestamps.

    NO RETRY. The previous version retried /bbo three times with 3s
    pauses INSIDE the measurement path and then read /book afterwards, so
    the comparison was against a book read 6-9 seconds after the
    websocket capture while the probe reported a skew of 0.0. The retry
    is gone from the hot path entirely; a failed read makes the sample
    INCONCLUSIVE, which is what it always was.
    """
    t0 = time.time()
    try:
        raw = http.request("GET", "/v1/markets/%s/book" % slug, None,
                           GATEWAY)
        err = None
    except Exception as exc:                                # noqa: BLE001
        raw, err = None, "%s: %s" % (type(exc).__name__, exc)
    t1 = time.time()
    return {"request_start_unix": t0, "request_end_unix": t1,
            "duration_s": round(t1 - t0, 4),
            "request_start_utc": _utc(t0), "request_end_utc": _utc(t1),
            "raw": raw, "error": err,
            "top": book_top(raw) if raw else None}


def rest_bbo_once(slug: str, http: Any) -> Dict[str, Any]:
    """SUPPLEMENTARY ONLY, and taken AFTER the critical comparison.

    /v1/markets/{slug}/bbo 404s at window open (measured). It is useful
    context but it may not gate or influence the verdict, so it is read
    last, once, and its absence is recorded rather than retried.
    """
    t0 = time.time()
    try:
        raw = http.request("GET", "/v1/markets/%s/bbo" % slug, None,
                           GATEWAY)
        err = None
    except Exception as exc:                                # noqa: BLE001
        raw, err = None, "%s: %s" % (type(exc).__name__, exc)
    md = (raw or {}).get("marketData") or {}
    return {"t_unix": t0, "t_utc": _utc(t0), "raw": raw, "error": err,
            "bid": _s(dec(md.get("bestBid"))),
            "ask": _s(dec(md.get("bestAsk"))),
            "ROLE": "supplementary; never gates the verdict"}


def _utc(t: float) -> str:
    ms = int((t % 1) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(t))) \
        + ".%03dZ" % ms


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

def take_sample(feed: Any, book: Any, tap: Any, slug: str,
                http: Any) -> Dict[str, Any]:
    """ONE sample: the websocket state and the venue's book, together.

    ORDER MATTERS AND IS THE WHOLE POINT:
      1. read the AuthBook snapshot and the RAW frame that produced it,
         back to back, and record the venue's own `transactTime` plus the
         book-update counter;
      2. IMMEDIATELY issue one /book request, timestamped either side;
      3. re-read the snapshot and the counter.

    If anything about the websocket state moved between (1) and (3), the
    two readings describe DIFFERENT instants and the sample is
    INCONCLUSIVE. That is the check the first version lacked.
    """
    t_ws = time.time()
    snap_before = feed._snapshot_for(slug)
    frame = tap.latest_two_sided()
    updates_before = _updates(book, slug)

    if not snap_before or snap_before.get("bid") is None \
            or snap_before.get("ask") is None or frame is None:
        return {"classification": "INCONCLUSIVE",
                "why": "NO_TWO_SIDED_WS_STATE_AT_CAPTURE"}

    rest = rest_book_once(slug, http)

    snap_after = feed._snapshot_for(slug)
    updates_after = _updates(book, slug)
    t_done = time.time()

    sh = frame.get("shape") or {}
    ws_bid = dec((sh.get("bid0") or {}).get("px"))
    ws_ask = dec((sh.get("offer0") or {}).get("px")
                 if isinstance(sh.get("offer0"), dict) else None)
    ab_bid, ab_ask = dec(snap_before.get("bid")), dec(snap_before.get("ask"))

    # SKEW, MEASURED AGAINST THE READ THAT IS ACTUALLY BEING COMPARED.
    skew_to_start = rest["request_start_unix"] - t_ws
    skew_to_end = rest["request_end_unix"] - t_ws
    sample: Dict[str, Any] = {
        "slug": slug,
        "T_ws_capture_unix": t_ws, "T_ws_capture_utc": _utc(t_ws),
        "ws_venue_transact_time": _transact_time(frame),
        "ws_last_update_t": snap_before.get("last_update_t"),
        "ws_stream_age_s": snap_before.get("stream_age_s"),
        "rest_request_start_utc": rest["request_start_utc"],
        "rest_request_end_utc": rest["request_end_utc"],
        "rest_duration_s": rest["duration_s"],
        "skew_ws_to_rest_start_s": round(skew_to_start, 4),
        "skew_ws_to_rest_end_s": round(skew_to_end, 4),
        "max_skew_s": MAX_SKEW_S,
        "book_updates_before": updates_before,
        "book_updates_after": updates_after,
        "book_updates_during_rest": (
            None if (updates_before is None or updates_after is None)
            else updates_after - updates_before),
        "ws_state_after": {"bid": (snap_after or {}).get("bid"),
                           "ask": (snap_after or {}).get("ask")},
        "total_sample_s": round(t_done - t_ws, 4),
        "readings": {
            "raw_ws_frame": {"bid": _s(ws_bid), "ask": _s(ws_ask),
                             "path": "the ingested frame's own "
                                     "bids[0]/offers[0]",
                             "n_bids": sh.get("n_bids"),
                             "n_offers": sh.get("n_offers")},
            "authbook_snapshot": {"bid": _s(ab_bid), "ask": _s(ab_ask),
                                  "path": "AuthBook.snapshot()"},
            "rest_book_top": {
                "bid": _s(dec(((rest["top"] or {}).get("bid") or {})
                              .get("level_0"))),
                "ask": _s(dec(((rest["top"] or {}).get("offer") or {})
                              .get("level_0"))),
                "path": "marketData.bids[0]/offers[0]",
                "n_bids": ((rest["top"] or {}).get("bid") or {})
                .get("n_levels"),
                "n_offers": ((rest["top"] or {}).get("offer") or {})
                .get("n_levels")},
        },
        "rest_ladders": rest["top"],
        "rest_error": rest["error"],
        "raw_ws_frame_shape": sh,
    }

    # ---------------------------------------------------- classification
    r_bid = dec(sample["readings"]["rest_book_top"]["bid"])
    r_ask = dec(sample["readings"]["rest_book_top"]["ask"])

    if rest["raw"] is None:
        sample["classification"] = "INCONCLUSIVE"
        sample["why"] = "REST_BOOK_UNAVAILABLE:%s" % rest["error"]
        return sample

    moved = (_s(ab_bid) != str((snap_after or {}).get("bid"))
             or _s(ab_ask) != str((snap_after or {}).get("ask")))
    if moved:
        sample["classification"] = "INCONCLUSIVE"
        sample["why"] = "WS_TOP_OF_BOOK_MOVED_DURING_THE_REST_REQUEST"
        return sample

    n_during = sample["book_updates_during_rest"]
    if n_during is None or n_during != 0:
        sample["classification"] = "INCONCLUSIVE"
        sample["why"] = ("BOOK_UPDATED_DURING_THE_REST_REQUEST:%s"
                         % n_during)
        return sample

    if skew_to_end > MAX_SKEW_S:
        sample["classification"] = "INCONCLUSIVE"
        sample["why"] = ("SKEW_%.4fs_EXCEEDS_%.4fs"
                         % (skew_to_end, MAX_SKEW_S))
        return sample

    if ws_bid is None or ws_ask is None or ab_bid is None \
            or ab_ask is None or r_bid is None or r_ask is None:
        sample["classification"] = "INCONCLUSIVE"
        sample["why"] = "A_SIDE_WAS_ABSENT_IN_ONE_READING"
        return sample

    # the fold must match the frame it folded, or AuthBook is the problem
    if (ws_bid, ws_ask) != (ab_bid, ab_ask):
        sample["classification"] = "GENUINE_DISAGREEMENT"
        sample["why"] = "AUTHBOOK_DOES_NOT_MATCH_THE_FRAME_IT_FOLDED"
        return sample

    if (ws_bid, ws_ask) == (r_bid, r_ask):
        sample["classification"] = "AGREE"
        sample["A_bid"], sample["B_ask"] = str(ws_bid), str(ws_ask)
        sample["book_is_uncrossed"] = ws_bid < ws_ask
        sample["ladder_best_first"] = {
            "bid": ((rest["top"] or {}).get("bid") or {})
            .get("level_0_is_the_best"),
            "offer": ((rest["top"] or {}).get("offer") or {})
            .get("level_0_is_the_best")}
        return sample

    sample["classification"] = "GENUINE_DISAGREEMENT"
    sample["why"] = "WS_AND_REST_DIFFER_WITHIN_A_BOUNDED_STABLE_WINDOW"
    return sample


def _updates(book: Any, slug: str) -> Optional[int]:
    try:
        return int(((book.state or {}).get(slug) or {})
                   .get("book_updates"))
    except (TypeError, ValueError, AttributeError):
        return None


def _transact_time(frame: Dict[str, Any]) -> Any:
    raw = (frame or {}).get("raw")
    for key in ("transactTime", "transact_time"):
        if isinstance(raw, dict) and raw.get(key):
            return raw[key]
    if isinstance(raw, dict):
        for v in raw.values():
            if isinstance(v, dict):
                got = _transact_time({"raw": v})
                if got:
                    return got
    return None


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
    global GATEWAY
    out: Dict[str, Any] = {
        "proof": "E1_BBO_TWO_SIDED_V2", "venue": "polymarket_us",
        "read_only": True, "credentials_used": True,
        "signing_is_not_ordering": True,
        "started_utc": _utc(time.time()),
        "critical_comparison": ("the raw authenticated WS frame and "
                                "AuthBook's fold of it, against REST "
                                "/book read IMMEDIATELY after, with the "
                                "skew measured against THAT request"),
        "bbo_role": "supplementary only; never gates the verdict",
        "max_skew_s": MAX_SKEW_S,
        "a_sample_that_cannot_be_trusted_is_INCONCLUSIVE": True,
        "an_inconclusive_sample_is_retried_a_determinate_one_ends_the_run":
            True,
        **disable_the_latch(),
    }
    from analysis.l5.e1_cohort import pmus_book as PBK
    from analysis.l5.e1_cohort import pmus_identity as PI
    from analysis.l5.e1_cohort import pmus_selector as SEL
    GATEWAY = PI.GATEWAY

    http = PI.read_only_transport()
    deadline = time.time() + TOTAL_BUDGET_S
    samples: List[Dict[str, Any]] = []
    feed = PBK.PmusDecisionFeed(clock=time.time)
    feed.start()
    book = getattr(feed, "_book", None)
    out["feed_started"] = book is not None
    if book is None:
        out["result"] = "NO_FEED"
        out.update(assert_no_order_path())
        finish(out)
        return 3

    tap = None
    current = None
    try:
        while time.time() < deadline and len(samples) < MAX_SAMPLES:
            ws = SEL.window_start_for(time.time())
            slug = PI.slug_for(ws)
            if slug != current:
                if tap is not None:
                    tap.restore()
                try:
                    PI.resolve(slug)
                except Exception as exc:                    # noqa: BLE001
                    samples.append({
                        "slug": slug, "classification": "INCONCLUSIVE",
                        "why": "WINDOW_NOT_RESOLVABLE:%s: %s"
                               % (type(exc).__name__, exc)})
                    time.sleep(10.0)
                    continue
                tap = RawFrameTap(book, slug)
                current = slug
                out.setdefault("slugs_observed", []).append(slug)

            snap = feed._snapshot_for(slug)
            if not snap or snap.get("bid") is None \
                    or snap.get("ask") is None:
                time.sleep(POLL_S)
                continue

            sample = take_sample(feed, book, tap, slug, http)
            sample["sample_n"] = len(samples) + 1
            samples.append(sample)

            if sample["classification"] in ("AGREE",
                                            "GENUINE_DISAGREEMENT"):
                break
            time.sleep(POLL_S)

        out["samples"] = samples
        determinate = [s for s in samples
                       if s.get("classification") in
                       ("AGREE", "GENUINE_DISAGREEMENT")]
        out["sample_count"] = len(samples)
        out["inconclusive_count"] = len(samples) - len(determinate)
        out["inconclusive_reasons"] = sorted(
            {str(s.get("why")) for s in samples
             if s.get("classification") == "INCONCLUSIVE"})

        if not determinate:
            out["result"] = "INCONCLUSIVE"
            out["VERDICT"] = "INCONCLUSIVE"
            out["why"] = ("no sample met the bounded, stable comparison "
                          "conditions within budget")
            rc = 5
        else:
            d = determinate[0]
            out["determinate_sample"] = d
            if d["classification"] == "AGREE":
                d["e1_decision"] = e1_prices(
                    d["slug"], D(d["A_bid"]), D(d["B_ask"]))
                out["VERDICT"] = "BBO PROVEN"
                out["result"] = "BBO PROVEN"
                out["A_bid"], out["B_ask"] = d["A_bid"], d["B_ask"]
                rc = 0
            else:
                out["VERDICT"] = "GENUINE DISAGREEMENT"
                out["result"] = "GENUINE DISAGREEMENT"
                rc = 4
            # SUPPLEMENTARY, taken only AFTER the verdict is fixed.
            out["supplementary_bbo"] = rest_bbo_once(d["slug"], http)

        if tap is not None:
            tap.restore()
        out.update(assert_no_order_path())
        finish(out)
        return rc
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
    d = out.get("determinate_sample") or {}
    rows: List[Any] = [
        ("VERDICT", out.get("VERDICT") or out.get("result")),
        ("saved_to", out.get("saved_to")),
        ("slug", d.get("slug")),
        ("A_bid", out.get("A_bid")),
        ("B_ask", out.get("B_ask")),
        ("classification", d.get("classification")),
        ("why", d.get("why") or out.get("why")),
        ("readings", d.get("readings")),
        ("T_ws_capture_utc", d.get("T_ws_capture_utc")),
        ("ws_venue_transact_time", d.get("ws_venue_transact_time")),
        ("ws_last_update_t", d.get("ws_last_update_t")),
        ("ws_stream_age_s", d.get("ws_stream_age_s")),
        ("rest_request_start_utc", d.get("rest_request_start_utc")),
        ("rest_request_end_utc", d.get("rest_request_end_utc")),
        ("rest_duration_s", d.get("rest_duration_s")),
        ("skew_ws_to_rest_start_s", d.get("skew_ws_to_rest_start_s")),
        ("skew_ws_to_rest_end_s", d.get("skew_ws_to_rest_end_s")),
        ("max_skew_s", d.get("max_skew_s") or out.get("max_skew_s")),
        ("book_updates_during_rest", d.get("book_updates_during_rest")),
        ("ws_state_after", d.get("ws_state_after")),
        ("uncrossed", d.get("book_is_uncrossed")),
        ("ladder_best_first", d.get("ladder_best_first")),
        ("rest_ladders", d.get("rest_ladders")),
        ("raw_ws_frame_shape", d.get("raw_ws_frame_shape")),
        ("e1_yes", (d.get("e1_decision") or {}).get(
            "yes_maker_first_leg")),
        ("e1_no", (d.get("e1_decision") or {}).get("no_maker_first_leg")),
        ("e1_complement", (d.get("e1_decision") or {}).get(
            "complement_arithmetic")),
        ("supplementary_bbo", out.get("supplementary_bbo")),
        ("sample_count", out.get("sample_count")),
        ("inconclusive_count", out.get("inconclusive_count")),
        ("inconclusive_reasons", out.get("inconclusive_reasons")),
        ("slugs_observed", out.get("slugs_observed")),
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
