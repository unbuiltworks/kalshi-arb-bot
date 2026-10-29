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
        n = note(rec)
        assert n["abs_prospective_before"] == "0", "the book was not flat"
        assert n["binds"] is False, "an OPEN from a flat book was capped"
        assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_a_repairing_leg_is_never_reachable_however_deep(tmp_path):
    ex, store = executor(tmp_path, k=D("0.0001"))
    store.apply_fill(MARKET, "UP", D("9.5"), D("0.30"), basis=EX.OWN)
    rec = drive(ex, side="DOWN", key="repair")
    assert rec["INVENTORY_CLASS"]["inventory_class"] == EX.REDUCE_RESIDUAL
    assert note(rec)["increases_abs_prospective"] is False
    assert note(rec)["binds"] is False
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_a_repairing_leg_on_a_short_residual_is_never_reachable(tmp_path):
    ex, store = executor(tmp_path, k=D("0.0001"))
    store.apply_fill(MARKET, "DOWN", D("9.5"), D("0.30"), basis=EX.OWN)
    assert store.residual(MARKET) == D("-9.5")
    rec = drive(ex, side="UP", key="repair_short")
    assert note(rec)["increases_abs_prospective"] is False
    assert note(rec)["binds"] is False
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
    assert n["basis"] == "PROSPECTIVE_NET"
    assert D(n["clip_used"]) == max(D("1"), q)
    assert D(n["cap_contracts"]) == D("2") * max(D("1"), q)
    assert n["units"] == "contracts"


def test_the_residual_it_compares_is_after_a_full_fill(tmp_path):
    q = qty_of(tmp_path)
    ex, store = executor(tmp_path / "b", k=D("2"))
    store.apply_fill(MARKET, "UP", D("3"), D("0.50"), basis=EX.OWN)
    n = note(drive(ex, side="UP", key="g"))
    assert D(n["residual_realized"]) == D("3")
    assert D(n["outstanding_residual"]) == 0
    assert D(n["prospective_after_if_filled"]) == D("3") + q
    assert D(n["abs_prospective_after"]) == D("3") + q


def test_a_short_residual_is_compared_on_magnitude(tmp_path):
    q = qty_of(tmp_path, side="DOWN")
    ex, store = executor(tmp_path / "c", k=D("2"))
    store.apply_fill(MARKET, "DOWN", D("3"), D("0.50"), basis=EX.OWN)
    n = note(drive(ex, side="DOWN", key="h"))
    assert D(n["prospective_after_if_filled"]) == -(D("3") + q)
    assert D(n["abs_prospective_after"]) == D("3") + q


# -------------------------------------------------------- at the boundary

def boundary_store(tmp_path, k, *, over: D):
    """A market where |residual after| lands exactly `over` past the cap.

    clip is pinned by a first fill larger than the order, so
    cap = k*clip is known before the candidate is priced.
    """
    q = qty_of(tmp_path)
    # CLIP CHOSEN SO THE RELATIVE RULE IS THE BINDING ONE. These tests
    # exercise the k*clip boundary, and since ABS_RESIDUAL_CAP_CONTRACTS (3)
    # now intersects it, a clip of 4 would make the ABSOLUTE rule decide
    # every case and the boundary under test would never be reached.
    # k=2 * 1.4 = 2.8, inside the absolute ceiling.
    clip = D("1.4")
    assert clip > q, (clip, q)
    cap = k * clip
    assert cap < EX.ABS_RESIDUAL_CAP_CONTRACTS, (
        "the relative rule must bind for these boundary tests")
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
    assert D(n["abs_prospective_after"]) == cap
    assert n["binds"] is False
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"


def test_one_hundredth_of_a_contract_past_the_cap_refuses(tmp_path):
    ex, store, cap, q = boundary_store(tmp_path, D("2"), over=D("0.01"))
    rec = drive(ex, side="UP", key="over")
    n = note(rec)
    assert D(n["abs_prospective_after"]) == cap + D("0.01")
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


