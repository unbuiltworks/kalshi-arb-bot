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
WS_BUDGET_S = float(os.getenv("PMUS_E1_BBO_WS_BUDGET_S") or 420.0)
WS_POLL_S = 1.0

#: MEASURED, NOT ASSUMED. The first run read
#: cpc-btc-updown-15m-2026-10-07-2215z at +842s -- 14 minutes into a
#: 15-minute window -- and found the book COLLAPSED: bestAsk null,
#: askDepth 0, askShares "0", n_offers 0. Near expiry a BTC15m window has
#: resolved in all but name (bestBid 0.9900, shortQuote 0.01), so there is
#: no two-sided book to compare and AuthBook correctly returns nothing.
#: A semantic comparison needs a LIVE two-sided book, which means early in
#: a window -- which is also exactly where E1 trades.
MAX_SECONDS_INTO_WINDOW = float(
    os.getenv("PMUS_E1_BBO_MAX_TTE_OFFSET_S") or 420.0)

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
    """REST /bbo and /book, each tolerant of a 404.

    MEASURED, NOT GUESSED. Run at +5s into a brand-new window, this
    crashed with `urllib.error.HTTPError: HTTP Error 404` from
    `/v1/markets/{slug}/bbo` -- even though `/v1/events/slug/{event}` had
    already resolved the market a moment earlier. The MARKET resource is
    not served the instant the window opens; the EVENT is. That matches
    `l5_pmus_transport`'s own note that `/v1/market/slug/` 404s for these
    windows while `/v1/events/slug/` carries them.

    This is a defect in the PROBE, not in E1: E1's decision path never
    calls /bbo -- it reads AuthBook -- and REST here is a cross-check
    only. So the cross-check retries and, failing that, records the
    refusal instead of taking the process down.
    """
    from analysis.l5.e1_cohort import pmus_identity as PI
    t = PI.read_only_transport()
    t0 = time.time()
    bbo_raw = _tolerant(PI.bbo, slug, t)
    t1 = time.time()
    book_raw = _tolerant(PI.book, slug, t)
    t2 = time.time()
    return {"bbo": {"t": t0, "elapsed_s": round(t1 - t0, 3),
                    "raw": bbo_raw,
                    "candidates": candidates(bbo_raw)},
            "book": {"t": t1, "elapsed_s": round(t2 - t1, 3),
                     "raw": book_raw,
                     "candidates": candidates(book_raw),
                     "ladders": ladders(book_raw)}}


def _tolerant(fn: Any, slug: str, t: Any, tries: int = 4,
              pause: float = 15.0) -> Dict[str, Any]:
    """Call a read-only REST reader, retrying a transient refusal.

    It NEVER raises and it never invents a payload: on exhaustion it
    returns a record naming the refusal, so a comparison built on it
    reads INCONCLUSIVE rather than silently comparing against {}.
    """
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
    return {"REST_READ_REFUSED": last, "attempts": tries,
            "this_is_a_cross_check_not_the_decision_feed": True}


