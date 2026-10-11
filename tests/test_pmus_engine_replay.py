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
    "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
    "intent": "ORDER_INTENT_BUY_LONG", "type": "ORDER_TYPE_LIMIT",
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
    assert r["owner_notional_own_terms"] == "0.279"
    gates = [x["gate"] for x in r["refusals"]]
    assert "per_market_notional_cap" in gates
    assert "spring_band" not in gates, "0.31 is inside [0.25, 0.75]"
    assert r["ENGINE_VERDICT"] == "RESIZED"
    assert D(r["size_ratio_owner_over_engine"]) == pytest.approx(D("5.58"),
                                                                abs=D("0.01"))


def test_every_refusing_gate_is_named_not_just_the_first():
    g = load_gates()
    r = judge({"id": "X", "slug": "s", "limit_price": "0.90",
               "quantity": "9", "participateDontInitiate": False,
               "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
               "intent": "ORDER_INTENT_BUY_LONG"}, g)
    gates = {x["gate"] for x in r["refusals"]}
    assert {"spring_band", "per_market_notional_cap", "max_residual_units",
            "maker_only"} <= gates
    assert r["ENGINE_VERDICT"] == "BLOCKED"


def test_the_cap_is_cumulative_per_market():
    g = load_gates()
    one = {"id": "A", "slug": "same", "limit_price": "0.30",
           "quantity": "0.15", "side": "ORDER_SIDE_BUY",
           "outcomeSide": "OUTCOME_SIDE_YES",
           "intent": "ORDER_INTENT_BUY_LONG"}          # 0.045, admissible alone
    assert judge(one, g)["ENGINE_VERDICT"] == "SUBMITTED"
    r = replay({"orders": [dict(one), dict(one, id="B")]})
    assert r["verdict_counts"].get("SUBMITTED") == 1
    assert r["orders"][1]["ENGINE_VERDICT"] in ("RESIZED", "BLOCKED")


