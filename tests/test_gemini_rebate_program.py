"""The published maker-rebate rules, and the arithmetic they force.

Gemini publishes the formula, the eligible price band and the round-down.
Applying them to this programme's actual size produces a number that
invalidates part of an earlier proposal, so these tests pin it exactly.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import rebate_program as RP


# ================================================== the published formula

def test_the_formula_is_the_published_one():
    """rebate_rate x taker_rate x C x P x (1 - P), crypto rate 0.30,
    taker 0.07 -- so 0.021 x C x P x (1 - P)."""
    assert RP.CRYPTO_REBATE_RATE == D("0.30")
    assert RP.TAKER_RATE == D("0.07")
    got = RP.accrual("1", "0.50")
    # 0.021 * 1 * 0.5 * 0.5 = 0.00525
    assert D(got["accrued_usd"]) == D("0.00525")
    assert got["formula"] == "rebate_rate * taker_rate * C * P * (1 - P)"
    assert "developer.gemini.com" in got["source"]


def test_CONTINUOUS_8s_ACTUAL_FILL_IS_WORTH_FOUR_HUNDREDTHS_OF_A_CENT():
    """C8B filled YES 0.01 @ 0.31 as maker. Under the published rules that
    fill accrues $0.0000449, and the day it sat in would need $0.01 of
    accrual before any cash moved."""
    got = RP.accrual("0.01", "0.31")
    assert D(got["accrued_usd"]) == D("0.021") * D("0.01") * D("0.31") * D("0.69")
    assert D(got["accrued_usd"]) < D("0.0001")


# ==================================================== the eligible band

def test_a_fill_outside_the_price_band_earns_NOTHING():
    """Not 'less' -- nothing. C4's two fills at 0.92 and 0.14 are both
    outside $0.20-$0.80."""
    for p in ("0.92", "0.14", "0.19", "0.81"):
        got = RP.accrual("0.01", p)
        assert got["accrued_usd"] == "0", p
        assert got["eligibility"]["eligible"] is False


@pytest.mark.parametrize("p", ["0.20", "0.50", "0.80"])
def test_the_band_edges_are_inclusive(p):
    assert RP.eligible(p)["eligible"] is True


def test_band_coverage_over_this_programmes_own_five_fills():
    """Three of five. The L5 OPEN rung follows the bid and the habitat census
    puts 19.9% of cycles with a YES bid at or below 0.10, where the rebate
    does not reach at all."""
    got = RP.band_coverage(["0.92", "0.14", "0.46", "0.21", "0.31"])
    assert got["n"] == 5 and got["eligible"] == 3 and got["outside_band"] == 2
    assert got["eligible_share"] == 0.6


def test_an_unreadable_price_is_neither_eligible_nor_ineligible():
    got = RP.band_coverage([None, "abc", "0.50"])
    assert got["unreadable"] == 2 and got["eligible"] == 1
    assert got["outside_band"] == 0


# ============================================ the round-down that decides it

def test_TEN_FILLS_OF_0_01_ARE_PAID_NOTHING():
    """THE NUMBER THAT INVALIDATES PART OF THE EARLIER PROPOSAL. A run that
    produces ten 0.01 maker fills at realistic prices accrues about $0.0005
    and is paid $0.00. Not 'probably nothing' -- arithmetically nothing."""
    fills = [{"contracts": "0.01", "price": "0.31"}] * 10
    got = RP.daily_accrual(fills)
    assert got["n_fills"] == 10 and got["n_eligible"] == 10
    assert got["pays_nothing"] is True
    assert got["would_pay_usd"] == "0.00"
    assert D(got["accrued_usd"]) < D("0.001")
    assert D(got["shortfall_to_one_cent_usd"]) > D("0.009")


def test_how_much_volume_ONE_CENT_needs():
    got = RP.contracts_for_one_cent("0.50")
    assert got["computable"] is True
    # 0.01 / (0.021 * 0.25) = 1.9048 contracts
    assert got["contracts_needed"] == "1.9048"
    assert got["fills_of_0_01_needed"] == "190.5"


def test_the_worst_realistic_price_needs_even_more():
    got = RP.contracts_for_one_cent("0.31")
    assert float(got["fills_of_0_01_needed"]) > 200


def test_the_round_is_DOWN_and_at_the_DAY_not_the_fill():
    """Per-fill rounding would make every 0.01 fill $0.00 and no credit could
    ever exist -- yet seven do. Daily aggregation then flooring is the only
    reading consistent with holding $0.01 credits, and the module says so."""
    fills = [{"contracts": "1", "price": "0.50"}] * 4      # 4 x 0.00525
    got = RP.daily_accrual(fills)
    assert D(got["accrued_usd"]) == D("0.02100")
    assert got["would_pay_usd"] == "0.02"                  # floored, not 0.021
    assert "rounded DOWN" in got["rounding"]
    assert "only reading consistent" in RP.daily_accrual.__doc__


def test_a_day_of_ineligible_fills_pays_nothing_and_says_why():
    got = RP.daily_accrual([{"contracts": "100", "price": "0.95"}])
    assert got["n_eligible"] == 0 and got["n_outside_band"] == 1
    assert got["would_pay_usd"] == "0.00"


# ======================================================== the 5% cap

def test_the_notional_cap_is_applied_but_never_binds_at_these_rates():
    """rebate/notional = 0.021 x (1 - P), which is at most 0.021 -- well
    under the 5% cap. The cap is implemented anyway so a future rate change
    cannot silently exceed it."""
    got = RP.accrual("1", "0.21")
    assert got["capped"] is False
    high = RP.accrual("1", "0.50", rate=D("100"))
    assert high["capped"] is True
    assert D(high["accrued_usd"]) == RP.NOTIONAL_CAP * D("1") * D("0.50")


# =============================================== accrual is NOT realized

def test_an_accrual_is_never_called_realized():
    got = RP.accrual("0.01", "0.31")
    assert "Not a rebate, not realized" in got["labels"]
    assert "accrued_usd" in got and "realized" not in got


def test_the_ledger_still_keeps_accruals_out_of_the_total():
    """The published rules make an accrual computable; they do not make it
    money. It must still land in `estimated_not_counted`."""
    from src.gemini_execution import rebates as RB
    acc = RP.daily_accrual([{"contracts": "0.01", "price": "0.31"}])
    got = RB.realized_ledger(
        matched_pair_pnl="0", directional_residual_pnl="0",
        spread_capture="0", confirmed_maker_rebates="0",
        confirmed_other_rewards="0", fees="0", settlement_cashflow="0",
        estimated_rebates=acc["accrued_usd"])
    assert got["realized_net_pnl"] == "0"
    assert got["estimated_not_counted"]["estimated_rebates"] == acc["accrued_usd"]


# ================================= does the confirmed record agree?

def test_the_confirmed_aggregate_is_CONSISTENT_with_the_published_rate():
    """$0.51 over $52.2573 is 97.59 bps. Under the formula that implies a
    notional-weighted average fill price of 0.535 -- inside the eligible
    band, so the confirmed credits do not contradict the current rate."""
    got = RP.consistency_with_confirmed("97.59")
    assert got["verdict"] == "CONSISTENT_WITH_PUBLISHED_RULES"
    assert got["implied_avg_price"] == "0.5353"
    assert got["inside_eligible_band"] is True


def test_consistency_is_explicitly_NOT_proof():
    """Ineligible fills sit in the denominator earning nothing, which drags
    the observed rate down and biases the implied price. This check can only
    fail to contradict."""
    got = RP.consistency_with_confirmed("97.59")
    assert "CONSISTENT IS NOT PROVEN" in got["caveat"]


def test_a_rate_the_rules_cannot_explain_is_reported_as_such():
    # 0.021 * (1 - P) = 0.0005 would need P = 0.976, outside the band
    got = RP.consistency_with_confirmed("5")
    assert got["verdict"] == "NOT_EXPLAINED_BY_PUBLISHED_RULES"


# ================================== no per-day attribution is fabricated

def test_NO_per_day_attribution_is_produced():
    """Both mappings were tried and neither closes: same-day makes Aug 28
    need an average price above 1.00, T+1 makes Aug 30 need the same. So
    neither is true and this module produces no daily mapping."""
    import inspect
    src = inspect.getsource(RP)
    assert "does not close" in src
    for bad in ("attribute_daily", "per_day_credit", "map_credit_to_day"):
        assert bad not in src, bad


def test_the_payment_time_matches_our_confirmed_credits():
    """Paid daily at 5pm ET; our seven credits are stamped 21:03Z, which IS
    5pm ET under EDT. That is the strongest evidence the credits we hold were
    paid by THIS programme."""
    assert RP.PAID_AT_LOCAL == "17:00 America/New_York"
    assert "21:03Z" in RP.__doc__ or "21:03Z" in inspect_source()


def inspect_source():
    import inspect
    return inspect.getsource(RP)
