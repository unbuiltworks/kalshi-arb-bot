from decimal import Decimal
from types import SimpleNamespace

import src.odds_api_io_kalshi_mlb_prop_hot_shadow as hot

D = Decimal


def _event():
    return {
        "id": "evt1",
        "home": "Milwaukee Brewers",
        "away": "Seattle Mariners",
        "date": "2026-08-20T18:11:00Z",
    }


def _kprop():
    return hot.KalshiProp(
        event_id="evt1", family="baseball_player_strikeouts", stat="strikeouts",
        player="George Kirby", player_key="georgekirby", threshold=D("7"),
        ticker="KXMLBKS-TEST", start_ms=1787249460000,
    )


def test_half_line_maps_to_binary_integer_threshold_and_integer_line_rejected():
    assert hot.sportsbook_threshold("0.5") == D("1")
    assert hot.sportsbook_threshold("5.5") == D("6")
    assert hot.sportsbook_threshold("6.5") == D("7")
    assert hot.sportsbook_threshold("6") is None


def test_player_prop_store_matches_exact_event_player_stat_threshold_and_both_sides():
    prop = _kprop()
    store = hot.PropOddsIoStore({prop.lookup_key: prop})
    store.set_events([_event()])
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
    message = {
        "type": "updated", "seq": 1, "id": "evt1", "bookie": "DraftKings",
        "timestamp": __import__("time").time(),
        "markets": [{
            "name": "Player Props", "updatedAt": now_iso,
            "odds": [{"label": "George Kirby (Strikeouts)", "hdp": 6.5, "over": 4.32, "under": 1.20}],
        }],
    }
    accepted = store.ingest(message, freshness_ms=3000)
    assert accepted >= 1
    rows = store.fresh_for_ticker(prop.ticker, max_age_ms=3000)
    assert len(rows) == 1
    row = rows[0]
    assert row.book == "draftkings"
    assert row.threshold == D("7")
    assert row.yes_cost == D("1") / D("4.32")
    assert row.no_cost == D("1") / D("1.20")


def test_player_or_threshold_mismatch_does_not_join():
    prop = _kprop()
    store = hot.PropOddsIoStore({prop.lookup_key: prop})
    store.set_events([_event()])
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
    message = {
        "type": "updated", "seq": 2, "id": "evt1", "bookie": "FanDuel",
        "timestamp": __import__("time").time(),
        "markets": [{"name": "Player Props", "updatedAt": now_iso, "odds": [
            {"label": "Different Player (Strikeouts)", "hdp": 6.5, "over": 4.0, "under": 1.2},
            {"label": "George Kirby (Strikeouts)", "hdp": 7.5, "over": 7.0, "under": 1.1},
        ]}],
    }
    store.ingest(message, freshness_ms=3000)
    assert store.fresh_for_ticker(prop.ticker, max_age_ms=3000) == []


def test_fee_aware_plan_uses_complementary_kalshi_side_and_cap():
    rq = hot.RetailPropQuote(
        event_id="evt1", book="draftkings", player="George Kirby", player_key="georgekirby",
        stat="strikeouts", threshold=D("7"), yes_cost=D("0.20"), no_cost=D("0.85"),
        received_ms=1000, source_updated_ms=900,
    )
    kq = SimpleNamespace(yes_ask=D("0.82"), yes_depth=D("10"), no_ask=D("0.70"), no_depth=D("10"), received_ms=1000)
    plan = hot.best_plan(rq, kq, kalshi_fee_multiplier=D("0.5"), max_quantity=5, max_capital=D("5"))
    assert plan is not None
    assert plan["direction"] == "SPORTSBOOK_YES_KALSHI_NO"
    assert D(plan["total_capital"]) <= D("5")
    assert D(plan["conservative_locked_profit"]) > 0


def test_no_positive_math_is_not_manufactured():
    rq = hot.RetailPropQuote(
        event_id="evt1", book="fanduel", player="George Kirby", player_key="georgekirby",
        stat="strikeouts", threshold=D("7"), yes_cost=D("0.50"), no_cost=D("0.50"),
        received_ms=1000, source_updated_ms=900,
    )
    kq = SimpleNamespace(yes_ask=D("0.55"), yes_depth=D("10"), no_ask=D("0.55"), no_depth=D("10"), received_ms=1000)
    plan = hot.best_plan(rq, kq, kalshi_fee_multiplier=D("0.5"), max_quantity=5, max_capital=D("5"))
    assert plan is not None
    assert D(plan["conservative_locked_profit"]) < 0

# Deployment trigger only; no behavioral change.
