from decimal import Decimal as D
import pytest
from src.pmus_crypto_reward_economics import evaluate

def run(**kw):
    args = dict(pool=8, period_seconds=3600, remaining_seconds=1800,
                share_lower=".5", qualifying_fraction_lower=1,
                allocation_lower=1, accrued_lower=0,
                incremental_cost_upper=".5", scope_verified=True)
    args.update(kw)
    return evaluate(**args)

def test_positive():
    x = run()
    assert x["eligible"] and x["incremental_reward"] == D(2)
    assert x["net_lower"] == D("1.5")

def test_unknown_scope():
    assert not run(scope_verified=False)["eligible"]

def test_shared_pool_floor():
    assert run(allocation_lower=".25")["incremental_reward"] == 0

def test_cost():
    assert not run(incremental_cost_upper=2)["eligible"]

def test_accrued_not_double_counted():
    assert run(accrued_lower=7)["incremental_reward"] == 1

@pytest.mark.parametrize("kw", [
    {"pool": True}, {"pool": "NaN"}, {"remaining_seconds": 3601},
    {"share_lower": "1.1"}, {"allocation_lower": "-.1"},
    {"incremental_cost_upper": -1}, {"period_seconds": 0},
    {"payout_floor": ".5"}, {"accrued_lower": 9},
])
def test_invalid(kw):
    assert run(**kw)["reason"] == "invalid_inputs"


# ---- evidence levels and allocation hypotheses (owner 28 Nov) -----------
from src.pmus_crypto_reward_economics import (HYPOTHESES,
                                              allocation_scenarios,
                                              conservative, evaluate_row)


def test_every_result_carries_a_label_and_an_evidence_level():
    """The handoff labelled only the positive path; a refusal carried no
    label at all."""
    for x in (run(), run(scope_verified=False), run(pool="NaN"),
              run(incremental_cost_upper=2)):
        assert x["label"] == "modelled_not_paid"
        assert x["evidence"] in ("PUBLISHED", "INFERRED", "PAID")
    assert run()["evidence"] == "PAID"                 # scope_verified
    assert run(scope_verified=False)["evidence"] == "PUBLISHED"


def test_unknown_allocation_credits_nothing():
    x = run(scope_verified=False)
    assert x["reason"] == "unknown_pool_scope"
    assert x["incremental_reward"] == 0 and x["net_lower"] is None


@pytest.mark.parametrize("flag", [True, "yes", "per_window", 1, ""])
def test_only_a_named_hypothesis_selects_a_probe_credit(flag):
    x = run(scope_verified=False, probe_hypothesis=flag)
    assert x["reason"] == "unknown_pool_scope" and not x["eligible"]


def test_a_named_probe_hypothesis_is_credited_as_inferred_never_paid():
    x = run(scope_verified=False, probe_hypothesis="per_contract_window")
    assert x["eligible"] and x["incremental_reward"] == D(2)
    assert x["evidence"] == "INFERRED"
    assert x["credit_basis"] == "probe_hypothesis:per_contract_window"


def test_truthy_scope_flag_is_not_verification():
    assert run(scope_verified=1)["reason"] == "unknown_pool_scope"
    assert run(scope_verified="true")["reason"] == "unknown_pool_scope"


def test_paid_fraction_scales_the_credit_and_must_be_a_fraction():
    x = run(paid_fraction_lower="0.858")
    assert x["incremental_reward"] == D("1.716")
    assert run(paid_fraction_lower=0)["incremental_reward"] == 0
    assert run(paid_fraction_lower="1.2")["reason"] == "invalid_inputs"


def test_alt_hourly_row_under_each_hypothesis():
    """crypto_alt_updown_1h_20261110: pool 8, shared id across ETH/SOL/XRP.
    The three bases differ by 72x; none is confirmed."""
    sc = allocation_scenarios(pool=8, window_seconds=3600,
                              sharing_contracts=3)
    assert [s["hypothesis"] for s in sc] == list(HYPOTHESES)
    per_h = [s["usd_per_hour"] for s in sc]
    assert per_h[0] == D(8) * 3600 / (86400 * 3)        # $0.111 / hour
    assert per_h[1] == D(8) * 3600 / 86400              # $0.333 / hour
    assert per_h[2] == D(8)                             # $8.00 / hour
    assert abs(per_h[2] / per_h[0] - 72) < D("1e-9")
    assert all(s["evidence"] == "INFERRED" and
               s["pool_evidence"] == "PUBLISHED" for s in sc)


