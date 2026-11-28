"""Capital efficiency must not read a stranded lock off a tape that ended.

The defect this module exists to avoid: taking every lock duration from the
campaign log. A paired market's log records the moment capital is released;
a stranded market's does not, because nothing is released until the venue
settles. Reading both the same way makes stranded capital look ~25% cheaper
than it is, and stranded capital is the entire finding.
"""
from __future__ import annotations

import os

import pytest

from src import l5_capital_efficiency as ce

_SRC = open(ce.__file__).read()
_COHORT = "analysis/l5/l5_r5f_cohort.json"
_have = os.path.exists(_COHORT) and os.path.isdir("../r5f_logs")


def _cohort(**over):
    m = {"slug": "m", "cost_basis_usd": "36.0",
         "settled_utc": "2026-09-08T01:00:00Z",
         "directional_residual_units": "0", "matched_pairs": "10",
         "matched_contribution_usd": "1.0", "residual_contribution_usd": "0",
         "fills": 4, "qty_reconciliation": {"reconciles": True}}
    m.update(over)
    return {"markets": [m]}


def _fills(first="2026-09-08T00:00:00Z", closed="2026-09-08T00:30:00Z",
           last="2026-09-08T00:30:00Z"):
    from src.l5_sports_clock_study import iso_epoch
    return {"m": {"first_fill": iso_epoch(first),
                  "closed": iso_epoch(closed) if closed else None,
                  "last": iso_epoch(last)}}


# ---------------------------------------------------------------- anchoring
def test_a_paired_lock_ends_when_the_residual_reaches_zero():
    r = ce.lock_rows(_cohort(), _fills())[0]
    assert r["paired"] is True
    assert r["lock_s"] == 1800.0                       # 30 minutes
    assert r["lock_anchor"].endswith("(log)")
    assert r["dollar_hours"] == pytest.approx(36.0 * 0.5)


def test_a_stranded_lock_ends_at_venue_settlement_not_at_the_last_log_row():
    """The tape stops at 00:30 but the venue settles at 01:00."""
    r = ce.lock_rows(_cohort(directional_residual_units="5"),
                     _fills(closed=None))[0]
    assert r["paired"] is False
    assert r["log_lock_s"] == 1800.0                   # what the tape saw
    assert r["lock_s"] == 3600.0                       # what actually happened
    assert r["lock_anchor"] == "venue settlement"
    assert r["dollar_hours"] == pytest.approx(36.0)    # twice the log answer


def test_paired_needs_both_a_flat_log_and_zero_venue_residual():
    """One market's ledger read zero residual while the venue traded more."""
    # log says flat, venue says residual -> NOT paired
    r = ce.lock_rows(_cohort(directional_residual_units="4.34"), _fills())[0]
    assert r["paired"] is False
    # venue says zero but the log never went flat -> also not paired
    r = ce.lock_rows(_cohort(), _fills(closed=None))[0]
    assert r["paired"] is False


def test_a_market_with_no_first_fill_is_absent_not_zero():
    assert ce.lock_rows(_cohort(), {}) == []


# --------------------------------------------------------------- the rates
def test_stranded_share_is_of_dollar_hours_not_of_markets():
    rows = [{"slug": "a", "paired": True, "dollar_hours": 1.0, "lock_s": 10,
             "reconciles": True, "matched_pairs": 1.0,
             "matched_contribution_usd": 1.0, "residual_contribution_usd": 0.0,
             "cost_basis_usd": 1.0},
            {"slug": "b", "paired": False, "dollar_hours": 9.0, "lock_s": 900,
             "reconciles": True, "matched_pairs": 0.0,
             "matched_contribution_usd": 0.0,
             "residual_contribution_usd": -5.0, "cost_basis_usd": 1.0}]
    c = ce.efficiency(rows)["capital"]
    assert c["stranded_share_of_markets"] == 0.5
    assert c["stranded_share_of_dollar_hours"] == 0.9


def test_money_rates_use_only_reconciling_markets():
    rows = [{"slug": "ok", "paired": True, "dollar_hours": 2.0, "lock_s": 10,
             "reconciles": True, "matched_pairs": 10.0,
             "matched_contribution_usd": 1.0, "residual_contribution_usd": 0.0,
             "cost_basis_usd": 1.0},
            {"slug": "bad", "paired": True, "dollar_hours": 2.0, "lock_s": 10,
             "reconciles": False, "matched_pairs": 99.0,
             "matched_contribution_usd": -50.0,
             "residual_contribution_usd": 0.0, "cost_basis_usd": 1.0}]
    p = ce.efficiency(rows)["per_dollar_hour"]
    assert p["markets"] == 1
    assert p["dollar_hours"] == 2.0
    assert p["realized_usd_pre_credit"] == 1.0        # the -50 is excluded
    assert p["completed_pairs_per_dollar_hour"] == 5.0
    # but the excluded market's capital still counts as capital
    assert ce.efficiency(rows)["capital"]["total_dollar_hours"] == 4.0


def test_credits_are_reported_separately_and_never_folded_into_pre_credit():
    rows = [{"slug": "a", "paired": True, "dollar_hours": 1.0, "lock_s": 10,
             "reconciles": True, "matched_pairs": 1.0,
             "matched_contribution_usd": 1.0, "residual_contribution_usd": 0.0,
             "cost_basis_usd": 1.0}]
    p = ce.efficiency(rows, fills_in_cohort=ce.REBATE_FILLS)["per_dollar_hour"]
    assert p["realized_usd_pre_credit"] == 1.0
    assert p["confirmed_rebate_usd_attributable"] == pytest.approx(2.13)
    assert (p["realized_usd_per_dollar_hour_with_confirmed_credits"]
            > p["realized_usd_per_dollar_hour_pre_credit"])
    assert "never at accrued face value" in p["credits_note"]


def test_turnover_is_omitted_without_a_span_rather_than_guessed():
    assert "turnover" not in ce.efficiency([])


# -------------------------------------------------------------- the framing
def test_it_does_not_argue_for_abandoning_residual():
    o = ce.efficiency([])["opportunity_cost"]
    assert "HOLD policy is not in question" in o[
        "not_an_argument_to_abandon_residual"]
    assert "should be dumped" in o["not_an_argument_to_abandon_residual"]


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "post(",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_touch_the_frozen_completion_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert frozen not in _SRC, frozen


# ---------------------------------------------------------- against real data
@pytest.mark.skipif(not _have, reason="cohort or logs not present")
def test_stranded_capital_dominates_on_the_real_cohort():
    rep = ce.report()
    c = rep["capital"]
    assert c["stranded_share_of_dollar_hours"] > c["stranded_share_of_markets"]
    assert c["median_lock_s_stranded"] > c["median_lock_s_paired"]


@pytest.mark.skipif(not _have, reason="cohort or logs not present")
def test_every_stranded_market_is_anchored_to_settlement():
    for r in ce.report()["markets"]:
        assert r["lock_anchor"] == ("venue settlement" if not r["paired"]
                                    else "residual reached zero (log)")
        assert r["lock_s"] >= r["log_lock_s"]


@pytest.mark.skipif(not _have, reason="cohort or logs not present")
def test_mean_capital_in_use_is_far_below_the_bankroll():
    """The number that says bankroll is not the binding constraint."""
    t = ce.report()["turnover"]
    assert t["mean_capital_in_use_usd"] < 10.0
