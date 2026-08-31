"""Gemini full-venue capacity census (read-only, audit workstream 2026-08-31).

Question: is Gemini small, or were we looking through the crypto keyhole?
Measures, per prediction category, from the public events API:

  * active events: count, total volume, 24h volume, median contract
    duration (effective -> expiry = capital recycle time), subcategory mix;
  * recently settled events (last 14 days, found by walking to the END of
    the oldest-first settled listing): count/day, volume/day, median
    duration;
  * capture scenarios: theoretical $/day at 20/40/60% capture and
    0.2/0.5/1.0c net per turnover dollar.

UNITS CAVEAT: the API 'volume' fields are event contract volume (Gemini
reports venue size in contracts). Dollar turnover is approximated as
contracts x $0.50 average premium; ranking and relative sizes are robust to
this, absolute dollars carry the assumption.

Rebate eligibility (20-80c fills) and Liquidity Rewards pool sizes are NOT
readable from this endpoint — Sept 2 live verification covers those.

Runs fully parallel to the frozen L-series; touches nothing sealed.

    python3 -m src.gemini_maker.venue_census
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = "https://api.gemini.com/v1/prediction-markets/events"
OUT = Path("analysis/gemini/venue_census.json")

CATEGORIES = ("Crypto", "Sports", "Economics", "Politics", "Tech",
              "Business", "Commodities", "Weather", "Culture")
PAGE = 500
MAX_ACTIVE_PAGES = 12
SETTLED_LOOKBACK_DAYS = 14
SETTLED_TAIL_EVENTS = 6000          # pull at most this many newest settled
AVG_PREMIUM = 0.50                  # contracts -> $ proxy; stated caveat
CAPTURES = (0.20, 0.40, 0.60)
NET_PER_DOLLAR = (0.002, 0.005, 0.010)


def _get(params: Dict[str, Any], retries: int = 4) -> Optional[List[dict]]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}?{qs}", headers={
        "User-Agent": "kalshi-arb-bot/venue-census (read-only public data)",
        "Accept": "application/json"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
            if isinstance(d, dict) and "data" in d:
                return d["data"]
            if isinstance(d, list):
                return d
            return None   # {"error": ...}
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def _dur_h(e: dict) -> Optional[float]:
    try:
        eff = datetime.fromisoformat(e["effectiveDate"].replace("Z", "+00:00"))
        exp = datetime.fromisoformat(e["expiryDate"].replace("Z", "+00:00"))
        return (exp - eff).total_seconds() / 3600
    except Exception:
        return None


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def census_category(cat: str) -> Dict[str, Any]:
    # -- active --
    active: List[dict] = []
    for page in range(MAX_ACTIVE_PAGES):
        items = _get({"status": "active", "category": cat, "limit": PAGE,
                      "offset": page * PAGE})
        if not items:
            break
        active.extend(items)
        if len(items) < PAGE:
            break

    # -- settled tail: find the end of the oldest-first listing --
    hi = PAGE
    while True:
        items = _get({"status": "settled", "category": cat, "limit": 1,
                      "offset": hi})
        if not items:
            break
        hi *= 2
        if hi > 500_000:
            break
    lo, end = hi // 2, hi
    while lo < end:
        mid = (lo + end) // 2
        items = _get({"status": "settled", "category": cat, "limit": 1,
                      "offset": mid})
        if items:
            lo = mid + 1
        else:
            end = mid
    total_settled = lo
    start = max(0, total_settled - SETTLED_TAIL_EVENTS)
    settled: List[dict] = []
    off = start
    while off < total_settled:
        items = _get({"status": "settled", "category": cat, "limit": PAGE,
                      "offset": off})
        if not items:
            break
        settled.extend(items)
        off += len(items)

    cutoff = datetime.now(timezone.utc) - timedelta(days=SETTLED_LOOKBACK_DAYS)
    recent = []
    for e in settled:
        try:
            exp = datetime.fromisoformat(e["expiryDate"].replace("Z", "+00:00"))
        except Exception:
            continue
        if exp >= cutoff:
            recent.append((exp, e))
    by_day = defaultdict(float)
    for exp, e in recent:
        by_day[exp.strftime("%m-%d")] += _f(e.get("volume"))
    settled_vol = sum(_f(e.get("volume")) for _, e in recent)
    days_covered = max(len(by_day), 1)

    contracts_per_day = settled_vol / days_covered
    dollars_per_day = contracts_per_day * AVG_PREMIUM
    scen = {}
    for cap in CAPTURES:
        for net in NET_PER_DOLLAR:
            scen[f"cap{int(cap*100)}_net{net*100:.1f}c"] = round(
                dollars_per_day * cap * net, 1)

    subcats = defaultdict(float)
    for e in active:
        sc = e.get("subcategory")
        if isinstance(sc, dict):
            sc = sc.get("name") or sc.get("title") or sc.get("slug")
        subcats[str(sc or "?")] += _f(e.get("volume24h"))

    return {
        "active_events": len(active),
        "active_volume24h_contracts": round(
            sum(_f(e.get("volume24h")) for e in active), 1),
        "active_total_volume_contracts": round(
            sum(_f(e.get("volume")) for e in active), 1),
        "active_median_duration_h": round(_median(
            [_dur_h(e) for e in active]) or 0, 1),
        "settled_total_listed": total_settled,
        "settled_recent_events": len(recent),
        "settled_recent_days_covered": len(by_day),
        "settled_contracts_per_day": round(contracts_per_day, 1),
        "settled_median_duration_h": round(_median(
            [_dur_h(e) for _, e in recent]) or 0, 1),
        "est_dollars_per_day": round(dollars_per_day, 1),
        "capture_scenarios_usd_per_day": scen,
        "active_subcategory_volume24h": dict(sorted(
            subcats.items(), key=lambda x: -x[1])[:8]),
    }


def main() -> int:
    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "units_caveat": ("volume fields are event contracts; dollars assume "
                         f"{AVG_PREMIUM:.2f} avg premium/contract"),
        "lookback_days": SETTLED_LOOKBACK_DAYS,
        "categories": {},
    }
    for cat in CATEGORIES:
        print(f"census: {cat}...", flush=True)
        try:
            out["categories"][cat] = census_category(cat)
        except Exception as exc:
            out["categories"][cat] = {"error": repr(exc)}
    ranked = sorted(
        ((c, v) for c, v in out["categories"].items() if "error" not in v),
        key=lambda x: -x[1]["est_dollars_per_day"])
    out["ranked_by_est_dollars_per_day"] = [
        {"category": c,
         "est_dollars_per_day": v["est_dollars_per_day"],
         "median_duration_h": v["settled_median_duration_h"],
         "dollars_per_day_at_cap20_net1c":
             v["capture_scenarios_usd_per_day"].get("cap20_net1.0c")}
        for c, v in ranked]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\n{'category':<12}{'act_ev':>7}{'vol24h_ct':>11}{'set/day_ct':>11}"
          f"{'$/day est':>11}{'medDur_h':>9}{'$/d@20%,1c':>11}")
    for c, v in ranked:
        print(f"{c:<12}{v['active_events']:>7}"
              f"{v['active_volume24h_contracts']:>11,.0f}"
              f"{v['settled_contracts_per_day']:>11,.0f}"
              f"{v['est_dollars_per_day']:>11,.0f}"
              f"{v['settled_median_duration_h']:>9.1f}"
              f"{v['capture_scenarios_usd_per_day'].get('cap20_net1.0c', 0):>11,.1f}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
