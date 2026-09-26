"""Probe 08's one treatment: IMBALANCE_INCREASE_SUPPRESSED.

The rule is: while the market's REALIZED residual is non-zero, refuse a new
maker order that would increase |residual|. It refuses and does nothing else.
The tests below are written so that a change which quietly widens it -- into
a price change, a side change, a taker conversion, a cap, or a block on the
repairing side -- fails here rather than live.

Probe 07 is the control, so the switch must default OFF and the untreated
path must be bit-for-bit what it was.
"""
from __future__ import annotations

import pathlib
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_probe_manifest as PM
import tests.test_l5_live_execution as L
import scripts.l5_pmus_live_oneshot as ONE

MARKET = "cpc-btc-updown-15m-2026-09-26-0000z"


def limits():
    return EX.SafetyLimits(max_usd_per_market=D("5"),
                           max_abs_residual=D("10"),
                           max_total_exposure_usd=D("5"),
                           max_concurrent_markets=1,
                           max_live_orders=8,
                           daily_realized_loss_halt_usd=D("5"))


def executor(tmp_path, *, treated: bool, transport=None):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(
        store, limits(), transport=transport or L.FakeTransport(),
        lam=PM.LAMBDA, now_fn=L.NOW, maker_treatment=True, maker_only=False,
        suppress_imbalance_increase=treated)
    return ex, store


def drive(ex, **rowkw):
    return ex.handle(L.row(**rowkw), {}, market=MARKET, window="0000z")


# ------------------------------------------------- the classification itself

@pytest.mark.parametrize("residual,side,expected", [
    ("0", "UP", EX.FLAT_OPEN),
    ("0", "DOWN", EX.FLAT_OPEN),
    ("1", "UP", EX.INCREASE_RESIDUAL),
    ("1", "DOWN", EX.REDUCE_RESIDUAL),
    ("-1", "UP", EX.REDUCE_RESIDUAL),
    ("-1", "DOWN", EX.INCREASE_RESIDUAL),
    ("0.01", "UP", EX.INCREASE_RESIDUAL),
    ("-0.01", "UP", EX.REDUCE_RESIDUAL),
])
def test_the_truth_table(residual, side, expected):
    assert EX.inventory_class(D(residual), side) == expected


def test_every_class_is_one_of_exactly_three():
    seen = {EX.inventory_class(D(r), s)
            for r in ("-2", "0", "2") for s in ("UP", "DOWN")}
    assert seen == {EX.FLAT_OPEN, EX.INCREASE_RESIDUAL, EX.REDUCE_RESIDUAL}


def test_classification_reads_realized_residual_not_outstanding(tmp_path):
    """The directive says 'current realized residual'. An unfilled order has
    not moved the imbalance, and must not be allowed to fake one."""
    ex, store = executor(tmp_path, treated=True)
    store.record_submit("c1", {"market": MARKET, "side": "UP",
                               "qty": "5", "price": "0.7"})
    assert store.residual(MARKET) == 0
    rec = drive(ex, side="UP", key="k1")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.FLAT_OPEN
    assert rec["INVENTORY_CLASS"]["basis"].startswith("REALIZED")


# --------------------------------------------------- what the gate permits

def test_flat_open_is_permitted_under_treatment(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    assert store.residual(MARKET) == 0
    rec = drive(ex, side="UP", key="flat")
    assert rec["refused_reason"] != "IMBALANCE_INCREASE_SUPPRESSED"
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.FLAT_OPEN


def test_the_repairing_side_is_permitted_under_treatment(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    assert store.residual(MARKET) == D("2")
    rec = drive(ex, side="DOWN", key="repair")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.REDUCE_RESIDUAL
    assert rec["refused_reason"] != "IMBALANCE_INCREASE_SUPPRESSED"


def test_the_repairing_side_is_permitted_even_far_past_any_cap(tmp_path):
    """No residual threshold was authorized. Repair is never blocked by size."""
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("9.5"), D("0.70"))
    rec = drive(ex, side="DOWN", key="deep")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.REDUCE_RESIDUAL
    assert rec["refused_reason"] != "IMBALANCE_INCREASE_SUPPRESSED"


# --------------------------------------------------- what the gate refuses

def test_increasing_a_long_residual_is_refused(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="more_up")
    assert rec["refused_reason"] == "IMBALANCE_INCREASE_SUPPRESSED"
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"


def test_increasing_a_short_residual_is_refused(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "DOWN", D("2"), D("0.30"))
    assert store.residual(MARKET) == D("-2")
    rec = drive(ex, side="DOWN", key="more_down")
    assert rec["refused_reason"] == "IMBALANCE_INCREASE_SUPPRESSED"


def test_the_smallest_possible_residual_still_triggers_it(tmp_path):
    """Non-zero means non-zero. No tolerance band was authorized."""
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("0.01"), D("0.70"))
    rec = drive(ex, side="UP", key="tiny")
    assert rec["refused_reason"] == "IMBALANCE_INCREASE_SUPPRESSED"


