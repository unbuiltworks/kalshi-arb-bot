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
