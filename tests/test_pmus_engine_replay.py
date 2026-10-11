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