@pytest.mark.multi_submission
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


# ===================================================================
# PROSPECTIVE NET (B'). The gate reads realized inventory PLUS every live
# order in this market as if it filled. On 1130z the realized-only gate let
# four DOWN orders through while inventory sat inside the cap; they filled
# within seven seconds and took |residual| to 3.24 against a cap of 1.80.
# ===================================================================

def rest_order(store, coid, side, qty, price="0.10"):
    """A live order at the venue: submitted, acked, unfilled."""
    store.record_submit(coid, {"market": MARKET, "side": side,
                               "qty": str(qty), "price": price})


@pytest.mark.multi_submission
def test_1_four_same_side_opens_cannot_each_claim_flat(tmp_path):
    """THE 1130z FAILURE, as a test. Realized residual is 0 throughout --
    nothing has filled -- so under a realized-only gate every one of these is
    FLAT_OPEN and exempt. Under prospective net they consume each other's
    capacity and the burst is stopped."""
    # a transport that ACKs and fills NOTHING: the orders rest, exactly as
    # the four DOWN orders on 1130z rested while the gate called them flat.
    ex, store = executor(tmp_path, k=D("2"),
                         transport=L.FakeTransport(fill_qty="0"))
    assert store.residual(MARKET) == 0
    outcomes = []
    for i in range(4):
        rec = drive(ex, side="DOWN", key="burst%d" % i)
        n = note(rec)
        outcomes.append(rec.get("refused_reason") == "RESIDUAL_ABOVE_CAP")
        # realized residual never moves: nothing fills in this test
        assert store.residual(MARKET) == 0
        assert n["residual_realized"] == "0"
        assert n["inventory_class_realized"] == EX.FLAT_OPEN
    assert any(outcomes), (
        "four same-side opens all passed while realized residual was 0 -- "
        "each re-authorised itself as FLAT_OPEN, which is the 1130z defect")


def test_1b_a_single_open_from_a_truly_flat_book_still_passes(tmp_path):
    """The exemption must survive where it is legitimate."""
    ex, store = executor(tmp_path, k=D("2"))
    rec = drive(ex, side="UP", key="first")
    n = note(rec)
    assert n["abs_prospective_before"] == "0"
    assert n["binds"] is False
    assert rec.get("refused_reason") != "RESIDUAL_ABOVE_CAP"


def test_2_outstanding_same_side_orders_consume_capacity(tmp_path):
    """No fills at all. Capacity is consumed purely by resting orders."""
    ex, store = executor(tmp_path, k=D("2"),
                         transport=L.FakeTransport(fill_qty="0"))
    q = qty_of(tmp_path)
    rest_order(store, "o1", "UP", q)
    rest_order(store, "o2", "UP", q)
    rest_order(store, "o3", "UP", q)
    assert store.residual(MARKET) == 0
    assert store.outstanding_residual(MARKET) == q * 3
    rec = drive(ex, side="UP", key="more")
    n = note(rec)
    assert n["residual_realized"] == "0"
    assert D(n["outstanding_residual"]) == q * 3
    assert D(n["prospective_after_if_filled"]) == q * 4
    assert n["increases_abs_prospective"] is True
    assert rec["refused_reason"] == "RESIDUAL_ABOVE_CAP"


def test_3_opposite_side_repair_is_admissible_at_any_depth(tmp_path):
    """Repair asymmetry. Realized AND outstanding both deep on one side."""
    ex, store = executor(tmp_path, k=D("0.0001"),
                         transport=L.FakeTransport(fill_qty="0"))
    store.apply_fill(MARKET, "UP", D("3"), D("0.10"), basis=EX.OWN)
    rest_order(store, "o1", "UP", D("1"), price="0.10")
    rest_order(store, "o2", "UP", D("1"), price="0.10")
    assert store.residual(MARKET) == D("3")
    assert store.outstanding_residual(MARKET) == D("2")
    rec = drive(ex, side="DOWN", key="repair")
    n = note(rec)
    assert D(n["prospective_before"]) == D("5")
    assert n["increases_abs_prospective"] is False
    assert n["binds"] is False
    assert rec.get("refused_reason") != "RESIDUAL_ABOVE_CAP"


