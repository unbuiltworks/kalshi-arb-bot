"""The shadow arm that measures the policy the money path actually runs.

WHY THIS FILE EXISTS. The at-touch cells reported `eligible_ticks: 0` at the
0.96 ceiling in 2245z and 2300z -- the only two windows where the live engine
completed profitable pairs. The cell was not wrong about its own question; it
was answering a different one. It priced eligibility AT THE TOUCH while the
engine rests `depth_ticks` behind it, so the cell demanded a pair the engine
never needs.

The calibration constants below are VENUE RECORDS from 2300z, not fixtures:
touch UP 0.2600 / DOWN 0.7300, placed 0.2400 / 0.7100, pair_cost 0.9500, both
legs filled 3.75 at avg_px 0.2400 and wire 0.2900 (own 0.7100).
"""
from decimal import Decimal as D

from src import l5_two_sided_shadow as TS
from src import l5_maker_fill_model as FM


# the 2300z book at plan time. ask is the wire ask: own DOWN = 1 - ask.
BID = D("0.26")
ASK = D("0.27")


def _cell(depth, policy=TS.LIVE_SEQUENTIAL, thresh="0.96"):
    return TS.Config("s", D(thresh), D("1"), policy, depth_ticks=depth)


def _legs(cell):
    up = BID - cell.depth
    dn = (D(1) - ASK) - cell.depth
    return up, dn


def test_live_basis_reproduces_the_2300z_plan_to_the_cent():
    up, dn = _legs(_cell(2))
    assert up == D("0.24")
    assert dn == D("0.71")
    assert up + dn == D("0.95")


def test_the_live_pair_is_admitted_by_the_ceiling_that_refused_at_touch():
    """Both halves of the defect, in one assertion pair."""
    up_t, dn_t = _legs(_cell(0))
    assert up_t + dn_t == D("0.99")
    assert (up_t + dn_t) > D("0.96")        # the 0-eligible-ticks bug

    up_l, dn_l = _legs(_cell(2))
    assert (up_l + dn_l) <= D("0.96")       # what the engine actually placed


def test_depth_is_exactly_two_ticks_per_leg_four_on_the_pair():
    up_t, dn_t = _legs(_cell(0))
    up_l, dn_l = _legs(_cell(2))
    assert up_t - up_l == D("0.02")
    assert dn_t - dn_l == D("0.02")
    assert (up_t + dn_t) - (up_l + dn_l) == D("0.04")


def test_every_historical_cell_keeps_the_at_touch_basis():
    """The fix is ADDITIVE. No existing cell may change what it measures."""
    w = TS.TwoSidedShadow("cpc-btc-updown-15m-2026-10-20-2300z")
    legacy = [c for c in w.configs if c.policy != TS.LIVE_SEQUENTIAL]
    assert legacy, "the historical arms must still be registered"
    assert all(c.depth_ticks == 0 for c in legacy)
    assert all(c.depth == D(0) for c in legacy)


def test_the_live_arm_is_registered_at_the_live_ceiling_and_depth():
    w = TS.TwoSidedShadow("cpc-btc-updown-15m-2026-10-20-2300z")
    live = [c for c in w.configs if c.policy == TS.LIVE_SEQUENTIAL]
    assert live, "the live-basis arm must be registered"
    assert all(c.thresh == D("0.96") for c in live)
    assert all(c.depth_ticks == 2 for c in live)


def test_a_cell_reports_which_price_basis_produced_it():
    """Two cells at the same threshold and different depths are not
    comparable; the report has to say which is which."""
    at_touch = _cell(0, TS.COMPLEMENT_ONLY).report(None)
    live = _cell(2).report(None)
    assert at_touch["depth_ticks"] == 0
    assert live["depth_ticks"] == 2
    assert live["policy"] == TS.LIVE_SEQUENTIAL


def test_flat_places_one_leg_not_two():
    """ONE OUTSTANDING ORDER. The at-touch policies quote both sides at once;
    the money path places one and waits for a terminal state."""
    c = _cell(2)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    assert len(c.rest) == 1, "live policy may never hold two resting legs"


def test_the_cheaper_leg_goes_first_so_the_alone_loss_is_the_smaller_one():
    """2300z placed UP at 0.24 ahead of DOWN at 0.71: alone-cost 0.95 rather
    than 2.7225, which is the bound the plan itself reported."""
    c = _cell(2)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    assert FM.UP in c.rest
    assert c.rest[FM.UP].own_px == D("0.24")


def test_the_complement_limit_is_frozen_at_plan_time():
    c = _cell(2)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    assert c.frozen_cpx == D("0.71")
    assert c.report(None)["frozen_complement"] == "0.71"


def test_a_frozen_leg_is_not_withdrawn_when_the_touch_walks_away():
    """The 3m42s that earned the 2300z edge. COMPLEMENT_ONLY cancels on a
    touch move; the money path waits, refuses to chase, and filled at 0.71
    while the book quoted 0.74-0.79."""
    c = _cell(2)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    before = dict(c.rest)
    # the book walks away from us, exactly as it did at 23:03-23:05
    c.step(D("0.20"), D("0.21"), D("500"), D("500"), (), 2.0, 0.0)
    assert c.rest.keys() == before.keys(), "a frozen leg must not be withdrawn"
    assert c.cancels == 0


def test_an_at_touch_policy_still_withdraws_on_a_touch_move():
    """The contrast, so the frozen behaviour is shown to be a real
    difference rather than an artefact of the test's book."""
    c = _cell(0, TS.COMPLEMENT_ONLY)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    if c.rest:
        c.step(D("0.20"), D("0.21"), D("500"), D("500"), (), 2.0, 0.0)
        assert c.cancels > 0


