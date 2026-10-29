"""The P0: a fill total that is not split by side cannot answer the question."""
import io
import json

import pytest

from analysis.l5.gemini_surface import report as R


def _tape(tmp_path, rows):
    p = tmp_path / "gemini_surface_20260917T210000Z.jsonl"
    io.open(p, "w").write("".join(json.dumps(r) + "\n" for r in rows))
    return str(tmp_path)


def _fill(side, t, surface="touch_following", px="0.50"):
    return {"kind": "evidenced_fill", "surface": surface, "side": side,
            "px": px, "qty": "1", "evidence": R.STRICT, "t": t}


def _obs(bb="0.50", aa="0.51", surfaces=("touch_following",),
         rungs=None, residual="0"):
    """An observation shaped as src.gemini_l5_surface_shadow actually emits
    one: the touch at the top level, the ladder nested under each surface."""
    return {"kind": "observation", "best_bid": bb, "best_ask": aa,
            "spread": str(round(float(aa) - float(bb), 6)),
            "surfaces": {n: {"rungs": dict(rungs or {}),
                             "quotes_placed": 0, "live_quotes": 0,
                             "residual": residual}
                         for n in surfaces}}


def test_no_tape_says_so():
    assert R.report("/definitely/not/here")["ok"] is False


def test_one_sided_fills_are_named_as_such_not_reported_as_a_total(tmp_path):
    """17 fills, all BUY, is a DIFFERENT finding from 17 fills across both
    sides -- and an unsplit total cannot tell them apart."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]}]
    rows += [_fill("BUY", 100 + i) for i in range(17)]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["evidenced_fills"] == 17
    assert rep["fills_BUY"] == 17 and rep["fills_SELL"] == 0
    assert rep["two_sided"] is False
    assert rep["verdict"] == "BUY_ONLY_FILLS_SELL_NEVER_PLACED"
    assert rep["recycling"]["p_opposite_within_300s"] == 0.0


def test_two_sided_fills_are_distinguished(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _fill("BUY", 100), _fill("SELL", 110), _fill("BUY", 500)]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["two_sided"] is True and rep["verdict"] == "TWO_SIDED_FILLS"
    assert rep["fills_BUY"] == 2 and rep["fills_SELL"] == 1
    assert rep["recycling"]["n_opposite_within_15s"] == 1
    # BUY@100 -> SELL@110 is inside every mark from 15s up; SELL@110 -> BUY@500
    # is 390s and therefore outside 300s, so only ONE fill has an opposite
    assert rep["recycling"]["n_opposite_within_300s"] == 1


def test_fills_are_attributed_per_surface(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["a", "b"]},
            _fill("BUY", 1, surface="a"), _fill("SELL", 2, surface="a"),
            _fill("BUY", 3, surface="b")]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["by_surface"]["a"] == {"BUY": 1, "SELL": 1}
    assert rep["by_surface"]["b"]["BUY"] == 1


def test_no_fills_is_its_own_verdict(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _obs(rungs={"RESTABLE": 10, "book_reached_quote": 4,
                        "trade_flow_through_quote": 1, R.STRICT: 0})]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["verdict"] == "NO_EVIDENCED_FILLS"
    assert rep["rung_ladder"]["RESTABLE"] == 10
    assert rep["rung_ladder"]["trade_flow_through_quote"] == 1
    assert rep["recycling"]["n_fills"] == 0
    assert rep["recycling"]["p_opposite_within_30s"] is None


def test_the_ladder_is_read_from_where_the_shadow_writes_it(tmp_path):
    """It is nested under surfaces[name]. Reading the row's top level, where
    no such key exists, reported an empty ladder instead of a miss."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _obs(rungs={"RESTABLE": 3})]
    assert R.report(_tape(tmp_path, rows))["rung_ladder"] == {"RESTABLE": 3}


def test_the_ladder_is_not_summed_across_observations(tmp_path):
    """The shadow's counters are cumulative, so summing them would count each
    surviving quote once per observation it lived through."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _obs(rungs={"RESTABLE": 3}), _obs(rungs={"RESTABLE": 7})]
    assert R.report(_tape(tmp_path, rows))["rung_ladder"] == {"RESTABLE": 7}


def test_capacity_says_not_instrumented_rather_than_zero(tmp_path):
    """An n=0 reads as a measured absence. The sizes are simply not recorded."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]}, _obs()]
    cap = R.report(_tape(tmp_path, rows))["capacity"]
    assert cap["median_bid_size"] == "NOT_INSTRUMENTED"
    assert cap["queue_ahead_by_side"] == "NOT_INSTRUMENTED"
    assert cap["n_spreads"] == 1 and cap["median_spread"] == 0.01


# ------------------------------------------- the fork BUY_ONLY_FILLS hid

