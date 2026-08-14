from src.opticodds_stream import SportsbookQuoteStore, normalize_over_quote


def test_normalize_opticodds_over_with_limit_and_deeplink():
    odd = {
        "id": "abc",
        "fixture_id": "fixture-1",
        "sportsbook": "Caesars",
        "market": "Total Points",
        "selection_line": "over",
        "points": 184.5,
        "price": -120,
        "limits": {"max_stake": 240},
        "deep_link": {"ios": "caesars://bet/123"},
        "timestamp": 1730079534.712,
    }
    q = normalize_over_quote(
        odd, event_key="GSW-MEM", configured_stake_cap_cents=30000
    )
    assert q is not None
    assert q.max_stake_cents == 24000
    assert q.deeplink == "caesars://bet/123"
    assert q.source_id == "abc"


def test_no_limit_requires_configured_cap():
    odd = {
        "id": "abc",
        "sportsbook": "Caesars",
        "selection_line": "over",
        "points": 184.5,
        "price": -120,
        "limits": None,
    }
    assert normalize_over_quote(
        odd, event_key="X", configured_stake_cap_cents=0
    ) is None


def test_snapshot_nested_fixture_odds_are_seeded():
    store = SportsbookQuoteStore()
    store.apply_snapshot_payload(
        {
            "data": [
                {
                    "id": "fixture-1",
                    "odds": [
                        {
                            "id": "odd-1",
                            "sportsbook": "Caesars",
                            "selection_line": "over",
                            "points": 184.5,
                            "price": -120,
                            "limits": {"max": 500},
                            "timestamp": 100.0,
                        }
                    ],
                }
            ]
        },
        fixture_to_event={"fixture-1": "GAME"},
        configured_stake_cap_cents=30000,
    )
    quotes = store.for_event("GAME")
    assert len(quotes) == 1
    assert quotes[0].source_id == "odd-1"
    assert quotes[0].max_stake_cents == 30000


def test_older_snapshot_cannot_overwrite_newer_stream_quote():
    store = SportsbookQuoteStore()
    fixture_map = {"fixture-1": "GAME"}
    store.apply_odds_payload(
        {
            "data": [
                {
                    "id": "odd-1",
                    "fixture_id": "fixture-1",
                    "sportsbook": "Caesars",
                    "selection_line": "over",
                    "points": 184.5,
                    "price": -105,
                    "limits": {"max": 500},
                    "timestamp": 200.0,
                }
            ]
        },
        fixture_to_event=fixture_map,
        configured_stake_cap_cents=30000,
    )
    store.apply_snapshot_payload(
        {
            "data": [
                {
                    "id": "fixture-1",
                    "odds": [
                        {
                            "id": "odd-1",
                            "sportsbook": "Caesars",
                            "selection_line": "over",
                            "points": 184.5,
                            "price": -120,
                            "limits": {"max": 500},
                            "timestamp": 100.0,
                        }
                    ],
                }
            ]
        },
        fixture_to_event=fixture_map,
        configured_stake_cap_cents=30000,
    )
    quote = store.for_event("GAME")[0]
    assert quote.american_odds == -105
    assert quote.observed_ms == 200000
