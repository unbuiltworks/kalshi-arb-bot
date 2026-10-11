"""The manual-vs-engine comparison must read the engine's real constants and
must never invent a fill.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.pmus_manual_audit.engine_replay import (judge, load_gates,
                                                         replay)

#: verbatim from pmus-readonly-recovery's own PMUS_RECOVERY_BODY log line,
#: 2026-10-10T12:00:35Z. A real venue order, fully filled.
REAL_FILLED = {
    "id": "CREX7ZDP2YC6", "slug": "cpc-btc-updown-15m-2026-09-27-1300z",
    "side": "ORDER_SIDE_BUY", "type": "ORDER_TYPE_LIMIT",
    "limit_price": "0.31", "quantity": "0.9", "cumQuantity": "0.9",
}


def test_gates_come_from_the_engine_modules_not_from_this_file():
    g = load_gates()
    from src import pmus_micro_executor as micro
    from src import pmus_sports_l5_decision as dec
    from src import pmus_spring_l5_session as spring

    assert g.notional_cap == micro.PER_MARKET_FIRST_LEG_CAP
    assert (g.band_lo, g.band_hi) == (spring.SPRING_BAND_LO,
                                      spring.SPRING_BAND_HI)
    assert g.pair_cap == dec.PAIR_OBSERVATION_CAP
    assert g.hard_pair_bound == dec.hard_pair_bound()


def test_the_tighter_pair_cap_always_wins():
    g = load_gates()
    assert g.effective_pair_cap == min(g.pair_cap, g.hard_pair_bound)
    assert g.effective_pair_cap <= D("0.98")


def test_a_real_filled_order_is_over_the_per_market_cap():
    g = load_gates()
    r = judge(REAL_FILLED, g)
    assert r["owner_notional"] == "0.279"
    gates = [x["gate"] for x in r["refusals"]]
    assert "per_market_notional_cap" in gates
    assert "spring_band" not in gates, "0.31 is inside [0.25, 0.75]"
    assert r["ENGINE_VERDICT"] == "RESIZED"
    assert D(r["size_ratio_owner_over_engine"]) == pytest.approx(D("5.58"),
                                                                abs=D("0.01"))


def test_every_refusing_gate_is_named_not_just_the_first():
    g = load_gates()
    r = judge({"id": "X", "slug": "s", "limit_price": "0.90",
               "quantity": "9", "participateDontInitiate": False}, g)
    gates = {x["gate"] for x in r["refusals"]}
    assert {"spring_band", "per_market_notional_cap", "max_residual_units",
            "maker_only"} <= gates
    assert r["ENGINE_VERDICT"] == "BLOCKED"


def test_the_cap_is_cumulative_per_market():
    g = load_gates()
    one = {"id": "A", "slug": "same", "limit_price": "0.30",
           "quantity": "0.15"}          # 0.045, admissible alone
    assert judge(one, g)["ENGINE_VERDICT"] == "SUBMITTED"
    r = replay({"orders": [dict(one), dict(one, id="B")]})
    assert r["verdict_counts"].get("SUBMITTED") == 1
    assert r["orders"][1]["ENGINE_VERDICT"] in ("RESIZED", "BLOCKED")


def test_maker_only_uses_the_venue_flag_and_not_the_price():
    g = load_gates()
    passive = judge({"id": "A", "slug": "s", "limit_price": "0.30",
                     "quantity": "0.1",
                     "participateDontInitiate": True}, g)
    assert "maker_only" not in [x["gate"] for x in passive["refusals"]]
    aggressive = judge({"id": "B", "slug": "s", "limit_price": "0.30",
                        "quantity": "0.1",
                        "participateDontInitiate": False}, g)
    assert "maker_only" in [x["gate"] for x in aggressive["refusals"]]


def test_an_incomplete_walk_is_surfaced():
    r = replay({"activities_walk": {"complete": False, "ended": "max_pages"},
                "orders": []})
    assert r["walk_complete"] is False
    assert r["walk_ended"] == "max_pages"


def test_nothing_in_the_replay_decides_a_fill():
    """Fill facts must be echoed from the venue, never computed here."""
    import inspect

    from analysis.l5.pmus_manual_audit import engine_replay as er

    src = inspect.getsource(er)
    for banned in ("touch", "would_fill", "assume_fill", "filled =" ):
        assert banned not in src.replace("touch=fill", ""), banned
    g = load_gates()
    r = judge(REAL_FILLED, g)
    assert r["venue_cumQuantity"] == "0.9"


def test_a_manual_order_does_not_consume_the_engines_own_cap():
    """`spring_cumulative_notional` sums the CAMPAIGN LEDGER's rows for a
    market. An order the owner placed in the venue UI is not in that ledger,
    so it can never have blocked an engine submission."""
    r = replay({"orders": [
        {"id": "MANUAL", "slug": "same", "limit_price": "0.29",
         "quantity": "67.01",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"},
        {"id": "ENGINE", "slug": "same", "limit_price": "0.45",
         "quantity": "0.02",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"},
    ]})
    by_id = {o["order_id"]: o for o in r["orders"]}
    assert by_id["MANUAL"]["ORIGIN"] == "MANUAL"
    assert by_id["ENGINE"]["ORIGIN"] == "AUTOMATED"
    # the engine's own $0.009 order is well inside the $0.05 cap and must
    # be admitted, regardless of the 67-unit manual order beside it
    assert by_id["ENGINE"]["ENGINE_VERDICT"] == "SUBMITTED", \
        by_id["ENGINE"]["refusals"]
    assert r["origin_counts"] == {"MANUAL": 1, "AUTOMATED": 1}


def test_resize_quantity_is_never_negative():
    g = load_gates()
    r = judge({"id": "X", "slug": "s", "limit_price": "0.30",
               "quantity": "1"}, g, cumulative_notional=D("9.99"))
    assert D(r["engine_quantity_if_resized"]) == 0
    assert r["ENGINE_VERDICT"] == "BLOCKED"
    assert r["size_ratio_owner_over_engine"] is None


def test_the_largest_difference_is_judged_on_manual_orders_only():
    r = replay({"orders": [
        {"id": "M", "slug": "a", "limit_price": "0.29", "quantity": "67.01",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"},
        {"id": "E", "slug": "b", "limit_price": "0.90", "quantity": "9",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"},
    ]})
    assert r["largest_difference_scope"] == "manual orders only"
    assert r["manual_orders"] == 1
    assert set(r["manual_refusing_gate_counts"]) == {
        "per_market_notional_cap", "max_residual_units"}
    assert r["largest_behavioural_difference"] == "per_market_notional_cap"


def test_a_sell_order_is_structurally_unreachable_not_merely_resized():
    """`pmus_sports_l5_decision.decide` returns no_action/l5_sell_is_noop for
    a sell relation, and the spring session records the same stop_reason. The
    engine's whole vocabulary -- OPEN, EXTEND, COMPLETE -- is buys."""
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "outcomeSide": "OUTCOME_SIDE_NO", "limit_price": "0.29",
               "quantity": "0.1",              # small enough for every cap
               "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}, g)
    gates = {x["gate"] for x in r["refusals"]}
    assert gates == {"no_sell_path"}, gates
    assert r["ENGINE_VERDICT"] == "BLOCKED", "structural, never RESIZED"
    hit = [x for x in r["refusals"] if x["gate"] == "no_sell_path"][0]
    assert hit["engine_reason"] == "l5_sell_is_noop"
    assert hit["structural"] is True


def test_the_engine_really_does_treat_sell_as_a_noop():
    """Read it from the engine, so this test fails if the engine ever gains
    a sell path and the replay is left claiming it has none."""
    import inspect

    from src import pmus_sports_l5_decision as dec

    assert "l5_sell_is_noop" in inspect.getsource(dec)


def test_a_structural_refusal_can_never_be_resized():
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "limit_price": "0.30", "quantity": "67"}, g)
    assert r["ENGINE_VERDICT"] == "BLOCKED"
    assert {"no_sell_path", "per_market_notional_cap",
            "max_residual_units"} <= {x["gate"] for x in r["refusals"]}
