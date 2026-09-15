"""The production path: venue-neutral intent -> adapter evidence -> route.

These pin the separations that make the whole thing auditable. Each one
corresponds to a way this program has previously confused a belief with an
observation.
"""
from decimal import Decimal as D

import pytest

from src.l5_adapters.base import AdapterEvaluation, no_market, within_ceiling
from src.l5_adapters.evaluate import attach_fill_evidence, evaluate
from src.l5_intent import ACTIONS, DesiredInventoryAction
from src.l5_router import route
from src.l5_venue_adapter import GeminiAdapter, NormalizedBook, PMUSAdapter


def _intent(**kw):
    base = dict(cohort="BTC15m", market_key="btc-updown-15m-1789470900",
                source_state_id="s1", t_utc=1789470900.0, tte_s=600.0,
                action="OPEN", desired_outcome="Up", desired_inventory="10",
                incremental_qty="10", max_acceptable_price="0.60",
                current_matched="0", residual="0",
                model_version="v0.2-85b4260cc30a")
    base.update(kw)
    return DesiredInventoryAction(**base)


def _book(venue="PMUS", bid="0.55", ask="0.57", bq="100", aq="100"):
    return NormalizedBook(venue=venue, market_id="m", t=0.0,
                          yes_bid=D(bid), yes_ask=D(ask),
                          yes_bid_qty=D(bq), yes_ask_qty=D(aq),
                          tick=D("0.01"), min_price=D("0.01"),
                          max_price=D("0.99"), tte_s=600.0)


# ----------------------------------------------------------------- the intent

def test_intent_id_is_stable_under_annotation():
    """Annotating an intent must not change its identity, or the same decision
    acquires two ids and an adapter can appear to have scored one it never saw."""
    a = _intent()
    assert a.id == _intent(notes="annotated", extra={"x": 1}).id
    assert a.id != _intent(max_acceptable_price="0.59").id


def test_ceiling_must_be_a_real_economic_bound():
    for bad in ("0", "1", "1.5", "-0.1"):
        with pytest.raises(ValueError):
            _intent(max_acceptable_price=bad)


def test_an_intent_to_do_nothing_is_rejected():
    with pytest.raises(ValueError):
        _intent(incremental_qty="0")


def test_the_intent_carries_no_venue_anything():
    """FIDELITY produces intent; it does not know who will execute it. If a
    venue, fee or submission price leaks in here, the two questions -- what
    would L5 do, and can we do it here -- stop being separable."""
    d = _intent().as_dict()
    for forbidden in ("venue", "fee", "submission_price", "token_id", "symbol",
                      "spread", "would_fill", "maker", "taker"):
        assert not any(forbidden in k for k in d), forbidden
    assert set(ACTIONS) == {"OPEN", "EXTEND", "COMPLETE", "SWITCH"}


# ---------------------------------------------------------------- the adapter

def test_modelled_fill_is_never_promoted_to_a_real_one():
    with pytest.raises(ValueError):
        AdapterEvaluation(venue="PMUS", intent_id="i", market_key="m",
                          evaluated_at=0.0, filled=True, stage="filled")


def test_liquidity_role_requires_venue_evidence():
    """Inferring 'we rested, therefore maker' is the assumption that produced
    unsupported rebate accounting before."""
    with pytest.raises(ValueError):
        AdapterEvaluation(venue="PMUS", intent_id="i", market_key="m",
                          evaluated_at=0.0, liquidity_role="maker")


def test_buying_down_prices_off_the_no_ask_not_the_yes_ask():
    """Acquiring Down means lifting the NO ask (1 - yes_bid). Getting this
    inversion wrong silently prices the wrong side of every Down intent."""
    b = _book(bid="0.55", ask="0.57")
    up = evaluate(_intent(desired_outcome="Up"), PMUSAdapter(), b, now=0.0)
    dn = evaluate(_intent(desired_outcome="Down"), PMUSAdapter(), b, now=0.0)
    assert up.available_price == "0.57"
    assert dn.available_price == "0.45"


def test_ceiling_is_compared_on_effective_cost_not_the_quote():
    """A quote under the ceiling whose fee pushes it over is not executable."""
    i = _intent(max_acceptable_price="0.575")
    ev = evaluate(i, PMUSAdapter(), _book(ask="0.57"), now=0.0)
    assert D(ev.available_price) < D(i.max_acceptable_price)
    assert D(ev.effective_cost) > D(i.max_acceptable_price)
    assert ev.within_ceiling is False and ev.would_submit is False


def test_would_fill_is_labelled_an_upper_bound():
    ev = evaluate(_intent(), PMUSAdapter(), _book(aq="1000"), now=0.0)
    assert ev.would_fill is True
    assert "UPPER BOUND" in ev.would_fill_basis
    assert ev.filled is None, "a book alone can never establish a fill"


