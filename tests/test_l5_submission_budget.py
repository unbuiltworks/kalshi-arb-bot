"""The owner's submission budget, independent of strategy selection.

WHY IT EXISTS. The 1215z window placed TWO orders under a one-window
authorization and both were LEGITIMATE: the two-sided engine takes a
per-(market, round, leg) accumulation claim, so a round is two claims and
`L5_ACC_MAX_ROUNDS` permits eight rounds -- sixteen submissions in one
window, bounded only by capital. `submission_sid` bounds the OTHER strategy
to one, and `max_live_orders` bounds concurrency, not the total. So "this
authorization may place N orders" was a property of the written mandate and
of no gate at all.

Counted from the durable LOCK FILES across BOTH claim namespaces, so the
budget cannot be sidestepped by which strategy chose the order.
"""
import pathlib

import pytest

from src import l5_live_authorization as AUTH

EXP = "BUDGET-TEST"
MKT = "cpc-btc-updown-15m-2026-10-20-1215z"


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    path = str(tmp_path / "auth.jsonl")
    monkeypatch.setenv(AUTH.LEDGER_ENV, path)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, EXP)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    return path


def test_UNSET_does_not_bind_which_preserves_the_pair_that_worked(ledger):
    assert AUTH.max_submissions() is None
    assert AUTH.submission_budget_refusal(ledger, EXP) is None
    # the real 1215z shape: two per-leg claims, both permitted
    AUTH.claim_accumulation_leg(MKT, 3, "DOWN", {})
    AUTH.claim_accumulation_leg(MKT, 3, "UP", {})
    assert len(AUTH.submissions_spent(ledger, EXP)) == 2


def test_a_budget_of_TWO_permits_exactly_the_1215z_pair_and_no_more(
        ledger, monkeypatch):
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "2")
    AUTH.claim_accumulation_leg(MKT, 3, "DOWN", {})
    AUTH.claim_accumulation_leg(MKT, 3, "UP", {})
    assert len(AUTH.submissions_spent(ledger, EXP)) == 2
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg(MKT, 4, "DOWN", {})
    assert e.value.reason == "SUBMISSION_BUDGET_EXHAUSTED"
    assert e.value.detail["spent"] == 2
    assert e.value.detail["max_submissions"] == 2


def test_the_budget_binds_ACROSS_strategies_not_within_one(ledger,
                                                           monkeypatch):
    """THE POINT. One accumulation claim plus one single-submission claim is
    two submissions against one authorization, and a budget that counted only
    its own namespace would permit both plus more."""
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "2")
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {})
    AUTH.claim_submission(MKT, {})
    assert len(AUTH.submissions_spent(ledger, EXP)) == 2
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg(MKT, 0, "UP", {})
    assert e.value.reason == "SUBMISSION_BUDGET_EXHAUSTED"


def test_a_budget_of_ONE_stops_the_second_leg_of_a_pair(ledger, monkeypatch):
    """A deliberate consequence worth seeing: a one-submission budget cannot
    complete a pair, so a pair mandate needs at least two."""
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "1")
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {})
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg(MKT, 0, "UP", {})
    assert e.value.reason == "SUBMISSION_BUDGET_EXHAUSTED"


def test_a_budget_of_ZERO_refuses_everything(ledger, monkeypatch):
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "0")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission(MKT, {})
    assert e.value.reason == "SUBMISSION_BUDGET_EXHAUSTED"


def test_an_UNREADABLE_budget_is_never_unlimited(ledger, monkeypatch):
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "lots")
    assert AUTH.max_submissions() == 0
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission(MKT, {})
    assert e.value.reason == "SUBMISSION_BUDGET_EXHAUSTED"


def test_the_count_SURVIVES_A_RESTART_because_it_reads_the_volume(
        ledger, monkeypatch):
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "2")
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {})
    AUTH.claim_accumulation_leg(MKT, 0, "UP", {})
    # a brand new process reads the same lock files
    assert len(AUTH.submissions_spent(ledger, EXP)) == 2
    assert AUTH.submission_budget_refusal(ledger, EXP)["reason"] == (
        "SUBMISSION_BUDGET_EXHAUSTED")


def test_a_DIFFERENT_experiment_has_its_own_budget(ledger, monkeypatch):
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "1")
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {})
    assert AUTH.submission_budget_refusal(ledger, EXP) is not None
    assert AUTH.submission_budget_refusal(ledger, "OTHER-EXP") is None


def test_the_budget_counts_ATTEMPTS_of_the_same_leg_too(ledger, monkeypatch):
    """A replaced leg is another submission and another order at the venue."""
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "2")
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {}, attempt=0)
    AUTH.claim_accumulation_leg(MKT, 0, "DOWN", {}, attempt=1)
    assert len(AUTH.submissions_spent(ledger, EXP)) == 2
    with pytest.raises(AUTH.Refused):
        AUTH.claim_accumulation_leg(MKT, 0, "UP", {})
