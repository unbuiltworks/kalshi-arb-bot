from datetime import datetime, timezone

from src.weather_backtest import build_backtest_row, conservative_run_time, decision_time_for, summarize


def twc_market(result="yes"):
    return {
        "ticker": "KXHIGHCHI-26AUG14-B80.5",
        "event_ticker": "KXHIGHCHI-26AUG14",
        "title": "Will the maximum temperature be 80-81° on Aug 14, 2026?",
        "result": result,
        "rules_primary": "If the maximum temperature recorded at Chicago (CLIMDW) is between 80-81° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        "rules_secondary": "The official and final value is reported by the Weather Company.",
    }


def test_decision_and_conservative_run_times_have_no_lookahead():
    decision = decision_time_for(datetime(2026, 8, 14, tzinfo=timezone.utc).date(), 19)
    assert decision.isoformat() == "2026-08-13T19:00:00+00:00"
    run = conservative_run_time(decision, availability_lag_hours=6)
    assert run.isoformat() == "2026-08-13T12:00:00+00:00"
    assert run < decision


def test_backtest_row_keeps_twc_regime_and_scores_finalized_result():
    decision = datetime(2026, 8, 13, 19, tzinfo=timezone.utc)
    run = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    row = build_backtest_row(
        "KXHIGHCHI",
        twc_market("yes"),
        {"gfs": 80.2, "ecmwf": 80.8, "icon": 81.0},
        decision,
        run,
    )
    assert row is not None
    assert row.settlement_source == "THE_WEATHER_COMPANY"
    assert row.settlement_location_key == "CLIMDW"
    assert row.actual_yes == 1
    assert 0.0 <= row.predicted_yes_probability <= 1.0
    assert 0.0 <= row.brier <= 1.0
    assert row.log_loss >= 0.0


def test_backtest_rejects_unfinalized_or_unparseable_market():
    decision = datetime(2026, 8, 13, 19, tzinfo=timezone.utc)
    run = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    market = twc_market("")
    assert build_backtest_row("KXHIGHCHI", market, {"gfs": 80.0}, decision, run) is None


def test_summary_separates_settlement_regimes():
    decision = datetime(2026, 8, 13, 19, tzinfo=timezone.utc)
    run = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    twc = build_backtest_row("KXHIGHCHI", twc_market("yes"), {"gfs": 80.2, "ecmwf": 80.8}, decision, run)
    nws_market = {
        "ticker": "KXHIGHCHI-26AUG13-B80.5",
        "event_ticker": "KXHIGHCHI-26AUG13",
        "title": "Will the maximum temperature be 80-81° on Aug 13, 2026?",
        "result": "no",
        "rules_primary": "Resolves from the National Weather Service Climatological Report (Daily).",
    }
    nws = build_backtest_row("KXHIGHCHI", nws_market, {"gfs": 80.0, "ecmwf": 79.5}, decision, run)
    assert twc is not None and nws is not None
    summary = summarize([twc, nws])
    keys = set(summary["by_settlement_source_and_series"])
    assert "THE_WEATHER_COMPANY:KXHIGHCHI" in keys
    assert "UNVERIFIED:KXHIGHCHI" in keys
    assert summary["rows"] == 2


def test_backtest_module_has_no_live_trading_surface():
    import src.weather_backtest as wb
    forbidden = {"place_order", "cancel_order", "buy_yes", "buy_no", "submit_order", "execute_trade"}
    assert forbidden.isdisjoint(set(dir(wb)))
