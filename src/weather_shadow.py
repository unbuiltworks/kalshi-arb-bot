"""Read-only Kalshi weather market scanner.

No authentication/order entry. Public Kalshi + forecast/NWS data are normalized into
shadow rows. Settlement remains unapproved until listing-specific station/source audit.
"""
from __future__ import annotations
import argparse, json, urllib.parse, urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from src.fee_calc import taker_fee_cents
from src.weather_forecast import (
    CITY_CONFIG, bracket_probability, event_date, fetch_model_highs,
    fetch_observed_running_high, forecast_distribution, model_eligibility, parse_bracket,
)

KALSHI_PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
WEATHER_SERIES = {"KXHIGHNY":"New York City","KXHIGHCHI":"Chicago","KXHIGHMIA":"Miami","KXHIGHAUS":"Austin","KXHIGHLA":"Los Angeles"}
SETTLEMENT_STATUS = "SETTLEMENT_AUDIT_REQUIRED"
TRADE_CLASSIFICATION = "SHADOW"
CATEGORY = "weather"
MODEL_STATUS = "MODEL_RESEARCH_ONLY"
_LAST_GOOD_CONTEXT: dict[tuple[str,str], dict[str,Any]] = {}


def _to_cents(value: Any) -> int | None:
    if value is None or value == "": return None
    if isinstance(value, str):
        try: return int(round(float(value)*100))
        except ValueError: return None
    if isinstance(value,(int,float)):
        if isinstance(value,float) and 0 <= value <= 1 and not value.is_integer(): return int(round(value*100))
        return int(round(value))
    return None


def _first(market: dict[str,Any], *keys: str) -> Any:
    for key in keys:
        if key in market and market[key] not in (None,""): return market[key]
    return None


def _text(market: dict[str,Any], *keys:str) -> str | None:
    raw=_first(market,*keys)
    if raw is None:return None
    text=str(raw).strip()
    return text[:2000] if text else None


@dataclass(frozen=True)
class WeatherShadowRow:
    observed_at: str; category: str; classification: str; settlement_status: str; model_status: str; model_eligibility: str; data_quality_status: str
    series_ticker: str; city: str; station_hint: str | None; event_ticker: str | None; market_ticker: str; title: str | None; subtitle: str | None; close_time: str | None
    rules_primary: str | None; rules_secondary: str | None
    yes_bid_cents: int | None; yes_ask_cents: int | None; no_bid_cents: int | None; no_ask_cents: int | None
    volume: float | None; open_interest: float | None; yes_depth_hint: float | None; no_depth_hint: float | None
    q1_yes_taker_fee_cents: int | None; q1_no_taker_fee_cents: int | None
    forecast_target_date: str | None; forecast_models: dict[str,float] | None; forecast_mean_f: float | None; forecast_std_f: float | None
    observed_running_high_f: float | None; model_yes_probability: float | None; model_fair_yes_cents: int | None
    q1_yes_model_ev_cents: float | None; q1_no_model_ev_cents: float | None
    source: str = "kalshi_public_api"; forecast_source: str = "open_meteo_public"; observation_source: str = "nws_public_api"


def _parse_observed_at(value: str | None) -> datetime:
    if not value: return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z","+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def build_shadow_row(series_ticker: str, market: dict[str,Any], observed_at: str | None=None, forecast_context: dict[str,Any] | None=None) -> WeatherShadowRow:
    ticker = str(market.get("ticker") or "")
    yes_bid=_to_cents(_first(market,"yes_bid_dollars","yes_bid")); yes_ask=_to_cents(_first(market,"yes_ask_dollars","yes_ask"))
    no_bid=_to_cents(_first(market,"no_bid_dollars","no_bid")); no_ask=_to_cents(_first(market,"no_ask_dollars","no_ask"))
    yes_fee=taker_fee_cents(1,yes_ask,ticker) if yes_ask is not None else None; no_fee=taker_fee_cents(1,no_ask,ticker) if no_ask is not None else None
    def num(*keys:str)->float|None:
        raw=_first(market,*keys)
        if raw is None:return None
        try:return float(raw)
        except (TypeError,ValueError):return None
    observed_dt=_parse_observed_at(observed_at); target=event_date(market.get("event_ticker")); base_elig=model_eligibility(series_ticker,target,observed_dt) if series_ticker in CITY_CONFIG else "NO_CITY_CONFIG"
    bracket=parse_bracket(market.get("title")); model_highs=None; mean_f=std_f=model_prob=running_high=None; quality="NO_MODEL_DATA"
    if forecast_context:
        model_highs=forecast_context.get("models") or None; mean_f=forecast_context.get("mean_f"); std_f=forecast_context.get("std_f"); running_high=forecast_context.get("observed_running_high_f"); quality=forecast_context.get("data_quality_status","FRESH")
        if mean_f is not None and std_f is not None and bracket is not None:
            model_prob=bracket_probability(float(mean_f),float(std_f),bracket,float(running_high) if running_high is not None else None)
    eligibility=base_elig
    if base_elig=="INTRADAY_OBSERVATION_CONDITIONED" and running_high is None: eligibility="INTRADAY_OBSERVATIONS_REQUIRED"
    fair_cents=int(round(model_prob*100)) if model_prob is not None else None; yes_ev=no_ev=None
    if eligibility in {"FORECAST_ONLY_RESEARCH","INTRADAY_OBSERVATION_CONDITIONED"}:
        if model_prob is not None and yes_ask is not None and yes_fee is not None: yes_ev=round(model_prob*100.0-yes_ask-yes_fee,2)
        if model_prob is not None and no_ask is not None and no_fee is not None: no_ev=round((1.0-model_prob)*100.0-no_ask-no_fee,2)
    cfg=CITY_CONFIG.get(series_ticker,{})
    return WeatherShadowRow(observed_dt.isoformat(),CATEGORY,TRADE_CLASSIFICATION,SETTLEMENT_STATUS,MODEL_STATUS,eligibility,quality,series_ticker,WEATHER_SERIES.get(series_ticker,series_ticker),cfg.get("station"),market.get("event_ticker"),ticker,market.get("title"),market.get("subtitle"),market.get("close_time"),_text(market,"rules_primary"),_text(market,"rules_secondary"),yes_bid,yes_ask,no_bid,no_ask,num("volume_fp","volume"),num("open_interest_fp","open_interest"),num("yes_ask_size_fp","yes_ask_size"),num("no_ask_size_fp","no_ask_size"),yes_fee,no_fee,target.isoformat() if target else None,model_highs,round(float(mean_f),2) if mean_f is not None else None,round(float(std_f),2) if std_f is not None else None,round(float(running_high),2) if running_high is not None else None,round(float(model_prob),6) if model_prob is not None else None,fair_cents,yes_ev,no_ev)


