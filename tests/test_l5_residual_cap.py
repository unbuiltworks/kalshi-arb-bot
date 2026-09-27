"""RESIDUAL_CAP: the one addition on top of frozen e2bd442.

    refuse iff  |residual after this fill|  >  k * max(largest clip this
                market has FILLED, this order's own qty)

Units are CONTRACTS on both sides of the comparison. k is dimensionless.

The rule can only ever DECLINE. These tests are written so that a change
which quietly widens it -- into a price change, a side change, a taker
conversion, a block on an opening leg, or a block on a repairing leg --
fails here rather than live. `residual_cap_k=None` is the default and must
be indistinguishable from the frozen executor.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_probe_manifest as PM
from src import l5_pmus_live_runner as R
import tests.test_l5_live_execution as L

MARKET = "cpc-btc-updown-15m-2026-09-26-0000z"


def limits():
    return EX.SafetyLimits(max_usd_per_market=D("5"),
                           max_abs_residual=D("10"),
                           max_total_exposure_usd=D("5"),
                           max_concurrent_markets=1,
                           max_live_orders=8,
                           daily_realized_loss_halt_usd=D("5"))


def executor(tmp_path, *, k, transport=None):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(
        store, limits(), transport=transport or L.FakeTransport(),
        lam=PM.LAMBDA, now_fn=L.NOW, maker_treatment=True, maker_only=False,
        suppress_imbalance_increase=False, residual_cap_k=k)
    return ex, store


def drive(ex, **rowkw):
    return ex.handle(L.row(**rowkw), {}, market=MARKET, window="0000z")


def note(rec):
    return rec.get("RESIDUAL_CAP")


# ------------------------------------------------------- the clip it counts

def test_max_clip_is_zero_before_any_fill(tmp_path):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    assert store.max_clip(MARKET) == D(0)


def test_max_clip_is_the_largest_single_fill_not_the_total(tmp_path):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    for q in ("2", "7", "3"):
        store.apply_fill(MARKET, "UP", D(q), D("0.50"), basis=EX.OWN)
    assert store.residual(MARKET) == D("12")
    assert store.max_clip(MARKET) == D("7")


def test_max_clip_is_per_market(tmp_path):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    store.apply_fill(MARKET, "UP", D("9"), D("0.50"), basis=EX.OWN)
    assert store.max_clip("cpc-btc-updown-15m-2026-09-26-0015z") == D(0)


def test_max_clip_survives_a_reload(tmp_path):
    path = str(tmp_path / "inv.json")
    EX.InventoryStore(path).apply_fill(MARKET, "UP", D("4"), D("0.5"),
                                       basis=EX.OWN)
    assert EX.InventoryStore(path).max_clip(MARKET) == D("4")


def test_max_clip_refuses_an_unidentified_market(tmp_path):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    with pytest.raises(EX.UnknownMarket):
        store.max_clip("")


# ------------------------------------------------ what the gate cannot touch

def test_an_opening_leg_from_flat_is_never_reachable(tmp_path):
    """FLAT_OPEN sits in front of the cap, so no k can refuse an OPEN."""
    for k in ("0.0001", "1", "2", "1000"):
        ex, store = executor(tmp_path / k, k=D(k))
        assert store.residual(MARKET) == 0
        rec = drive(ex, side="UP", key="open")
        assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.FLAT_OPEN
        assert note(rec) is None, "the cap was even evaluated on an OPEN"
        assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_a_repairing_leg_is_never_reachable_however_deep(tmp_path):
    ex, store = executor(tmp_path, k=D("0.0001"))
    store.apply_fill(MARKET, "UP", D("9.5"), D("0.30"), basis=EX.OWN)
    rec = drive(ex, side="DOWN", key="repair")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.REDUCE_RESIDUAL
    assert note(rec) is None
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_a_repairing_leg_on_a_short_residual_is_never_reachable(tmp_path):
    ex, store = executor(tmp_path, k=D("0.0001"))
    store.apply_fill(MARKET, "DOWN", D("9.5"), D("0.30"), basis=EX.OWN)
    assert store.residual(MARKET) == D("-9.5")
    rec = drive(ex, side="UP", key="repair_short")
    assert note(rec) is None
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


@pytest.mark.parametrize("resid_side,cand_side", [("UP", "UP"), ("DOWN", "DOWN")])
def test_only_an_increasing_acquisition_is_ever_evaluated(tmp_path, resid_side,
                                                          cand_side):
    ex, store = executor(tmp_path / (resid_side + cand_side), k=D("2"))
    store.apply_fill(MARKET, resid_side, D("1"), D("0.50"), basis=EX.OWN)
    rec = drive(ex, side=cand_side, key="inc")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.INCREASE_RESIDUAL
    assert note(rec) is not None


# -------------------------------------------------------------- the formula

def qty_of(tmp_path, side="UP"):
    """What the frozen path sizes this row at, with the cap off."""
    ex, store = executor(tmp_path / "probe", k=None)
    store.apply_fill(MARKET, side, D("1"), D("0.50"), basis=EX.OWN)
    rec = drive(ex, side=side, key="sizeprobe")
    return D(str(rec["PMUS_TRANSLATION"]["qty"]))


def test_the_cap_is_k_times_the_clip_and_the_clip_includes_this_order(tmp_path):
    q = qty_of(tmp_path)
    ex, store = executor(tmp_path / "a", k=D("2"))
    store.apply_fill(MARKET, "UP", D("1"), D("0.50"), basis=EX.OWN)
    n = note(drive(ex, side="UP", key="f"))
    assert D(n["max_clip_filled"]) == D("1")
    assert D(n["clip_used"]) == max(D("1"), q)
    assert D(n["cap_contracts"]) == D("2") * max(D("1"), q)
    assert n["units"] == "contracts"


def test_the_residual_it_compares_is_after_a_full_fill(tmp_path):
    q = qty_of(tmp_path)
    ex, store = executor(tmp_path / "b", k=D("2"))
    store.apply_fill(MARKET, "UP", D("3"), D("0.50"), basis=EX.OWN)
    n = note(drive(ex, side="UP", key="g"))
    assert D(n["residual_before"]) == D("3")
    assert D(n["residual_after_if_filled"]) == D("3") + q
    assert D(n["abs_residual_after"]) == D("3") + q


def test_a_short_residual_is_compared_on_magnitude(tmp_path):
    q = qty_of(tmp_path, side="DOWN")
    ex, store = executor(tmp_path / "c", k=D("2"))
    store.apply_fill(MARKET, "DOWN", D("3"), D("0.50"), basis=EX.OWN)
    n = note(drive(ex, side="DOWN", key="h"))
    assert D(n["residual_after_if_filled"]) == -(D("3") + q)
    assert D(n["abs_residual_after"]) == D("3") + q


# -------------------------------------------------------- at the boundary

def boundary_store(tmp_path, k, *, over: D):
    """A market where |residual after| lands exactly `over` past the cap.

    clip is pinned by a first fill larger than the order, so
    cap = k*clip is known before the candidate is priced.
    """
    q = qty_of(tmp_path)
    clip = D("4")                       # comfortably above the order size
    assert clip > q
    cap = k * clip
    ex, store = executor(tmp_path / ("bd%s" % over), k=k)
    store.apply_fill(MARKET, "UP", clip, D("0.20"), basis=EX.OWN)
    extra = cap + over - q - clip
    if extra > 0:
        store.apply_fill(MARKET, "UP", extra, D("0.20"), basis=EX.OWN)
    assert store.max_clip(MARKET) == clip, "the helper moved the clip"
    return ex, store, cap, q


def test_exactly_at_the_cap_passes(tmp_path):
    ex, store, cap, q = boundary_store(tmp_path, D("2"), over=D("0"))
    rec = drive(ex, side="UP", key="at")
    n = note(rec)
    assert D(n["abs_residual_after"]) == cap
    assert n["binds"] is False
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_one_hundredth_of_a_contract_past_the_cap_refuses(tmp_path):
    ex, store, cap, q = boundary_store(tmp_path, D("2"), over=D("0.01"))
    rec = drive(ex, side="UP", key="over")
    n = note(rec)
    assert D(n["abs_residual_after"]) == cap + D("0.01")
    assert n["binds"] is True
    assert rec["refused_reason"] == "RESIDUAL_ABOVE_CAP"
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"


def test_one_hundredth_below_the_cap_passes(tmp_path):
    ex, store, cap, q = boundary_store(tmp_path, D("2"), over=D("-0.01"))
    rec = drive(ex, side="UP", key="under")
    assert note(rec)["binds"] is False
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


# ------------------------------------------------- a refusal moves nothing

def test_a_refused_action_sends_nothing_and_reserves_nothing(tmp_path):
    tp = L.FakeTransport()
    ex, store = executor(tmp_path, k=D("0.5"), transport=tp)
    store.apply_fill(MARKET, "UP", D("4"), D("0.20"), basis=EX.OWN)
    before_live = store.live_order_count()
    before_resid = store.residual(MARKET)
    rec = drive(ex, side="UP", key="nosend")
    assert rec["refused_reason"] == "RESIDUAL_ABOVE_CAP"
    assert tp.sent == [], "a capped action reached the transport"
    assert rec["ORDER"] is None
    assert store.live_order_count() == before_live
    assert store.residual(MARKET) == before_resid


def test_the_cap_never_changes_price_side_or_mode(tmp_path):
    """It refuses or it does nothing. It never rewrites the order."""
    q = qty_of(tmp_path)
    off, s_off = executor(tmp_path / "off", k=None)
    on, s_on = executor(tmp_path / "on", k=D("100"))
    for s in (s_off, s_on):
        s.apply_fill(MARKET, "UP", D("1"), D("0.50"), basis=EX.OWN)
    a = drive(off, side="UP", key="same")
    b = drive(on, side="UP", key="same")
    assert a["PMUS_TRANSLATION"] == b["PMUS_TRANSLATION"]
    assert a["refused_reason"] == b["refused_reason"]


# ------------------------------------------------------- the switch is OFF

def test_the_default_is_off(tmp_path):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(store, limits(), transport=L.FakeTransport(),
                             lam=PM.LAMBDA, now_fn=L.NOW)
    assert ex.residual_cap_k is None


def test_off_never_evaluates_or_refuses(tmp_path):
    ex, store = executor(tmp_path, k=None)
    store.apply_fill(MARKET, "UP", D("9"), D("0.20"), basis=EX.OWN)
    rec = drive(ex, side="UP", key="native")
    assert note(rec) is None
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


@pytest.mark.parametrize("val,expect", [
    ("", None), ("   ", None), ("2", D("2")), ("2.0", D("2.0")),
    ("0", None), ("-2", None), ("two", None), ("YES", None), ("1e1", D("10")),
])
def test_the_env_switch_reads_only_a_positive_number(monkeypatch, val, expect):
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", val)
    assert R.residual_cap_k() == expect


def test_the_env_switch_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("PMUS_RESIDUAL_CAP_K", raising=False)
    assert R.residual_cap_k() is None


# ------------------------------- the cap only ever subtracts from NATIVE

def test_an_envelope_refusal_keeps_its_own_reason(tmp_path):
    """The cap sits behind every frozen check, so it can never RELABEL a
    refusal the frozen executor already made. If it could, the bind count
    would silently absorb envelope refusals and stop meaning 'what this
    policy cost us'."""
    ex, store = executor(tmp_path, k=D("0.0001"))
    # 9 UP contracts at $0.90 is $8.10 of worst-case loss: past the $5 gate.
    store.apply_fill(MARKET, "UP", D("9"), D("0.90"), basis=EX.OWN)
    rec = drive(ex, side="UP", key="envelope")
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"
    assert note(rec) is None, "the cap was evaluated on an already-refused action"


def test_every_action_the_cap_refuses_was_permitted_by_the_frozen_path(tmp_path):
    """Run the same actions twice, cap off and cap on. The capped run's
    refusals must be a strict SUBSET of the frozen run's submissions."""
    off, s_off = executor(tmp_path / "n", k=None)
    on, s_on = executor(tmp_path / "c", k=D("1"))
    for s in (s_off, s_on):
        s.apply_fill(MARKET, "UP", D("2"), D("0.30"), basis=EX.OWN)
    capped = []
    for i in range(6):
        a = drive(off, side="UP", key="k%d" % i)
        b = drive(on, side="UP", key="k%d" % i)
        if b.get("refused_reason") == "RESIDUAL_ABOVE_CAP":
            capped.append(i)
            assert a.get("refused_reason") != "RESIDUAL_ABOVE_CAP"
            assert (a.get("ORDER") or {}).get("submitted") is True, (
                "the cap refused an action the frozen path did not send")
    assert capped, "the cap never bound; this test proved nothing"
