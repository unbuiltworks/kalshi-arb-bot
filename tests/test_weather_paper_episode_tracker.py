from src.weather_paper_episode_tracker import advance_weather_episode, summarize


def _row(**overrides):
    ticker = "KXHIGHMIA-26AUG21-B92.5"
    market_date = "2026-08-21"
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "settlement_certified_shadow": True,
        "ev_certified_shadow": True,
        "exact_market_source_verified": True,
        "exact_market_source_audit": {
            "verified": True,
            "market_ticker": ticker,
            "market_date": market_date,
            "settlement_source_provider": "THE_WEATHER_COMPANY",
            "settlement_source_url": "https://weather.com/kalshi",
        },
        "blockers": [],
        "market_ticker": ticker,
        "market_date": market_date,
        "quote_observed_at": "2026-08-21T15:00:00+00:00",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "best_shadow_side": {
            "side": "NO",
            "ask_cents": 36,
            "available_contracts": 10,
            "forecast_side_probability": "0.891",
        },
    }
    row.update(overrides)
    return row


def test_repeated_resting_quote_opens_once_and_price_move_is_move():
    events, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    assert [e["event"] for e in events] == ["OPEN"]
    events, state = advance_weather_episode(_row(quote_observed_at="2026-08-21T15:00:01+00:00"), prior_state=state, observed_ms=2000)
    assert events == []
    moved = _row(
        quote_observed_at="2026-08-21T15:00:02+00:00",
        best_shadow_side={"side": "NO", "ask_cents": 35, "available_contracts": 8, "forecast_side_probability": "0.90"},
    )
    events, state = advance_weather_episode(moved, prior_state=state, observed_ms=3000)
    assert [e["event"] for e in events] == ["MOVE"]
    assert next(iter(state.values()))["opened_ms"] == 1000


def test_close_then_same_side_reopen_gets_new_episode_id():
    events, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    first_id = events[0]["episode_id"]
    events, state = advance_weather_episode(None, prior_state=state, observed_ms=3000)
    assert [e["event"] for e in events] == ["CLOSE"]
    assert events[0]["duration_lower_bound_ms"] == 0
    assert events[0]["duration_upper_bound_ms"] == 2000
    events, state = advance_weather_episode(_row(quote_observed_at="2026-08-21T15:00:04+00:00"), prior_state=state, observed_ms=4000)
    assert [e["event"] for e in events] == ["OPEN"]
    assert events[0]["episode_id"] != first_id


def test_uncertified_row_closes_prior_episode_instead_of_counting_capacity():
    _, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    blocked = _row(ev_certified_shadow=False, blockers=["MODEL_RESEARCH_ONLY"])
    events, new_state = advance_weather_episode(blocked, prior_state=state, observed_ms=2000)
    assert [e["event"] for e in events] == ["CLOSE"]
    assert new_state == {}


def test_summary_is_explicitly_paper_only():
    events, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    closed, _ = advance_weather_episode(None, prior_state=state, observed_ms=2000)
    summary = summarize(events + closed)
    assert summary["open"] == 1
    assert summary["close"] == 1
    assert summary["paper_only"] is True
    assert summary["live_probe_ready"] is False
    assert summary["order_entry"] is False