def test_fill_evidence_is_the_only_route_to_filled():
    ev = evaluate(_intent(), PMUSAdapter(), _book(), now=0.0)
    with pytest.raises(ValueError):
        attach_fill_evidence(ev, {}, filled_qty="10")
    out = attach_fill_evidence(ev, {"venue_order_id": "X1", "status": "FILLED"},
                               filled_qty="10", liquidity_role="taker",
                               latency_s=0.4)
    assert out.filled is True and out.stage == "filled"
    assert out.liquidity_role == "taker"


def test_no_market_is_a_result_not_an_error():
    ev = no_market("GEMINI", _intent(), "no equivalent 15m contract")
    assert ev.stage == "no_market" and ev.would_submit is False


def test_both_venues_share_one_evaluation_path():
    """Two evaluators would make every venue difference ambiguous: economics,
    or an implementation accident?"""
    i = _intent()
    for adapter in (PMUSAdapter(), GeminiAdapter()):
        ev = evaluate(i, adapter, _book(venue=adapter.venue), now=0.0)
        assert ev.venue == adapter.venue
        assert ev.available_price == "0.57", "both price the same book alike"


def test_gemini_taker_fee_is_an_unverified_gap_and_refuses_rather_than_zeroes():
    """GeminiAdapter.taker_fee returns None -- 'not vendored: GAP
    (unverified)'. The conservative default must therefore REFUSE to price
    Gemini, with a reason, rather than substitute a zero fee. A fabricated 0
    reads as the cheapest possible execution and would make Gemini win every
    routing decision on an economics we have never confirmed.

    This is the single blocker on Gemini reaching executable-shadow VERIFIED.
    """
    ev = evaluate(_intent(), GeminiAdapter(), _book(venue="GEMINI"), now=0.0)
    assert ev.expected_fee is None
    assert "unavailable" in ev.expected_fee_basis
    assert ev.would_submit is False
    assert "fee unknown" in ev.would_submit_reason
    assert ev.effective_cost is None, "no cost may be invented from a null fee"


def test_a_gemini_maker_quote_still_prices_when_explicitly_asked():
    """The maker fee IS vendored, so Gemini can be evaluated on a maker basis
    when the caller asks for it explicitly -- never by default."""
    ev = evaluate(_intent(), GeminiAdapter(), _book(venue="GEMINI"),
                  now=0.0, assume_taker_fee=False)
    assert ev.expected_fee is not None
    assert "maker" in ev.expected_fee_basis


# ----------------------------------------------------------------- the router

def _ev(venue, eff, depth, ok=True, wf=True, intent=None):
    return AdapterEvaluation(venue=venue, intent_id=(intent or _intent()).id,
                             market_key="m", evaluated_at=0.0,
                             available_depth=depth, effective_cost=eff,
                             within_ceiling=ok, would_submit=ok, would_fill=wf)


def test_router_may_choose_neither():
    i = _intent()
    d = route(i, [_ev("PMUS", "0.90", "100", ok=False),
                  _ev("GEMINI", "0.95", "100", ok=False)])
    assert d.route == "NEITHER"
    assert len(d.declined) == 2


def test_router_ranks_on_effective_cost():
    i = _intent()
    assert route(i, [_ev("PMUS", "0.58", "100"),
                     _ev("GEMINI", "0.55", "100")]).route == "GEMINI"


def test_unknown_depth_fails_closed():
    """None must not read as 'enough'."""
    i = _intent()
    assert route(i, [_ev("PMUS", "0.55", None)]).route == "NEITHER"


def test_capacity_is_a_constraint_not_a_tiebreak():
    """A venue that cannot supply the quantity does not win on price."""
    i = _intent()
    d = route(i, [_ev("PMUS", "0.58", "100"), _ev("GEMINI", "0.50", "1")])
    assert d.route == "PMUS"


def test_would_fill_only_breaks_ties():
    """would_fill is an upper bound; ranking on it would prefer whichever venue
    displays more size."""
    i = _intent()
    d = route(i, [_ev("PMUS", "0.55", "100", wf=False),
                  _ev("GEMINI", "0.58", "100", wf=True)])
    assert d.route == "PMUS"


def test_both_is_opt_in_only():
    i = _intent()
    evs = [_ev("PMUS", "0.58", "100"), _ev("GEMINI", "0.55", "100")]
    assert route(i, evs).route == "GEMINI"
    assert route(i, evs, allow_both=True).route == "BOTH"


def test_router_refuses_evaluations_of_a_different_intent():
    i = _intent()
    other = _intent(max_acceptable_price="0.59")
    with pytest.raises(ValueError):
        route(i, [_ev("PMUS", "0.55", "100", intent=other)])