def test_a_sell_side_that_rested_is_not_a_sell_side_that_was_never_placed(
        tmp_path):
    """Opposite conclusions: one is a defect in our surface, the other is an
    observation about the market."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _obs(surfaces=("touch_following",)), _fill("BUY", 100)]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["placements"]["sell_ever_placed"] is True
    assert rep["placements"]["totals"]["SELL"] == 1
    assert rep["verdict"] == "BUY_ONLY_FILLS_SELL_RESTED_UNFILLED"


def test_a_bid_only_surface_is_reported_as_never_placing_a_sell(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["single_level"]},
            _obs(surfaces=("single_level",)), _fill("BUY", 100,
                                                    surface="single_level")]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["placements"]["by_surface"]["single_level"]["SELL"] == 0
    assert rep["placements"]["sell_ever_placed"] is False
    assert rep["verdict"] == "BUY_ONLY_FILLS_SELL_NEVER_PLACED"


def test_placements_come_from_the_shadows_own_surface_definitions(tmp_path):
    """Replayed through surface_quotes, so a definition changed in the shadow
    changes here too and the two cannot disagree."""
    rows = [{"kind": "session_start", "surfaces": ["multi_level_ladder"]},
            _obs(surfaces=("multi_level_ladder",))]
    per = R.report(_tape(tmp_path, rows))["placements"]["by_surface"]
    # the ladder is three bids and three offers by construction
    assert per["multi_level_ladder"]["BUY"] == 3
    assert per["multi_level_ladder"]["SELL"] == 3
    # every one of them rests: bids below the ask, offers above the bid
    assert per["multi_level_ladder"]["SELL_restable"] == 3


def test_an_unreadable_touch_is_counted_not_skipped(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            {"kind": "observation", "best_bid": None, "best_ask": None,
             "surfaces": {"touch_following": {"residual": "0"}}}]
    pl = R.report(_tape(tmp_path, rows))["placements"]
    assert pl["observations_unreadable"] == 1
    assert pl["totals"]["SELL"] == 0


def test_recycling_needs_both_legs_evidenced(tmp_path):
    """A resting opposite quote is not an interaction; only a fill is."""
    r = R.recycling([_fill("BUY", 100), _fill("BUY", 101)])
    assert r["n_opposite_within_300s"] == 0
    assert "not an interaction" in r["labels"]


def test_the_labels_state_what_the_split_is_for(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["a"]}, _fill("BUY", 1)]
    joined = " ".join(R.report(_tape(tmp_path, rows))["labels"])
    assert "broken complement" in joined
    assert "queue position is" in joined


# ------------------------------- an instrumented segment beats a replay

def _obs_native(placed, rungs, surface="two_sided_symmetric"):
    o = _obs(surfaces=(surface,))
    o["surfaces"][surface].update({
        "quotes_placed_by_side": placed, "rungs_by_side": rungs,
        "queue_ahead_by_side": {"BUY": {"live_quotes": 1},
                                "SELL": {"live_quotes": 1}}})
    return o


def test_a_recorded_split_is_used_in_preference_to_a_reconstructed_one(
        tmp_path):
    """What the surface DID beats a replay of what it would have wanted."""
    rows = [{"kind": "session_start", "surfaces": ["two_sided_symmetric"]},
            _obs_native({"BUY": 9, "SELL": 4},
                        {"SELL|RESTABLE": 4, "SELL|book_reached_quote": 3,
                         "SELL|trade_flow_through_quote": 0})]
    pl = R.report(_tape(tmp_path, rows))["placements"]
    assert "recorded by the shadow itself" in pl["method"]
    assert pl["totals"]["SELL"] == 4 and pl["totals"]["BUY"] == 9
    # the thing reconstruction cannot reach
    assert pl["totals"]["SELL_trade_flow"] == 0
    assert pl["by_surface"]["two_sided_symmetric"]["queue_ahead"] is not None


def test_a_recorded_split_is_not_summed_across_observations(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["two_sided_symmetric"]},
            _obs_native({"BUY": 3, "SELL": 1}, {}),
            _obs_native({"BUY": 9, "SELL": 4}, {})]
    pl = R.report(_tape(tmp_path, rows))["placements"]
    assert pl["totals"]["BUY"] == 9 and pl["totals"]["SELL"] == 4


def test_a_sell_side_with_flow_but_no_fill_is_a_queue_finding(tmp_path):
    """Placed, rested, reached, traded through -- and still no fill. That is
    a queue-position finding, not a broken sell side."""
    rows = [{"kind": "session_start", "surfaces": ["two_sided_symmetric"]},
            _obs_native({"BUY": 9, "SELL": 9},
                        {"SELL|RESTABLE": 9, "SELL|book_reached_quote": 7,
                         "SELL|trade_flow_through_quote": 5}),
            _fill("BUY", 100, surface="two_sided_symmetric")]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["verdict"] == "BUY_ONLY_FILLS_SELL_RESTED_UNFILLED"
    assert rep["placements"]["totals"]["SELL_trade_flow"] == 5
