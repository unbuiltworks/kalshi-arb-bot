"""Venue-neutral risk arithmetic, and the equivalence that stops it drifting.

"How much can we lose" must mean the same thing on every venue. These assert
the shared implementation agrees NUMERICALLY with the live PMUS executor, so
neither can move without this failing -- which gives the protection of shared
code without refactoring a running money path to get it.
"""
from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as pmus
from src.l5_intent import DesiredInventoryAction
from src.l5_risk import (
    GEMINI_PROBE_SHELL, RiskShell, budget_tripped, combined_loss,
    guaranteed_floor, unsettled_floor_total,
)


class _Inv:
    """Minimal stand-in with the fields PMUS's floor reads."""
    def __init__(self, up, dn, cu, cd):
        self.up, self.dn = D(str(up)), D(str(dn))
        self.cost_up, self.cost_dn = D(str(cu)), D(str(cd))


def _intent(**kw):
    base = dict(cohort="BTC15m", market_key="m", source_state_id="s",
                t_utc=0.0, tte_s=600.0, action="OPEN", desired_outcome="Up",
                desired_inventory="10", incremental_qty="10",
                max_acceptable_price="0.40", current_matched="0",
                residual="0", model_version="v0.2")
    base.update(kw)
    return DesiredInventoryAction(**base)


# ------------------------------------------------------------- equivalence

@pytest.mark.parametrize("up,dn,cu,cd", [
    (20.34, 16.06, 11.0, 10.5939),      # the documented live MIL -2.5 shape
    (0, 11.76, 0, 4.5359),
    (5, 5, 2.4, 2.4),
    (0, 0, 0, 0),
])
def test_the_floor_matches_the_live_pmus_implementation(up, dn, cu, cd):
    inv = _Inv(up, dn, cu, cd)
    assert guaranteed_floor(up, dn, D(str(cu)) + D(str(cd))) == \
        pmus.guaranteed_settlement_floor(inv)


def test_the_documented_live_case_reproduces():
    """min(20.34, 16.06) - 21.5939 = -5.53, knowable before settlement and
    invisible to a realized-only stop."""
    assert guaranteed_floor("20.34", "16.06", "21.5939") == D("-5.5339")


def test_combined_loss_matches_pmus():
    invs = [_Inv(0, 11.76, 0, 4.5359), _Inv(20.34, 16.06, 11.0, 10.5939),
            _Inv(5, 5, 2.0, 2.0)]
    floors = [guaranteed_floor(i.up, i.dn, i.cost_up + i.cost_dn) for i in invs]
    assert combined_loss("-3.25", floors) == \
        pmus.combined_loss(D("-3.25"), invs)


def test_only_negative_floors_count():
    """A favourable open position must not subsidise another market's
    guaranteed loss against the budget."""
    assert unsettled_floor_total(["-2.00", "5.00", "-1.00"]) == D("-3.00")


def test_the_boundary_is_inclusive_like_pmus():
    """At exactly the stop, the stop HAS fired."""
    assert budget_tripped("-30.00", "-30.00") is True
    assert budget_tripped("-29.99", "-30.00") is False
    assert budget_tripped(D("-30.00"), D("-30.00")) == \
        pmus.combined_budget_tripped(D("-30.00"), [], budget=D("-30.00"))


# ------------------------------------------------------------------- shell

def test_a_shell_cannot_be_built_without_real_bounds():
    """A risk shell that works unconfigured is one that can be deployed with
    nobody having decided the limits."""
    ok = dict(venue="X", daily_loss_stop="-5.00", max_inventory_shares="10",
              max_market_spend="5.00", max_concurrent_markets=1,
              deadman_s=300.0)
    RiskShell(**ok)
    for bad in (dict(daily_loss_stop="0"), dict(daily_loss_stop="5.00"),
                dict(max_inventory_shares="0"), dict(max_market_spend="0"),
                dict(max_concurrent_markets=0), dict(deadman_s=0)):
        with pytest.raises(ValueError):
            RiskShell(**{**ok, **bad})


def test_each_refusal_names_the_bound_it_hit():
    s = GEMINI_PROBE_SHELL
    i = _intent()
    assert "DAILY STOP" in s.check(realized="-5.00", floors=[], intent=i).reason
    assert "DEADMAN" in s.check(realized="0", floors=[], intent=i,
                                last_heartbeat_age_s=9999).reason
    assert "INVENTORY CAP" in s.check(realized="0", floors=[], intent=i,
                                      current_inventory="9").reason
    assert "MARKET SPEND CAP" in s.check(realized="0", floors=[], intent=i,
                                         market_spent="4.00").reason
    assert "CONCURRENCY" in s.check(realized="0", floors=[], intent=i,
                                    open_markets=1).reason


def test_the_spend_bound_prices_at_the_intent_ceiling_not_the_quote():
    """The worst price we would accept is the right one for a spend bound; a
    bound computed from a hoped-for quote is not a bound."""
    s = GEMINI_PROBE_SHELL
    cheap, dear = _intent(max_acceptable_price="0.10"), _intent(
        max_acceptable_price="0.60")
    assert s.check(realized="0", floors=[], intent=cheap).allowed is True
    v = s.check(realized="0", floors=[], intent=dear)
    assert v.allowed is False and "6.00" in v.reason


def test_an_unsettled_guaranteed_loss_counts_against_the_stop():
    """The whole reason combined_loss exists: a realized-only stop cannot see
    a position whose worst branch is already negative."""
    s = GEMINI_PROBE_SHELL
    floors = [guaranteed_floor("0", "11.76", "4.5359")]      # -4.5359
    v = s.check(realized="-0.50", floors=floors, intent=_intent())
    assert v.allowed is False and "DAILY STOP" in v.reason


def test_gemini_probe_shell_is_tiny_and_not_an_authorisation():
    """Bounds written down so the shell cannot ship with nobody having chosen
    them. Gemini is NOT live."""
    s = GEMINI_PROBE_SHELL
    assert s.venue == "GEMINI"
    assert D(s.daily_loss_stop) == D("-5.00")
    assert D(s.max_inventory_shares) <= D("10")
    assert s.max_concurrent_markets == 1