def fetch_series_markets(series_ticker:str,status:str="open",limit:int=100)->list[dict[str,Any]]:
    params=urllib.parse.urlencode({"series_ticker":series_ticker,"status":status,"limit":limit}); url=f"{KALSHI_PUBLIC_BASE}/markets?{params}"
    req=urllib.request.Request(url,headers={"User-Agent":"kalshi-arb-weather-shadow/1.0"})
    with urllib.request.urlopen(req,timeout=10) as response: payload=json.load(response)
    markets=payload.get("markets",[]); return markets if isinstance(markets,list) else []


def _context_for(series_ticker:str,target,observed_dt:datetime)->dict[str,Any]:
    key=(series_ticker,target.isoformat()); prior=_LAST_GOOD_CONTEXT.get(key)
    models=fetch_model_highs(series_ticker,target); dist=forecast_distribution(models); fresh_models=bool(models and dist)
    if not fresh_models and prior:
        models=prior.get("models") or {}; dist=(prior.get("mean_f"),prior.get("std_f")) if prior.get("mean_f") is not None and prior.get("std_f") is not None else None
    elig=model_eligibility(series_ticker,target,observed_dt); running=None; fresh_obs=False
    if elig=="INTRADAY_OBSERVATION_CONDITIONED":
        try: running=fetch_observed_running_high(series_ticker,target,observed_dt)
        except Exception: running=None
        fresh_obs=running is not None
        if running is None and prior: running=prior.get("observed_running_high_f")
    quality="FRESH"
    if not fresh_models or (elig=="INTRADAY_OBSERVATION_CONDITIONED" and not fresh_obs): quality="STALE_LAST_GOOD" if prior else "UPSTREAM_MISSING"
    context={"models":models,"mean_f":dist[0] if dist else None,"std_f":dist[1] if dist else None,"observed_running_high_f":running,"data_quality_status":quality}
    if context["mean_f"] is not None and context["std_f"] is not None and (elig!="INTRADAY_OBSERVATION_CONDITIONED" or running is not None): _LAST_GOOD_CONTEXT[key]=dict(context)
    return context


def scan_weather(series:Iterable[str]=WEATHER_SERIES)->list[WeatherShadowRow]:
    observed_dt=datetime.now(timezone.utc); observed_at=observed_dt.isoformat(); rows=[]; cache={}
    for series_ticker in series:
        for market in fetch_series_markets(series_ticker):
            target=event_date(market.get("event_ticker")); context=None
            if target is not None and series_ticker in CITY_CONFIG:
                key=(series_ticker,target.isoformat())
                if key not in cache: cache[key]=_context_for(series_ticker,target,observed_dt)
                context=cache[key]
            rows.append(build_shadow_row(series_ticker,market,observed_at=observed_at,forecast_context=context))
    return rows


def main()->None:
    parser=argparse.ArgumentParser(description="Read-only Kalshi weather shadow scanner"); parser.add_argument("--series",action="append",choices=sorted(WEATHER_SERIES)); parser.add_argument("--jsonl"); args=parser.parse_args()
    rows=scan_weather(args.series or WEATHER_SERIES.keys()); encoded=[asdict(r) for r in rows]
    for row in encoded: print(json.dumps(row,sort_keys=True))
    if args.jsonl:
        with open(args.jsonl,"a",encoding="utf-8") as fh:
            for row in encoded: fh.write(json.dumps(row,sort_keys=True)+"\n")

if __name__=="__main__": main()
