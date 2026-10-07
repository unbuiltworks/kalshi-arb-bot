"""THE BBO SEMANTIC PROBE. Read-only. Answers one question with venue data.

THE QUESTION. E1's window-3 attempt recorded
`price_source: PMUS_BEST_BID, price_sent: 0.1300` while a
`"value": "0.4900"` also appeared in the same BBO/book payload. Until it
is established WHICH FIELD is the executable YES best bid, E1's first-leg
price is a guess, because the first leg's price IS that parse.

WHAT THIS DOES NOT DO. It does not infer semantics from field names. It
takes three independent readings of the SAME SLUG at approximately the
same time and compares them:

  1. REST  GET /v1/markets/{slug}/bbo   -- what the proven lineage reads
  2. REST  GET /v1/markets/{slug}/book  -- the depth, with its level order
  3. WS    AuthBook, the authenticated decision feed E1 actually uses

and prints EVERY candidate field it can find in each, with its JSON path,
so a reader can see which values exist and which one each reader chose.
The verdict is then a comparison of readings, not a reading of names.

THE PROVEN LINEAGE IS THE TIE-BREAK. `pmus_q1_executor.fresh_bbo` reads
`marketData.bestBid.value` / `bestAsk.value`, and
`l5_order_body.venue_price` encodes that `price.value` is ALWAYS the long
(YES) side's price -- to buy NO at X the wire value is 1.00 - X, learned
when 24 of 24 NO-side post-only orders priced in NO terms were rejected
on 2026-09-05/06. That module has actually traded on this venue. If the
three readings agree with it, the parser is correct and nothing should
change.

ORDERING IS IMPOSSIBLE HERE, BY CONSTRUCTION, NOT BY A FLAG:

  * every REST read goes through `pmus_auth.ReadOnlyTransport`, which
    refuses a non-GET in the TRANSPORT rather than in a caller's flag;
  * `analysis.l5.e1_cohort.latch.consume_attempt` and
    `consume_complement` are REPLACED WITH RAISES before any work, so a
    latch slot cannot be spent even by a bug;
  * it imports no order body and no preview gate, and asserts via
    `sys.modules` afterwards that none appeared;
  * it never constructs an order, a preview or a POST of any kind. There
    is no code path here that names an order endpoint.

Credentials ARE used: the only PMUS decision feed is authenticated, and
PMUS serves /bbo to authenticated callers. Signing is not ordering.

Exit 0 on a verdict, 2 if no live window could be read, 3 if the feed
never produced a two-sided book.

Run:
    python3 -m scripts.l5_e1_bbo_semantics_probe
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Optional

ONE = D("1")

#: anything here means this probe reached an order-capable module.
FORBIDDEN_MODULES = (
    "analysis.l5.live_exec2.orders",
    "src.l5_preview_gate",
    "src.pmus_q1_executor",
)

#: how long to let the authenticated book fill before giving up.
WS_BUDGET_S = 45.0
WS_POLL_S = 0.5

#: how close the REST and WS readings must be in TIME for a disagreement
#: to mean anything. A push feed and a cached REST route are not the same
#: instant, and a book that moved between them is not a parse defect.
MAX_SKEW_S = 10.0


def _disable_the_latch() -> Dict[str, Any]:
    """Make spending an attempt slot IMPOSSIBLE, not merely unlikely."""
    from analysis.l5.e1_cohort import latch as L

    def _refuse(*a: Any, **k: Any) -> Any:
        raise AssertionError(
            "REFUSED: a read-only BBO semantic probe tried to consume an "
            "E1 latch slot")

    L.consume_attempt = _refuse            # type: ignore[assignment]
    L.consume_complement = _refuse         # type: ignore[assignment]
    return {"latch_consume_replaced_with_a_raise": True}


def _assert_no_order_path() -> Dict[str, Any]:
    present = sorted(m for m in FORBIDDEN_MODULES if m in sys.modules)
    if present:
        raise SystemExit(
            "REFUSED: an order-capable module is loaded in a read-only "
            "probe: %r" % (present,))
    return {"forbidden_modules_loaded": [], "venue_post": "none",
            "order_constructed": False, "preview_sent": False,
            "latch_touched": False}


# --------------------------------------------------------------- candidates

def candidates(node: Any, path: str = "$") -> List[Dict[str, Any]]:
    """EVERY numeric-looking price in a payload, with its JSON path.

    This is the heart of the probe. It does not know which field is the
    bid; it enumerates every value that COULD be one, so the comparison
    below is over the real option set rather than over the two fields
    someone remembered.
    """
    out: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(candidates(v, "%s.%s" % (path, k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(candidates(v, "%s[%d]" % (path, i)))
    else:
        d = _dec(node)
        if d is not None:
            out.append({"path": path, "raw": node, "decimal": str(d),
                        "in_unit_interval": D("0") < d < ONE})
    return out


def _dec(x: Any) -> Optional[D]:
    if isinstance(x, bool) or x is None:
        return None
    try:
        d = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return d


# ------------------------------------------------------------------- reads

def read_rest(slug: str) -> Dict[str, Any]:
    from analysis.l5.e1_cohort import pmus_identity as PI
    t = PI.read_only_transport()
    t0 = time.time()
    bbo_raw = PI.bbo(slug, t)
    t1 = time.time()
    book_raw = PI.book(slug, t)
    t2 = time.time()
    return {"bbo": {"t": t0, "elapsed_s": round(t1 - t0, 3),
                    "raw": bbo_raw,
                    "candidates": candidates(bbo_raw)},
            "book": {"t": t1, "elapsed_s": round(t2 - t1, 3),
                     "raw": book_raw,
                     "candidates": candidates(book_raw)}}


def proven_lineage_read(bbo_raw: Dict[str, Any]) -> Dict[str, Any]:
    """What `pmus_q1_executor.fresh_bbo` would read from THIS payload.

    Called with the payload rather than over the network, and the function
    is not imported -- importing the executor is forbidden above. The
    expression is reproduced here and PINNED BY TEST against the
    executor's own source, so this cannot drift from the thing it claims
    to reproduce.
    """
    md = bbo_raw.get("marketData") or {}
    return {"bid": (md.get("bestBid") or {}).get("value"),
            "ask": (md.get("bestAsk") or {}).get("value"),
            "reader": "pmus_q1_executor.fresh_bbo (reproduced, test-pinned)"}


def read_ws(slug: str) -> Dict[str, Any]:
    from analysis.l5.e1_cohort import pmus_book as PBK
    feed = PBK.PmusDecisionFeed(clock=time.time)
    feed.start()
    t0 = time.time()
    snap = None
    polls = 0
    try:
        while time.time() - t0 < WS_BUDGET_S:
            polls += 1
            snap = feed._snapshot_for(slug)
            if snap and snap.get("bid") is not None \
                    and snap.get("ask") is not None:
                break
            time.sleep(WS_POLL_S)
        health = feed.health({"slug": slug})
        book = feed.book({"slug": slug}) if health.get("ok") else None
        frames = _frame_evidence(feed, slug)
    finally:
        feed.stop()
    return {"t": time.time(), "polls": polls, "snapshot": snap,
            "health": health, "decision_book": book,
            "frame_evidence": frames,
            "reader": "src.l5_pmus_auth_book.AuthBook via PmusDecisionFeed"}


def _frame_evidence(feed: Any, slug: str) -> Dict[str, Any]:
    """The RAW SHAPES AuthBook saw, which is what makes its answer
    checkable rather than a matter of belief: `pre_book` keeps the first
    frames per slug with their bestBid/bestAsk and their top levels."""
    book = getattr(feed, "_book", None)
    if book is None:
        return {"available": False}
    pre = dict(getattr(book, "pre_book", {}) or {})
    st = dict((getattr(book, "state", {}) or {}).get(slug) or {})
    hist = (st.get("bbo_history") or [])[-5:]
    return {"available": True,
            "pre_book_shapes_for_this_slug": pre.get(slug),
            "book_updates": st.get("book_updates"),
            "both_sides_served": st.get("both_sides_served"),
            "last_bbo_history": hist,
            "connected": getattr(book, "connected", None),
            "connects": getattr(book, "connects", None),
            "subscribed": list(getattr(book, "subscribed", []) or [])}


# ----------------------------------------------------------------- verdict

def compare(rest: Dict[str, Any], ws: Dict[str, Any]) -> Dict[str, Any]:
    bbo_raw = rest["bbo"]["raw"]
    proven = proven_lineage_read(bbo_raw)
    snap = ws.get("snapshot") or {}
    skew = abs(ws["t"] - rest["bbo"]["t"])

    pb, pa = _dec(proven["bid"]), _dec(proven["ask"])
    wb, wa = _dec(snap.get("bid")), _dec(snap.get("ask"))

    out: Dict[str, Any] = {
        "rest_bbo_t": rest["bbo"]["t"], "ws_t": ws["t"],
        "skew_s": round(skew, 3), "max_skew_s": MAX_SKEW_S,
        "skew_is_small_enough_for_a_disagreement_to_mean_anything":
            skew <= MAX_SKEW_S,
        "proven_lineage": proven,
        "authbook": {"bid": snap.get("bid"), "ask": snap.get("ask"),
                     "bid_shares": snap.get("bid_shares"),
                     "ask_shares": snap.get("ask_shares"),
                     "source": snap.get("source")},
        "bid_agrees": (pb is not None and wb is not None and pb == wb),
        "ask_agrees": (pa is not None and wa is not None and pa == wa),
    }

    # THE TWO TRANSFORMATIONS, STATED AND COMPUTED FROM THE SAME READING.
    if wb is not None and wa is not None:
        out["yes_buy_maker_price"] = {
            "rule": "the YES best bid, unchanged",
            "outcome_terms": str(wb),
            "wire_price_value": str(wb),
            "why_the_wire_equals_it": "price.value is the long (YES) side's "
                                      "price, so a YES order needs no map",
        }
        no_bid = ONE - wa
        out["no_buy_maker_price"] = {
            "rule": "1 - YES ask (the NO best bid)",
            "outcome_terms": str(no_bid),
            "wire_price_value": str(ONE - no_bid),
            "why_the_wire_differs": "price.value is ALWAYS the long side's "
                                    "price, so a NO order at X is sent as "
                                    "1 - X; 1 - (1 - YES ask) = YES ask",
        }
        out["complement_arithmetic_against_venue_data"] = {
            "yes_bid": str(wb), "yes_ask": str(wa),
            "no_bid_is_1_minus_yes_ask": str(ONE - wa),
            "no_ask_is_1_minus_yes_bid": str(ONE - wb),
            "the_source_book_is_uncrossed": wb < wa,
            "the_complement_is_uncrossed": (ONE - wa) < (ONE - wb),
            "pair_cost_of_both_best_bids": str(wb + (ONE - wa)),
        }

    # WHICH OTHER VALUES WERE IN THE PAYLOAD, and would any of them have
    # been chosen by a looser parse? This is the 0.1300-vs-0.4900 question.
    others = [c for c in rest["bbo"]["candidates"] if c["in_unit_interval"]]
    out["every_unit_interval_value_in_the_bbo_payload"] = others
    out["distinct_unit_interval_values"] = sorted(
        {c["decimal"] for c in others})
    out["the_chosen_bid_is_one_of_them"] = (
        proven["bid"] is not None
        and str(_dec(proven["bid"])) in out["distinct_unit_interval_values"])

    if out["bid_agrees"] and out["ask_agrees"]:
        out["VERDICT"] = "AGREE: AuthBook and the proven lineage read the " \
                         "same executable YES BBO"
    elif not out["skew_is_small_enough_for_a_disagreement_to_mean_anything"]:
        out["VERDICT"] = "INCONCLUSIVE: the two readings are too far apart " \
                         "in time to compare"
    else:
        out["VERDICT"] = "DISAGREE: investigate before any decision " \
                         "depends on this parse"
    return out


def main() -> int:
    guard = _disable_the_latch()
    from analysis.l5.e1_cohort import pmus_identity as PI
    from analysis.l5.e1_cohort import pmus_selector as SEL

    out: Dict[str, Any] = {
        "probe": "E1_BBO_SEMANTICS", "venue": "polymarket_us",
        "identity": "slug_plus_outcome_side", "ctf_token_ids_used": False,
        "read_only": True, "credentials_used": True,
        "signing_is_not_ordering": True,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **guard,
    }

    now = time.time()
    slug = None
    tried = []
    for start in [SEL.window_start_for(now)] + SEL.next_window_starts(now, 2):
        s = PI.slug_for(start)
        try:
            PI.resolve(s)
            slug = s
            out["window_start"] = start
            out["seconds_into_window"] = round(now - start, 1)
            break
        except Exception as exc:                            # noqa: BLE001
            tried.append({"slug": s, "refused": "%s: %s"
                          % (type(exc).__name__, exc)})
    out["slugs_tried"] = tried
    if slug is None:
        out["result"] = "NO_LIVE_WINDOW"
        out.update(_assert_no_order_path())
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return 2
    out["slug"] = slug

    out["rest"] = read_rest(slug)
    out["ws"] = read_ws(slug)
    out["comparison"] = compare(out["rest"], out["ws"])
    out.update(_assert_no_order_path())

    snap = out["ws"].get("snapshot") or {}
    rc = 0 if (snap.get("bid") is not None
               and snap.get("ask") is not None) else 3
    out["result"] = "COMPARED" if rc == 0 else "NO_TWO_SIDED_WS_BOOK"
    out["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
