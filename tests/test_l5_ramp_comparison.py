"""OWNER 5570456323: early-ramp comparison is built from the canonical days and the scoreboard."""
from __future__ import annotations

from src import l5_ramp_comparison as rc


def test_l5_days_and_aggregates():
    days = rc.l5_days()
    keys = sorted(days)
    assert keys[0] == "2026-03-19" and len(keys) == 22
    early = rc.aggregate_days(days, keys[:7], "early")
    assert early["days"] == 7 and early["turnover_per_day_usd"] > 0
    assert 0.5 < early["pair_completion_share"] < 1.0
    assert abs(early["pair_completion_share"] + early["residual_share_of_bought"] - 1.0) < 1e-6
    mature = rc.aggregate_days(days, [k for k in keys if k >= "2026-04-06"], "mature")
    assert mature["turnover_per_day_usd"] > early["turnover_per_day_usd"]


def test_ours_by_cohort_and_reproducibility():
    us = rc.ours()
    assert set(us) >= {"R3L", "R4"}
    # R4 complete: 3 markets (market 3 aec-atp-petbru-alecan added $1.9912 of
    # turnover on 2026-09-07)
    assert us["R4"]["markets"] == 3 and us["R4"]["maker_taker_mix"].startswith("100% maker")
    assert abs(sum(us["R4"]["turnover_by_calendar_day_usd"].values()) - 20.61) < 0.01
    out = rc.study()
    r = out["reproducibility"]
    assert abs(r["our_at_size_calendar_day_usd"] - 55.25) < 0.05
    assert r["calendar_day_ratio_ours_at_size_over_l5_early"] < 0.2
    assert len(r["not_reproducible_yet"]) == 3