def test_3b_repair_is_admissible_on_a_short_book_too(tmp_path):
    ex, store = executor(tmp_path, k=D("0.0001"),
                         transport=L.FakeTransport(fill_qty="0"))
    store.apply_fill(MARKET, "DOWN", D("3"), D("0.10"), basis=EX.OWN)
    rest_order(store, "o1", "DOWN", D("1"), price="0.10")
    rec = drive(ex, side="UP", key="repair_short")
    assert note(rec)["increases_abs_prospective"] is False
    assert rec.get("refused_reason") != "RESIDUAL_ABOVE_CAP"


def test_4_capacity_reopens_once_repair_shrinks_prospective(tmp_path):
    """The cap is a bound, not a latch -- now measured prospectively."""
    ex, store = executor(tmp_path, k=D("2"),
                         transport=L.FakeTransport(fill_qty="0"))
    q = qty_of(tmp_path)
    for i in range(3):
        rest_order(store, "o%d" % i, "UP", q)
    blocked = drive(ex, side="UP", key="blocked")
    assert blocked["refused_reason"] == "RESIDUAL_ABOVE_CAP"
    # DOWN orders rest and shrink the prospective book back to flat
    for i in range(3):
        rest_order(store, "d%d" % i, "DOWN", q)
    assert store.outstanding_residual(MARKET) == 0
    reopened = drive(ex, side="UP", key="reopened")
    assert D(note(reopened)["abs_prospective_before"]) == 0
    assert reopened.get("refused_reason") != "RESIDUAL_ABOVE_CAP", (
        "capacity did not reopen after the prospective book returned to flat")


def test_5_equality_at_the_cap_passes_prospectively(tmp_path):
    ex, store = executor(tmp_path, k=D("2"),
                         transport=L.FakeTransport(fill_qty="0"))
    q = qty_of(tmp_path)
    # one resting UP order of exactly q; cap = 2*max(0, q) = 2q; after = 2q
    rest_order(store, "o1", "UP", q)
    rec = drive(ex, side="UP", key="equal")
    n = note(rec)
    assert D(n["abs_prospective_after"]) == D(n["cap_contracts"]) == q * 2
    assert n["binds"] is False
    assert rec.get("refused_reason") != "RESIDUAL_ABOVE_CAP"


def test_6_the_rule_cannot_loosen_any_existing_gate(tmp_path):
    """It sits behind every frozen check, so an envelope refusal keeps its own
    reason and the cap is not even evaluated. A gate it could bypass would
    show up here as a RESIDUAL_CAP note on an already-refused action."""
    ex, store = executor(tmp_path, k=D("2"))
    store.apply_fill(MARKET, "UP", D("9"), D("0.90"), basis=EX.OWN)   # $8.10 worst case
    rec = drive(ex, side="DOWN", key="envelope")
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    assert rec["refused_reason"] != "RESIDUAL_ABOVE_CAP"
    assert note(rec) is None


