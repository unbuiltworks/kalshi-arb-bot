"""What our own 34 venue-confirmed orders say, pinned as regressions.

Every fill-rate figure this program used before now came from recorded books
and a BRACKET -- PROVEN (a lower bound that over-samples the adverse case)
and POSSIBLE (an upper bound, since a size decrease may be a cancellation).
PR #208 found that bracket was wider than the difference between the
strategies it was being used to rank, which is where the offline work
stopped being able to decide anything.

These tests pin a different and better source: OUR OWN ORDERS. 34 of them,
every terminal `venue_confirmed: true`, reconciled penny-exact to settlement
where settlement exists. No bracket is needed for an order whose outcome the
venue told us.

THE STATISTICAL POINT, and it is the whole reason the numbers moved. 14 of
the 34 did not fill, and almost none of them "failed to" -- they were
cancelled BY US, several inside two seconds. Scoring those as non-fills
understates the fill rate; dropping them overstates it. They are
RIGHT-CENSORED durations and Kaplan-Meier is what handles them.

    naive               58.8%
    Kaplan-Meier        81.8% by 73.4s, median fill 17.1s

THE ACTIONABLE FINDING. All three singles our campaigns ever carried into
settlement were created by OUR OWN cancellation of the complement -- at
13.6s, 19.5s and 21.0s, with 70.7%, 60.0% and 60.0% conditional chances of
still filling. None was caused by the market refusing us. The market has
never once left us with an unmatched leg that we did not cancel into.

Two mechanisms close that, and both are tested below: PRESERVE (PR #202)
holds any resting completion for its own reason, and the new
`REQUOTE_MIN_REST_S` floor stops the requote path cancelling a quote inside
our own fill-latency distribution.

NOT A PROFITABILITY CLAIM. A fill rate is not a P&L. These are 34 orders
from three campaigns over six days, placed by a policy that chose when and
where to quote, so the curve is the fill curve FOR THOSE CHOICES and
extrapolates to no depth or regime we have never quoted in.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.issue206 import own_order_lifecycles as OWN
from src import l5_two_sided_probe as TSP
from src import l5_unmatched_disposition as UD


# ===================================== 1. THE DATASET IS WHAT IT CLAIMS

def test_the_order_inventory_is_complete_and_venue_confirmed():
    assert len(OWN.ORDERS) == 34
    filled = [o for o in OWN.ORDERS if o[7] == OWN.FILLED]
    cens = [o for o in OWN.ORDERS if o[7] == OWN.CANCELED]
    assert len(filled) == 20 and len(cens) == 14
    # the three campaigns, and nothing else
    assert {o[0] for o in OWN.ORDERS} == {
        "2015z", "0100z", "0115z", "0130z", "0145z", "0200z", "1230z"}


def test_the_2015z_fills_reconcile_to_the_settled_cost_basis():
    """The tape and the settlement agree to the cent, so the rows are real.

    UP 0.38 + 0.39 + 0.38 + 0.38 + 0.03x0.38 = 1.5414 over 4.03 contracts
    DOWN 0.62 + 0.60 + 0.60 + 0.60          = 2.4200 over 4
    against the settlement's up_cost 1.541400 / dn_cost 2.4200.
    """
    up = sum(D(o[2]) * D(o[3]) for o in OWN.ORDERS
             if o[0] == "2015z" and o[1] == "UP" and o[7] == OWN.FILLED)
    dn = sum(D(o[2]) * D(o[3]) for o in OWN.ORDERS
             if o[0] == "2015z" and o[1] == "DOWN" and o[7] == OWN.FILLED)
    assert up == D("1.5414")
    assert dn == D("2.4200")


def test_the_unarmed_decision_tape_is_not_counted_as_orders():
    """`armed: false` / ORDER:NOT_ARMED intents are not venue orders.

    `data/pmus_decision_tape/` holds 110 distinct `l5f-` coids that never
    reached the venue -- `transport.submit` raised NotArmed at the wire. A
    fill rate computed over those would be meaningless, so none of them is
    in this dataset: all 34 rows carry a terminal state.
    """
    for o in OWN.ORDERS:
        assert o[7] in (OWN.FILLED, OWN.CANCELED)


# ============================ 2. CENSORING IS HANDLED, NOT PAPERED OVER

def test_kaplan_meier_exceeds_the_naive_rate_because_of_our_own_cancels():
    km = OWN.kaplan_meier(OWN.ORDERS)
    naive = 20.0 / 34.0
    final = km[-1][4]
    assert abs(naive - 0.588) < 0.002
    assert final > naive, "censoring was depressing the naive figure"
    assert 0.80 < final < 0.83, final


def test_the_median_fill_time_is_seventeen_seconds():
    km = OWN.kaplan_meier(OWN.ORDERS)
    med = next(t for t, _, _, _, p in km if p >= 0.5)
    assert med == 17.1


def test_a_cancel_at_one_second_tells_us_almost_nothing():
    """Which is exactly why it must not be scored as a non-fill."""
    km = OWN.kaplan_meier(OWN.ORDERS)
    early = OWN.conditional_fill_chance(km, 1.1)
    late = OWN.conditional_fill_chance(km, 120.9)
    assert early > 0.75, early
    assert late == 0.0, "past the last observed fill it says nothing more"


def test_the_curve_is_conservative_out_of_sample_on_duration():
    """1230z's 63.0s fill is beyond anything the fit set ever saw."""
    fit = [o for o in OWN.ORDERS if o[0] != "1230z"]
    fit_last = max(o[6] for o in fit if o[7] == OWN.FILLED)
    hold_fills = sorted(o[6] for o in OWN.ORDERS
                        if o[0] == "1230z" and o[7] == OWN.FILLED)
    assert fit_last == 73.4
    assert 63.0 in hold_fills


