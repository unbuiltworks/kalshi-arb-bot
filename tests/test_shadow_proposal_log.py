"""The per-proposal row must be scorable, and must never read as a fill.

OWNER 2026-09-11: "Emit a durable per-proposal lifecycle row sufficient to
evaluate the pre-registered <=2-tick/high-flow hypothesis proposal-by-proposal
... Preserve explicit SHADOW / TRADE_THROUGH_PROXY semantics; never call these
fills or realized P&L."

The service already computed all of this and logged only AGGREGATES. An
aggregate cannot be re-cut, and the gate needs residual size, side, cost basis
and resolution joined on one row -- so the whole point of these tests is that
the row carries the join keys and keeps its labels.
"""
from __future__ import annotations

import json

from src import pmus_shadow_proposal_log as plog

MATCHED = {
    "id": "p1", "slug": "aec-nfl-bal-ind-2026-09-13", "tier": "T1_top8",
    "status": "VIRTUAL_MATCHED", "side_yes": True, "px": "0.59",
    "q1_size": 1, "max_pair_cost": "0.98", "t_post": 100.0, "t_fill": 145.1,
    "latency_s": 45.1, "touched": True, "traded_through": 12.0,
    "fillable_qty": 1.0, "pair_cost": "0.83",
    "matched_after": 1, "residual_before": -3, "residual_after": 0,
    "surplus_avg": "0.39",
    "first_leg": {"side": "NO", "px": "0.39", "t": 40.0},
    "state_at_post": {"distance_to_touch": -0.02, "spread": 0.01,
                      "flow_usd_per_min": 399.0, "mid_stdev": 0.0489,
                      "mid_range": 0.2, "bid_touch_qty": 91.0, "mid": 0.515,
                      "band_position": 0.53, "in_band": True, "sport": "nfl",
                      "market_type": "spread", "implied_pair_cost": "0.98"},
}
STRANDED = {**MATCHED, "id": "p2", "status": "NO_FILL", "t_fill": None,
            "latency_s": None, "traded_through": 0.0, "pair_cost": None,
            "matched_after": 0, "residual_after": -3}


# ----------------------------------------------------- the gate's join keys
def test_the_row_carries_everything_the_gate_needs_to_score_a_residual():
    """Size, side, cost basis and a slug to join resolution on. Without all
    four the gate cannot be computed, which is the whole reason for this."""
    r = plog.lifecycle_row(STRANDED)
    assert r["residual_units"] == -3
    assert r["residual_side"] == "down"
    assert r["surplus_avg_cost"] == "0.39"
    assert r["slug"] == "aec-nfl-bal-ind-2026-09-13"


def test_the_row_carries_every_decision_time_field_the_rule_may_use():
    r = plog.lifecycle_row(MATCHED)
    for f in ("distance_to_touch", "spread", "flow_usd_per_min", "mid_stdev",
              "mid_range", "bid_touch_qty", "mid", "band_position", "sport",
              "market_type"):
        assert r[f] is not None, f


def test_trade_through_timing_is_carried_for_both_sides():
    r = plog.lifecycle_row(MATCHED)
    assert r["first_side"] == "NO" and r["first_px"] == "0.39"
    assert r["first_t"] == 40.0
    assert r["complement_t_post"] == 100.0
    assert r["complement_t_trade_through"] == 145.1
    assert r["complement_latency_s"] == 45.1


# ------------------------------------------------------- the outcome class
def test_both_one_and_unresolved_are_derived_from_status():
    assert plog.lifecycle_row(MATCHED)["two_sided_outcome"] == plog.BOTH
    assert plog.lifecycle_row(STRANDED)["two_sided_outcome"] == plog.FIRST_ONLY
    pending = {**MATCHED, "status": "PENDING"}
    assert plog.lifecycle_row(pending)["two_sided_outcome"] == plog.UNRESOLVED


def test_every_row_says_why_neither_cannot_appear():
    """A proposal exists only after a virtual first leg, so NEITHER is
    impossible here. If that is not on the row, its absence reads as a
    measurement that both sides always trade."""
    r = plog.lifecycle_row(MATCHED)
    assert "NEITHER cannot occur" in r["outcome_note"]
    assert "by construction" in r["outcome_note"]


# ------------------------------------------------- the labels that must hold
def test_the_row_declares_shadow_and_trade_through_proxy():
    r = plog.lifecycle_row(MATCHED)
    assert "SHADOW: no order was placed" in r["semantics"]
    assert "TRADE_THROUGH_PROXY" in r["semantics"]
    assert "UPPER BOUND" in r["semantics"]
    assert "not fills" in r["semantics"]
    assert "no realized P&L" in r["semantics"]


def test_no_field_is_named_as_a_fill_or_a_realized_amount():
    """Banned USE, not banned word: `complement_t_trade_through` is fine and
    `realized_usd` is not. Checks field NAMES, so the semantics sentence --
    which must say the words -- cannot trip it."""
    r = plog.lifecycle_row(MATCHED)
    for k in r:
        assert not k.startswith("realized"), k
        assert k not in ("filled", "fill_qty", "realized_usd", "pnl"), k
    assert r["complement_qty_upper_bound"] == 1.0, (
        "the quantity is named an upper bound because that is what it is")


