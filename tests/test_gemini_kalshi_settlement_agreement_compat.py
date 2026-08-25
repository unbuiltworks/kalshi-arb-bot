from datetime import datetime, timezone

from src.gemini_kalshi_settlement_agreement import AgreementRow


def test_agreement_row_expiry_utc_alias_matches_canonical_expiry():
    expiry = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
    row = AgreementRow(
        asset="BTC",
        expiry=expiry,
        gemini_ticker="BTC-TEST",
        kalshi_ticker="KXBTC15M-TEST",
        gemini_outcome="yes",
        kalshi_outcome="no",
        agrees=False,
        gemini_strike=100.0,
        gemini_settlement=101.0,
        gemini_margin_bps=100.0,
    )
    assert row.expiry_utc is expiry
    assert row.as_dict()["expiry_utc"] == expiry.isoformat()
