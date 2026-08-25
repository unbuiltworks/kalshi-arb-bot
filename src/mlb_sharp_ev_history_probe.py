"""Read-only proof that the MLB sharp-EV backtest data sources are usable.

Spends one The Odds API historical US/h2h request and makes public Kalshi
historical-data reads. No order entry and no trading-state writes.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json, os
from typing import Any
import httpx

ODDS_URL = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
PROBE_DATE = "2026-04-10T21:40:00Z"
KALSHI_TICKER = "KXMLBGAME-26APR101840AZPHI-AZ"


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _match(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        teams = {str(event.get("home_team") or "").lower(), str(event.get("away_team") or "").lower()}
        if any("arizona" in x for x in teams) and any("philadelphia" in x for x in teams):
            return event
    return None


def _close(side: Any) -> float | None:
    if not isinstance(side, dict): return None
    for key in ("close_dollars", "close"):
        if side.get(key) is not None:
            try: return float(side[key])
            except (TypeError, ValueError): return None
    return None


def main() -> None:
    key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not key: raise SystemExit("THE_ODDS_API_KEY is required")
    out: dict[str, Any] = {"status":"STARTED","read_only":True,"order_entry":False,"probe_date_requested":PROBE_DATE,"kalshi_ticker":KALSHI_TICKER}
    with httpx.Client(timeout=30.0, follow_redirects=True) as http:
        r = http.get(ODDS_URL, params={"apiKey":key,"regions":"us","markets":"h2h","oddsFormat":"decimal","dateFormat":"iso","date":PROBE_DATE})
        out["odds_http_status"] = r.status_code
        out["odds_quota"] = {"remaining":r.headers.get("x-requests-remaining"),"used":r.headers.get("x-requests-used"),"last_cost":r.headers.get("x-requests-last")}
        if r.status_code != 200:
            out["status"]="ODDS_HISTORY_FAILED"; out["odds_error"]=r.text[:500]; print(json.dumps(out,sort_keys=True)); raise SystemExit(2)
        p=r.json(); snap=str(p.get("timestamp") or ""); events=[x for x in (p.get("data") or []) if isinstance(x,dict)]; game=_match(events)
        out["odds_snapshot_timestamp"]=snap; out["odds_event_count"]=len(events); out["odds_matching_game_found"]=game is not None
        if game:
            out["odds_matching_game"]={"id":game.get("id"),"home_team":game.get("home_team"),"away_team":game.get("away_team"),"commence_time":game.get("commence_time"),"bookmaker_count":len(game.get("bookmakers") or [])}
        m=http.get(f"{KALSHI_BASE}/historical/markets/{KALSHI_TICKER}")
        out["kalshi_market_http_status"]=m.status_code
        if m.status_code != 200:
            out["status"]="KALSHI_MARKET_FAILED"; out["kalshi_error"]=m.text[:500]; print(json.dumps(out,sort_keys=True)); raise SystemExit(3)
        mr=(m.json() or {}).get("market") or {}; out["kalshi_market"]={k:mr.get(k) for k in ("ticker","event_ticker","status","result","occurrence_datetime","settlement_ts","title","yes_sub_title")}
        if not snap:
            out["status"]="ODDS_SNAPSHOT_TIMESTAMP_MISSING"; print(json.dumps(out,sort_keys=True)); raise SystemExit(4)
        sts=_ts(snap)
        c=http.get(f"{KALSHI_BASE}/historical/markets/{KALSHI_TICKER}/candlesticks",params={"start_ts":sts-300,"end_ts":sts+300,"period_interval":1})
        out["kalshi_candles_http_status"]=c.status_code
        if c.status_code != 200:
            out["status"]="KALSHI_CANDLES_FAILED"; out["kalshi_candles_error"]=c.text[:500]; print(json.dumps(out,sort_keys=True)); raise SystemExit(5)
        rows=[x for x in ((c.json() or {}).get("candlesticks") or []) if isinstance(x,dict)]; nearest=None; delta=None
        for row in rows:
            try: d=abs(int(row.get("end_period_ts"))-sts)
            except (TypeError,ValueError): continue
            if delta is None or d<delta: delta=d; nearest=row
        out["kalshi_candle_count_10m_window"]=len(rows); out["alignment_delta_seconds"]=delta; out["alignment_within_120s"]=delta is not None and delta<=120
        if nearest: out["aligned_kalshi_candle"]={"end_period_ts":nearest.get("end_period_ts"),"yes_bid_close":_close(nearest.get("yes_bid")),"yes_ask_close":_close(nearest.get("yes_ask")),"trade_price_close":_close(nearest.get("price")),"volume":nearest.get("volume_fp",nearest.get("volume"))}
        out["status"]="HISTORY_PROBE_OK" if game is not None and out["alignment_within_120s"] else "PROBE_INCOMPLETE"
        print(json.dumps(out,sort_keys=True,default=str))
        if out["status"] != "HISTORY_PROBE_OK": raise SystemExit(6)

if __name__ == "__main__": main()
