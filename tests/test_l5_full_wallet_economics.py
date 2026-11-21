"""Full-wallet economic-target audit. Pins the composition, the hypothesis
verdicts, and the constraint that actually binds."""
from decimal import Decimal as D

from src import l5_full_wallet_economics as F


# --- ingestion honesty ------------------------------------------------------

def test_the_canonical_tape_covered_under_a_tenth_of_the_trading():
    assert F.canonical_tape_fill_coverage() < D("0.10")


def test_the_wallet_trades_only_crypto_updown():
    assert F.NON_CRYPTO_TRADES == 0
    assert sum(F.TRADES_BY_ASSET.values()) == sum(F.TRADES_BY_DURATION.values())


def test_no_merges_in_the_full_wallet_either():
    assert F.ACTIVITY_TYPES["MERGE"] == 0
    assert F.ACTIVITY_TYPES["SPLIT"] == 0


# --- the accounting ---------------------------------------------------------

def test_trading_is_negative_on_the_full_wallet():
    assert F.trading_net() == D("-2828.77")
    assert F.trading_net() < 0


def test_incentives_dwarf_the_trading_loss():
    assert F.incentives_exceed_trading_loss_multiple() > D("20")


def test_total_reconciles_with_the_articles_claim():
    assert F.total_economics() == D("68722.97")
    assert F.reconciles_with_claim() is True


def test_unredeemed_markets_are_total_losses_not_a_gap():
    assert F.UNREDEEMED["median_paired_share"] == D("0.000")
    assert F.UNREDEEMED["median_buy"] < D("20")
    assert F.REDEEMED_MEDIAN_PAIRED_SHARE > D("0.5")


def test_fees_are_not_observed_rather_than_asserted_zero():
    assert F.FEES == "NOT OBSERVED"


# --- hypotheses -------------------------------------------------------------

def test_h1_rejected_h2_supported():
    assert F.HYPOTHESES["H1_trading_positive_amplified"] == "REJECTED"
    assert F.HYPOTHESES["H2_incentives_create_the_business"] == "SUPPORTED"


def test_h3_notes_there_is_only_one_category():
    assert "only one category" in F.HYPOTHESES["H3_categories_differ"]


def test_eth_and_btc_differ_in_sign_but_eth_is_immaterial():
    assert F.BY_ASSET["eth"]["net"] > 0 > F.BY_ASSET["btc"]["net"]
    assert F.BY_ASSET["eth"]["buy"] / F.BUY_USD < D("0.05")


# --- behaviour vs payout ----------------------------------------------------

def test_behaviour_tracked_the_payout_rate_on_volume_and_residual():
    assert F.payout_rate_rose_and_so_did_residual() is True
    assert F.MONTHLY[-1][5] > F.MONTHLY[1][5] * 3   # residual share tripled


def test_incentive_attribution_stays_unknown():
    assert "UNKNOWN" in F.INCENTIVE_MARKET_ATTRIBUTION
    assert F.INCENTIVE_PROGRAM_TERMS == "NOT HELD"


# --- PMUS and the binding constraint ----------------------------------------

def test_our_trading_loses_two_orders_of_magnitude_more_per_dollar():
    assert F.our_trading_loss_multiple_of_l5() > D("100")


def test_our_rebate_rate_is_already_inside_l5s_range():
    assert F.PER_DOLLAR["pmus_incentive"] > D("0.00275")


def test_the_incentive_rate_is_not_the_binding_constraint():
    """Even L5's best observed rate would not rescue our trading loss."""
    assert F.incentive_rate_is_the_binding_constraint() is False
    assert F.BINDING_CONSTRAINT.startswith("trading loss")


def test_pmus_rewards_count_zero_until_credited():
    assert "UNPROVEN" in F.VENUE_MAP["pmus_liquidity_rewards"]
    assert F.PMUS["rebates_credited"] == D("2.1300")


def test_the_series_our_shadow_trades_has_no_subsidy():
    assert "no active" in F.VENUE_MAP["kalshi_KXBTC15M_KXETH15M"]


def test_the_kalshi_analogue_is_flagged_unverified():
    assert "UNVERIFIED" in F.VENUE_MAP["kalshi_KXCRYPTOLEAD15M"]


# --- deliverable ------------------------------------------------------------

def test_the_verdict_and_that_it_authorises_nothing():
    assert F.VERDICT == "INCENTIVE_DRIVEN_LIQUIDITY_BUSINESS"
    assert F.AUTHORISES_RESTART == ()
    assert F.AUTHORISES_MODEL_CHANGE == ()