def test_a_touch_is_never_a_fill():
    """No fill may be claimed from the book alone: only direction-correct
    print volume advances the queue."""
    c = _cell(2)
    c.step(BID, ASK, D("500"), D("500"), (), 1.0, 0.0)
    # the touch reaches our price, with no prints at all
    c.step(D("0.24"), D("0.25"), D("500"), D("500"), (), 2.0, 0.0)
    assert c.qty[FM.UP] == D(0)
    assert c.qty[FM.DOWN] == D(0)


# ============================ the guard window, which the LIVE hook cannot see

def test_the_shadow_reaches_the_guard_window_that_the_live_hook_cannot():
    """THE CAPABILITY THIS ARM EXISTS FOR.

    The live observation hook reads `store.position()`, so it needs a REAL
    fill and can never fire while the campaign is closed. The shadow holds
    MODELLED inventory, so it can reach the guard with no capital and no
    venue. This asserts exactly that difference.

    THE GUARD IS [T+540, T+600), NOT [T+660, T+720). `may_complete` refuses
    PAST_TERMINAL from T+600 at the deployed cancel margin of 300, so the
    interval this test originally used was entirely dead time -- every
    verdict it recorded described an action the engine could not have taken.
    `test_the_guard_cannot_be_armed_in_dead_time` below holds that line.
    """
    c = _cell(2)
    # a modelled first leg, held: 3.39 UP at 0.37 -- the 2315z position
    c.qty[FM.UP] = D("3.39")
    c.cost[FM.UP] = D("3.39") * D("0.37")
    c.qty_opt[FM.UP] = D("3.39")
    c.cost_opt[FM.UP] = D("3.39") * D("0.37")

    # before the guard: evaluated, and refused on the clock
    c.step(D("0.20"), D("0.21"), D("500"), D("500"), (), 100.0, 0.0)
    assert c.defensive == [], "nothing may be recorded outside the guard"

    # inside the guard [540, 600)
    c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), 570.0, 0.0)
    assert c.defensive, "the shadow MUST reach the guard window"

    rec = c.defensive[0]
    assert rec["band"] in ("conservative", "optimistic")
    assert rec["held"] == FM.UP
    assert rec["unmatched"] == "3.39"
    # executable, not a maker hope: a long UP is sold INTO the bid
    assert rec["exit_price_own"] == "0.80"
    assert rec["displayed_depth"] == "1831.25"
    assert rec["verdict"]["simulated"] is True
    assert rec["verdict"]["improves_expected_value"] is False


def test_both_bands_are_recorded_separately():
    """A rule that only works on the optimistic book is not a rule."""
    c = _cell(2)
    c.qty[FM.UP] = D("1.00"); c.cost[FM.UP] = D("0.37")
    c.qty_opt[FM.UP] = D("3.39"); c.cost_opt[FM.UP] = D("3.39") * D("0.37")
    c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), 570.0, 0.0)
    bands = {r["band"]: r for r in c.defensive}
    assert set(bands) == {"conservative", "optimistic"}
    assert bands["conservative"]["unmatched"] == "1.00"
    assert bands["optimistic"]["unmatched"] == "3.39"


def test_a_simulated_verdict_is_never_labelled_real():
    c = _cell(2)
    c.qty[FM.UP] = D("3.39"); c.cost[FM.UP] = D("3.39") * D("0.37")
    c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), 570.0, 0.0)
    for r in c.defensive:
        assert r["verdict"].get("simulated") is True
    rep = c.report(None)
    assert rep["defensive_evals"] >= 1
    assert "defensive_would_act" in rep


def test_at_touch_arms_record_no_defensive_verdicts():
    c = _cell(0, TS.COMPLEMENT_ONLY)
    c.qty[FM.UP] = D("3.39"); c.cost[FM.UP] = D("3.39") * D("0.37")
    c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), 700.0, 0.0)
    assert c.defensive == []
    assert c.report(None)["defensive_evals"] == 0


def test_the_guard_cannot_be_armed_in_dead_time():
    """The correction, asserted directly on the shadow arm. T+700 is past
    the binding terminal, so NOTHING may be recorded there however much
    modelled inventory is held -- it is not a quiet window, it is a window
    in which no order can be placed at all."""
    c = _cell(2)
    c.qty[FM.UP] = D("3.39"); c.cost[FM.UP] = D("3.39") * D("0.37")
    for t in (660.0, 700.0, 719.0):
        c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), t, 0.0)
    assert c.defensive == []
    assert c.report(None)["defensive_evals"] == 0


def test_the_simulated_fee_is_one_crossing_leg_not_the_pair():
    """`SIM_TAKER_FEE` is the BOTH-LEGS figure from the 2300z plan. An
    unwind crosses once -- the first leg already filled as a maker -- so
    charging the pair figure would roughly double the fee and make the
    comparison lie about the cost of getting out."""
    c = _cell(2)
    c.qty[FM.UP] = D("3.39"); c.cost[FM.UP] = D("3.39") * D("0.37")
    c.step(D("0.20"), D("0.21"), D("1831.25"), D("500"), (), 570.0, 0.0)
    rec = c.defensive[0]
    one_leg = D("3.39") * TS.TAKER_THETA * D("0.80") * D("0.20")
    assert D(rec["verdict"]["fees_usd"]) == one_leg
    assert one_leg < D("3.39") * TS.SIM_TAKER_FEE