def test_alt_daily_row_has_two_distinct_bases():
    """crypto_alt_updown_1d: for a 24 h market the window and the day are
    the same length, so only the cross-asset split is open: $50 or $150."""
    sc = allocation_scenarios(pool=150, window_seconds=86400,
                              sharing_contracts=3)
    assert [s["usd_per_24h"] for s in sc] == [D(50), D(150), D(150)]


def test_btc_daily_row_is_the_same_under_every_basis():
    sc = allocation_scenarios(pool=400, window_seconds=86400,
                              sharing_contracts=1)
    assert {s["usd_per_24h"] for s in sc} == {D(400)}


@pytest.mark.parametrize("kw", [
    {"pool": 0}, {"window_seconds": 0}, {"window_seconds": 90000},
    {"sharing_contracts": 0}, {"sharing_contracts": True}, {"pool": "x"},
])
def test_scenarios_refuse_unreadable_rows(kw):
    args = dict(pool=8, window_seconds=3600, sharing_contracts=3)
    args.update(kw)
    with pytest.raises((ValueError, ArithmeticError)):
        allocation_scenarios(**args)


def row(**kw):
    args = dict(pool=150, window_seconds=86400, sharing_contracts=3,
                hypothesis="shared_program_et_day", remaining_seconds=86400,
                share_lower=".05", qualifying_fraction_lower=".8",
                accrued_lower=0, incremental_cost_upper=".5")
    args.update(kw)
    return evaluate_row(**args)


def test_row_without_a_hypothesis_fails_closed():
    for h in (None, "", "even_split", True):
        x = row(hypothesis=h)
        assert x["reason"] == "unknown_pool_scope"
        assert x["incremental_reward"] == 0 and x["evidence"] == "PUBLISHED"


def test_row_credit_by_hypothesis():
    shared = row()                       # 50 x .05 x .8 = 2.00
    own = row(hypothesis="per_contract_et_day")        # 150 x .04 = 6.00
    assert shared["incremental_reward"] == D(2)
    assert own["incremental_reward"] == D(6)
    assert shared["evidence"] == own["evidence"] == "INFERRED"


def test_only_the_paid_verified_hypothesis_is_reported_paid():
    a = row(paid_verified_hypothesis="shared_program_et_day")
    b = row(hypothesis="per_contract_et_day",
            paid_verified_hypothesis="shared_program_et_day")
    assert a["evidence"] == "PAID" and b["evidence"] == "INFERRED"


def test_hourly_window_remaining_is_clamped_to_the_period():
    x = row(pool=8, window_seconds=3600, hypothesis="per_contract_window",
            remaining_seconds=86400, share_lower=1,
            qualifying_fraction_lower=1, incremental_cost_upper=0)
    assert x["incremental_reward"] == D(8)


def test_two_contract_quote_is_below_the_floor_under_every_basis():
    """2 contracts against a 2,000 target: a share of a tenth of a percent
    projects cents, under the $1 floor on every allocation basis."""
    rs = [row(hypothesis=h, share_lower="0.001") for h in HYPOTHESES]
    assert all(r["incremental_reward"] == 0 for r in rs)
    assert all(r["projected_before_floor"] < 1 for r in rs)
    assert not conservative(rs)["eligible"]


def test_decision_never_rests_on_the_optimistic_hypothesis():
    rs = [row(hypothesis=h, share_lower=".02", incremental_cost_upper="1.5")
          for h in HYPOTHESES]
    # shared: 50 x .02 x .8 = 0.80 -> under the floor; own pool: 2.40
    assert [r["eligible"] for r in rs] == [False, True, True]
    c = conservative(rs)
    assert not c["eligible"]
    assert c["binding_hypothesis"] == "shared_program_et_day"
    assert c["net_lower"] == D("-1.5") and c["evidence"] == "INFERRED"


def test_conservative_of_nothing_or_of_a_refusal_is_not_eligible():
    assert not conservative([])["eligible"]
    assert not conservative([row(hypothesis=None)])["eligible"]
    assert conservative([row(hypothesis=None), row()])["net_lower"] is None
