"""Daily watch on Kalshi incentive programs for 15-minute crypto/commodity series.

Answers, on every run:
- which watched series have ACTIVE regular-account liquidity/volume programs now;
- what changed since the last snapshot (new programs, lapsed programs, parameter
  changes) -- in particular whether the crypto-15m incentives renew past their
  current end date;
- whether KXBTC15M / KXETH15M (our unsubsidized trading-edge series) ever gain
  programs.

Distinction maintained: this endpoint is Kalshi's regular-account Rewards
system. The Designated Liquidity Provider listing ("Crypto 15min markets",
currently through Aug 31) is a separate negotiated Market Maker Agreement and
is NOT visible here; a DLP listing never implies our account earns anything.
Incentives are accounting-tracked as incentive_pending only; nothing here may
feed executability or edge math.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
WATCH_SERIES = (
    "KXBTC15M", "KXETH15M",              # our current engine (no programs = unsubsidized)
    "KXCRYPTOLEAD15M",                    # live incentivized 15m crypto series
    "KXGOLD15M", "KXWTI15M", "KXNATGAS15M", "KXCOPPER15M", "KXSILVER15M",
)
STATE_PATH = Path("data/policy_d_port/incentive_watch_state.json")


def _series_of(ticker: str) -> str:
    return ticker.split("-")[0] if ticker else ""


def fetch_programs() -> List[Dict[str, Any]]:
    programs: List[Dict[str, Any]] = []
    cursor = None
    with httpx.Client(timeout=10, headers={"User-Agent": "arb-incentive-watch/1.0"}) as http:
        for _ in range(200):
            params: Dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload = http.get(f"{KALSHI_REST}/incentive_programs", params=params).json()
            batch = payload.get("incentive_programs", [])
            programs.extend(batch)
            cursor = payload.get("next_cursor")
            if not cursor or not batch:
                break
    return [p for p in programs if _series_of(p.get("market_ticker", "")) in WATCH_SERIES]


def summarize(programs: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_series: Dict[str, Dict[str, Any]] = {}
    for series in WATCH_SERIES:
        rows = [p for p in programs if _series_of(p["market_ticker"]) == series]
        active = [p for p in rows if p["start_date"] <= now <= p["end_date"]]
        upcoming = [p for p in rows if p["start_date"] > now]
        by_series[series] = {
            "programs_total": len(rows),
            "active_now": len(active),
            "upcoming": len(upcoming),
            "latest_end_date": max((p["end_date"] for p in rows), default=None),
            "reward_values": sorted({p["period_reward"] for p in rows}, reverse=True)[:3],
            "types": sorted({p["incentive_type"] for p in rows}),
        }
    return {"observed_at": now, "by_series": by_series}


def diff(prev: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    prev_by = (prev or {}).get("by_series", {})
    for series, snap in cur["by_series"].items():
        old = prev_by.get(series, {})
        if old.get("latest_end_date") != snap["latest_end_date"]:
            notes.append(
                f"{series}: horizon {old.get('latest_end_date')} -> {snap['latest_end_date']}"
                + ("  <-- RENEWED/EXTENDED" if (snap["latest_end_date"] or "") > (old.get("latest_end_date") or "") else "")
            )
        if old.get("programs_total", 0) == 0 and snap["programs_total"] > 0:
            notes.append(f"{series}: FIRST PROGRAMS APPEARED ({snap['programs_total']})")
        if old.get("active_now", 0) > 0 and snap["active_now"] == 0 and snap["upcoming"] == 0:
            notes.append(f"{series}: PROGRAMS LAPSED (was {old['active_now']} active)")
    return notes


def run() -> Dict[str, Any]:
    programs = fetch_programs()
    current = summarize(programs)
    previous = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    changes = diff(previous, current)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(current, indent=1, sort_keys=True))
    return {"summary": current, "changes": changes}


if __name__ == "__main__":  # pragma: no cover
    result = run()
    for series, snap in result["summary"]["by_series"].items():
        print(f"{series}: total={snap['programs_total']} active={snap['active_now']} "
              f"upcoming={snap['upcoming']} horizon={snap['latest_end_date']}")
    print("CHANGES:", result["changes"] or "none (baseline or steady state)")
