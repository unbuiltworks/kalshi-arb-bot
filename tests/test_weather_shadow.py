from datetime import date, datetime, timezone

from src.weather_forecast import bracket_probability, climate_window_utc, event_date, model_eligibility, parse_bracket
from src.weather_shadow import CATEGORY, MODEL_STATUS, SETTLEMENT_STATUS, TRADE_CLASSIFICATION, build_shadow_row


def test_current_dollar_fields_are_normalized_to_cents():
    market={"ticker":"KXHIGHNY-26AUG20-B84.5","event_ticker":"KXHIGHNY-26AUG20","title":"Will the maximum temperature be 84-85° on Aug 20, 2026?","yes_bid_dollars":"0.41","yes_ask_dollars":"0.44","no_bid_dollars":"0.55","no_ask_dollars":"0.58","volume_fp":"123.0","open_interest_fp":"45.0"}
    row=build_shadow_row("KXHIGHNY",market,observed_at="2026-08-20T14:00:00+00:00")
    assert (row.yes_bid_cents,row.yes_ask_cents,row.no_bid_cents,row.no_ask_cents)==(41,44,55,58)
    assert row.volume==123.0 and row.open_interest==45.0
    assert row.category==CATEGORY=="weather" and row.classification==TRADE_CLASSIFICATION=="SHADOW"
    assert row.settlement_status==SETTLEMENT_STATUS=="SETTLEMENT_AUDIT_REQUIRED" and row.model_status==MODEL_STATUS=="MODEL_RESEARCH_ONLY"
    assert row.station_hint=="KNYC" and row.forecast_target_date=="2026-08-20"
    assert row.model_eligibility=="INTRADAY_OBSERVATION_CONDITIONED"


def test_legacy_integer_cent_fields_remain_supported_for_read_only_replay():
    row=build_shadow_row("KXHIGHCHI",{"ticker":"KXHIGHCHI-26AUG20-B80.5","yes_bid":31,"yes_ask":35,"no_bid":64,"no_ask":68})
    assert (row.yes_bid_cents,row.yes_ask_cents,row.no_bid_cents,row.no_ask_cents)==(31,35,64,68)


def test_missing_prices_never_become_fake_zero_quotes():
    row=build_shadow_row("KXHIGHMIA",{"ticker":"KXHIGHMIA-X"})
    assert row.yes_bid_cents is None and row.yes_ask_cents is None and row.no_bid_cents is None and row.no_ask_cents is None
    assert row.q1_yes_model_ev_cents is None and row.q1_no_model_ev_cents is None


def test_event_date_and_integer_temperature_brackets_are_parsed_conservatively():
    assert event_date("KXHIGHNY-26AUG21").isoformat()=="2026-08-21"
    assert parse_bracket("Will the maximum temperature be 82-83° on Aug 21, 2026?")==(81.5,83.5)
    assert parse_bracket("Will the maximum temperature be >85° on Aug 21, 2026?")== (85.5,None)
    assert parse_bracket("Will the maximum temperature be <78° on Aug 21, 2026?")== (None,77.5)


def test_standard_time_climate_windows_are_fixed_even_during_dst():
    target=date(2026,8,21)
    ny_start,ny_end=climate_window_utc("KXHIGHNY",target); chi_start,chi_end=climate_window_utc("KXHIGHCHI",target); la_start,la_end=climate_window_utc("KXHIGHLA",target)
    assert ny_start.isoformat()=="2026-08-21T05:00:00+00:00" and chi_start.isoformat()=="2026-08-21T06:00:00+00:00" and la_start.isoformat()=="2026-08-21T08:00:00+00:00"
    assert (ny_end-ny_start).total_seconds()==86400 and (chi_end-chi_start).total_seconds()==86400 and (la_end-la_start).total_seconds()==86400


def test_model_eligibility_marks_same_day_as_observation_conditioned():
    observed=datetime(2026,8,20,18,0,tzinfo=timezone.utc)
    assert model_eligibility("KXHIGHNY",date(2026,8,20),observed)=="INTRADAY_OBSERVATION_CONDITIONED"
    assert model_eligibility("KXHIGHNY",date(2026,8,21),observed)=="FORECAST_ONLY_RESEARCH"


def test_future_forecast_context_produces_after_fee_shadow_ev():
    market={"ticker":"KXHIGHCHI-26AUG21-B80.5","event_ticker":"KXHIGHCHI-26AUG21","title":"Will the maximum temperature be 80-81° on Aug 21, 2026?","yes_ask":30,"no_ask":72}
    context={"models":{"gfs":80.4,"ecmwf":80.8,"icon":80.6},"mean_f":80.6,"std_f":2.2}
    row=build_shadow_row("KXHIGHCHI",market,observed_at="2026-08-20T18:00:00+00:00",forecast_context=context)
    assert row.model_eligibility=="FORECAST_ONLY_RESEARCH" and row.q1_yes_model_ev_cents is not None and row.q1_no_model_ev_cents is not None


def test_running_high_eliminates_impossible_lower_bracket():
    p=bracket_probability(82.0,2.5,(79.5,81.5),observed_high_f=82.2)
    assert p==0.0


def test_intraday_context_conditions_probability_and_emits_shadow_ev():
    market={"ticker":"KXHIGHCHI-26AUG20-B79.5","event_ticker":"KXHIGHCHI-26AUG20","title":"Will the maximum temperature be 79-80° on Aug 20, 2026?","yes_ask":80,"no_ask":25}
    context={"models":{"gfs":79.6,"ecmwf":75.1,"icon":80.1},"mean_f":78.27,"std_f":3.09,"observed_running_high_f":82.2}
    row=build_shadow_row("KXHIGHCHI",market,observed_at="2026-08-20T18:00:00+00:00",forecast_context=context)
    assert row.model_eligibility=="INTRADAY_OBSERVATION_CONDITIONED" and row.observed_running_high_f==82.2
    assert row.model_yes_probability==0.0 and row.q1_yes_model_ev_cents is not None and row.q1_no_model_ev_cents is not None


def test_module_has_no_live_trading_surface():
    import src.weather_forecast as wf, src.weather_shadow as ws
    forbidden={"place_order","cancel_order","buy_yes","buy_no","submit_order","execute_trade"}
    assert forbidden.isdisjoint(set(dir(ws))) and forbidden.isdisjoint(set(dir(wf)))
