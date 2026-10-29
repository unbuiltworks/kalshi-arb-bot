"""The probe package must stay disarmed, must not oversell itself, and must
keep its own success criterion honest."""
from decimal import Decimal as D

from src import kalshi_reward_probe_package as P


def test_it_is_disarmed_and_needs_an_explicit_go():
    assert P.ARMED is False
    assert P.ORDER_ENTRY is False
    assert P.REQUIRES_OWNER_GO is True


def test_its_purpose_is_a_measurement_not_a_profit():
    assert "measurement purchase" in P.PURPOSE
    assert "must not be justified or judged on expected profit" in P.PURPOSE


def test_a_credited_zero_counts_as_success():
    """A $0.00 credit proves the full-book denominator and closes the venue
    at this bankroll. That is a result, not a failure."""
    s = P.SUCCESS_IS_A_NUMBER_NOT_A_PROFIT
    assert "including $0.00" in s
    assert "closes Kalshi at this bankroll" in s


def test_it_refuses_to_claim_positive_expectancy():
    n = P.NOT_YET_A_POSITIVE_EXPECTANCY_CLAIM
    assert "does NOT assert defensibly positive total expectancy" in n
    assert "must not be escalated as though it did" in n
    assert "share of score" in n and "residual economics" in n


def test_the_cheapness_rests_on_the_fee_reward_asymmetry():
    """Reward is earned resting; fee is charged on fill. The probe can
    succeed having traded nothing at all."""
    d = P.__doc__
    assert "REWARD IS EARNED ON RESTING, FEE ONLY ON FILL" in d
    assert "neither fills     -> $0.00 cost" in d


def test_demo_is_recorded_as_tested_and_closed():
    d = P.__doc__
    assert "143 programs, ZERO active" in d
    assert "2026-07-30" in d
    assert "cannot credit a reward" in d


def test_the_bounds_are_small_and_named():
    b = P.BOUNDS
    assert b["max_quote_notional_usd"] == D("30.00")
    assert b["max_trading_loss_usd"] == D("1.50")
    assert b["max_periods"] == 1
    assert b["post_only"] is True
    assert b["two_sided"] is True
    assert b["no_requote"] is True
    assert b["hold_residual_to_settlement"] is True


def test_it_forbids_taking_requoting_and_touching_pmus():
    j = " ".join(P.WHAT_IT_MUST_NOT_DO)
    assert "no taker orders" in j
    assert "no re-quote" in j
    assert "PMUS spring probe's arming keys" in j
    assert "daily stop" in j


def test_targets_are_the_measured_best_legs():
    assert P.TARGETS == ("KXNATGAS15M", "KXCOPPER15M")


def test_arming_requires_a_creditable_account_first():
    """An account that cannot show its own reward ledger makes the whole
    probe pointless -- check before, not after, the order."""
    j = " ".join(P.PRECONDITIONS_FOR_ARMING)
    assert "read access to its own reward ledger" in j
    assert "BEFORE any order" in j
    assert "PMUS remains stopped" in j


def test_credited_reward_is_a_separate_status_from_realized():
    assert "fourth, separate status" in P.ACCOUNTING
    assert "Never sum a credited reward into a realised trading P&L" in P.ACCOUNTING


# ------------------------------------------------------------- preflight
def test_the_reward_path_is_a_balance_residue_because_no_ledger_exists():
    r = P.PREFLIGHT["reward_credit_observation_path"]
    assert "NO reward-ledger endpoint" in r["method"]
    assert "31 plausible names return 404" in r["method"]
    assert ("credited_reward = balance_delta - realised_fill_cash "
            "- settlement_cash") == r["formula"]
    assert "OTHERWISE IDLE account" in r["requires"]


def test_a_same_hour_zero_must_not_be_reported_as_a_credited_zero():
    """paid_out lags 12-48h. Reading $0.00 at settlement measures the lag,
    not the reward, and would falsely close the venue."""
    t = P.PREFLIGHT["reward_credit_observation_path"]["timing_caveat"]
    assert "at least 48h later" in t
    assert "means NOTHING" in t
    assert "must not be reported as a credited zero" in t
    assert "48h" in P.PREFLIGHT["pass_fail"]["ALSO_PASS"]


def test_the_headline_downside_is_not_presented_as_the_real_risk():
    """-$0.60 is the both-fill case. The one-sided fill is the real one."""
    m = P.PREFLIGHT["max_exposure"]
    assert m["worst_case_both_fill_usd"] == "-0.60 at pair cost 1.02"
    assert "-30.00 absolute worst case" in m["worst_case_one_side_fills_usd"]
    assert "not the -$0.60 headline" in m["worst_case_one_side_fills_usd"]
    assert "cannot prevent the one-sided-fill case" in m["stop_note"]
    assert "Sizing is the real control" in " ".join(m["stop_note"].split())


def test_it_does_not_chase_a_complete_on_a_one_sided_fill():
    """Chasing is what produced the PMUS residual."""
    c = P.PREFLIGHT["cancel_and_deadman"]["on_one_side_filled"]
    assert "HOLD the filled leg to settlement" in " ".join(c.split())
    assert "do NOT chase a complete" in " ".join(c.split())
    assert "-6.458%/$" in c


def test_the_quote_deliberately_does_not_improve_the_touch():
    q = P.PREFLIGHT["quote_construction"]
    assert q["post_only"] is True if isinstance(q.get("post_only"), bool) else True
    assert "NOT improving" in q["price"]
    assert "wants LOW" in " ".join(q["price"].split())
    assert q["requote"] == "NEVER -- one placement, then observe"


def test_a_contaminated_window_is_void_not_interpreted():
    f = P.PREFLIGHT["pass_fail"]
    assert "Void the run" in f["FAIL_VOID"]
    assert "do not interpret a contaminated residue" in f["FAIL_VOID"]


def test_one_period_is_not_a_licence_to_scale():
    w = P.PREFLIGHT["what_it_does_not_establish"]
    assert "one observation" in w
    assert "Do not scale on a single credited number" in " ".join(w.split())


def test_the_preflight_is_recorded_as_not_yet_run():
    b = P.PREFLIGHT_BLOCKER
    assert b.startswith("NOT YET RUN")
    assert "no Kalshi credentials" in b
    assert "would pull secrets into the transcript" in b
