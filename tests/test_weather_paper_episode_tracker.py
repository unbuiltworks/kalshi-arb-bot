from src.weather_paper_episode_tracker import (
    advance_weather_episode,
    advance_weather_snapshot,
    ledger_events_for_episode_open,
    summarize,
)


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


def _second_row(**overrides):
    ticker = "KXHIGHCHI-26AUG21-B80.5"
    market_date = "2026-08-21"
    row = _row(
        market_ticker=ticker,
        market_date=market_date,
        exact_market_source_audit={
            "verified": True,
            "market_ticker": ticker,
            "market_date": market_date,
            "settlement_source_provider": "THE_WEATHER_COMPANY",
            "settlement_source_url": "https://weather.com/kalshi",
        },
        best_shadow_side={
            "side": "YES",
            "ask_cents": 41,
            "available_contracts": 7,
            "forecast_side_probability": "0.72",
        },
    )
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
    first_group = events[0]["trade_group_id"]
    events, state = advance_weather_episode(None, prior_state=state, observed_ms=3000)
    assert [e["event"] for e in events] == ["CLOSE"]
    assert events[0]["duration_lower_bound_ms"] == 0
    assert events[0]["duration_upper_bound_ms"] == 2000
    events, state = advance_weather_episode(_row(quote_observed_at="2026-08-21T15:00:04+00:00"), prior_state=state, observed_ms=4000)
    assert [e["event"] for e in events] == ["OPEN"]
    assert events[0]["episode_id"] != first_id
    assert events[0]["trade_group_id"] != first_group


def test_uncertified_row_closes_prior_episode_instead_of_counting_capacity():
    _, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    blocked = _row(ev_certified_shadow=False, blockers=["MODEL_RESEARCH_ONLY"])
    events, new_state = advance_weather_episode(blocked, prior_state=state, observed_ms=2000)
    assert [e["event"] for e in events] == ["CLOSE"]
    assert new_state == {}


def test_full_snapshot_does_not_close_other_active_market_when_rows_are_processed_together():
    events, state = advance_weather_snapshot(
        [_row(), _second_row()],
        prior_state={},
        observed_ms=1000,
    )
    assert [e["event"] for e in events] == ["OPEN", "OPEN"]
    assert len(state) == 2

    events, state2 = advance_weather_snapshot(
        [
            _row(quote_observed_at="2026-08-21T15:00:01+00:00"),
            _second_row(quote_observed_at="2026-08-21T15:00:01+00:00"),
        ],
        prior_state=state,
        observed_ms=2000,
    )
    assert events == []
    assert len(state2) == 2
    assert {s["market_ticker"] for s in state2.values()} == {
        "KXHIGHMIA-26AUG21-B92.5",
        "KXHIGHCHI-26AUG21-B80.5",
    }


def test_full_snapshot_closes_only_market_missing_from_complete_poll_cycle():
    _, state = advance_weather_snapshot(
        [_row(), _second_row()],
        prior_state={},
        observed_ms=1000,
    )
    events, new_state = advance_weather_snapshot(
        [_row(quote_observed_at="2026-08-21T15:00:02+00:00")],
        prior_state=state,
        observed_ms=3000,
    )
    assert [e["event"] for e in events] == ["CLOSE"]
    assert events[0]["market_ticker"] == "KXHIGHCHI-26AUG21-B80.5"
    assert {s["market_ticker"] for s in new_state.values()} == {"KXHIGHMIA-26AUG21-B92.5"}


def test_single_row_helper_fails_closed_on_multi_market_state():
    _, state = advance_weather_snapshot(
        [_row(), _second_row()],
        prior_state={},
        observed_ms=1000,
    )
    try:
        advance_weather_episode(_row(), prior_state=state, observed_ms=2000)
    except ValueError as exc:
        assert str(exc) == "MULTI_MARKET_STATE_REQUIRES_ADVANCE_WEATHER_SNAPSHOT"
    else:
        raise AssertionError("multi-market row-by-row use must fail closed")


def test_only_open_emits_episode_scoped_idempotent_paper_fill_and_fee():
    opened, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    ledger = ledger_events_for_episode_open(opened[0])
    assert len(ledger) == 2
    assert {e.event_type for e in ledger} == {"fill", "fee"}
    assert len({e.trade_group_id for e in ledger}) == 1
    group = ledger[0].trade_group_id
    assert group == opened[0]["trade_group_id"]
    assert {e.external_id for e in ledger} == {f"{group}:fill", f"{group}:fee"}

    moved = _row(
        quote_observed_at="2026-08-21T15:00:02+00:00",
        best_shadow_side={"side": "NO", "ask_cents": 35, "available_contracts": 8, "forecast_side_probability": "0.90"},
    )
    move_events, state = advance_weather_episode(moved, prior_state=state, observed_ms=2000)
    assert [e["event"] for e in move_events] == ["MOVE"]
    assert ledger_events_for_episode_open(move_events[0]) == []

    _, state = advance_weather_episode(None, prior_state=state, observed_ms=3000)
    reopened, _ = advance_weather_episode(_row(quote_observed_at="2026-08-21T15:00:04+00:00"), prior_state=state, observed_ms=4000)
    reopened_ledger = ledger_events_for_episode_open(reopened[0])
    assert len(reopened_ledger) == 2
    assert reopened_ledger[0].trade_group_id != group


def test_summary_is_explicitly_paper_only():
    events, state = advance_weather_episode(_row(), prior_state={}, observed_ms=1000)
    closed, _ = advance_weather_episode(None, prior_state=state, observed_ms=2000)
    summary = summarize(events + closed)
    assert summary["open"] == 1
    assert summary["close"] == 1
    assert summary["paper_only"] is True
    assert summary["paper_fill_semantics"] == "ONE_SYNTHETIC_FILL_PER_OBSERVED_OPEN"
    assert summary["live_probe_ready"] is False
    assert summary["order_entry"] is False