def test_a_refused_action_sends_nothing(tmp_path):
    tp = L.FakeTransport()
    ex, store = executor(tmp_path, treated=True, transport=tp)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="nosend")
    assert tp.sent == [], "a suppressed action reached the transport"
    assert rec["ORDER"] is None, "a suppressed action recorded an order"


def test_a_refused_action_reserves_nothing(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    before = store.live_order_count()
    drive(ex, side="UP", key="nores")
    assert store.live_order_count() == before
    assert store.residual(MARKET) == D("2"), "inventory moved on a refusal"


# ------------------------------------------- the control must be untouched

def test_the_switch_defaults_off(monkeypatch):
    monkeypatch.delenv(ONE.IMBALANCE_SUPPRESS_ENV, raising=False)
    assert ONE.imbalance_suppress_enabled() is False


@pytest.mark.parametrize("val,on", [("YES", True), ("1", True), ("TRUE", True),
                                    ("", False), ("NO", False), ("0", False),
                                    ("maybe", False)])
def test_the_switch_reads_only_explicit_yes(monkeypatch, val, on):
    monkeypatch.setenv(ONE.IMBALANCE_SUPPRESS_ENV, val)
    assert ONE.imbalance_suppress_enabled() is on


def test_untreated_the_same_action_is_not_refused(tmp_path):
    """Probe 07's behaviour, reproduced exactly with the switch off."""
    ex, store = executor(tmp_path, treated=False)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="control")
    assert rec["refused_reason"] != "IMBALANCE_INCREASE_SUPPRESSED"
    assert rec["INVENTORY_CLASS"]["treatment_active"] is False


def test_untreated_still_classifies_every_action(tmp_path):
    """The class is evidence, not a side effect of the treatment."""
    ex, store = executor(tmp_path, treated=False)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="classify")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.INCREASE_RESIDUAL


# ----------------------------------------- it refuses, and nothing else

def test_the_treatment_never_changes_price_or_quantity(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    a, _ = executor(tmp_path / "a", treated=False)
    b, _ = executor(tmp_path / "b", treated=True)
    ra = drive(a, side="UP", key="px")
    rb = drive(b, side="UP", key="px")
    assert ra["PMUS_TRANSLATION"] == rb["PMUS_TRANSLATION"]


def test_the_treatment_never_flips_a_side(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="flip")
    assert rec["L5_NATIVE"]["side"] == "UP"
    assert rec["ORDER"] is None


def test_the_treatment_cannot_raise_exposure(tmp_path):
    """It can only decline, so the treated path's risk is never the higher."""
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    a, sa = executor(tmp_path / "a", treated=False)
    b, sb = executor(tmp_path / "b", treated=True)
    for s in (sa, sb):
        s.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    for i in range(4):
        drive(a, side="UP", key="a%d" % i)
        drive(b, side="UP", key="b%d" % i)
    assert (sb.risk_exposure_usd(MARKET) <= sa.risk_exposure_usd(MARKET))


def test_classification_does_not_advance_the_provenance_chain(tmp_path):
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    rec = drive(ex, side="UP", key="stage")
    assert rec["stage_reached"] != "INVENTORY_CLASS"


def test_the_reason_string_is_exact(tmp_path):
    """Reports and the manifest both key on it; a rename is a silent break."""
    ex, store = executor(tmp_path, treated=True)
    store.apply_fill(MARKET, "UP", D("2"), D("0.70"))
    assert drive(ex, side="UP", key="s")["refused_reason"] == (
        "IMBALANCE_INCREASE_SUPPRESSED")


# -------------------------------------------------------- the report side

def test_the_summary_counts_by_inventory_class():
    rows = [
        {"l5_side": "UP", "inventory_class": EX.FLAT_OPEN, "submitted": True},
        {"l5_side": "UP", "inventory_class": EX.INCREASE_RESIDUAL,
         "refused_reason": "IMBALANCE_INCREASE_SUPPRESSED"},
        {"l5_side": "DOWN", "inventory_class": EX.REDUCE_RESIDUAL,
         "submitted": True},
    ]
    s = ONE.action_summary(rows)
    assert s["imbalance_increase_suppressed"] == 1
    assert s["by_inventory_class"][EX.INCREASE_RESIDUAL]["suppressed"] == 1
    assert s["by_inventory_class"][EX.REDUCE_RESIDUAL]["submitted"] == 1
    assert s["by_inventory_class"][EX.FLAT_OPEN]["permitted"] == 1


def test_the_action_row_carries_the_class_and_the_residual():
    assert "inventory_class" in ONE.ACTION_COLUMNS
    assert "residual_before" in ONE.ACTION_COLUMNS
    rec = {"kind": "action", "market": MARKET,
           "L5_NATIVE": {"side": "UP"}, "PMUS_TRANSLATION": {},
           "INVENTORY_CLASS": {"inventory_class": EX.REDUCE_RESIDUAL,
                               "residual_before": "-2"}}
    row = ONE.action_table([rec], MARKET)[0]
    assert row["inventory_class"] == EX.REDUCE_RESIDUAL
    assert row["residual_before"] == "-2"
