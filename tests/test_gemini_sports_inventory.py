from src.gemini_sports_inventory import summarize


def test_summarize_preserves_unknown_live_pp_codes_and_metadata():
    events = [
        {
            "ticker": "GEMI-MLB-2608161905-PHI-NYM-PPHRR",
            "title": "Philadelphia at New York M: Hits + Runs + RBIs",
            "effectiveDate": "2026-08-16T19:05:00Z",
            "expiryDate": "2026-08-17T03:00:00Z",
            "contracts": [
                {
                    "label": "Bryce Harper: 2+ hits + runs + RBIs",
                    "ticker": "GEMI-MLB-2608161905-PHI-NYM-PPHRR-BHARPER-2",
                    "instrumentSymbol": "TEST-HRR",
                    "marketState": "open",
                }
            ],
        },
        {
            "ticker": "GEMI-MLB-2608161905-PHI-NYM-PPHITS",
            "title": "Philadelphia at New York M: Hits",
            "contracts": [{"label": "Bryce Harper: 1+ hits", "ticker": "HITS-1"}],
        },
    ]

    result = summarize(events)

    assert result["live_pp_market_type_counts"]["MLB:PPHRR"] == 1
    assert result["live_pp_market_type_counts"]["MLB:PPHITS"] == 1
    assert result["unknown_pp_market_type_counts"] == {"MLB:PPHRR": 1}
    assert result["unknown_pp_codes_require_identity_audit"] == ["MLB:PPHRR"]

    unknown = result["live_pp_market_type_examples"]["MLB:PPHRR"][0]
    assert unknown["known_family"] is None
    assert unknown["title"] == "Philadelphia at New York M: Hits + Runs + RBIs"
    assert unknown["contracts"][0]["label"] == "Bryce Harper: 2+ hits + runs + RBIs"

    known = result["live_pp_market_type_examples"]["MLB:PPHITS"][0]
    assert known["known_family"] == "baseball_player_hits"
    assert result["classification"] == "INVENTORY_ONLY_UNKNOWN_CODES_PRESERVED"
    assert result["order_entry"] is False
