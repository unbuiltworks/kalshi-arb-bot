"""The recommender picks ONE lever and must pick the right one.

The owner's standing rule is that capital is never proposed unless capital
is shown to bind, and the ranking must not send the next campaign after a
rounding error. Campaign 6 is the worked case: a $0.0970 defensive loss
against $1.6878 of edge is 5.7% and was the CORRECT call, while a 174s
arming delay cost a whole window of four.
"""
import importlib.util
import pathlib
from decimal import Decimal as D

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "campaign_report",
    pathlib.Path(__file__).resolve().parents[1]
    / "analysis" / "l5" / "campaign_report.py")
CR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CR)


def pair(cost, pnl):
    return {"w": "x", "qty": D(1), "cost": D(str(cost)),
            "pnl": D(str(pnl)), "defensive": D(str(cost)) > D("1.00")}


HEALTHY = dict(
    sent={i: {} for i in range(19)},
    filled=[{}] * 12,
    pairs=[pair("0.87", "0.26"), pair("0.85", "0.29")],
    defensive=D(0),
    peak=D("5.6382"),
    occ={"a": 110.0, "b": 63.0, "c": 63.0},
    refusals=[{}] * 23,
    qd=[1173.0, 2756.0, 4429.0],
    stranded={},
)


def rec(**over):
    kw = dict(HEALTHY)
    kw.update(over)
    return CR.recommend(**kw)


def test_capital_is_recommended_only_when_it_binds():
    out = rec(peak=D("11.5"))          # 96% of the $12 envelope
    assert "RAISE THE CASH ENVELOPE" in out


def test_capital_is_excluded_and_said_so_when_it_did_not_bind():
    out = rec()
    assert "RAISE THE CASH ENVELOPE" not in out
    assert "Capital did NOT bind" in out
    assert "excluded" in out


def test_stranded_inventory_outranks_everything_but_capital():
    out = rec(stranded={("1600z", "UP"): D("2.29")})
    assert "STRANDED" in out


def test_a_lost_window_outranks_an_immaterial_defensive_loss():
    """Campaign 6 exactly: 174s arming vs a 5.7% defensive drag."""
    out = rec(defensive=D("-0.0970"), pairs=C6_PAIRS,
              arming_late=[174.0])
    assert "ARMING LATENCY" in out
    assert "DEFENSIVE" not in out


#: Campaign 6's real six pairs. Edge is $1.6878, so its $0.0970 defensive
#: loss is 5.7% -- the figure the materiality bar exists to ignore.
C6_PAIRS = [pair("0.99", "0.0188"), pair("0.87", "0.26"),
            pair("1.05", "-0.0970"), pair("0.55", "0.9045"),
            pair("0.85", "0.2895"), pair("0.90", "0.2150")]


def test_campaign_6_defensive_loss_is_below_the_materiality_bar():
    assert float(CR.edge_of(C6_PAIRS)) == pytest.approx(1.6878)
    assert 0.0970 / 1.6878 < CR.DEFENSIVE_MATERIAL_FRACTION


def test_an_immaterial_defensive_loss_is_not_the_recommendation():
    """5.7% of edge is noise; with arming clean it must fall through."""
    out = rec(defensive=D("-0.0970"), pairs=C6_PAIRS)
    assert "REDUCE DEFENSIVE" not in out
    assert "EXTEND DURATION" in out


def test_a_material_defensive_loss_is_the_recommendation():
    out = rec(defensive=D("-0.60"),
              pairs=HEALTHY["pairs"] + [pair("1.20", "-0.60")])
    assert "REDUCE DEFENSIVE" in out


def test_slot_contention_recommends_a_second_order_only_when_occupied():
    out = rec(occ={"a": 500.0, "b": 480.0, "c": 460.0})
    assert "SECOND RESTING ORDER" in out
    # and NOT at Campaign 6's 13% occupancy
    assert "SECOND RESTING ORDER" not in rec()


def test_queue_position_needs_both_a_poor_fill_rate_and_a_deep_queue():
    out = rec(filled=[{}] * 5, qd=[3000.0])      # 26% fill, deep queue
    assert "QUEUE POSITION" in out
    shallow = rec(filled=[{}] * 5, qd=[10.0])    # poor fill, shallow queue
    assert "QUEUE POSITION" not in shallow


def test_the_healthy_case_recommends_duration_and_never_capital():
    out = rec()
    assert "EXTEND DURATION" in out
    assert "capital" not in out.split("Capital did NOT bind")[1].lower() \
        or "RAISE" not in out
