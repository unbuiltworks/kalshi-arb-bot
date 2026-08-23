# Railway production gate for dynamic final-lag Polymarket discovery.
from src import current_polymarket_mlb_props as discovery
from src import mlb_prop_final_lag_live_q1_entry as entry


def _event(series="KXMLBHIT", player="John Doe", threshold="2"):
    event_ticker = f"{series}-26AUG23NYYBOS"
    return {
        "series_ticker": series,
        "event_ticker": event_ticker,
        "title": "MLB player hits" if series == "KXMLBHIT" else "Other",
        "markets": [
            {
                "series_ticker": series,
                "event_ticker": event_ticker,
                "ticker": f"{event_ticker}-{player.replace(' ', '').upper()}",
                "title": f"{player}: {threshold}+ hits?",
            }
        ],
    }


def test_kalshi_seed_players_uses_only_strict_supported_identity():
    rows = [
        _event(player="John Doe"),
        _event(player="Jane Roe"),
        _event(series="UNSUPPORTED", player="Bad Seed"),
    ]
    assert discovery.kalshi_seed_players(rows) == ["Jane Roe", "John Doe"]


def test_dynamic_player_queries_are_deduplicated_and_cleaned():
    assert discovery._normalized_queries([
        "  John   Doe ",
        "john doe",
        "Jane Roe",
        "x",
        "",
    ]) == ["Jane Roe", "John Doe"]


def test_live_entry_installs_seeded_discovery_without_changing_strategy_namespace():
    assert entry.impl.decided._discover is entry._discover_seeded
    assert entry.impl.STRATEGY == "sports_gk_final_lag_kp_q1"
