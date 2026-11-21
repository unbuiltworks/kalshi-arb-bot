"""Pull Gemini BTC 5m / 15m / 1h tape + resolutions, 2026-09-16 .. 09-22.

AUTHORITATIVE PUBLIC ENDPOINTS ONLY:
  GET /v1/trades/{symbol}                      the venue's own tape
  GET /v1/prediction-markets/events/{ticker}   strikes, resolutionSide, settlementValue

Retention was probed before writing this: 09-16 returns trades, 09-14 returns
an empty list, 09-10 is rejected as an unknown symbol. So ~7 days is the whole
usable history and the split below is the most that exists, not a choice.

RATE LIMITED GLOBALLY. A shared token bucket, not a per-thread sleep, because
per-thread sleeps multiply into a burst the venue sees as one client.
"""
import json, sys, time, threading, urllib.request, urllib.error
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.gemini.com"
RATE = 5.0                      # requests per second, global
_lock = threading.Lock()
_next = [0.0]


def _slot():
    with _lock:
        now = time.time()
        t = max(now, _next[0])
        _next[0] = t + 1.0 / RATE
    d = t - time.time()
    if d > 0:
        time.sleep(d)


def get(path, tries=3):
    for i in range(tries):
        _slot()
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return {"__error__": f"HTTP {e.code}"}
            if i == tries - 1:
                return {"__error__": f"HTTP {e.code}"}
            time.sleep(1.0 * (i + 1))
        except Exception as e:                                  # noqa: BLE001
            if i == tries - 1:
                return {"__error__": str(e)}
            time.sleep(1.0 * (i + 1))
    return {"__error__": "unreachable"}


def trades(sym):
    d = get(f"/v1/trades/{sym}?limit_trades=500")
    if isinstance(d, dict):
        return None
    return [[int(t["timestamp"]), str(t.get("type", "")).lower(),
             str(t["price"]), str(t["amount"])]
            for t in d if t.get("price") and t.get("amount")]


def event(ticker):
    d = get(f"/v1/prediction-markets/events/{ticker}")
    return None if (isinstance(d, dict) and "__error__" in d) else d


DAYS = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19",
        "2026-09-20", "2026-09-21", "2026-09-22"]
#: BTC5m is restricted to a CONTIGUOUS daily block so the "previous contract"
#: chain the causal gate needs is never broken by sampling.
M5_HOURS = range(10, 18)


def tickers():
    out = []
    for day in DAYS:
        d = dt.date.fromisoformat(day)
        ymd = d.strftime("%y%m%d")
        for h in range(24):
            for m in (0, 15, 30, 45):
                out.append(("15m", f"BTC15M{ymd}{h:02d}{m:02d}", day, h))
        for h in M5_HOURS:
            for m in range(0, 60, 5):
                out.append(("5m", f"BTC05M{ymd}{h:02d}{m:02d}", day, h))
        for h in range(24):
            out.append(("1h", f"BTC{ymd}{h:02d}00", day, h))
    return out


def one(job):
    dur, ticker, day, hour = job
    ev = event(ticker)
    if not ev:
        return None
    cs = ev.get("contracts") or ev.get("data", {}).get("contracts") or []
    if isinstance(ev, dict) and "data" in ev and isinstance(ev["data"], dict):
        cs = ev["data"].get("contracts") or cs
    rows = []
    for c in cs:
        sym = c.get("instrumentSymbol")
        if not sym:
            continue
        tp = trades(sym)
        rows.append({
            "symbol": sym, "duration": dur, "event_ticker": ticker,
            "day": day, "hour": hour,
            "resolution": c.get("resolutionSide"),
            "settlement": c.get("settlementValue"),
            "strike": (c.get("strike") or {}).get("value"),
            "strike_type": (c.get("strike") or {}).get("type"),
            "expiry": c.get("expiryDate"), "start": c.get("effectiveDate"),
            "status": c.get("status"),
            "prints": tp or [],
            "tape_readable": tp is not None,
        })
    return rows


if __name__ == "__main__":
    jobs = tickers()
    print(f"event tickers to fetch: {len(jobs)}", file=sys.stderr, flush=True)
    out = []
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(one, jobs):
            done += 1
            if res:
                out += res
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)} events, {len(out)} contracts",
                      file=sys.stderr, flush=True)
    json.dump(out, open(sys.argv[1], "w"))
    print(f"contracts written: {len(out)}", file=sys.stderr)
