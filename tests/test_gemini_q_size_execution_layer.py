"""q is an EXECUTION-LAYER parameter, not a behavioural one.

THE GAP THIS CLOSES. Run 35573430715 was authorized as a q=1 probe, ran with
LIVE_Q1 correctly in force, and placed orders of 0.01 -- because the envelope
was a CAP and `continuous_probe.ACTION_SIZE` was the only thing that actually
SET a size. The authorization said one thing and the wire said another, and
nothing in the tree noticed.

WHAT MAY AND MAY NOT MOVE. The policy chooses side, price, timing, rung
ordering, OPEN/EXTEND/COMPLETE classification and same-side-run behaviour.
None of that may change.

AND THE ENGINE IS NOT SIZE-BLIND, which I assumed at first and proof 1
disproved. `probe_params` overrides the lane's base_parent_qty,
secondary_rung_qty and max_residual, and `desired_surface` reads them -- so
leaving those at the 0.01 scale while sending q=1 SUPPRESSED AN EXTEND that
shadow emits. The real invariant is that size enters only through those three
proportional overrides plus the stamped qty, so every ratio the policy
reasons about is scale-INVARIANT and the surface comes back identical.

The seven proofs below are the owner's, in order.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE


def _state(**over):
    s = {"market_key": "GEMI-BTC05M2609181055-UP", "state_id": "s1",
         "t_utc": 1000.0, "tte_s": 200.0, "cohort": "BTC5m",
         "state_age_s": 1.0, "inventory_known": True,
         "outcomes": {"Up": {"bid": "0.64", "ask": "0.93", "tick": "0.01"},
                      "Down": {"bid": "0.36", "ask": "0.65", "tick": "0.01"}},
         "inventory": {}}
    s.update(over)
    return s


def _behaviour(actions):
    """Everything about an action EXCEPT its quantity."""
    return [{k: v for k, v in a.items() if k != "qty"} for a in actions]


# ============================================================ PROOF 1
# identical market state -> same action/side/price, only quantity changes


@pytest.mark.parametrize("inv_fills", [
    [],                                        # flat: OPEN both sides
    [("YES", "0.01", "0.64")],                 # leaning: EXTEND + COMPLETE
    [("YES", "0.01", "0.64"), ("YES", "0.01", "0.63")],   # a run
    [("NO", "0.01", "0.36")],                  # the mirror
])
def test_1_same_state_same_decision_only_quantity_differs(inv_fills):
    """The whole claim, driven on real states rather than asserted."""
    def _inv(scale):
        inv = CP.Inventory()
        for leg, q, px in inv_fills:
            inv.apply_fill(leg, str(D(q) * scale), px, source="t")
        return inv

    shadow = CP.plan(_state(), _inv(D("1")))
    live = CP.plan(_state(), _inv(D("100")), action_size=D("1"))

    assert _behaviour(shadow["actions"]) == _behaviour(live["actions"])
    assert _behaviour(shadow["holds"]) == _behaviour(live["holds"])
    assert shadow["stop"] == live["stop"]
    # and the ONLY difference is the quantity
    assert [a["qty"] for a in shadow["actions"]] == ["0.01"] * len(
        shadow["actions"])
    assert [a["qty"] for a in live["actions"]] == ["1"] * len(live["actions"])


def test_1b_classification_and_run_behaviour_are_untouched():
    """OPEN/EXTEND/COMPLETE and same-side-run come from inventory alone, and
    `classify` is not handed a size at all -- so it CANNOT depend on one."""
    import inspect
    assert "action_size" not in inspect.signature(CP.classify).parameters
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.64", source="t")
    assert CP.classify("Up", inv) == CP.EXTEND
    assert CP.classify("Down", inv) == CP.COMPLETE


def test_1c_the_engine_sees_a_size_and_that_is_why_it_must_scale():
    """MY FIRST VERSION OF THIS TEST ASSERTED THE OPPOSITE AND WAS WRONG.

    `probe_params` overrides the lane's base_parent_qty, secondary_rung_qty
    and max_residual, and `desired_surface` reads them -- so the engine very
    much does see a size. Proof 1 caught it: at q=1 with 0.01-scaled params
    the oscillator suppressed an EXTEND that shadow emits.

    So the invariant is not "the engine is size-blind". It is that size
    enters ONLY through these three proportional overrides, which means the
    picture the engine reasons about is scale-INVARIANT.
    """
    sp1 = CP.probe_params(action_size=D("0.01"))
    sp100 = CP.probe_params(action_size=D("1"))
    scaled = {"base_parent_qty", "secondary_rung_qty", "max_residual"}
    for f in sp1.params:
        a, b = sp1.params[f].value, sp100.params[f].value
        if f in scaled:
            assert D(str(b)) == D(str(a)) * 100, f
        else:
            assert a == b, ("%s changed with q; only the three size-scaled "
                            "overrides may" % f)


# ============================================================ PROOF 2
# the actual order frame constructed by the live path contains quantity 1


def test_2_the_wire_frame_carries_quantity_1():
    """Not the plan, not the intent -- the rendered frame itself."""
    p = CP.plan(_state(), CP.Inventory(), action_size=D("1"))
    frames = CP.frames_for(p, "GEMI-BTC05M2609181055-UP")
    assert frames, "no frame was produced"
    for f in frames:
        params = f["frame"]["params"]
        got = str(params.get("quantity", params.get("amount")))
        assert D(got) == D("1"), f["frame"]


def test_2b_the_shadow_frame_still_carries_0_01():
    p = CP.plan(_state(), CP.Inventory())
    for f in CP.frames_for(p, "GEMI-BTC05M2609181055-UP"):
        params = f["frame"]["params"]
        assert D(str(params.get("quantity",
                                params.get("amount")))) == D("0.01")


# ============================================================ PROOF 3
# every dollar-based envelope calculation uses that actual quantity


def test_3_dollar_bounds_are_computed_from_the_sent_quantity():
    """Arithmetic on the scenario, not a re-use of envelope internals: one
    contract at $0.40 commits $0.40, and a hundredth of one commits $0.004."""
    inv = CP.Inventory()
    assert LE.would_breach("YES", "0.40", inv, qty=D("1"),
                           env=LE.LIVE_Q1) is None       # $0.40 <= $2.50
    # the SAME price at q=1 against the shadow envelope is refused, because
    # there the dollar cap is sized for hundredths
    assert LE.would_breach("YES", "0.40", inv, qty=D("1"),
                           env=LE.SHADOW_Q001) is not None


def test_3b_a_price_that_would_exceed_2_50_at_q1_is_refused():
    """$2.50 is the binding number, and it binds on the ACTUAL qty."""
    inv = CP.Inventory()
    # one contract at 0.99 is $0.99 -- fine
    assert LE.would_breach("YES", "0.99", inv, qty=D("1"),
                           env=LE.LIVE_Q1) is None
    # three contracts at 0.99 is $2.97 -- over, and the qty is why
    assert LE.would_breach("YES", "0.99", inv, qty=D("3"),
                           env=LE.LIVE_Q1) is not None


def test_3c_the_probes_own_bounds_scale_so_they_do_not_bind_first():
    """The inner bounds are denominated in 0.01 units. Unscaled, a q=1 action
    would breach them on the FIRST rung and suppress quotes shadow allows --
    a behavioural change. Scaled, they sit at the same number of RUNGS."""
    b1 = CP.scaled_bounds(D("0.01"))
    b100 = CP.scaled_bounds(D("1"))
    for key in ("max_residual", "max_imbalance", "max_yes", "max_no",
                "max_total_spend"):
        assert b100[key] == b1[key] * 100, key
    # and the AUTHORIZED dollar cap is still far tighter than the scaled
    # inner one, so the money is bounded by LIVE_Q1 and not by this
    assert LE.LIVE_Q1.max_total_spend_usd < b100["max_total_spend"]


# ============================================================ PROOF 4
# partial fills at q=1 update committed cash, residual and outstanding state


def test_4_a_partial_fill_at_q1_updates_inventory_and_residual():
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.4", "0.50", source="venue_ws")   # partial of 1
    assert inv.yes_qty == D("0.4")
    assert inv.residual() == D("0.4")
    assert inv.spend() == D("0.2")                            # 0.4 * 0.50
    inv.apply_fill("YES", "0.6", "0.50", source="venue_ws")   # the remainder
    assert inv.yes_qty == D("1")
    assert inv.spend() == D("0.5")


def test_4b_the_outstanding_ledger_tracks_a_q1_partial_and_its_remainder(
        tmp_path):
    from src.gemini_execution import outstanding_ledger as OL
    led = OL.OutstandingLedger(str(tmp_path / "o.jsonl"),
                               auth_id="A", symbol="GEMI-X")
    led.intent("c1", leg="YES", price="0.50", qty="1")
    led.accepted("c1", venue_order_id="v1")
    led.fill("c1", "0.4", partial=True)
    committed = led.committed_usd()
    assert committed["readable"], committed
    # 0.6 of a contract at $0.50 is still working -> $0.30 still committed
    assert D(str(committed["usd"])) == D("0.30")
    led.fill("c1", "0.6", partial=False)
    # COMMITTED CASH IS RELEASED by the fill completing...
    assert D(str(led.committed_usd()["usd"])) == D("0")
    # ...but the ROW still blocks a restart, and that is the ledger's
    # governing rule, not an oversight: only an authoritative venue read may
    # declare an order's fate. My first version of this test asserted the
    # opposite and was wrong about the design.
    assert led.blocks_restart()


# ============================================================ PROOF 5
# the authorized bounds still fail closed at q=1


def test_5_committed_cash_2_50_still_fails_closed():
    inv = CP.Inventory()
    inv.apply_fill("YES", "1", "0.90", source="t")            # $0.90 spent
    inv.apply_fill("NO", "1", "0.90", source="t")             # $1.80 spent
    # one more contract at 0.90 would be $2.70 > $2.50
    assert LE.would_breach("YES", "0.90", inv, qty=D("1"),
                           env=LE.LIVE_Q1) is not None


def test_5b_residual_2_00_still_fails_closed():
    inv = CP.Inventory()
    inv.apply_fill("YES", "2", "0.10", source="t")   # 2 contracts residual
    why = LE.would_breach("YES", "0.10", inv, qty=D("1"), env=LE.LIVE_Q1)
    assert why is not None


def test_5c_two_working_orders_and_one_market_still_bind():
    assert LE.LIVE_Q1.max_working_orders == 2
    assert LE.LIVE_Q1.max_concurrent_markets == 1
    inv = CP.Inventory()
    resting = ({"leg": "YES", "price": "0.10", "qty": "1"},
               {"leg": "NO", "price": "0.10", "qty": "1"})
    why = LE.would_breach("YES", "0.10", inv, qty=D("1"), resting=resting,
                          env=LE.LIVE_Q1)
    assert why is not None, "a third working order must be refused"


def test_5d_an_action_larger_than_the_authorized_size_is_refused():
    inv = CP.Inventory()
    assert LE.would_breach("YES", "0.10", inv, qty=D("2"),
                           env=LE.LIVE_Q1) is not None


# ============================================================ PROOF 6
# no fallback can silently revert an authorized LIVE_Q1 invocation to 0.01


def test_6_a_supplied_size_is_refused_never_defaulted():
    """`action_size or ACTION_SIZE` would turn each of these into 0.01
    silently -- which is the exact shape of the bug being closed."""
    for bad in (0, "0", "", "abc", D("-1"), []):
        with pytest.raises(CP.ActionSizeRefused):
            CP.resolve_action_size(bad)
    # and ONLY absence defaults
    assert CP.resolve_action_size(None) == CP.ACTION_SIZE
    assert CP.resolve_action_size(D("1")) == D("1")


def test_6b_the_driver_refuses_a_rung_that_came_back_downsized():
    """LE.would_breach ALLOWS a rung smaller than action_size, because a
    partial remainder legitimately is. So it cannot catch a freshly decided
    rung that reverted to 0.01 -- the driver checks the decision itself."""
    import inspect
    from src.gemini_execution import ws_continuous as WC
    src = inspect.getsource(WC.Driver.decide)
    assert "action_size=self.env.action_size" in src
    i = src.index("action_size=self.env.action_size")
    j = src.index('str(a.get("qty")) != str(self.env.action_size)')
    assert i < j, "the size must be passed before the decision is checked"
    assert "silently downsized" in src

    # and a smaller rung really is permitted by the envelope alone, which is
    # why the driver-side check is not redundant
    assert LE.would_breach("YES", "0.10", CP.Inventory(), qty=D("0.01"),
                           env=LE.LIVE_Q1) is None


def test_6c_the_default_everywhere_else_is_still_the_shadow_size():
    assert LE.DEFAULT is LE.SHADOW_Q001
    assert LE.DEFAULT.action_size == D("0.01")
    assert CP.ACTION_SIZE == D("0.01")
    assert CP.plan(_state(), CP.Inventory())["actions"][0]["qty"] == "0.01"
    import inspect
    from src.gemini_execution import ws_continuous as WC
    assert (inspect.signature(WC.Driver.__init__)
            .parameters["envelope"].default is LE.DEFAULT)


# ============================================================ PROOF 7
# repository-wide: no OTHER independent size setter between decision and
# transport


def test_7_only_one_thing_sets_a_size():
    """A second size setter is how the authorization and the wire disagree.
    Asserted over the source of every module on the decision->transport path.
    """
    import inspect
    from src.gemini_execution import payload as P
    from src.gemini_execution import ws_continuous as WC

    # the plan stamps q, and it is the resolved one
    plan_src = inspect.getsource(CP.plan)
    assert '"qty": str(q)' in plan_src
    assert '"qty": str(ACTION_SIZE)' not in plan_src

    # the driver forwards the rung's own qty; it does not re-derive one
    place_src = inspect.getsource(WC.Driver.place)
    assert 'qty = str(rung["leg"]), str(rung["price"]), str(rung["qty"])' \
        in place_src.replace("leg, price, ", "")
    assert "quantity=qty" in place_src
    assert "ACTION_SIZE" not in place_src

    # the frame builder uses the action's qty
    assert 'quantity=str(a["qty"])' in inspect.getsource(CP.frames_for)

    # and the payload builder takes the quantity it is handed
    assert "quantity" in inspect.signature(P.build_ws_order).parameters


def test_7b_no_module_on_the_live_path_hardcodes_a_quantity():
    """Sweeps the FILES, so a new size setter added later is caught here."""
    import pathlib
    import re
    root = pathlib.Path("src/gemini_execution")
    # ACTION_SIZE may be DEFINED once and read only where a default belongs
    definitions = []
    for path in sorted(root.glob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r"^\s*(ACTION_SIZE|action_size)\s*=\s*D\(", line):
                definitions.append("%s:%d" % (path.name, n))
    # COUNTED PER FILE, NOT PINNED TO LINE NUMBERS. The first version listed
    # "live_envelope.py:171" and broke the moment a line was inserted above
    # it -- a brittle assertion about layout rather than about size setters.
    # Counting keeps the guard exact: a NEW setter anywhere still fails here.
    from collections import Counter
    by_file = Counter(d.split(":")[0] for d in definitions)
    assert dict(by_file) == {
        "continuous_probe.py": 1,   # ACTION_SIZE, the shadow constant
        "live_envelope.py": 3,      # SHADOW_Q001, LIVE_Q1, LIVE_Q1_BTC15M_2LVL
    }, definitions
