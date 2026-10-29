"""Two simultaneous maker orders for ONE bounded pair. Implemented, INACTIVE.

WHY. With one live-order slot the complement can never rest beside the first
leg: it is refused MAX_LIVE_ORDERS -- 331 times across the 8-window campaign
of 2026-10-23 -- and can only be placed once the first leg leaves the book,
which is exactly the interval in which the book walks away and the pair
breaches the 0.96 cap. Three of eight windows never completed, costing
-$0.6684 against +$0.8020 from the five that did.

WHAT THIS IS NOT. It is not `max_live_orders = 2`, which would permit two
independent FIRST legs sharing one cash envelope -- the 1215z failure mode.
The second slot is granted only to an order demonstrably on the other side
of the same round in the same market as the one order already resting.

OFF BY DEFAULT, by owner instruction: the mechanism is approved, activation
is withheld until the lifecycle and risk accounting are verified. With
L5_TS_TWO_ORDER_PAIR unset, every assertion here about current behaviour
holds unchanged.

Nothing here submits an order, cancels one, uses a credential or opens a
socket.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS


MKT = "cpc-btc-updown-15m-2026-10-23-2145z"

#: the campaign's eight planned pairs: (window, UP own, DOWN own, qty)
PLANS = [
    ("2145z", D("0.60"), D("0.35"), D("1.03")),
    ("2200z", D("0.74"), D("0.21"), D("0.94")),
    ("2215z", D("0.62"), D("0.33"), D("1.01")),
    ("2230z", D("0.12"), D("0.83"), D("1.47")),
    ("2245z", D("0.50"), D("0.45"), D("1.10")),
    ("2300z", D("0.59"), D("0.36"), D("1.03")),
    ("2315z", D("0.57"), D("0.38"), D("1.05")),
    ("2330z", D("0.21"), D("0.74"), D("1.36")),
]

ENVELOPE = D("1.65")


def resting(side="DOWN", market=MKT):
    return {"c1": {"market": market, "side": side, "state": "LIVE",
                   "qty": "1.03", "remaining": "1.03",
                   "venue_order_id": "V1"}}


def slot(side="UP", market=MKT, live=None, claimed=None):
    return TS.second_leg_slot_refusal(
        market=market, side=side,
        live_orders=resting() if live is None else live,
        legs_claimed={"DOWN": True, "UP": False} if claimed is None
        else claimed)


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, "1")


# ============================================ requirement 1: joint cash

class TestBothLegsAreAlreadyReservedTogether:
    """The planner needs no change: it sizes against the cash gate's own
    basis, which charges a resting DOWN leg a DOLLAR A SHARE and the UP leg
    its price -- i.e. the cost of BOTH legs filling in full."""

    def test_the_obligation_basis_is_one_plus_the_up_price(self):
        src = inspect.getsource(TS.build_orders)
        assert "oblig_per_contract = D(1) + up_px" in src

    @pytest.mark.parametrize("window, up, dn, qty", PLANS)
    def test_every_planned_pair_fits_the_existing_envelope(self, window, up,
                                                           dn, qty):
        """All eight of the campaign's plans already reserved both legs
        inside $1.65 while only one of them could ever rest."""
        assert qty * (D(1) + up) <= ENVELOPE, window

    @pytest.mark.parametrize("window, up, dn, qty", PLANS)
    def test_the_reservation_exceeds_what_both_fills_actually_cost(
            self, window, up, dn, qty):
        """So two resting legs cannot overspend the envelope: the reserved
        obligation is strictly more than the gross cash of both fills."""
        assert qty * (up + dn) < qty * (D(1) + up), window


# ====================================== requirement 5 and the slot itself

class TestTheSlotIsGrantedOnlyToOneBoundedPair:

    def test_it_is_off_by_default(self):
        assert TS.two_order_pair_enabled() is False
        assert slot() == "TWO_ORDER_PAIR_DISABLED"

    @pytest.mark.parametrize("raw, on", [
        ("1", True), ("YES", True), ("yes", True), ("TRUE", True),
        ("true", True), ("0", False), ("no", False), ("", False),
        ("2", False), ("y", False), (" 1 ", True),
    ])
    def test_only_an_explicit_yes_enables_it(self, monkeypatch, raw, on):
        monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, raw)
        assert TS.two_order_pair_enabled() is on

    def test_the_complement_of_the_resting_leg_qualifies(self, enabled):
        assert slot(side="UP") is None

    def test_a_same_side_order_is_an_extend_and_does_not_qualify(self,
                                                                 enabled):
        assert slot(side="DOWN") == "RESTING_ORDER_IS_THE_SAME_SIDE"

    def test_another_market_does_not_qualify(self, enabled):
        assert slot(market="other") == "RESTING_ORDER_IS_ANOTHER_MARKET"

    def test_with_nothing_resting_the_ordinary_slot_covers_it(self, enabled):
        assert slot(live={}) == "NO_FIRST_LEG_RESTING"

    def test_a_third_order_is_never_part_of_one_pair(self, enabled):
        two = dict(resting())
        two["c2"] = {"market": MKT, "side": "UP", "state": "LIVE"}
        assert slot(live=two) == "PAIR_ALREADY_RESTING"

    def test_a_replacement_of_a_claimed_leg_does_not_qualify(self, enabled):
        """Requirement 5: this is what stops a re-quote being mistaken for a
        second leg and doubling the exposure."""
        assert slot(side="UP", claimed={"DOWN": True, "UP": True}) == \
            "THIS_SIDE_ALREADY_CLAIMED"

    def test_an_order_from_another_round_does_not_qualify(self, enabled):
        assert slot(claimed={"DOWN": False, "UP": False}) == \
            "RESTING_ORDER_IS_ANOTHER_ROUND"

    @pytest.mark.parametrize("live, claimed, reason", [
        (None, None, None),                                   # control
        ({"c1": {"market": MKT, "side": "", "state": "LIVE"}},
         {"DOWN": True}, "RESTING_ORDER_SIDE_UNREADABLE"),
        ({"c1": {"market": "", "side": "DOWN", "state": "LIVE"}},
         {"DOWN": True}, "RESTING_ORDER_IS_ANOTHER_MARKET"),
    ])
    def test_an_unreadable_row_fails_closed(self, enabled, live, claimed,
                                            reason):
        got = slot(live=live, claimed=claimed)
        assert got == reason

    def test_an_absent_claim_map_fails_closed(self, enabled):
        assert slot(claimed={}) == "CLAIM_STATE_UNREADABLE"
        assert TS.second_leg_slot_refusal(
            market=MKT, side="UP", live_orders=resting(),
            legs_claimed=None) == "CLAIM_STATE_UNREADABLE"

    def test_an_unreadable_live_order_map_fails_closed(self, enabled):
        assert TS.second_leg_slot_refusal(
            market=MKT, side="UP", live_orders=None,
            legs_claimed={"DOWN": True}) == "LIVE_ORDERS_UNREADABLE"


# ================================= requirement 4: the slot buys no headroom

class TestTheSlotRaisesTheCeilingByExactlyOne:

    def limits(self, **kw):
        base = dict(max_usd_per_market=D("20"), max_abs_residual=D("3"),
                    max_total_exposure_usd=D("2"), max_concurrent_markets=1,
                    max_live_orders=1, daily_realized_loss_halt_usd=D("5"))
        base.update(kw)
        return LX.SafetyLimits(**base)

    def call(self, limits, n, ok, **kw):
        base = dict(price=D("0.35"), qty=D("1"), market="m",
                    committed_by_market={}, residual_after=D(0),
                    realized_today=D(0), open_markets=["m"],
                    outstanding_by_market={}, risk_by_market={"m": D("0.5")},
                    live_order_count=n, pair_second_leg_ok=ok)
        base.update(kw)
        return limits.refuse(**base)

    def test_without_the_evidence_one_order_is_still_the_ceiling(self):
        assert self.call(self.limits(), 1, False) == "MAX_LIVE_ORDERS"

    def test_with_the_evidence_a_second_order_passes(self):
        assert self.call(self.limits(), 1, True) is None

    def test_a_third_order_is_refused_even_with_the_evidence(self):
        assert self.call(self.limits(), 2, True) == "MAX_LIVE_ORDERS"

    def test_the_default_is_false_so_no_caller_change_is_needed(self):
        d = inspect.signature(LX.SafetyLimits.refuse).parameters
        assert d["pair_second_leg_ok"].default is False

    @pytest.mark.parametrize("kw, reason", [
        ({"orders_uncertain": "a cancel did not verify"},
         "ORDER_STATE_UNCERTAIN"),
        ({"realized_today": D("-5")}, "DAILY_REALIZED_LOSS_HALT"),
        ({"risk_by_market": {"m": D("9")}}, None),
    ])
    def test_the_slot_does_not_bypass_any_other_gate(self, kw, reason):
        """Requirement 4 and 5: the slot grants ONE order slot and nothing
        else. Uncertainty still refuses FIRST, and the loss halt still
        binds, with the evidence present."""
        got = self.call(self.limits(), 1, True, **kw)
        if reason is not None:
            assert got == reason
        else:
            assert got != "MAX_LIVE_ORDERS"

    def test_an_unreadable_exposure_ceiling_still_refuses_first(self):
        lim = self.limits()
        lim.max_total_exposure_usd = None
        assert self.call(lim, 1, True) == \
            "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_the_account_wide_ceiling_is_unchanged_at_two_dollars(self):
        assert self.limits().max_total_exposure_usd == D("2")

    def test_the_residual_ceiling_is_unchanged_at_three(self):
        assert LX.ABS_RESIDUAL_CAP_CONTRACTS == D("3")
        assert self.limits().max_abs_residual == D("3")

    def test_one_market_is_unchanged(self):
        assert self.limits().max_concurrent_markets == 1


# ===================================== requirements 2, 3, 6: the lifecycle

class TestTheLifecycleAlreadyTracksLegsIndependently:

    def test_each_order_is_keyed_by_its_own_client_and_venue_id(self):
        src = inspect.getsource(LX.InventoryStore.record_submit)
        assert '"live_orders"' in src
        assert "intent.get(\"side\")" in src
        src2 = inspect.getsource(LX.InventoryStore.attach_venue_id)
        assert "venue_order_id" in src2

    def test_a_partial_fill_reduces_only_its_own_row(self):
        """`apply_fill` owns the row arithmetic: it subtracts the filled
        quantity from THAT coid's `remaining`, marks it PARTIALLY_FILLED
        while anything is left, and pops it only when nothing is."""
        src = inspect.getsource(LX.InventoryStore.apply_fill)
        assert "PARTIALLY_FILLED" in src
        assert "live.get(coid)" in src
        assert "live.pop(coid, None)" in src

    def test_a_cancel_names_one_order_and_proves_it_terminal(self):
        src = inspect.getsource(LX.deadman)
        assert "only" in inspect.signature(LX.deadman).parameters
        assert "still_open" in src
        assert "VENUE" in src

    def test_either_fill_order_is_handled_by_reading_held_quantity(self):
        """Requirement 3: the complement path keys off the OTHER side
        actually holding filled quantity, not off an assumed order."""
        src = inspect.getsource(LX.FaithfulExecutor.handle)
        assert "position(market" in src

    def test_maker_only_and_no_crossing_are_unchanged(self):
        assert TS.max_pair_cost() == D("0.96")
        assert TS.depth_ticks() == 2
        for side in ("UP", "DOWN"):
            q = TS.quote_own_price(side, bid="0.62", ask="0.63", tick="0.01",
                                   depth_ticks=TS.depth_ticks())
            assert not TS.leg_crosses(side, q, bid="0.62", ask="0.63")

    def test_the_slot_decision_is_recorded_for_audit(self):
        src = inspect.getsource(LX.FaithfulExecutor.handle)
        assert "PAIR_SECOND_SLOT" in src
        assert "second_leg_slot_refusal" in src

    def test_the_slot_is_established_before_the_gate_runs(self):
        """By AST: evidence first, then `limits.refuse`."""
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(LX.FaithfulExecutor.handle)))
        ev, gate = [], []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = (n.func.attr if isinstance(n.func, ast.Attribute)
                    else getattr(n.func, "id", ""))
            if name == "second_leg_slot_refusal":
                ev.append(n.lineno)
            if name == "refuse" and isinstance(n.func, ast.Attribute) \
                    and getattr(n.func.value, "attr", "") == "limits":
                gate.append(n.lineno)
        assert ev and gate
        assert min(ev) < min(gate)


# =============================== requirement 7: does it keep the economics?

class TestWhatRestingBothLegsCostsAndBuys:
    """The honest accounting, from the eight settled windows. These are
    arithmetic restatements of realized settlements, not predictions."""

    #: (window, realized pair cost or None if unmatched, qty, settled)
    SETTLED = [
        ("2145z", D("0.91"), D("1.03"), D("0.0927")),
        ("2200z", D("0.95"), D("0.94"), D("0.0495")),
        ("2215z", D("0.76"), D("1.01"), D("0.2424")),
        ("2230z", None, D("1.47"), D("-0.1764")),
        ("2245z", None, D("1.10"), D("-0.0930")),
        ("2300z", D("0.74"), D("1.03"), D("0.2678")),
        ("2315z", None, D("1.05"), D("-0.3990")),
        ("2330z", D("0.89"), D("1.36"), D("0.1496")),
    ]

    def test_the_campaign_netted_what_the_ledger_says(self):
        assert sum(s for _, _, _, s in self.SETTLED) == D("0.1336")

    def test_five_of_eight_completed(self):
        done = [w for w, p, _, _ in self.SETTLED if p is not None]
        assert len(done) == 5
        assert sum(s for _, p, _, s in self.SETTLED if p is not None) == \
            D("0.8020")
        assert sum(s for _, p, _, s in self.SETTLED if p is None) == \
            D("-0.6684")

    def test_locking_both_legs_at_plan_prices_forfeits_the_upside(self):
        """THE COST, and it is large. The complement is priced against the
        CURRENT book from the REALISED first-leg cost, so four of the five
        completions beat the planned 0.95: 0.91, 0.76, 0.74, 0.89. Resting
        both legs at plan prices locks the pair at 0.95 and earns the plan
        edge of 0.05 instead."""
        at_plan = sum(q * D("0.05")
                      for _, p, q, _ in self.SETTLED if p is not None)
        actual = sum(s for _, p, _, s in self.SETTLED if p is not None)
        assert at_plan == D("0.2685")
        assert actual == D("0.8020")
        forfeited = actual - at_plan
        assert forfeited == D("0.5335")
        # four times the whole campaign's net
        assert forfeited > 3 * sum(s for _, _, _, s in self.SETTLED)

    def test_realized_pairs_beat_the_plan_in_four_of_five(self):
        better = [w for w, p, _, _ in self.SETTLED
                  if p is not None and p < D("0.95")]
        assert len(better) == 4
        assert set(better) == {"2145z", "2215z", "2300z", "2330z"}

    @pytest.mark.parametrize("window, up, dn, qty", PLANS)
    def test_being_stranded_on_the_dear_side_costs_more(self, window, up,
                                                        dn, qty):
        """THE NEW FAILURE MODE. Today the CHEAPER leg is always placed
        first, so an unmatched window can only strand the cheap side. With
        both legs resting, the dear side can fill alone -- and the residual
        loss is qty times its own price."""
        cheap, dear = min(up, dn), max(up, dn)
        assert qty * dear >= qty * cheap

    def test_the_dear_side_stranding_is_one_to_seven_times_worse(self):
        ratios = [max(up, dn) / min(up, dn) for _, up, dn, _ in PLANS]
        assert min(ratios) > D("1.1")
        assert max(ratios) > D("6.9")          # 2230z: 0.83 vs 0.12

    def test_so_the_mechanism_is_not_obviously_profitable(self):
        """A measured cost of $0.5335 in forfeited complement improvement,
        plus a new dear-side stranding risk, against an UNMEASURED gain in
        completion rate. This is why activation is withheld."""
        assert TS.two_order_pair_enabled() is False
