"""P0: the driver is family-generic. P1: RETAIN holds, and the second level
is NOT authorized by the frozen surface.

RUN 35609233642 is the reason for the first half. Discovery, the family
selector, the envelope and the entry band all resolved BTC15m; the DRIVER
then refused the contract, because its constructor validated against the
module literal "GEMI-BTC05M" instead of the envelope it was handed.
"""
import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import live_probe_spec as SPEC
from src.gemini_execution import live_readiness as LR
from src.gemini_execution import ws_continuous as WC

BTC15M = "GEMI-BTC15M2609211430-UP"
BTC5M = "GEMI-BTC05M2609211420-UP"


# ============================================================ P0: the driver

def test_the_driver_accepts_a_btc15m_symbol_under_a_btc15m_envelope(tmp_path):
    """THE EXACT CRASH FROM RUN 35609233642."""
    d = WC.Driver("NO-AUTHORIZATION-IN-FORCE", BTC15M,
                  ledger_path=str(tmp_path / "l.jsonl"),
                  envelope=LE.LIVE_Q1_BTC15M)
    assert d._symbol == BTC15M


def test_the_family_comes_from_the_envelope_not_a_module_constant(tmp_path):
    """A BTC15m symbol under a BTC5m envelope is still refused -- nothing was
    loosened, the check simply reads the right source."""
    with pytest.raises(CP.ProbeStop) as e:
        WC.Driver("A", BTC15M, ledger_path=str(tmp_path / "l.jsonl"),
                  envelope=LE.LIVE_Q1)
    assert "GEMI-BTC05M" in str(e.value)

    with pytest.raises(CP.ProbeStop):
        WC.Driver("A", BTC5M, ledger_path=str(tmp_path / "l.jsonl"),
                  envelope=LE.LIVE_Q1_BTC15M)


def test_the_guard_is_bound_to_the_envelopes_family_not_btc5m(tmp_path):
    """The second pin, and the quieter one: WindowGuard already ACCEPTED a
    cohort_prefix and was never given one, so _cohort_prefix -> _leg_slots
    returned 1 on BTC15m and the two-level geometry was unreachable even with
    the constructor fixed."""
    d = WC.Driver("A", BTC15M, ledger_path=str(tmp_path / "l.jsonl"),
                  envelope=LE.LIVE_Q1_BTC15M)
    # before bind() the constructor's own family is the answer, not ""
    assert d._cohort_prefix() == "GEMI-BTC15M"
    assert WC._leg_slots(d._cohort_prefix()) == 2


def test_the_market_state_label_is_derived_not_the_literal_BTC5m():
    """state["cohort"] was the literal "BTC5m" on every BTC15m state the
    driver built. The engine carries it into the Surface record, so it
    decided nothing -- which is exactly why it corrupted evidence unnoticed."""
    assert CP.cohort_label(BTC15M) == "BTC15m"
    assert CP.cohort_label(BTC5M) == "BTC5m"
    assert CP.cohort_label("GEMI-NOTAFAMILY-UP") == "BTC5m"   # standing default


def test_the_envelope_hash_covers_every_declared_envelope_and_both_families():
    import json
    env = WC.envelope()
    assert env["cohort_prefixes"] == ",".join(CP.COHORT_PREFIXES)
    assert "GEMI-BTC15M" in env["cohort_prefixes"]
    names = set(json.loads(env["envelope_shas"]))
    assert LE.LIVE_Q1_BTC15M.name in names
    assert names == {e.name for e in LE.ALL}


# ================================== P1: RETAIN, and what authorizes a level

def _surface(fam, desired, working, now):
    return WC.reconcile_surface(desired, working, now=now, cohort_prefix=fam)


