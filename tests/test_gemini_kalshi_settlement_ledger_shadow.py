from dataclasses import dataclass

from src import gemini_kalshi_settlement_ledger_shadow as ledger


@dataclass
class Row:
    asset: str
    agrees: bool


def test_summarize_counts_by_asset_and_disagreements():
    result = ledger.summarize([
        Row("BTC", True),
        Row("BTC", False),
        Row("SOL", True),
        Row("DOGE", False),
    ])
    assert result["matched_15m_windows"] == 3
    assert result["agreements"] == 2
    assert result["disagreements"] == 1
    assert result["by_asset"]["BTC"] == {"matched": 2, "agreed": 1, "disagreed": 1}
    assert result["by_asset"]["SOL"] == {"matched": 1, "agreed": 1, "disagreed": 0}
    assert result["by_asset"]["ETH"] == {"matched": 0, "agreed": 0, "disagreed": 0}
    assert result["trade_authorized"] is False
    assert result["read_only"] is True
    assert result["order_entry"] is False


def test_ledger_scopes_kalshi_to_exact_supported_15m_series():
    assert ledger.KALSHI_15M_SERIES == {
        "BTC": "KXBTC15M",
        "ETH": "KXETH15M",
        "SOL": "KXSOL15M",
        "XRP": "KXXRP15M",
    }
    assert tuple(ledger.KALSHI_15M_SERIES) == ledger.ASSETS
