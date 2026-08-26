from decimal import Decimal as D

from src.sports_kgp_micro_policy import MicroCandidate, MicroLivePolicy, prediction_pair_only, validate_micro_candidate

FP = "a" * 64


def candidate(**overrides):
    values = dict(
        venues=("kalshi", "gemini"),
        quantity=D("1"),
        combined_capital_usd=D("0.97"),
        after_fee_profit_per_contract=D("0.03"),
        quote_skew_ms=40,
        max_book_age_ms=90,
        persistence_ms=10,
        approved_for_execution=True,
        rule_fingerprint_sha256=FP,
    )
    values.update(overrides)
    return MicroCandidate(**values)


def test_all_three_prediction_pairs_supported():
    assert prediction_pair_only(("kalshi", "gemini"))
    assert prediction_pair_only(("kalshi", "polymarket"))
    assert prediction_pair_only(("gemini", "polymarket"))


def test_sportsbook_is_never_supported():
    assert not prediction_pair_only(("fanduel", "kalshi"))
    ok, reason = validate_micro_candidate(candidate(venues=("sportsbook:fanduel", "kalshi")))
    assert not ok and reason == "prediction_market_pair_only"


def test_exact_approved_micro_candidate_qualifies():
    assert validate_micro_candidate(candidate()) == (True, "qualified")


def test_rule_approval_and_fingerprint_are_mandatory():
    assert validate_micro_candidate(candidate(approved_for_execution=False))[1] == "contract_not_execution_approved"
    assert validate_micro_candidate(candidate(rule_fingerprint_sha256=""))[1] == "invalid_rule_fingerprint"


def test_quantity_and_capital_fail_closed():
    assert validate_micro_candidate(candidate(quantity=D("2")))[1] == "quantity_exceeds_micro_cap"
    assert validate_micro_candidate(candidate(combined_capital_usd=D("2.01")))[1] == "capital_exceeds_micro_cap"


def test_profit_freshness_and_persistence_fail_closed():
    assert validate_micro_candidate(candidate(after_fee_profit_per_contract=D("0.0299")))[1] == "after_fee_profit_below_floor"
    assert validate_micro_candidate(candidate(quote_skew_ms=51))[1] == "quote_skew_too_large"
    assert validate_micro_candidate(candidate(max_book_age_ms=101))[1] == "book_too_old"
    assert validate_micro_candidate(candidate(persistence_ms=9))[1] == "opportunity_not_persistent"


def test_defaults_are_first_probe_small():
    p = MicroLivePolicy()
    assert p.max_contracts_per_leg == 1
    assert p.max_combined_capital_usd == D("2.00")
    assert p.min_after_fee_profit_per_contract == D("0.03")
    assert p.max_attempts_per_arm == 1
