"""PMUS_RN1_REWARD_MIN_RULE=threshold (issue #251): expected payable reward
is credited only when our projected total for the PROGRAMME AND PERIOD
reaches the $1 minimum; the paid fraction is a calibration multiplier only;
unknown paid fraction or pool: nothing credited."""
import time

import pytest

from src import pmus_sports_two_sided as M
from tests.test_pmus_reward_qualification_scope import A, B, _plan, e  # noqa

LIVE = {"programId": "setka_day_of_20261120", "period": "day_of",
        "start": "2026-11-20T00:00:00Z"}
OTHER = {"programId": "atp_day_of_20261120", "period": "day_of",
         "start": "2026-11-20T00:00:00Z"}
BY = {"PAID": {"usd": "24.09"}, "SKIPPED": {"usd": "4.00"}}


@pytest.fixture(autouse=True)
def _rule(monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "threshold")
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "contract_period")
    monkeypatch.setattr(M, "_PAID_FRACTION", {"v": None, "t": 0.0})
    yield


def _per(e, monkeypatch, pers):
    monkeypatch.setattr(e, "rn1_reward", lambda slug: pers.get(slug))


def test_below_the_programme_minimum_pays_nothing(e, monkeypatch):
    M.rn1_set_paid_fraction(BY)
    _per(e, monkeypatch, {A: dict(LIVE), B: None})
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.10)    # 0.30 over 3 h
    assert not q["qualifies"] and q["rule"] == "threshold"
    monkeypatch.setattr(e, "_rn1_reward_estimate", lambda *a, **k: None)
    inc = e.rn1_incentive(A)
    assert inc["why"] == "below_minimum_payout" and inc["ticks"] == 0


def test_other_contracts_of_the_same_programme_count_toward_it(
        e, monkeypatch):
    """Score share is per contract, but the minimum is per programme and
    period: B (same programme, another contract) lifts A over $1."""
    M.rn1_set_paid_fraction(BY)
    _per(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    _plan(e, B, 0.25)                                  # B: 0.25/h planned
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.10)    # (0.25+0.10) x 3
    assert q["scope"] == "program_period"
    assert q["projected_usd"] == pytest.approx(1.05, abs=1e-3)
    assert q["qualifies"]


def test_another_programme_never_counts(e, monkeypatch):
    M.rn1_set_paid_fraction(BY)
    _per(e, monkeypatch, {A: dict(LIVE), B: dict(OTHER)})
    _plan(e, B, 5.0)
    assert not e.rn1_reward_qualifies(A, extra_usd_h=0.10)["qualifies"]


def test_unknown_paid_fraction_credits_nothing(e, monkeypatch):
    _per(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    _plan(e, B, 5.0)
    assert not e.rn1_reward_qualifies(A, extra_usd_h=1.0)["qualifies"]
    monkeypatch.setattr(e, "_rn1_reward_estimate", lambda *a, **k: None)
    assert e.rn1_incentive(A)["why"] == "paid_fraction_unavailable"


def test_no_period_is_no_pool(e, monkeypatch):
    M.rn1_set_paid_fraction(BY)
    _per(e, monkeypatch, {A: {"programId": "x"}, B: None})
    assert not e.rn1_reward_qualifies(A, extra_usd_h=100.0)["qualifies"]


def test_paid_fraction_is_a_multiplier_not_a_waiver(monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_CALIBRATION", False)
    M.rn1_set_paid_fraction(BY)
    base = max(0.0, min(1.0, M.RANK_REWARD_HAIRCUT))
    assert M.rn1_reward_mult(A, LIVE) == pytest.approx(base * 24.09 / 28.09)


def test_accrual_in_the_programme_counts_once(e, monkeypatch):
    M.rn1_set_paid_fraction(BY)
    _per(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    pk = M.rn1_pool_key(LIVE)
    e.b.m(A)["reward_theo_pool"] = {pk: 1.0, M.rn1_pool_key(LIVE, A): 1.0}
    e.b.m(B)["reward_theo_pool"] = {pk: 2.0, M.rn1_pool_key(LIVE, B): 2.0}
    q = e.rn1_reward_qualifies(A)
    assert q["accrued_pool_usd"] == pytest.approx(3.0 * 0.4)  # A+B once
