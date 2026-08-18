from dataclasses import dataclass

from src import gemini_kalshi_settlement_ledger_shadow as ledger


@dataclass
class Row:
    asset: str
    agrees: bool
    gemini_margin_bps: float | None = None


def test_summarize_counts_by_asset_and_disagreements():
    result = ledger.summarize([
        Row("BTC", True, 12.0),
        Row("BTC", False, 1.13),
        Row("SOL", True, -6.0),
        Row("DOGE", False, 0.1),
    ])
    assert result["matched_15m_windows"] == 3
    assert result["agreements"] == 2
    assert result["disagreements"] == 1
    assert result["by_asset"]["BTC"] == {"matched": 2, "agreed": 1, "disagreed": 1}
    assert result["by_asset"]["SOL"] == {"matched": 1, "agreed": 1, "disagreed": 0}
    assert result["by_asset"]["ETH"] == {"matched": 0, "agreed": 0, "disagreed": 0}
    assert result["max_disagreement_final_gemini_abs_margin_bps"] == "1.13"
    assert result["trade_authorized"] is False
    assert result["read_only"] is True
    assert result["order_entry"] is False


def test_margin_bucket_boundaries_use_absolute_final_gemini_margin():
    assert ledger._margin_bucket(0) == "lt_1bp"
    assert ledger._margin_bucket(-0.999) == "lt_1bp"
    assert ledger._margin_bucket(1) == "1_to_lt_2bp"
    assert ledger._margin_bucket(-1.999) == "1_to_lt_2bp"
    assert ledger._margin_bucket(2) == "2_to_lt_5bp"
    assert ledger._margin_bucket(-4.999) == "2_to_lt_5bp"
    assert ledger._margin_bucket(5) == "5_to_lt_10bp"
    assert ledger._margin_bucket(-9.999) == "5_to_lt_10bp"
    assert ledger._margin_bucket(10) == "ge_10bp"
    assert ledger._margin_bucket(None) == "unknown"
    assert ledger._margin_bucket("bad") == "unknown"


def test_margin_cohorts_count_matches_and_disagreements():
    result = ledger.summarize([
        Row("BTC", True, 0.4),
        Row("ETH", False, 0.8),
        Row("SOL", False, -1.1),
        Row("XRP", True, 3.0),
        Row("BTC", True, 7.0),
        Row("ETH", True, 15.0),
    ])
    cohorts = result["by_final_gemini_abs_margin_bps"]
    assert cohorts["lt_1bp"] == {"matched": 2, "agreed": 1, "disagreed": 1}
    assert cohorts["1_to_lt_2bp"] == {"matched": 1, "agreed": 0, "disagreed": 1}
    assert cohorts["2_to_lt_5bp"] == {"matched": 1, "agreed": 1, "disagreed": 0}
    assert cohorts["5_to_lt_10bp"] == {"matched": 1, "agreed": 1, "disagreed": 0}
    assert cohorts["ge_10bp"] == {"matched": 1, "agreed": 1, "disagreed": 0}
    assert cohorts["unknown"] == {"matched": 0, "agreed": 0, "disagreed": 0}
    assert result["margin_cohort_semantics"]["not_entry_margin"] is True


def test_ledger_scopes_kalshi_to_exact_supported_15m_series():
    assert ledger.KALSHI_15M_SERIES == {
        "BTC": "KXBTC15M",
        "ETH": "KXETH15M",
        "SOL": "KXSOL15M",
        "XRP": "KXXRP15M",
    }
    assert tuple(ledger.KALSHI_15M_SERIES) == ledger.ASSETS
