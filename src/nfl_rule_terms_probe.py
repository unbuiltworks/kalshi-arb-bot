"""Read-only current NFL contract-terms fingerprint probe for Gemini + Kalshi.

Fetches one active NFL moneyline/spread/total event from each venue and captures the
exact contract rule/terms surfaces that must be audited before any alert/execution.
No auth, no prices, no orders.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
from typing import Any, Mapping

import httpx
from pypdf import PdfReader

GEMINI_LIST = "https://api.gemini.com/v1/prediction-markets/events"
GEMINI_GET = "https://api.gemini.com/v1/prediction-markets/events/{ticker}"
KALSHI_EVENTS = "https://external-api.kalshi.com/trade-api/v2/events"
SERIES = {"moneyline":"KXNFLGAME","spread":"KXNFLSPREAD","total":"KXNFLTOTAL"}
KEYWORDS = ("tie", "overtime", "postpon", "cancel", "resched", "invalid", "void", "fair price", "settle", "resolution", "winner", "score")


def _contexts(text: str, radius: int = 180) -> list[str]:
    clean = re.sub(r"\s+", " ", text or " ").strip()
    low = clean.lower()
    out: list[str] = []
    for kw in KEYWORDS:
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx < 0:
                break
            snippet = clean[max(0, idx-radius):min(len(clean), idx+len(kw)+radius)]
            if snippet not in out:
                out.append(snippet)
            start = idx + len(kw)
            if len(out) >= 18:
                return out
    return out


async def _terms_text(http: httpx.AsyncClient, url: str) -> dict[str, Any]:
    if not url:
        return {"url": "", "status": "missing", "contexts": []}
    try:
        r = await http.get(url, follow_redirects=True)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "pdf" in ctype or r.content[:4] == b"%PDF":
            reader = PdfReader(io.BytesIO(r.content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
            kind = "pdf"
        else:
            text = r.text
            kind = "text"
        return {
            "url": url,
            "status": r.status_code,
            "content_type": ctype,
            "kind": kind,
            "text_chars": len(text),
            "contexts": _contexts(text),
        }
    except Exception as exc:
        return {"url": url, "status": "error", "error_type": type(exc).__name__, "error": str(exc)[:180], "contexts": []}


async def gemini_probe(http: httpx.AsyncClient) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for family in ("moneyline", "spread", "total"):
        r = await http.get(GEMINI_LIST, params={
            "status":"active", "sport":"american_football", "sports_market_type":family,
            "limit":100, "offset":0,
        })
        r.raise_for_status()
        body = r.json()
        events = [x for x in body.get("data") or body.get("events") or [] if isinstance(x, Mapping)]
        event = next((x for x in events if "NFL" in str(x.get("ticker") or "").upper()), None)
        if event is None:
            result[family] = {"status":"no_active_nfl_event"}
            continue
        ticker = str(event.get("ticker") or "")
        detail = await http.get(GEMINI_GET.format(ticker=ticker))
        detail.raise_for_status()
        row = detail.json()
        contracts = [x for x in row.get("contracts") or [] if isinstance(x, Mapping)]
        sample = contracts[0] if contracts else {}
        terms_url = str(sample.get("termsAndConditionsUrl") or "")
        result[family] = {
            "ticker": ticker,
            "title": row.get("title"),
            "sportsMarket": row.get("sportsMarket"),
            "status": row.get("status"),
            "expiryDate": row.get("expiryDate"),
            "contract": {
                "instrumentSymbol": sample.get("instrumentSymbol"),
                "label": sample.get("label"),
                "ticker": sample.get("ticker"),
                "strike": sample.get("strike"),
                "expiryDate": sample.get("expiryDate"),
                "termsAndConditionsUrl": terms_url,
            },
            "terms": await _terms_text(http, terms_url),
        }
    return result


async def _kalshi_series(http: httpx.AsyncClient, series: str) -> list[dict[str, Any]]:
    last: httpx.Response | None = None
    for attempt in range(5):
        r = await http.get(KALSHI_EVENTS, params={
            "limit":50,"status":"open","series_ticker":series,"with_nested_markets":"true",
        })
        last = r
        if r.status_code != 429:
            r.raise_for_status()
            body = r.json()
            return [dict(x) for x in body.get("events") or [] if isinstance(x, Mapping)]
        await asyncio.sleep(min(4.0, 0.5*(2**attempt)))
    assert last is not None
    last.raise_for_status()
    return []


async def kalshi_probe(http: httpx.AsyncClient) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for family, series in SERIES.items():
        events = await _kalshi_series(http, series)
        event = events[0] if events else {}
        markets = [x for x in event.get("markets") or [] if isinstance(x, Mapping)]
        market = markets[0] if markets else {}
        # Kalshi API may expose rule text directly on event/market; capture only
        # rule-relevant scalar keys instead of dumping the full book.
        relevant: dict[str, Any] = {}
        for source_name, source in (("event", event), ("market", market)):
            for key, value in source.items():
                k = str(key).lower()
                if any(token in k for token in ("rule","settle","result","close","expiration","strike","title","subtitle")):
                    if isinstance(value, (str,int,float,bool)) or value is None:
                        relevant[f"{source_name}.{key}"] = value
        rule_text = " ".join(str(v) for k,v in relevant.items() if "rule" in k.lower() and v)
        result[family] = {
            "series": series,
            "event_ticker": event.get("event_ticker"),
            "market_ticker": market.get("ticker"),
            "relevant_fields": relevant,
            "rule_contexts": _contexts(rule_text),
        }
        await asyncio.sleep(0.15)
    return result


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(gemini_probe(http), kalshi_probe(http))
    return {
        "status":"NFL_RULE_TERMS_PROBE",
        "gemini":gemini,
        "kalshi":kalshi,
        "read_only":True,
        "order_entry":False,
        "certification":"UNAUDITED_DO_NOT_ALERT_OR_EXECUTE",
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), separators=(",",":"), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
