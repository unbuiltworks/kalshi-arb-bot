"""The ceiling named DAILY must measure a DAY; the cumulative cap must not move.

Until 2026-10-25 both L5-scoped ceilings read the same post-baseline cumulative
figure, so L5_DAILY_LOSS_LIMIT was a cumulative budget that never reset -- which
is how the Oct 25 campaign was refused at "5.245700>=5" on a day whose own
realized loss was a fraction of that.
"""
from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates


def gates(**kw):
    return ValidationGates(**kw)


def _refusal(**kw):
    g = gates(max_l5_daily_loss_usd=kw.pop("daily", None),
              max_l5_cumulative_loss_usd=kw.pop("cumulative", None),
              # keep the ACCOUNT-WIDE cap out of the way; it runs first by
              # design and would otherwise mask the L5-scoped verdict here.
              max_cumulative_loss_usd=kw.pop("acct", D("1000")))
    return g.refuse(order_qty=D("1"), order_price=D("0.50"),
                    order_intent="OPEN",
                    l5_scoped_realized=kw.pop("cum_realized", D(0)),
                    l5_scoped_realized_today=kw.pop("today_realized", D(0)),
                    **kw)


def test_daily_ceiling_reads_the_DAY_not_the_post_baseline_total():
    """The exact Oct 25 numbers: -5.2457 cumulative, a small loss today."""
    r = _refusal(daily=D("20"), cumulative=D("20"),
                 cum_realized=D("-5.2457"), today_realized=D("-0.6864"))
    assert r is None, r


def test_a_day_that_breaches_the_daily_ceiling_still_refuses():
    r = _refusal(daily=D("20"), cumulative=D("20"),
                 cum_realized=D("-5.2457"), today_realized=D("-20.01"))
    assert r is not None and r.startswith("L5_DAILY_LOSS_LIMIT:"), r


def test_cumulative_cap_is_NOT_weakened_and_still_refuses_independently():
    """Nothing lost today, but the post-baseline total is over the cap."""
    r = _refusal(daily=D("20"), cumulative=D("20"),
                 cum_realized=D("-20.01"), today_realized=D("0"))
    assert r is not None and r.startswith("L5_CUMULATIVE_LOSS_LIMIT:"), r


def test_no_historical_loss_is_reset_the_cumulative_figure_is_untouched():
    """A day-scoped reading must not shrink what the cumulative cap sees."""
    over = _refusal(daily=D("20"), cumulative=D("5"),
                    cum_realized=D("-5.2457"), today_realized=D("0"))
    assert over is not None and over.startswith("L5_CUMULATIVE_LOSS_LIMIT:"), over
    assert "5.2457" in over


def test_missing_day_figure_falls_back_to_the_STRICTER_cumulative_measure():
    """Absent is not zero. It is not laxer either: it reads the bigger number.

    A caller that supplies no day figure keeps its pre-change behaviour, so no
    caller can obtain a looser daily gate by omitting an input.
    """
    r = _refusal(daily=D("5"), cumulative=D("50"),
                 cum_realized=D("-5.2457"), today_realized=None)
    assert r is not None and r.startswith("L5_DAILY_LOSS_LIMIT:"), r
    assert "5.2457" in r


def test_an_unreadable_day_figure_refuses_rather_than_passing():
    g = gates(max_l5_daily_loss_usd=D("20"), max_l5_cumulative_loss_usd=D("20"),
              max_cumulative_loss_usd=D("1000"))
    r = g.refuse(order_qty=D("1"), order_price=D("0.50"), order_intent="OPEN",
                 l5_scoped_realized=D("-1"),
                 l5_scoped_realized_today=D("NaN"))
    assert r == "L5_SCOPED_REALIZED_TODAY_UNREADABLE", r


def test_unrealized_loss_still_counts_against_the_DAILY_budget():
    """The day-scoped measure keeps the unrealized term; it is not dropped."""
    r = _refusal(daily=D("20"), cumulative=D("50"),
                 cum_realized=D("0"), today_realized=D("-1.0"),
                 cumulative_unrealized_loss=D("19.5"))
    assert r is not None and r.startswith("L5_DAILY_LOSS_LIMIT:"), r
    assert "20.5" in r


def test_account_wide_cumulative_cap_runs_FIRST_and_is_untouched():
    """Unchanged precedence: the account-wide cap refuses before L5 scoping."""
    r = _refusal(daily=D("20"), cumulative=D("20"), acct=D("20"),
                 cum_realized=D("0"), today_realized=D("0"),
                 cumulative_unrealized_loss=D("20.5"))
    assert r is not None and "CUMULATIVE_LOSS_LIMIT" in r, r


def test_NEGATIVE_CONTROL_old_shared_measure_would_have_refused_the_oct25_day():
    """Proves the fix changes the outcome that mattered.

    Under the old code both ceilings read the cumulative figure, so -5.2457
    against a $5 daily ceiling refused. The same inputs with a day-scoped
    daily measure do not refuse on the DAILY ceiling.
    """
    old_shared = max(D(0), -D("-5.2457"))
    assert old_shared >= D("5")                      # old behaviour: refused
    r = _refusal(daily=D("5"), cumulative=D("20"),
                 cum_realized=D("-5.2457"), today_realized=D("-0.6864"))
    assert r is None or not r.startswith("L5_DAILY_LOSS_LIMIT:"), r