def test_maker_only_uses_the_venue_flag_and_not_the_price():
    g = load_gates()
    passive = judge({"id": "A", "slug": "s", "limit_price": "0.30",
                     "quantity": "0.1", "side": "ORDER_SIDE_BUY",
                     "outcomeSide": "OUTCOME_SIDE_YES",
                     "intent": "ORDER_INTENT_BUY_LONG",
                     "participateDontInitiate": True}, g)
    assert "maker_only" not in [x["gate"] for x in passive["refusals"]]
    aggressive = judge({"id": "B", "slug": "s", "limit_price": "0.30",
                        "quantity": "0.1", "side": "ORDER_SIDE_BUY",
                        "outcomeSide": "OUTCOME_SIDE_YES",
                        "intent": "ORDER_INTENT_BUY_LONG",
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
         "quantity": "67.01", "side": "ORDER_SIDE_SELL",
         "outcomeSide": "OUTCOME_SIDE_NO",
         "intent": "ORDER_INTENT_BUY_SHORT",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"},
        {"id": "ENGINE", "slug": "same", "limit_price": "0.45",
         "quantity": "0.02", "side": "ORDER_SIDE_BUY",
         "outcomeSide": "OUTCOME_SIDE_YES",
         "intent": "ORDER_INTENT_BUY_LONG",
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
               "quantity": "1", "side": "ORDER_SIDE_BUY",
               "outcomeSide": "OUTCOME_SIDE_YES",
               "intent": "ORDER_INTENT_BUY_LONG"}, g,
              cumulative_notional=D("9.99"))
    assert D(r["engine_quantity_if_resized"]) == 0
    assert r["ENGINE_VERDICT"] == "BLOCKED"
    assert r["size_ratio_owner_over_engine"] is None


def test_the_largest_difference_is_judged_on_manual_orders_only():
    r = replay({"orders": [
        {"id": "M", "slug": "a", "limit_price": "0.29", "quantity": "67.01",
         "side": "ORDER_SIDE_SELL", "outcomeSide": "OUTCOME_SIDE_NO",
         "intent": "ORDER_INTENT_BUY_SHORT",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"},
        {"id": "E", "slug": "b", "limit_price": "0.90", "quantity": "9",
         "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
         "intent": "ORDER_INTENT_BUY_LONG",
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"},
    ]})
    assert r["largest_difference_scope"] == "manual orders only"
    assert r["manual_orders"] == 1
    assert set(r["manual_refusing_gate_counts"]) == {
        "per_market_notional_cap", "max_residual_units"}, \
        r["manual_refusing_gate_counts"]
    assert r["largest_behavioural_difference"] == "per_market_notional_cap"


def test_buy_short_is_a_NO_PURCHASE_and_the_engine_can_express_it():
    """PMUS states price and side in LONG (YES) token terms: "buy NO" is
    "sell YES", and `l5_order_body` sends price.value = 1 - own price. So
    ORDER_SIDE_SELL + OUTCOME_SIDE_NO + ORDER_INTENT_BUY_SHORT is a PURCHASE
    of NO, which is exactly the engine's DOWN leg -- not a missing
    capability. An earlier draft refused it as `no_sell_path`; that was a
    misreading of the venue and is withdrawn."""
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "outcomeSide": "OUTCOME_SIDE_NO",
               "intent": "ORDER_INTENT_BUY_SHORT",
               "limit_price": "0.29", "quantity": "0.07",
               "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}, g)
    assert r["direction"] == "SHORT"
    assert r["own_terms_price"] == "0.71", "wire 0.29 is own 0.71"
    assert r["wire_price_value"] == "0.29"
    assert "no_sell_path" not in {x["gate"] for x in r["refusals"]}
    eq = r["engine_equivalent_action"]
    assert eq["engine_side"] == "DOWN"
    assert eq["outcome_side"] == "OUTCOME_SIDE_NO"
    assert eq["own_terms_price"] == "0.71"
    assert eq["wire_price_value"] == "0.29"
    assert r["CATEGORY"] == "2_EXPRESSED_DIFFERENTLY"
    assert r["ENGINE_VERDICT"] == "SUBMITTED"


def test_the_own_terms_price_drives_every_gate_not_the_wire_price():
    """Judging a short on its wire price understates what it costs. Wire
    0.29 x 67.01 reads $19.43; the position actually costs 0.71 x 67.01 =
    $47.58, and it is the second number the engine's constants are about."""
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "outcomeSide": "OUTCOME_SIDE_NO",
               "intent": "ORDER_INTENT_BUY_SHORT",
               "limit_price": "0.29", "quantity": "67.01"}, g)
    assert r["owner_notional_own_terms"] == "47.5771"
    assert r["own_terms_price"] == "0.71"


def test_a_short_at_a_wire_price_that_is_out_of_band_in_own_terms():
    """Wire 0.80 is own 0.20, which is BELOW the 0.25 band floor -- a gate
    that reading the wire price would have missed entirely."""
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "outcomeSide": "OUTCOME_SIDE_NO",
               "intent": "ORDER_INTENT_BUY_SHORT",
               "limit_price": "0.80", "quantity": "0.05"}, g)
    assert r["own_terms_price"] == "0.20"
    assert "spring_band" in {x["gate"] for x in r["refusals"]}
    assert r["CATEGORY"] == "3_BLOCKED_BY_PRICING_OR_INVENTORY_POLICY"


def test_an_unreadable_direction_is_never_assumed_long():
    g = load_gates()
    r = judge({"id": "X", "slug": "s", "limit_price": "0.30",
               "quantity": "0.1"}, g)
    assert r["direction"] == "UNKNOWN"
    assert r["CATEGORY"] == "1_UNSUPPORTED_ACTION"
    assert "direction_unreadable" in {x["gate"] for x in r["refusals"]}


def test_the_liquidation_ambiguity_is_reported_not_resolved():
    g = load_gates()
    r = judge({"id": "S", "slug": "s", "side": "ORDER_SIDE_SELL",
               "outcomeSide": "OUTCOME_SIDE_NO",
               "intent": "ORDER_INTENT_BUY_SHORT",
               "limit_price": "0.29", "quantity": "0.07"}, g)
    assert "l5_sell_is_noop" in r["liquidation_ambiguity"]
    assert "inventory" in r["liquidation_ambiguity"]


def test_only_capital_refusal_is_category_4():
    g = load_gates()
    # own 0.50, qty 1 -> $0.50: over the $0.05 cap, inside the band, and
    # 1 unit is inside the residual cap, so capital is the ONLY refusal
    r = judge({"id": "X", "slug": "s", "side": "ORDER_SIDE_BUY",
               "outcomeSide": "OUTCOME_SIDE_YES",
               "intent": "ORDER_INTENT_BUY_LONG",
               "limit_price": "0.50", "quantity": "1"}, g)
    assert {x["gate"] for x in r["refusals"]} == {"per_market_notional_cap"}
    assert r["CATEGORY"] == "4_BLOCKED_SOLELY_BY_CAPITAL_LIMITS"
    assert r["ENGINE_VERDICT"] == "RESIZED"