def _open(fam, now=1000.0):
    w = WC.WorkingOrders()
    desired = [{"leg": "YES", "price": "0.50", "qty": "1"},
               {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface(fam, desired, w, now)
    for a in p["add"]:
        r = a["rung"]
        w.add("coid-%s" % r["price"], leg=r["leg"], price=r["price"],
              qty=r["qty"], now=now)
    return w


@pytest.mark.parametrize("fam", ["GEMI-BTC15M", "GEMI-BTC05M"])
def test_a_price_move_under_30s_RETAINS_and_never_cancels(fam):
    """THE P1 REQUIREMENT: accepted resting order + model price changes
    before age 30s => RETAIN, not CANCEL/REPLACE."""
    now = 1000.0
    w = _open(fam, now)
    moved = [{"leg": "YES", "price": "0.51", "qty": "1"},
             {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface(fam, moved, w, now + 12.0)

    retained = [r for r in p["retain"] if r["order"]["leg"] == "YES"]
    assert len(retained) == 1, p
    assert retained[0]["age_s"] == pytest.approx(12.0)
    assert retained[0]["age_s"] < CP.STALE_ORDER_S
    assert not any(c["order"]["leg"] == "YES" for c in p["cancel"]), (
        "a moved desired price is NOT a reason to surrender queue position")


def test_on_BTC15m_the_lifecycle_ALSO_adds_a_second_level_beside_the_retain():
    """THE TRACE THE OWNER ASKED FOR, asserted rather than described.

    Nothing in the desired surface asked for two YES prices: rung_count is 1
    and tick_offsets is [0], and `moved` below carries exactly one YES rung.
    The second level comes from reconcile_surface finding a free leg slot.
    """
    now = 1000.0
    w = _open("GEMI-BTC15M", now)
    moved = [{"leg": "YES", "price": "0.51", "qty": "1"},
             {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface("GEMI-BTC15M", moved, w, now + 12.0)

    assert len([a for a in p["add"] if a["rung"]["leg"] == "YES"]) == 1
    resting_after = len(p["keep"]) + len(p["retain"]) + len(p["add"])
    assert resting_after == 3, p

    # BTC5m, same inputs, does NOT -- the slot is full, so the level waits
    w5 = _open("GEMI-BTC05M", now)
    p5 = _surface("GEMI-BTC05M", moved, w5, now + 12.0)
    assert not [a for a in p5["add"] if a["rung"]["leg"] == "YES"]
    assert len(p5["keep"]) + len(p5["retain"]) + len(p5["add"]) == 2


def test_capacity_is_not_authority():
    """(1) technically representable vs (2) what the frozen policy authorizes.

    The envelope must cover (2). EP006 proved L5 HAD two adjacent levels
    resting on BTC15m; it did not establish WHEN the second is created, and
    no frozen rule says. So (1) > (2) is an UNFROZEN EXECUTION RULE, not a
    reason to widen capital.
    """
    s15, s5 = SPEC.BTC15M_Q1, SPEC.BTC5M_Q1
    assert LR.policy_max_rungs(s15) == 2       # outcomes x rung_count
    assert LR.lifecycle_max_rungs(s15) == 4    # x leg_slots
    assert LR.policy_max_rungs(s5) == 2
    assert LR.lifecycle_max_rungs(s5) == 2     # BTC5m has no gap

    assert LE.LIVE_Q1_BTC15M.max_working_orders >= LR.policy_max_rungs(s15)


def test_the_gate_refuses_an_unfrozen_execution_rule():
    r = {"policy_max_rungs": 2, "lifecycle_max_rungs": 4,
         "max_working_orders": 2, "family": "btc15m",
         "symbol_family": "GEMI-BTC15M", "envelope_id": "X_BTC15M_V1",
         "action_size": "1", "frame_quantity": "1",
         "rest_floor_invoked": True, "retained_counts_as_risk": True,
         "mutations_sent": 0}
    got = LR.cross_field_invariants(r)
    assert got["ok"] is False
    assert any("UNFROZEN EXECUTION RULE" in v for v in got["violations"])
    # and it is NOT the "envelope too small" rule firing -- that one passes
    assert not any("envelope allows" in v for v in got["violations"])

    r["lifecycle_max_rungs"] = 2
    assert LR.cross_field_invariants(r)["ok"] is True


def test_the_two_order_envelope_refuses_the_third_resting_rung():
    """What would actually happen mid-window today: the envelope holds."""
    inv = CP.Inventory()
    resting = [{"leg": "NO", "price": "0.47", "qty": "1"},
               {"leg": "YES", "price": "0.50", "qty": "1"}]
    why = LE.would_breach("YES", "0.51", inv, qty="1", resting=resting,
                          env=LE.LIVE_Q1_BTC15M)
    assert why and "already resting" in why
    # the 4-order envelope would allow it -- which is precisely why it must
    # not be adopted merely because the lifecycle can reach for it
    assert LE.would_breach("YES", "0.51", inv, qty="1", resting=resting,
                           env=LE.LIVE_Q1_BTC15M_2LVL) is None
