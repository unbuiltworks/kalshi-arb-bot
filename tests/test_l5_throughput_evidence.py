"""L5's own 70,435 venue trades, pinned as the source of the gate's priors.

THE PRIORITY CORRECTION THIS ENCODES. The reopening gate shipped with a
single-leg outcome of -$0.2440, taken from `hold $` in
QUEUE_DEPLETION_FINDINGS.md -- recorded books under PROVEN fills, where a
leg counts filled only when the opposite touch CROSSED our price. Every leg
in that sample therefore filled because the market moved against it, so the
constant measured the sampling rule rather than the market. At 6.4x the pair
edge it refused every reopening at the deployed depth, turning a gate meant
to PRICE repeated rounds into one that forbade them.

L5's actual fills say an unmatched leg is worth about +$0.037, which is also
what this module's own EV identity predicts for a leg bought at a fair
price: EV(hold) = EV(complete) exactly, so only the WORST CASE differs.

These tests pin the measured figures the priors now rest on, and -- more
importantly -- pin that the RISK side was not relaxed to get throughput.
Capacity still refuses what cannot be afforded.

NOT OUR MONEY. Every figure here is L5's. Our confirmed settled realized net
is -$15.6109 all-time and nothing below changes that.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_unmatched_disposition as UD

#: Measured over the 186 btc15m windows in `data/l5/raw/activity.jsonl`
#: where the redemption equals one side's quantity exactly, so the OUTCOME
#: is directly determinable and nothing is attributed. See
#: analysis/l5/issue206/L5_THROUGHPUT_MECHANICS.md.
L5_MATCHED_CONTRACTS = 121836.9
L5_MATCHED_BASIS = D("0.9641")
L5_PAIR_PNL_PER_CONTRACT = D("0.0359")
L5_RESIDUAL_CONTRACTS = 20069.1
L5_RESIDUAL_BASIS = D("0.3489")
L5_RESIDUAL_WIN_RATE = D("0.386")
L5_RESIDUAL_PNL_PER_CONTRACT = D("0.0367")

#: Throughput, from the full 527-window btc15m sample.
L5_NET_PER_WINDOW = D("68.7")
L5_SPEND_PER_WINDOW = D("1065")
L5_TRADES_PER_WINDOW_MEAN = 31.8
L5_TRADES_PER_WINDOW_P90 = 135
L5_WINDOWS_POSITIVE = D("0.687")
#: net per window by throughput bucket -- the correlation that matters
L5_NET_BY_THROUGHPUT = {(0, 10): D("0.51"), (10, 50): D("-1.98"),
                        (50, 150): D("61.06"), (150, 10 ** 9): D("146.22")}


def base(**over):
    out = dict(market="x", rounds_used=1, worst_leg_own_px=D("0.4900"),
               qty=D("1"), bid=D("0.48"), ask=D("0.49"), tick=D("0.01"),
               depth_ticks=2, now_s=0.0, window_end_s=900.0,
               exposure_headroom_usd=D("1.00"))
    out.update(over)
    return out


# ================================ 1. THE PRIORS ARE THE MEASURED ONES

#: The repaired union supersedes the small-sample figures above: 4,720
#: COMPLETE windows, 1,240,600 residual contracts, basis 0.3205.
L5_REPAIRED_RESIDUAL_PER_CONTRACT = D("-0.0236")
L5_REPAIRED_PAIR_PER_CONTRACT = D("0.0350")


def test_the_single_leg_outcome_is_the_REPAIRED_measurement():
    assert UD.REOPEN_SINGLE_OUTCOME_USD == L5_REPAIRED_RESIDUAL_PER_CONTRACT
    assert UD.REOPEN_SINGLE_OUTCOME_USD > D("-0.2440"), \
        "far above the superseded recorded-book artefact"
    assert UD.REOPEN_SINGLE_OUTCOME_USD < D("0"), \
        "and a LOSS, which the small sample had hidden"


def test_the_matched_edge_is_stable_across_every_cut_ever_measured():
    """Which is why the repair is believed rather than its convenient half.

    +0.0356 (501 windows), +0.0359 (186 determinable), +0.0350 (4,720
    COMPLETE after repair).
    """
    for v in (L5_PAIR_PNL_PER_CONTRACT, L5_REPAIRED_PAIR_PER_CONTRACT):
        assert D("0.034") < v < D("0.036"), v


def test_the_pairing_rate_is_our_own_orders_not_the_touch_crossing_rule():
    assert UD.REOPEN_P_PAIR == D("0.727")
    assert UD.REOPEN_P_SINGLE == D("0.273")
    assert UD.REOPEN_P_PAIR + UD.REOPEN_P_SINGLE == D("1.000")


def test_L5s_residual_beat_its_own_basis():
    """38.6% won against a 34.9% basis -- a 3.7-point edge, not a rout."""
    assert L5_RESIDUAL_WIN_RATE > L5_RESIDUAL_BASIS
    implied = L5_RESIDUAL_WIN_RATE - L5_RESIDUAL_BASIS
    assert D("0.03") < implied < D("0.05")


def test_L5s_pair_edge_is_about_three_and_a_half_cents():
    assert D("0.03") < L5_PAIR_PNL_PER_CONTRACT < D("0.04")
    assert abs((D(1) - L5_MATCHED_BASIS) - L5_PAIR_PNL_PER_CONTRACT) \
        < D("0.0005"), "a matched pair redeems $1, so edge = 1 - basis"


# ============================= 2. THROUGHPUT IS WHAT PAYS, MEASURED

def test_profit_rises_with_throughput_not_with_caution():
    lo = L5_NET_BY_THROUGHPUT[(0, 10)]
    hi = L5_NET_BY_THROUGHPUT[(150, 10 ** 9)]
    assert hi > lo * 100, (lo, hi)


def test_low_throughput_windows_earn_essentially_nothing():
    assert L5_NET_BY_THROUGHPUT[(0, 10)] < D("1.00")


def test_L5_does_not_win_every_window():
    """Aggregate, not per-window, is the objective."""
    assert D("0.6") < L5_WINDOWS_POSITIVE < D("0.75")


def test_our_authorized_scale_cannot_reach_the_target_on_this_edge():
    """THE DOMINANT CONSTRAINT, and it is not the controller.

    L5 realises ~6.4% of turnover. $480/day needs ~$7,500/day of turnover,
    about $78 a window. At the authorized $1 the same edge yields cents.
    """
    rate = L5_NET_PER_WINDOW / L5_SPEND_PER_WINDOW
    assert D("0.05") < rate < D("0.08")
    per_window_needed = D("480") / D("96")
    assert (per_window_needed / rate) > D("70"), \
        "turnover per window needed to reach the target"


# ====================== 3. THROUGHPUT WAS UNBLOCKED, RISK WAS NOT

def test_a_repeated_round_is_now_permitted_at_the_deployed_depth():
    r = UD.reopen_decision(**base())
    assert r["reopen"] is True
    assert D(r["expectancy_usd"]) == D("0.0299")


def test_the_old_constant_still_forbids_it_if_passed_back_in():
    """One parameter was the whole blanket restriction."""
    r = UD.reopen_decision(**base(single_outcome_usd=D("-0.2440")))
    assert r["reopen"] is False
    assert r["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"


@pytest.mark.parametrize("kw,why", [
    ({"exposure_headroom_usd": D("0.10")},
     "EXPOSURE_HEADROOM_BELOW_WORST_CASE"),
    ({"outstanding_worst_case_usd": D("0.80")},
     "EXPOSURE_HEADROOM_BELOW_WORST_CASE"),
    ({"daily_loss_room_usd": D("0.10")},
     "DAILY_LOSS_ROOM_BELOW_WORST_CASE"),
    ({"now_s": 800.0}, "INSUFFICIENT_RUNWAY_FOR_A_ROUND"),
    ({"bid": None}, "BOOK_NOT_TWO_SIDED"),
])
def test_every_hard_bound_still_refuses(kw, why):
    """Expectancy decides whether a round is WORTH taking; capacity decides
    whether we can afford being wrong. Only the first was re-sourced."""
    r = UD.reopen_decision(**base(**kw))
    assert r["reopen"] is False, (kw, r.get("reason"))
    assert r["reason"] == why


def test_the_worst_case_is_still_the_dearer_leg():
    r = UD.reopen_decision(**base())
    assert D(r["worst_case_usd"]) == D("0.4900")


def test_locked_profit_still_finances_nothing():
    a = UD.reopen_decision(**base(locked_pair_pnl_usd=D("0")))
    b = UD.reopen_decision(**base(locked_pair_pnl_usd=D("500")))
    assert D(a["expectancy_usd"]) == D(b["expectancy_usd"])
    assert a["locked_pnl_is_not_a_loss_reserve"] is True


def test_the_preserve_and_safety_work_is_untouched():
    """The corrected safeguards must survive the throughput change."""
    assert UD.PRESERVE in UD.ACTIONS
    assert UD.COMPLETE_TAKER in UD.CROSSING
    from src import l5_two_sided_probe as TSP
    assert TSP.REQUOTE_MIN_REST_S == 14.0
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E.FaithfulExecutor.probe_reopen_decision)
    assert "CLAIMS_UNREADABLE_NO_UNVERIFIED_OPENING" in src
    assert "verified_reduces_not_opens" in src