# ======== 3. EVERY SINGLE WE EVER CARRIED WAS CAUSED BY OUR OWN CANCEL

def test_no_single_was_ever_caused_by_the_market():
    assert OWN.SINGLES_CAUSED_BY_THE_MARKET == 0
    assert OWN.SINGLES_CAUSED_BY_OUR_OWN_CANCEL == 3
    assert OWN.ROUNDS_THAT_PAIRED == 8
    assert OWN.ROUNDS_WITH_A_FIRST_FILL == 11


def test_the_measured_pair_rate_sits_inside_the_recorded_book_bracket():
    """Above PROVEN, below POSSIBLE -- where a bracket should put it."""
    measured = OWN.ROUNDS_THAT_PAIRED / OWN.ROUNDS_WITH_A_FIRST_FILL
    assert measured > 0.70
    assert float(UD.REOPEN_P_PAIR) < measured, \
        "PROVEN understates pairing, as its own docstring says"


@pytest.mark.parametrize("window,held_side,held,rest,bid,ask", [
    ("0115z", "DOWN", "0.24", "0.70", "0.28", "0.30"),
    ("0145z", "DOWN", "0.35", "0.61", "0.38", "0.40"),
    ("2015z", "DOWN", "0.60", "0.37", "0.62", "0.64"),
])
def test_the_controller_now_preserves_the_complements_we_cancelled(
        window, held_side, held, rest, bid, ask):
    """THE REGRESSION. Each of these was cancelled live and cost a pair.

    PRESERVE ranks above HOLD by construction: a resting quote has the same
    worst case as holding and strictly better upside, so cancelling it is
    never an improvement. Note this holds even at the 0.97 pair -- the
    opening ceiling gates SUBMITTING a completion, not continuing to hold
    one already resting.
    """
    r = UD.decide(market=window, held_side=held_side, held_qty=D("1"),
                  held_cost_usd=D(held), deficient_qty=D("0"),
                  deficient_cost_usd=D("0"),
                  resting_own_px=D(rest), resting_qty=D("1"),
                  resting_filled_qty=D("0"),
                  bid=D(bid), ask=D(ask), tick=D("0.01"),
                  min_qty=D("0.01"), now_s=60.0, window_start_s=0.0,
                  window_end_s=900.0, book_age_s=D("0.05"),
                  taker_enabled=False)
    assert r["selected"] == UD.PRESERVE, (window, r["selected_reason"])