def test_6b_turning_the_cap_on_never_sends_more_than_leaving_it_off(tmp_path):
    """THE REAL "cannot loosen" PROPERTY, and stronger than one contrived
    envelope case: prospective accounting CAN admit an action that
    realized-only would have refused (a resting repair nets off), so the
    guarantee worth proving is against NATIVE. Drive the same sequence with
    the cap off and on; what the capped executor sends must be a subset of
    what the uncapped one sends, action for action."""
    off_tp, on_tp = L.FakeTransport(fill_qty="0"), L.FakeTransport(fill_qty="0")
    off, s_off = executor(tmp_path / "off", k=None, transport=off_tp)
    on, s_on = executor(tmp_path / "on", k=D("2"), transport=on_tp)
    for s in (s_off, s_on):
        s.apply_fill(MARKET, "UP", D("1"), D("0.10"), basis=EX.OWN)
    for i in range(6):
        drive(off, side="UP", key="s%d" % i)
        drive(on, side="UP", key="s%d" % i)
    sent_off = {s["client_order_id"] for s in off_tp.sent}
    sent_on = {s["client_order_id"] for s in on_tp.sent}
    assert sent_on <= sent_off, (
        "the cap caused an order the uncapped path never sent: %s"
        % (sent_on - sent_off))
    assert len(sent_on) < len(sent_off), "the cap never bound; test proved nothing"


def test_6c_the_dollar_gate_already_counted_outstanding_orders(tmp_path):
    """Context for the change, asserted rather than assumed: the $5
    worst-case gate was ALREADY prospective -- it prices live orders as if
    they fill. The residual cap was the only thing reading realized-only,
    which is why it was the thing that overshot."""
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    store.apply_fill(MARKET, "UP", D("6"), D("0.90"), basis=EX.OWN)
    bare = store.risk_exposure_usd(MARKET)
    assert bare == D("5.40")
    rest_order(store, "hedge", "DOWN", D("8"), price="0.10")
    hedged = store.risk_exposure_usd(MARKET, extra_side="UP",
                                     extra_qty=D("0.45"), extra_price=D("0.60"))
    assert hedged < bare, "outstanding orders are not priced into the risk gate"


# ------------------------- the approved absolute ceiling intersects k*clip

def test_a_large_clip_is_capped_by_the_approved_absolute_three(tmp_path):
    """RESIDUAL_CAP_k2 scales with the clip, so at clip 4 it would permit 8
    contracts -- well past the 3 the owner approved. The absolute ceiling
    now decides instead."""
    q = qty_of(tmp_path)
    ex, store = executor(tmp_path / "abs", k=D("2"))
    store.apply_fill(MARKET, "UP", D("4"), D("0.20"), basis=EX.OWN)
    assert store.max_clip(MARKET) == D("4")
    rec = drive(ex, side="UP", key="abs")
    n = note(rec)
    assert D(n["cap_contracts"]) == EX.ABS_RESIDUAL_CAP_CONTRACTS
    assert D(n["relative_cap_contracts"]) == D("8.0")
    assert n["which_binds"] == "ABSOLUTE"
    # 4 contracts held already exceeds 3, and this order increases it
    assert n["binds"] is True


def test_the_relative_rule_still_decides_below_the_absolute(tmp_path):
    q = qty_of(tmp_path)
    ex, store = executor(tmp_path / "rel", k=D("2"))
    store.apply_fill(MARKET, "UP", D("1.0"), D("0.20"), basis=EX.OWN)
    rec = drive(ex, side="UP", key="rel")
    n = note(rec)
    assert n["which_binds"] == "RELATIVE"
    assert D(n["cap_contracts"]) == D("2.0")


def test_the_effective_cap_never_exceeds_three_at_any_clip(tmp_path):
    """Whatever the clip, the cap the gate actually applies is <= 3. A row
    that refuses before the cap note is reached emits no note, and that is
    not a violation -- it simply did not get that far."""
    seen = 0
    for clip in (D("1"), D("1.5"), D("2"), D("4"), D("20")):
        ex, store = executor(tmp_path / ("c%s" % clip), k=D("2"))
        store.apply_fill(MARKET, "UP", clip, D("0.20"), basis=EX.OWN)
        rec = drive(ex, side="UP", key="c%s" % clip)
        n = note(rec)
        if n is None or "cap_contracts" not in n:
            continue
        seen += 1
        assert D(n["cap_contracts"]) <= EX.ABS_RESIDUAL_CAP_CONTRACTS, clip
    assert seen >= 3, "too few rows reached the cap note to be meaningful"
