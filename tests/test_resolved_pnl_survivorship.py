"""The survivorship bug, made impossible.

2026-09-10: September trading P&L was reported as +$7,125.74 from a market
set filtered on `redeem_qty > 0`, labelled "settlement-observed markets
only". A market lost completely redeems nothing, so it has no redemption
row -- the filter excluded losers BY CONSTRUCTION. It dropped 708 of 4,217
resolved markets, 16.8%, every one a total loss. The corrected figure is
-$5,047.88. **The sign flipped.**

Nothing was miscalculated. The population was wrong, in a way that read as
diligence. That is why this is a test and not a habit.

The invariant the owner asked for: a fully losing market -- purchases and a
resolution, no REDEEM row -- must still enter P&L as `-spend`.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_resolved_pnl as rp

NOW = 1_000_000.0
RESOLVED = NOW - 5_000.0          # comfortably past the settle buffer


def _m(slug, spend, redeem, res=RESOLVED):
    return {"slug": slug, "spend": spend, "redeem": redeem,
            "resolution_ts": res}


# ---------------------------------------------------- the invariant itself
def test_a_total_loss_enters_pnl_as_minus_spend():
    """THE INVARIANT. Bought, resolved, redeemed nothing -> -spend."""
    assert rp.market_pnl(spend="12.50", redeem=None) == D("-12.50")
    assert rp.market_pnl(spend="12.50", redeem=0) == D("-12.50")
    assert rp.market_pnl(spend="12.50", redeem="0.00") == D("-12.50")


def test_a_winner_enters_as_redeem_minus_spend():
    assert rp.market_pnl(spend="10.00", redeem="14.97") == D("4.97")


def test_zero_redemption_markets_are_counted_not_skipped():
    out = rp.resolved_pnl([_m("win", "10", "12"), _m("lose", "10", 0)], NOW)
    assert out["n_resolved"] == 2
    assert out["n_zero_redemption"] == 1
    assert out["trading_pnl_usd"] == "-8"        # +2 and -10


def test_the_bug_reproduces_if_losers_are_dropped():
    """The exact shape of the September error: same maths, wrong population."""
    universe = [_m("win", "10", "12"), _m("lose", "10", 0)]
    survivors = [m for m in universe if D(str(m["redeem"] or 0)) > 0]
    good = rp.resolved_pnl(universe, NOW)
    bad = rp.resolved_pnl(survivors, NOW)
    assert good["trading_pnl_usd"] == "-8"
    assert bad["trading_pnl_usd"] == "2"
    assert D(bad["trading_pnl_usd"]) > 0 > D(good["trading_pnl_usd"]), \
        "dropping losers must be able to flip the sign -- that is the bug"


# ------------------------------------------ membership is RESOLUTION only
def test_membership_never_depends_on_redemption():
    """A resolved market with no redemption is IN; an unresolved one is OUT
    however much it redeemed."""
    assert rp.is_resolved(_m("x", "1", 0), NOW) is True
    assert rp.is_resolved(_m("y", "1", "99", res=NOW + 10), NOW) is False


def test_the_settle_buffer_only_delays_it_never_excludes_on_outcome():
    just_resolved = _m("x", "1", 0, res=NOW - 10)
    assert rp.is_resolved(just_resolved, NOW, settle_buffer_s=600) is False
    assert rp.is_resolved(just_resolved, NOW, settle_buffer_s=0) is True


def test_unresolved_markets_are_excluded_so_open_positions_are_not_losses():
    """An open position has spend and no redemption yet -- it must NOT be
    booked as a total loss just because it looks like one."""
    out = rp.resolved_pnl([_m("open", "50", 0, res=NOW + 900)], NOW)
    assert out["n_resolved"] == 0
    assert out["trading_pnl_usd"] == "0"


# ------------------------------------------------- the pre-filter refusal
def test_a_prefiltered_set_is_REFUSED_rather_than_answered():
    """The damage cannot be repaired downstream: the losers are gone. So the
    only safe answer is to refuse, not to return a confident wrong number."""
    survivors = [_m(f"m{i}", "10", "11") for i in range(250)]
    with pytest.raises(rp.SurvivorshipFilterError) as e:
        rp.resolved_pnl(survivors, NOW)
    assert "pre-filtered" in str(e.value)
    assert "16.8%" in str(e.value), "the refusal cites the real rate"


def test_a_healthy_set_with_losers_passes():
    rows = [_m(f"w{i}", "10", "11") for i in range(220)]
    rows += [_m(f"l{i}", "10", 0) for i in range(40)]
    out = rp.resolved_pnl(rows, NOW)
    assert out["n_zero_redemption"] == 40


def test_small_samples_are_not_refused():
    """A handful of winners is ordinary; only a LARGE all-winner set is
    evidence of a filter. Refusing tiny sets would make the guard useless."""
    rows = [_m(f"w{i}", "10", "11") for i in range(5)]
    assert rp.resolved_pnl(rows, NOW)["n_resolved"] == 5


# --------------------------------------------------- mutation: it has teeth
def test_dropping_the_zero_redeem_branch_changes_the_answer():
    """Mutation. If market_pnl ever treated a missing redemption as 'skip'
    instead of '-spend', this is the test that fails."""
    universe = [_m("a", "100", 0), _m("b", "100", "150")]
    real = rp.resolved_pnl(universe, NOW)
    mutant = sum((rp.market_pnl(m["spend"], m["redeem"])
                  for m in universe if m["redeem"]), D("0"))
    assert D(real["trading_pnl_usd"]) == D("-50")
    assert mutant == D("50")
    assert real["trading_pnl_usd"] != str(mutant)


def test_the_september_numbers_are_pinned_as_the_worked_example():
    """Regression against the real event, so the correction cannot quietly
    revert: 708 of 4,217 resolved markets redeemed nothing."""
    rows = [_m(f"z{i}", "10", 0) for i in range(708)]
    rows += [_m(f"k{i}", "10", "11") for i in range(4217 - 708)]
    out = rp.resolved_pnl(rows, NOW)
    assert out["n_resolved"] == 4217
    assert out["n_zero_redemption"] == 708
    assert round(100 * 708 / 4217, 1) == 16.8


def test_the_module_says_why_it_exists():
    import inspect
    doc = inspect.getdoc(rp) or ""
    assert "708" in doc and "16.8%" in doc
    assert "redemption" in doc.lower()