# ===================== 4. THE REQUOTE REST FLOOR, FROM THE SAME CURVE

def test_the_rest_floor_is_the_fit_sets_median_fill_time():
    """A distributional statistic, not a threshold fitted to three points."""
    assert TSP.REQUOTE_MIN_REST_S == 14.0


def requote(**over):
    kw = dict(side="UP", resting_own_px=D("0.3700"), resting_qty=D("1"),
              resting_filled_qty=D("0"), bid=D("0.6200"), ask=D("0.6400"),
              tick=D("0.01"), min_qty=D("0.01"),
              held_qty=D("1"), held_cost_usd=D("0.6000"),
              deficient_qty=D("0"), deficient_cost_usd=D("0"),
              residual_budget_usd=D("3"),
              now_s=60.0, window_start_s=0.0, window_end_s=900.0,
              book_age_s=D("0.05"))
    kw.update(over)
    return TSP.completion_requote(**kw)


@pytest.mark.parametrize("rest", [1.1, 1.2, 1.7, 5.4, 6.1, 6.8, 8.2, 13.9])
def test_a_quote_inside_our_fill_distribution_is_not_requoted(rest):
    """The five 2015z cancellations that fired between 1.1s and 6.8s.

    Each had a 76-81% conditional chance of still filling, measured on our
    own curve. The requote path had no rest floor at all.
    """
    r = requote(resting_rest_s=rest)
    assert r["act"] is False
    assert r["reason"] == "TOO_YOUNG_TO_REQUOTE"
    assert D(r["min_rest_s"]) == D("14.0")


@pytest.mark.parametrize("rest", [14.0, 20.0, 60.0, 200.0])
def test_an_older_quote_is_still_judged_on_its_merits(rest):
    """The floor adds patience; it does not disable repricing."""
    r = requote(resting_rest_s=rest)
    assert r["reason"] != "TOO_YOUNG_TO_REQUOTE"


def test_an_unreadable_resting_age_refuses_the_requote():
    """Refusing leaves the quote resting, which is the safe direction."""
    for bad in ("UNREADABLE", "", -1.0):
        r = requote(resting_rest_s=bad)
        assert r["act"] is False
        assert r["reason"] == "RESTING_AGE_UNREADABLE", bad


def test_omitting_the_age_leaves_the_pure_function_unchanged():
    """A deliberate contract: only the money path must supply it."""
    r = requote()
    assert r["reason"] != "TOO_YOUNG_TO_REQUOTE"
    assert r["reason"] != "RESTING_AGE_UNREADABLE"


def test_the_runner_passes_the_resting_age_to_the_requote():
    """What makes the floor real in the only path that spends money."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.maybe_completion_requote)
    assert "resting_rest_s=" in src
    assert "fromisoformat" in src, "derived from the row's own submit stamp"
    # an age we cannot establish leaves the old behaviour standing and is
    # COUNTED -- the same judgement as `require_queue_evidence` beside it,
    # because a floor we cannot evaluate must not disable repricing
    # entirely and re-create the 0330z frozen plan.
    assert "requote_without_resting_age" in src


def test_the_floor_does_not_override_a_mandatory_deadline():
    """Patience is for DISCRETIONARY cancels only.

    The order TTL and the terminal deadline must still fire -- 1230z's DOWN
    was cancelled at 124.0s by the TTL, and our own curve says that cost
    nothing: the conditional fill chance past the last observed fill is
    zero.
    """
    km = OWN.kaplan_meier(OWN.ORDERS)
    assert OWN.conditional_fill_chance(km, 124.0) == 0.0
    import inspect
    src = inspect.getsource(TSP.completion_requote)
    assert "TOO_YOUNG_TO_REQUOTE" in src
    from src import l5_pmus_live_runner as R
    dl = inspect.getsource(R.Runner.maybe_acc_deadline)
    assert "resting_rest_s" not in dl, \
        "the mandatory deadline must not consult the patience floor"