def ladders(payload: Any) -> Dict[str, Any]:
    """The ORDER of the ladder, stated rather than assumed.

    `_level()` in the shared recorder takes `levels[0]` and calls it the
    best. That is an ASSUMPTION ABOUT ORDERING, and since the PMUS
    websocket carries no bestBid/bestAsk at all (measured: its book frames
    have keys bids/offers/state/stats/transactTime and nothing else), it
    is the assumption E1's entire first-leg price rests on. So the first
    five levels of each side are recorded, with whether the side is
    monotonic in the direction that would make levels[0] the best.
    """
    md = (payload or {}).get("marketData") or payload or {}
    out: Dict[str, Any] = {}
    for name, key, want_desc in (("bids", "bids", True),
                                 ("offers", "offers", False)):
        rows = md.get(key) or []
        px = []
        for r in rows[:12]:
            if isinstance(r, dict):
                v = r.get("px")
                px.append({"px": (v or {}).get("value")
                           if isinstance(v, dict) else v,
                           "qty": r.get("qty")})
        vals = [_dec(r["px"]) for r in px]
        vals = [v for v in vals if v is not None]
        desc = all(a >= b for a, b in zip(vals, vals[1:]))
        asc = all(a <= b for a, b in zip(vals, vals[1:]))
        out[name] = {
            "n_levels": len(rows),
            "first_12": px,
            "monotonic_descending": desc,
            "monotonic_ascending": asc,
            "levels_0_is_the_best": (desc if want_desc else asc),
            "best_by_value": (str(max(vals)) if (vals and want_desc)
                              else (str(min(vals)) if vals else None)),
            "level_0_value": str(vals[0]) if vals else None,
            "level_0_equals_best_by_value": (
                bool(vals) and (vals[0] == (max(vals) if want_desc
                                            else min(vals)))),
        }
    return out


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
    if "REST_READ_REFUSED" in (bbo_raw or {}):
        return {"VERDICT": "INCONCLUSIVE: the REST cross-check was "
                           "refused, so there is nothing to compare "
                           "against",
                "rest_read_refused": bbo_raw.get("REST_READ_REFUSED"),
                "bid_agrees": None, "ask_agrees": None}
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
    # WAIT FOR A WINDOW THAT IS STILL EARLY. The current window is used
    # only if it is young enough to plausibly have two sides; otherwise
    # the probe sleeps to the next window open. A collapsed near-expiry
    # book cannot answer a semantic question about executable quotes.
    cur = SEL.window_start_for(now)
    if (now - cur) > MAX_SECONDS_INTO_WINDOW:
        nxt = SEL.next_window_starts(now, 1)[0]
        wait = max(0.0, (nxt + 5.0) - time.time())
        out["waited_for_next_window_s"] = round(wait, 1)
        out["why_waited"] = (
            "the current window was %.0fs old, past the %.0fs bound; a "
            "near-expiry BTC15m book has no ask side to compare"
            % (now - cur, MAX_SECONDS_INTO_WINDOW))
        time.sleep(wait)
        now = time.time()
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
    # A SECOND REST READ, IMMEDIATELY AFTER THE WS SNAPSHOT. The first
    # read happens before the feed has filled, so its skew is the whole
    # readiness wait -- which made the first run INCONCLUSIVE for a
    # reason that had nothing to do with the parse. This one is taken
    # within a second of the snapshot being read.
    out["rest_after_ws"] = read_rest(slug)
    out["comparison"] = compare(out["rest_after_ws"], out["ws"])
    out["comparison_against_the_first_rest_read"] = compare(
        out["rest"], out["ws"])
    out.update(_assert_no_order_path())

    snap = out["ws"].get("snapshot") or {}
    rc = 0 if (snap.get("bid") is not None
               and snap.get("ask") is not None) else 3
    out["result"] = "COMPARED" if rc == 0 else "NO_TWO_SIDED_WS_BOOK"
    out["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # PERSIST FIRST, PRINT SECOND, AND PRINT COMPACTLY.
    #
    # The full record enumerates every numeric value in two payloads plus
    # AuthBook's frame evidence -- hundreds of lines. Railway drops logs
    # above 500/sec, and that is exactly how the window-3 rejection detail
    # was lost: 5,704 lines dropped from the only copy. So the full record
    # goes to the VOLUME, and stdout gets a summary small enough to
    # survive.
    out["saved_to"] = _persist(out)
    _print_summary(out)
    return rc


def _persist(out: Dict[str, Any]) -> Optional[str]:
    base = os.getenv("PMUS_E1_BBO_DIR") or "/state/pmus-ws/e1_bbo_semantics"
    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "bbo_semantics.%s.%s.json"
                            % (out.get("slug", "unknown"),
                               time.strftime("%Y%m%dT%H%M%SZ",
                                             time.gmtime())))
        with open(path, "w", encoding="utf8") as fh:
            json.dump(out, fh, sort_keys=True, default=str, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        return path
    except OSError as exc:
        return "UNSAVED:%s" % type(exc).__name__


def _print_summary(out: Dict[str, Any]) -> None:
    """One compact line per fact, few enough to survive the rate limit."""
    c = out.get("comparison") or {}
    rows = [
        ("result", out.get("result")),
        ("slug", out.get("slug")),
        ("seconds_into_window", out.get("seconds_into_window")),
        ("saved_to", out.get("saved_to")),
        ("VERDICT", c.get("VERDICT")),
        ("rest_bestBid", (c.get("proven_lineage") or {}).get("bid")),
        ("rest_bestAsk", (c.get("proven_lineage") or {}).get("ask")),
        ("ws_bid", (c.get("authbook") or {}).get("bid")),
        ("ws_ask", (c.get("authbook") or {}).get("ask")),
        ("ws_bid_shares", (c.get("authbook") or {}).get("bid_shares")),
        ("ws_source", (c.get("authbook") or {}).get("source")),
        ("bid_agrees", c.get("bid_agrees")),
        ("ask_agrees", c.get("ask_agrees")),
        ("skew_s", c.get("skew_s")),
        ("distinct_unit_interval_values",
         c.get("distinct_unit_interval_values")),
        ("yes_buy_maker_price",
         (c.get("yes_buy_maker_price") or {}).get("outcome_terms")),
        ("no_buy_maker_price",
         (c.get("no_buy_maker_price") or {}).get("outcome_terms")),
        ("no_buy_wire_value",
         (c.get("no_buy_maker_price") or {}).get("wire_price_value")),
        ("complement", c.get("complement_arithmetic_against_venue_data")),
        ("ws_health_why", (out.get("ws") or {}).get("health", {}).get("why")),
        ("ws_book_updates",
         ((out.get("ws") or {}).get("frame_evidence") or {})
         .get("book_updates")),
        ("ws_connected",
         ((out.get("ws") or {}).get("frame_evidence") or {})
         .get("connected")),
        ("ws_pre_book_shapes",
         ((out.get("ws") or {}).get("frame_evidence") or {})
         .get("pre_book_shapes_for_this_slug")),
        ("rest_bbo_raw",
         (out.get("rest_after_ws") or out.get("rest") or {})
         .get("bbo", {}).get("raw")),
        ("rest_book_ladders",
         (out.get("rest_after_ws") or out.get("rest") or {})
         .get("book", {}).get("ladders")),
        ("waited_for_next_window_s", out.get("waited_for_next_window_s")),
        ("venue_post", out.get("venue_post")),
        ("latch_touched", out.get("latch_touched")),
    ]
    for k, v in rows:
        print("E1BBO %s=%s" % (k, json.dumps(v, default=str)))


if __name__ == "__main__":
    raise SystemExit(main())
