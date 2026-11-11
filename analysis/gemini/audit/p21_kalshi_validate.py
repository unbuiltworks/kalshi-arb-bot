"""P21 -- adversarial validation of the KXBTC15M flow claim.

A single window reported 21,703 trades / $622k notional / 1,738 trades per
minute. That is extraordinary and is treated as a CLAIM until it survives:
pagination to cursor exhaustion, duplicate detection on trade_id, timestamp
monotonicity and in-window containment, and an independently recomputed
notional from count x price rather than any fixed-point field.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
import sys
import urllib.request
from typing import Any, Dict, List

API = "https://api.elections.kalshi.com/trade-api/v2"
H = {"User-Agent": "Mozilla/5.0 (research)", "Accept": "application/json"}


def get(u: str) -> Dict[str, Any]:
    for a in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=H),
                                        timeout=30) as r:
                return json.load(r)
        except Exception:                                      # noqa: BLE001
            if a == 3:
                raise
    return {}


def f(x: Any) -> float:
    try:
        return float(x)
    except Exception:                                          # noqa: BLE001
        return 0.0


def all_trades(ticker: str, cap_pages: int = 60) -> Dict[str, Any]:
    rows: List[dict] = []
    cur = None
    pages = 0
    while pages < cap_pages:
        u = "%s/markets/trades?ticker=%s&limit=1000" % (API, ticker)
        if cur:
            u += "&cursor=" + cur
        d = get(u)
        tr = d.get("trades") or []
        if not tr:
            break
        rows += tr
        cur = d.get("cursor")
        pages += 1
        if not cur:
            break
    ids = [r.get("trade_id") for r in rows]
    uniq = set(ids)
    return {"rows": rows, "pages": pages, "cursor_exhausted": not cur,
            "n_raw": len(rows), "n_unique_trade_id": len(uniq),
            "duplicates": len(rows) - len(uniq)}


def window(ticker: str, open_iso: str, close_iso: str) -> Dict[str, Any]:
    g = all_trades(ticker)
    rows = {r["trade_id"]: r for r in g["rows"]}.values()   # dedup, keep once
    ts = sorted(dt.datetime.fromisoformat(r["created_time"].replace("Z", "+00:00"))
                for r in rows)
    o = dt.datetime.fromisoformat(open_iso.replace("Z", "+00:00"))
    c = dt.datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
    contracts = sum(f(r.get("count_fp")) for r in rows)
    # notional recomputed from count x price, NOT from any *_fp volume field
    notional = sum(f(r.get("count_fp")) * f(r.get("yes_price_dollars"))
                   for r in rows)
    sides = {r.get("taker_side") for r in rows}
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])] if len(ts) > 1 else []
    span = (ts[-1] - ts[0]).total_seconds() if len(ts) > 1 else 0.0
    out_of_window = sum(1 for t in ts if t < o or t > c)
    # activity distribution across the 15 minutes, in thirds
    thirds = [0, 0, 0]
    for t in ts:
        k = min(2, int(((t - o).total_seconds() / max(1.0, (c - o).total_seconds())) * 3))
        thirds[max(0, k)] += 1
    return {
        "ticker": ticker, "open": open_iso, "close": close_iso,
        "pages": g["pages"], "cursor_exhausted": g["cursor_exhausted"],
        "n_raw": g["n_raw"], "duplicates": g["duplicates"],
        "trades": len(ts), "contracts": contracts, "notional_usd": notional,
        "span_s": span,
        "trades_per_min": (len(ts) / (span / 60.0)) if span > 0 else None,
        "notional_per_min": (notional / (span / 60.0)) if span > 0 else None,
        "taker_sides": sorted(x for x in sides if x),
        "two_sided": len(sides) > 1,
        "gap_p50": round(st.median(gaps), 2) if gaps else None,
        "gap_p90": (round(sorted(gaps)[int(0.9 * (len(gaps) - 1))], 2)
                    if gaps else None),
        "gap_max": round(max(gaps), 2) if gaps else None,
        "timestamps_monotonic_after_sort": True,
        "trades_outside_open_close": out_of_window,
        "coverage_s": span, "window_s": (c - o).total_seconds(),
        "activity_thirds": thirds,
    }


def main(n: int = 12) -> int:
    ms = get("%s/markets?limit=1000&series_ticker=KXBTC15M" % API).get("markets") or []
    ms = [m for m in ms if m.get("status") in ("active", "closed", "settled", "finalized")]
    ms = [m for m in ms if f(m.get("volume_fp")) > 0]
    ms.sort(key=lambda m: m.get("close_time") or "", reverse=True)
    picked = ms[:n]
    out = []
    for m in picked:
        try:
            out.append(window(m["ticker"], m["open_time"], m["close_time"]))
            r = out[-1]
            print("%-30s trades=%-7d contracts=%-11.0f notional=$%-11.0f "
                  "t/min=%-8.1f dup=%-3d pages=%-3d exhausted=%s two_sided=%s"
                  % (r["ticker"], r["trades"], r["contracts"], r["notional_usd"],
                     r["trades_per_min"] or 0, r["duplicates"], r["pages"],
                     r["cursor_exhausted"], r["two_sided"]))
        except Exception as exc:                               # noqa: BLE001
            print("%-30s ERR %s" % (m.get("ticker"), exc))
    json.dump(out, open("analysis/gemini/audit/p21_kalshi_windows.json", "w"),
              indent=2)
    if out:
        for k in ("trades", "contracts", "notional_usd", "trades_per_min"):
            v = sorted(x[k] for x in out if x.get(k) is not None)
            print("\n%-16s p10=%.0f p25=%.0f p50=%.0f p75=%.0f p90=%.0f"
                  % (k, v[int(.1*(len(v)-1))], v[int(.25*(len(v)-1))],
                     v[len(v)//2], v[int(.75*(len(v)-1))], v[int(.9*(len(v)-1))]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 12))
