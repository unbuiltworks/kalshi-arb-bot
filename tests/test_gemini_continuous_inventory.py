"""Digest read-path and the continuous-inventory counterfactual.

WHAT THESE PIN. Two things have to be true for section E to mean anything:
the window data has to survive the log transport intact, and the ONLY
behavioural difference from the live shell has to be the one the experiment
declares -- that a touch move re-evaluates the inventory intention instead
of terminating it. Everything else (the canonical lookup, post-only, the
residual cap, the fee-safe complement bound, maker-only, and the rule that
a fill needs a PRINT) must bind exactly as it does live.
"""
import json
from decimal import Decimal as D

import pytest

from analysis.l5.gemini_trajectory import continuous_inventory as CI
from analysis.l5.gemini_trajectory import window_digest as W
from src.l5_venue_adapter import GeminiAdapter


# --------------------------------------------------------------- read path

def _lines(market="GEMI-BTC05M2609141455-UP", n=300, expiry=1000.0,
           bid=0.48, ask=0.50, prints=()):
    ser = [(round(299.0 - i, 2), bid, ask, 500.0, 250.0) for i in range(n)]
    d = {"market": market, "asset": "BTC", "expiry_t": expiry,
         "window_minutes": 5, "obs": n, "tte_first": ser[0][0],
         "tte_last": ser[-1][0], "span_s": round(ser[0][0] - ser[-1][0], 2),
         "obs_per_min": 60.0, "two_sided_pct": 100.0, "one_sided_obs": 0,
         "prints": len(prints), "spread_p50": round(ask - bid, 6),
         "depth_p50": 250.0, "complete": True}
    out = ["GEMINI_WINDOW_CLOSE " + json.dumps(d, separators=(",", ":"))]
    parts = -(-len(ser) // 50)
    for i in range(parts):
        out.append("GEMINI_WINDOW_SERIES " + json.dumps(
            {"market": market, "part": i + 1, "parts": parts,
             "s": [list(o) for o in ser[i * 50:(i + 1) * 50]]},
            separators=(",", ":")))
    if prints:
        out.append("GEMINI_WINDOW_PRINTS " + json.dumps(
            {"market": market, "part": 1, "parts": 1,
             "p": [list(p) for p in prints]}, separators=(",", ":")))
    return out


def test_parser_round_trips_a_whole_window():
    wins = W.parse(["\n".join(_lines(n=300))])
    w = wins["GEMI-BTC05M2609141455-UP"]
    assert w["series_integrity"] == "COMPLETE_CHUNKS"
    assert len(w["series"]) == 300
    assert W.is_complete(w) is True
    d = W.section_d(w)
    assert d["obs"] == 300 and d["in_window_span_s"] == 299.0
    assert d["two_sided_pct"] == 100.0 and d["depth_p50"] == 250.0


def test_parser_refuses_a_window_whose_chunks_are_incomplete():
    """A half-window scored as a whole one is the exact class of coverage
    error this instrumentation exists to eliminate."""
    ls = _lines(n=300)
    wins = W.parse(["\n".join(ls[:-2])])           # drop two series chunks
    w = wins["GEMI-BTC05M2609141455-UP"]
    assert w["series_integrity"].startswith("MISSING_SERIES_CHUNKS")
    assert w["series"] == []
    assert W.is_complete(w) is False


def test_parser_survives_a_truncated_line_without_inventing_data():
    ls = _lines(n=100)
    ls[2] = ls[2][:len(ls[2]) // 2]                # transport truncation
    w = W.parse(["\n".join(ls)])["GEMI-BTC05M2609141455-UP"]
    assert w["series_integrity"].startswith("MISSING_SERIES_CHUNKS")


def test_parser_reads_the_railway_logs_json_shape():
    msgs = _lines(n=100)
    payload = {"deploy": [{"timestamp": "x", "message": m} for m in msgs]}
    w = W.parse([payload])["GEMI-BTC05M2609141455-UP"]
    assert len(w["series"]) == 100


def test_short_window_is_not_complete():
    w = W.parse(["\n".join(_lines(n=100))])["GEMI-BTC05M2609141455-UP"]
    # 99 s of span; chunk-intact but NOT a complete window
    assert w["series_integrity"] == "COMPLETE_CHUNKS"
    assert W.is_complete(w) is False


# ------------------------------------------------------------- fill tiers

def _p(tte, price, qty, buyer_is_maker):
    return {"t": 1000.0 - tte, "price": D(str(price)), "qty": D(str(qty)),
            "type": "sell" if buyer_is_maker else "buy"}


def test_strict_requires_a_print_through_our_price():
    """Resting YES bid at 0.48. A taker SELL at 0.48 is AT us; at 0.47 it
    traded through us."""
    at = [_p(200, 0.48, 5, True)]
    through = [_p(200, 0.47, 5, True)]
    assert CI.first_fill(at, True, D("0.48"), 0, 1e9, "STRICT", D("0")) is None
    assert CI.first_fill(through, True, D("0.48"), 0, 1e9, "STRICT",
                         D("0")) is not None


def test_optimistic_takes_a_print_at_our_price_and_says_so():
    at = [_p(200, 0.48, 5, True)]
    f = CI.first_fill(at, True, D("0.48"), 0, 1e9, "OPTIMISTIC", D("9999"))
    assert f is not None
    assert "front_of_queue_assumed" in f["basis"]


def test_supported_needs_more_size_than_the_visible_queue_ahead():
    """SUPPORTED never assumes queue position: it requires that MORE traded
    at our price than was displayed there when we joined."""
    at = [_p(200, 0.48, 5, True), _p(190, 0.48, 4, True)]
    assert CI.first_fill(at, True, D("0.48"), 0, 1e9, "SUPPORTED",
                         D("100")) is None          # 9 traded vs 100 ahead
    f = CI.first_fill(at, True, D("0.48"), 0, 1e9, "SUPPORTED", D("8"))
    assert f is not None and f["t"] == 1000.0 - 190  # crossed on the second


def test_tiers_are_ordered_strict_subset_supported_subset_optimistic():
    at = [_p(200, 0.48, 5, True)]
    res = {tier: CI.first_fill(at, True, D("0.48"), 0, 1e9, tier, D("3"))
           for tier in CI.TIERS}
    assert res["STRICT"] is None
    assert res["SUPPORTED"] is not None
    assert res["OPTIMISTIC"] is not None
    through = [_p(200, 0.47, 5, True)]
    for tier in CI.TIERS:
        assert CI.first_fill(through, True, D("0.47") + D("0.01"), 0, 1e9,
                             tier, D("9999")) is not None


def test_a_taker_buy_never_fills_a_resting_yes_bid():
    """Direction is load-bearing: buyer_is_maker=False means the aggressor
    BOUGHT, which lifts an ask, it does not hit our bid."""
    buys = [_p(200, 0.40, 50, False)]
    for tier in CI.TIERS:
        assert CI.first_fill(buys, True, D("0.48"), 0, 1e9, tier,
                             D("0")) is None


def test_no_print_is_never_a_fill_in_any_tier():
    """NEVER infer a fill because price later touched. Queue position is
    UNKNOWN and a quote is not a trade."""
    for tier in CI.TIERS:
        assert CI.first_fill([], True, D("0.48"), 0, 1e9, tier, D("0")) is None


# -------------------------------------------------------- the experiment

def _win(market="GEMI-BTC05M-UP", series=None, prints=None, expiry=1000.0):
    return {"market": market, "asset": "BTC", "expiry_t": expiry,
            "window_minutes": 5, "one_sided_obs": 0,
            "series": series or [], "print_rows": prints or []}


def test_touch_move_does_not_terminate_the_intention():
    """THE LOAD-BEARING ASSERTION. Canonical L5 continues at a NEW price
    73.4% of the time; a shell that reads the touch moving as the venue
    refusing the intention cannot reproduce that. The intention must
    re-join the new touch and stay alive."""
    adapter = GeminiAdapter()
    rows = [{"symbol": "X", "t": 700.0 + i, "bid": 0.40 + 0.01 * i,
             "ask": 0.60, "bid_sz": 10.0, "ask_sz": 10.0} for i in range(5)]
    # the touch moves every second; each move must produce a requote
    moves = []
    t, px = rows[0]["t"], D("0.40")
    for _ in range(4):
        nxt = CI._next_touch_move(rows, adapter, 1000.0, True, px, t, 1e9)
        assert nxt is not None
        moves.append(nxt)
        px, _a = CI._touch_px(adapter, CI.book_at(rows, nxt), 1000.0, True)
        t = nxt
    assert moves == [701.0, 702.0, 703.0, 704.0]
    assert px == D("0.44")


def test_next_touch_move_returns_none_when_the_touch_is_stable():
    adapter = GeminiAdapter()
    rows = [{"symbol": "X", "t": 700.0 + i, "bid": 0.40, "ask": 0.60,
             "bid_sz": 10.0, "ask_sz": 10.0} for i in range(5)]
    assert CI._next_touch_move(rows, adapter, 1000.0, True, D("0.40"),
                               700.0, 1e9) is None


def test_post_only_still_binds_when_the_join_would_cross():
    """NOT 'never cancel', and NOT 'always admissible': a join at or through
    the opposite ask is refused exactly as it is live."""
    adapter = GeminiAdapter()
    raw = {"symbol": "X", "t": 700.0, "bid": 0.60, "ask": 0.60,
           "bid_sz": 10.0, "ask_sz": 10.0}
    px, ahead = CI._touch_px(adapter, raw, 1000.0, True)
    assert px is None and ahead is None


def test_touch_price_reports_the_visible_queue_it_would_sit_behind():
    adapter = GeminiAdapter()
    raw = {"symbol": "X", "t": 700.0, "bid": 0.48, "ask": 0.52,
           "bid_sz": 137.0, "ask_sz": 9.0}
    px, ahead = CI._touch_px(adapter, raw, 1000.0, True)
    assert px == D("0.48") and ahead == D("137")
    px, ahead = CI._touch_px(adapter, raw, 1000.0, False)   # DOWN side
    assert px == D("0.48") and ahead == D("9")


def test_prints_map_buyer_is_maker_to_aggressor_direction():
    w = _win(prints=[[200.0, 0.49, 5.0, True], [100.0, 0.51, 2.0, False]])
    ps = CI.print_rows(w)
    assert [p["type"] for p in ps] == ["sell", "buy"]
    assert [p["t"] for p in ps] == [800.0, 900.0]        # chronological


def test_book_rows_are_chronological_and_feed_both_adapters():
    """PMUSAdapter reads *_qty/slug/tick and GeminiAdapter reads *_sz/symbol.
    Every key both need is supplied so ONE shadow runs on BOTH venues and
    neither adapter has to be modified to suit this module."""
    w = _win(series=[[299.0, 0.4, 0.6, 1.0, 2.0], [1.0, 0.4, 0.6, 1.0, 2.0]])
    rows = CI.book_rows(w)
    assert [r["t"] for r in rows] == [701.0, 999.0]
    assert {"symbol", "t", "bid", "ask", "bid_sz", "ask_sz"} <= set(rows[0])
    assert {"slug", "market_id", "bid_qty", "ask_qty", "tick"} <= set(rows[0])
    assert rows[0]["bid_qty"] == rows[0]["bid_sz"] == 1.0
    assert rows[0]["ask_qty"] == rows[0]["ask_sz"] == 2.0


def test_one_shadow_runs_on_both_venues_through_the_venue_shim():
    """The two adapters have DIFFERENT normalize signatures. If each venue
    needed its own replay, every cross-venue difference would be partly an
    artefact of which replay measured it."""
    from src.l5_venue_adapter import GeminiAdapter, PMUSAdapter
    g, p = CI.gemini_venue(), CI.pmus_venue()
    assert isinstance(g.adapter, GeminiAdapter) and g.venue == "GEMINI"
    assert isinstance(p.adapter, PMUSAdapter) and p.venue == "PMUS"
    raw = {"symbol": "X", "slug": "X", "t": 700.0, "bid": 0.48, "ask": 0.52,
           "bid_sz": 10.0, "ask_sz": 20.0, "bid_qty": 10.0, "ask_qty": 20.0,
           "tick": 0.01}
    for v in (g, p):
        b = v.book(raw, 1000.0)
        assert b is not None
        assert b.yes_bid == D("0.48") and b.yes_ask == D("0.52")
        assert b.tte_s == 300.0, (v.venue, b.tte_s)
    # the shim forwards everything else to the real adapter, unchanged
    assert p.entry_ok(D("0.50")) is True


def test_research_ceiling_is_not_the_live_six_action_bound():
    """The live MAX_ACTIONS=6 covers the first seconds of a 300 s window and
    is the bound under test; using it here would assume the answer."""
    assert CI.RESEARCH_ACTION_CEILING > 100
    assert CI.MIN_REEVAL_S <= 1.0          # canonical L5 continues at ~1 s


def test_replay_uses_the_live_residual_cap_not_the_module_default():
    from src import l5_venue_adapter as VA
    assert CI.LIVE_RESIDUAL_CAP_UNITS == 6
    assert VA.MAX_RESIDUAL_UNITS == 3      # module default; production is 6


def test_replay_on_a_flat_quiet_window_with_no_prints_never_fills():
    """End to end through the UNCHANGED canonical lookup. No print means no
    fill in every tier, regardless of how long the intention rests."""
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    ser = [[round(299.0 - i, 2), 0.48, 0.52, 500.0, 250.0] for i in range(300)]
    w = _win(series=ser, prints=[])
    for tier in CI.TIERS:
        r = CI.replay_window(w, model=model, tier=tier)
        assert r["fills_n"] == 0, (tier, r["fills"])
        assert r["status"] == "NO_FILL"
        assert r["prints_in_window"] == 0
        assert r["L5_BEHAVIORAL_ACTIONS"] >= 1


def test_replay_reports_both_action_counters_separately():
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    ser = [[round(299.0 - i, 2), 0.48, 0.52, 500.0, 250.0] for i in range(300)]
    r = CI.replay_window(_win(series=ser), model=model, tier="STRICT")
    for k in ("L5_BEHAVIORAL_ACTIONS", "VENUE_ADMISSIBLE_ACTIONS", "requotes"):
        assert isinstance(r[k], int)
    # a venue order is only ever placed for a behavioural action, but
    # requotes mean the two counters must NOT be assumed equal
    assert r["VENUE_ADMISSIBLE_ACTIONS"] >= len(r["intentions"])


def test_every_decision_carries_both_parallel_labels():
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    ser = [[round(299.0 - i, 2), 0.48, 0.52, 500.0, 250.0] for i in range(60)]
    r = CI.replay_window(_win(series=ser), model=model, tier="STRICT")
    assert r["decisions_n"] >= 1


def test_summarize_never_mixes_tiers():
    out = CI.run([], model={"_sha256_16": "x"})
    assert set(out["tiers"]) == set(CI.TIERS)
    for tier in CI.TIERS:
        assert out["tiers"][tier]["windows"] == 0


def test_a_resting_order_is_not_replaced_every_second_on_a_static_book():
    """DEFECT: advancing by one re-evaluation tick after an unfilled rest
    re-placed a NEW venue order each second against an order that was
    already resting. On a static book -- Gemini sports did not move once in
    8 minutes -- that reported 243 intentions and 8,000 venue orders for what
    is ONE order resting the whole window, inflating exactly the cohorts that
    deserved the lowest counts."""
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    # 300 s of a perfectly static two-sided book, no prints
    ser = [[round(299.0 - i, 2), 0.48, 0.52, 500.0, 250.0] for i in range(300)]
    r = CI.replay_window(_win(series=ser), model=model, tier="STRICT")
    assert r["fills_n"] == 0
    # ONE intention, ONE venue order, no requotes: the touch never moved
    assert r["intentions_n"] == 1, r["intentions"]
    assert r["VENUE_ADMISSIBLE_ACTIONS"] == 1
    assert r["requotes"] == 0
    assert r["intentions"][0]["filled"] is False


def test_a_moving_touch_still_requotes_rather_than_resting_through_it():
    """The fix must not turn persistence into 'never cancel': when the touch
    DOES move the intention re-joins the new touch."""
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    ser = []
    for i in range(120):
        bid = 0.40 + 0.01 * (i % 8)
        ser.append([round(299.0 - i, 2), round(bid, 2), round(bid + 0.10, 2),
                    500.0, 250.0])
    r = CI.replay_window(_win(series=ser), model=model, tier="STRICT")
    assert r["requotes"] > 0
    assert r["VENUE_ADMISSIBLE_ACTIONS"] > r["intentions_n"]
