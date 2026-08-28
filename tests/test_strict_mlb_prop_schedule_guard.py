from src.strict_mlb_prop_adapters import RuleAuditEvidence
from src.strict_mlb_prop_schedule_guard import (
    kalshi_event_start_utc,
    polymarket_start_utc,
    strict_scheduled_match,
)


def audit(series: str) -> RuleAuditEvidence:
    return RuleAuditEvidence(
        kalshi_series=series,
        kalshi_settlement_sources=("MLB official stats",),
        kalshi_contract_terms_url="https://example.test/kalshi-terms",
        polymarket_rules_ref="poly-rule-ref:test",
        kalshi_rule_summary="Official MLB stats for the named player/stat/game.",
        polymarket_rule_summary="Official MLB stats for the named player/stat/game.",
        equivalent=True,
    )


def poly(*, start: str = "2026-08-16T17:40:00Z", date: str = "2026-08-16") -> dict:
    return {
        "sportsMarketType": "baseball_player_hits",
        "slug": f"astatc-mlb-wsh-nym-{date}-hits-fralin-gte2",
        "question": "Will Francisco Lindor record at least 2 hits in WSH vs NYM?",
        "line": "2",
        "gameStartTime": start,
    }


def kalshi(*, ticker_time: str = "26AUG161340", occurrence: str | None = None) -> tuple[dict, dict]:
    event_ticker = f"KXMLBHIT-{ticker_time}WSHNYM"
    event = {
        "series_ticker": "KXMLBHIT",
        "event_ticker": event_ticker,
        "title": "Washington vs New York M: Hits",
    }
    market = {
        "ticker": f"{event_ticker}-NYMFLINDOR12-2",
        "event_ticker": event_ticker,
        "yes_sub_title": "Francisco Lindor: 2+ hits?",
    }
    if occurrence is not None:
        market["occurrence_datetime"] = occurrence
    return event, market


def test_same_scheduled_game_passes_with_ticker_schedule() -> None:
    event, market = kalshi()
    result = strict_scheduled_match(poly(), event, market, audit("KXMLBHIT"))
    assert result is not None
    assert result.game_date == "2026-08-16"
    assert result.order_entry is False


def test_consecutive_day_false_match_fails() -> None:
    event, market = kalshi(ticker_time="26AUG171340")
    assert strict_scheduled_match(poly(), event, market, audit("KXMLBHIT")) is None


def test_same_day_doubleheader_time_mismatch_fails() -> None:
    event, market = kalshi(ticker_time="26AUG161905")
    assert strict_scheduled_match(poly(), event, market, audit("KXMLBHIT")) is None


def test_small_start_time_skew_is_allowed() -> None:
    event, market = kalshi()
    p = poly(start="2026-08-16T17:44:30Z")
    assert strict_scheduled_match(p, event, market, audit("KXMLBHIT")) is not None


def test_start_time_over_five_minutes_fails() -> None:
    event, market = kalshi()
    p = poly(start="2026-08-16T17:45:01Z")
    assert strict_scheduled_match(p, event, market, audit("KXMLBHIT")) is None


def test_missing_poly_start_fails_closed() -> None:
    p = poly()
    p.pop("gameStartTime")
    event, market = kalshi()
    assert strict_scheduled_match(p, event, market, audit("KXMLBHIT")) is None


def test_occurrence_datetime_does_not_override_scheduled_ticker_clock() -> None:
    # Kalshi documents occurrence_datetime as when the underlying event occurred,
    # not its scheduled start. A live 12:15 ET MLB ticker carried 19:15Z there.
    event, market = kalshi(
        ticker_time="26AUG161215",
        occurrence="2026-08-16T19:15:00Z",
    )
    assert kalshi_event_start_utc(event, market).isoformat() == "2026-08-16T16:15:00+00:00"


def test_occurrence_datetime_cannot_rescue_wrong_scheduled_game() -> None:
    event, market = kalshi(
        ticker_time="26AUG161215",
        occurrence="2026-08-16T19:15:00Z",
    )
    assert strict_scheduled_match(
        poly(start="2026-08-16T16:15:00Z"), event, market, audit("KXMLBHIT")
    ) is not None
    assert strict_scheduled_match(
        poly(start="2026-08-16T19:15:00Z"), event, market, audit("KXMLBHIT")
    ) is None


def test_unparseable_kalshi_schedule_fails_closed_even_with_occurrence() -> None:
    event, market = kalshi(occurrence="2026-08-16T17:40:00Z")
    event["event_ticker"] = "KXMLBHIT-NOT-A-SCHEDULE"
    market["event_ticker"] = event["event_ticker"]
    assert kalshi_event_start_utc(event, market) is None
    assert strict_scheduled_match(poly(), event, market, audit("KXMLBHIT")) is None


def test_timestamp_parsers_are_explicit() -> None:
    event, market = kalshi()
    assert polymarket_start_utc(poly()).isoformat() == "2026-08-16T17:40:00+00:00"
    assert kalshi_event_start_utc(event, market).isoformat() == "2026-08-16T17:40:00+00:00"
