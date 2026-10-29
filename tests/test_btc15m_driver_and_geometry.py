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


@pytest.mark.parametrize("fam", ["GEMI-BTC15M", "GEMI-BTC05M"])
def test_the_moved_price_is_HELD_not_added_beside_the_retained_order(fam):
    """THE PINNED TRACE. Two resting, zero mutations, on BOTH families.

    This is what the docstring of `reconcile_surface` claimed all along --
    "its slot is then considered occupied, so the moved quote does NOT also
    go on as a second order" -- and what the code did only on BTC5m. On
    BTC15m `_leg_slots` returned 2, the RETAIN took one slot, and the moved
    price went on beside it: three resting after one price move, four if both
    legs moved. Capacity had become authority.

        t=0    YES .50 + NO .47                      =>  2 resting
        t=12   desired YES .51, age 12s < 30s floor
               RETAIN YES .50, KEEP NO .47, HOLD YES .51
                                                     =>  2 resting, 0 mutations
    """
    now = 1000.0
    w = _open(fam, now)
    moved = [{"leg": "YES", "price": "0.51", "qty": "1"},
             {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface(fam, moved, w, now + 12.0)

    assert [r["order"]["price"] for r in p["retain"]] == ["0.50"]
    assert [k["order"]["price"] for k in p["keep"]] == ["0.47"]
    assert [h["rung"]["price"] for h in p["hold"]] == ["0.51"]
    assert p["hold"][0]["held_by"] == "LIFECYCLE"
    assert p["hold"][0]["holder"]["price"] == "0.50"

    assert p["add"] == [], "no second price level on a leg the policy caps at 1"
    assert p["cancel"] == [], "and the retained order is not surrendered"
    assert p["mutations"] == 0
    assert len(p["keep"]) + len(p["retain"]) + len(p["add"]) == 2


@pytest.mark.parametrize("fam", ["GEMI-BTC15M", "GEMI-BTC05M"])
def test_past_the_floor_it_is_the_ORDINARY_cancel_replace(fam):
    """The replacement is not abandoned -- it waits. Once the retained order
    passes the floor the existing lifecycle cancels it and adds the new one
    on the SAME turn, so the working set is never temporarily wider than the
    policy authorises."""
    now = 1000.0
    w = _open(fam, now)
    moved = [{"leg": "YES", "price": "0.51", "qty": "1"},
             {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface(fam, moved, w, now + CP.STALE_ORDER_S + 1.0)

    assert [a["rung"]["price"] for a in p["add"]] == ["0.51"]
    assert [c["order"]["price"] for c in p["cancel"]] == ["0.50"]
    assert p["retain"] == [] and p["hold"] == []
    assert len(p["keep"]) + len(p["retain"]) + len(p["add"]) == 2


def test_the_leg_cap_is_the_TIGHTER_of_capacity_and_authority():
    """`_leg_slots` still bounds capacity and is still consulted -- it simply
    no longer grants anything the desired surface did not ask for."""
    now = 1000.0
    w = _open("GEMI-BTC15M", now)
    moved = [{"leg": "YES", "price": "0.51", "qty": "1"},
             {"leg": "NO", "price": "0.47", "qty": "1"}]
    p = _surface("GEMI-BTC15M", moved, w, now + 12.0)
    assert p["leg_slots"] == 2           # capacity, unchanged
    assert p["leg_caps"] == {"YES": 1, "NO": 1}   # authority, and it binds

    # a surface that genuinely asks for two rungs on a leg may have two --
    # the cap is read off the request, so a future frozen rung_count of 2
    # needs no change here. It still cannot exceed capacity.
    two = [{"leg": "YES", "price": "0.51", "qty": "1"},
           {"leg": "YES", "price": "0.52", "qty": "1"}]
    p2 = WC.reconcile_surface(two, WC.WorkingOrders(), now=now,
                              cohort_prefix="GEMI-BTC15M")
    assert p2["leg_caps"]["YES"] == 2
    assert len(p2["add"]) == 2
    p5 = WC.reconcile_surface(two, WC.WorkingOrders(), now=now,
                              cohort_prefix="GEMI-BTC05M")
    assert p5["leg_caps"]["YES"] == 1, "BTC5m capacity still binds at one"
    assert len(p5["add"]) == 1


def test_capacity_is_not_authority():
    """(1) what the lifecycle rests vs (2) what the frozen policy authorizes.

    They now agree, and (1) is MEASURED by driving the production reconciler
    rather than computed from a formula -- so re-widening the reconciler puts
    them back out of step on its own.
    """
    s15, s5 = SPEC.BTC15M_Q1, SPEC.BTC5M_Q1
    assert LR.policy_max_rungs(s15) == 2
    assert LR.lifecycle_max_rungs(s15) == 2
    assert LR.policy_max_rungs(s5) == 2
    assert LR.lifecycle_max_rungs(s5) == 2

    for s in (s15, s5):
        assert LR.lifecycle_max_rungs(s) <= LR.policy_max_rungs(s)
        assert LR.policy_max_rungs(s) <= s.envelope_obj.max_working_orders


def test_the_gate_would_still_catch_a_re_widened_lifecycle():
    """The rule that produced the blocker is intact, not deleted."""
    r = {"policy_max_rungs": 2, "lifecycle_max_rungs": 4,
         "max_working_orders": 2, "family": "btc15m",
         "symbol_family": "GEMI-BTC15M", "envelope_id": "X_BTC15M_V1",
         "action_size": "1", "frame_quantity": "1",
         "rest_floor_invoked": True, "retained_counts_as_risk": True,
         "mutations_sent": 0}
    got = LR.cross_field_invariants(r)
    assert got["ok"] is False
    assert any("UNFROZEN EXECUTION RULE" in v for v in got["violations"])
    r["lifecycle_max_rungs"] = 2
    assert LR.cross_field_invariants(r)["ok"] is True


def test_the_two_order_envelope_is_never_reached_for_a_third_rung():
    """BELT AND BRACES, IN THIS ORDER: the lifecycle no longer ASKS for a
    third rung, and if anything ever did, the envelope still refuses it."""
    inv = CP.Inventory()
    resting = [{"leg": "NO", "price": "0.47", "qty": "1"},
               {"leg": "YES", "price": "0.50", "qty": "1"}]
    why = LE.would_breach("YES", "0.51", inv, qty="1", resting=resting,
                          env=LE.LIVE_Q1_BTC15M)
    assert why and "already resting" in why


def test_the_four_order_envelope_is_declared_but_NOT_the_candidate():
    """It exists as a capability and is not what btc15m_q1 names. Widening
    to it would have authorised an execution rule nobody wrote, by way of a
    capital limit."""
    assert SPEC.BTC15M_Q1.envelope_obj is LE.LIVE_Q1_BTC15M
    assert SPEC.BTC15M_Q1.envelope_obj is not LE.LIVE_Q1_BTC15M_2LVL
    assert LE.LIVE_Q1_BTC15M.max_working_orders == 2
    assert LE.LIVE_Q1_BTC15M.max_committed_usd == CP.D("2.50")
