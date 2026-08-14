from src.opticodds_stream import normalize_over_quote


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