def test_resolution_is_null_by_design_and_says_so():
    r = plog.lifecycle_row(STRANDED)
    assert r["resolution"] is None
    assert "joined OFFLINE by slug" in r["resolution_note"]
    assert "null at emission by design rather than missing" in r["resolution_note"]
    r2 = plog.lifecycle_row(STRANDED, resolution={"side": "UP", "px": "1.00"})
    assert r2["resolution"] == {"side": "UP", "px": "1.00"}


# ------------------------------------------------------------- emission
def test_one_row_per_state_transition_not_per_cycle():
    seen, out = set(), []
    props = [MATCHED, STRANDED]
    assert plog.emit_new(props, seen, echo=out.append) == 2
    assert plog.emit_new(props, seen, echo=out.append) == 0, (
        "an unchanged proposal on every 70s cycle would bury the rows")
    advanced = [{**STRANDED, "status": "VIRTUAL_MATCHED"}]
    assert plog.emit_new(advanced, seen, echo=out.append) == 1
    assert len(out) == 3


def test_the_emitted_line_is_one_parseable_json_row():
    out = []
    plog.emit_new([MATCHED], set(), echo=out.append)
    line, = out
    assert line.startswith(plog.EMIT_PREFIX + " ")
    row = json.loads(line.split(" ", 1)[1])
    assert row["schema"] == plog.SCHEMA
    assert "\n" not in line, "one row, one line, or the log cannot be parsed"


def test_absent_is_none_and_never_zero():
    bare = {"id": "x", "slug": "s", "status": "WOULD_POST"}
    r = plog.lifecycle_row(bare)
    assert r["flow_usd_per_min"] is None and r["spread"] is None
    assert r["residual_units"] is None
    assert r["residual_side"] is None, "unknown is not 'none'"


# ------------------------------------------------------- wired, and safe
def test_the_breadth_report_emits_and_cannot_be_broken_by_it():
    import inspect
    from src import pmus_sports_shadow_breadth as SB
    src = inspect.getsource(SB)
    assert "plog.emit_new(" in src
    assert "proposal_rows_error" in src, "an emission must never stop the shadow"
    assert "self.proposal_rows_seen: set = set()" in src


def test_the_shadow_still_has_no_order_path():
    import inspect
    from src import pmus_shadow_proposal_log as m
    s = inspect.getsource(m)
    for banned in ("place_limit", "submit", "cancel", "Transport", "request("):
        assert banned not in s, banned


# ------------------------------------------- queue position (OWNER 2026-09-11)
def test_queue_ahead_is_null_and_explained_when_the_ladder_is_missing():
    """OWNER: "If we post at 79c while 211,000 shares are already bidding 81c,
    nobody can reach us." The row must carry that quantity -- and when the
    pipeline has discarded the ladder it must say NULL and say why, never
    estimate it from top-of-book. A guess here answers the question with a
    number we invented."""
    r = plog.lifecycle_row(STRANDED)          # no book_levels in the fixture
    assert r["queue_ahead_qty"] is None
    assert r["levels_better_than_us"] is None
    assert r["book_levels_available"] is False
    assert "PIPELINE gap, not a venue limitation" in r["queue_note"]
    assert "discards every level behind them" in r["queue_note"]


def test_queue_ahead_is_computed_when_the_ladder_survives():
    """The 211k-at-81c shape: we rest at 0.79 behind 211k at 0.81 and 40k at
    0.80, with 5k already at our own price."""
    prop = {**STRANDED, "px": "0.79", "state_at_post": {
        **STRANDED["state_at_post"],
        "book_levels": [{"px": "0.81", "qty": "211000"},
                        {"px": "0.80", "qty": "39923"},
                        {"px": "0.79", "qty": "5000"},
                        {"px": "0.78", "qty": "1000"}]}}
    r = plog.lifecycle_row(prop)
    assert r["book_levels_available"] is True
    assert r["queue_ahead_qty"] == 250923.0
    assert r["levels_better_than_us"] == 2
    assert r["size_at_our_price"] == 5000.0


def test_at_the_touch_nothing_is_ahead_and_that_needs_no_ladder():
    prop = {**STRANDED, "state_at_post": {
        **STRANDED["state_at_post"], "distance_to_touch": 0.0,
        "bid_touch_qty": 211000.0}}
    r = plog.lifecycle_row(prop)
    assert r["our_price_is_the_touch"] is True
    assert r["queue_ahead_qty"] == 0.0
    assert r["size_at_our_price"] == 211000.0
    assert r["levels_better_than_us"] == 0


def test_the_ladder_is_carried_through_from_the_shadow_state():
    """The execution shadow must pass book_levels along, or the field above
    can never populate however the collector changes."""
    import inspect
    from src import pmus_execution_shadow as ex
    src = inspect.getsource(ex.state_at_post)
    assert '"book_levels": row.get("book_levels")' in src
    assert '"ask_touch_qty": row.get("ask_touch_qty")' in src
