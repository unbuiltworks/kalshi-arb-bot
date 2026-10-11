"""Mar 27 breakpoint control: trader activation vs venue-liquidity shift.

Question (issue #167): did L5 change behavior on Mar 26/27, or did the
BTC-15m venue itself become materially more liquid/attractive at that
moment, making an unchanged policy suddenly viable?

Measures, per sampled BTC-15m window market, from PUBLIC read-only APIs:

  * total trade count (all participants) — found by binary-searching the
    end of the data-api /trades?market=<conditionId> listing;
  * dollar volume (full when the tape fits the fetch budget, otherwise
    scaled from the fetched fraction and flagged as an estimate);
  * unique wallets, L5's own share of volume;
  * CLOB rewards configuration (min_size / max_spread / rates) — was the
    reward config what changed?

SELECTION CAVEAT (stated, not hidden): the sampled pre-breakpoint windows
are the ones L5 chose to trade (we have no slug->conditionId resolver for
untouched March windows — gamma no longer lists them). If L5 picked the
livelier windows, pre-era volume is biased UP, which makes any measured
post-era increase a LOWER bound on the true venue shift.

    python3 -m src.polymarket_l5.venue_control
"""
from __future__ import annotations

import csv
import gzip
import json
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config

LEDGER_WINDOWS = config.OUTPUT_DIR / "l5_ledger_windows.csv.gz"
OUT = config.OUTPUT_DIR / "l5_venue_control.json"

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"

PRE_DAYS = ("2026-03-19", "2026-03-26")     # inclusive span, L5-traded windows
POST_DAYS = ("2026-03-27", "2026-03-28")
MAX_POST_SAMPLE = 24
PAGE = 500
FETCH_PAGE_BUDGET = 6          # full volume when count <= 3000
OFFSET_PROBE_CAP = 10000       # observed API offset ceiling


def _get(url: str, retries: int = 3) -> Optional[Any]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "kalshi-arb-bot/l5-venue-control (read-only research)"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1.0 * (i + 1))
    return None


def _count_trades(cid: str) -> Optional[int]:
    """Binary-search the end of the /trades listing (1-row probes)."""
    probe = _get(f"{DATA_API}/trades?market={cid}&limit=1&offset={OFFSET_PROBE_CAP}")
    if isinstance(probe, list) and probe:
        return OFFSET_PROBE_CAP  # >= cap; flagged upstream
    lo, hi = 0, OFFSET_PROBE_CAP
    while lo < hi:
        mid = (lo + hi) // 2
        rows = _get(f"{DATA_API}/trades?market={cid}&limit=1&offset={mid}")
        if isinstance(rows, list) and rows:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _market_stats(cid: str) -> Dict[str, Any]:
    n = _count_trades(cid)
    fetched: List[dict] = []
    for page in range(FETCH_PAGE_BUDGET):
        rows = _get(f"{DATA_API}/trades?market={cid}&limit={PAGE}"
                    f"&offset={page * PAGE}")
        if not isinstance(rows, list) or not rows:
            break
        fetched.extend(rows)
        if len(rows) < PAGE:
            break
    vol = sum(float(t.get("size", 0)) * float(t.get("price", 0))
              for t in fetched)
    l5_vol = sum(float(t.get("size", 0)) * float(t.get("price", 0))
                 for t in fetched
                 if str(t.get("proxyWallet", "")).lower() == L5_WALLET)
    wallets = {str(t.get("proxyWallet", "")).lower() for t in fetched}
    complete = n is not None and len(fetched) >= n
    est_vol = vol if complete else (vol * n / max(len(fetched), 1)
                                    if n and fetched else None)
    rewards = None
    clob = _get(f"{CLOB_API}/markets/{cid}")
    if isinstance(clob, dict):
        rewards = clob.get("rewards")
    return {
        "trades_total": n,
        "count_at_probe_cap": n == OFFSET_PROBE_CAP,
        "trades_fetched": len(fetched),
        "volume_usd": round(est_vol, 2) if est_vol is not None else None,
        "volume_is_estimate": not complete,
        "unique_wallets_in_fetched": len(wallets),
        "l5_share_of_fetched_volume": round(l5_vol / vol, 4) if vol else None,
        "rewards_config": rewards,
    }


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[len(xs) // 2], 2) if xs else None


def main() -> int:
    by_day: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    with gzip.open(LEDGER_WINDOWS, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["asset"] != "BTC" or r["horizon_m"] != "15":
                continue
            day = datetime.fromtimestamp(float(r["first_ts"]),
                                         tz=timezone.utc).strftime("%Y-%m-%d")
            by_day[day].append(r)

    pre = [r for d, rows in by_day.items()
           if PRE_DAYS[0] <= d <= PRE_DAYS[1] for r in rows]
    post_all = [r for d, rows in by_day.items()
                if POST_DAYS[0] <= d <= POST_DAYS[1] for r in rows]
    step = max(len(post_all) // MAX_POST_SAMPLE, 1)
    post = post_all[::step][:MAX_POST_SAMPLE]

    cohorts: Dict[str, Any] = {}
    for name, sel in (("pre_mar19_26", pre), ("post_mar27_28", post)):
        stats = []
        for r in sel:
            s = _market_stats(r["condition_id"])
            s["slug"] = r["slug"]
            s["l5_buy_usdc"] = round(float(r["buy_usdc"]), 2)
            stats.append(s)
            print(f"{name} {r['slug']}: trades={s['trades_total']} "
                  f"vol=${s['volume_usd']} rewards={s['rewards_config']}",
                  flush=True)
        rw_min = [s["rewards_config"].get("min_size") for s in stats
                  if isinstance(s.get("rewards_config"), dict)]
        rw_spread = [s["rewards_config"].get("max_spread") for s in stats
                     if isinstance(s.get("rewards_config"), dict)]
        cohorts[name] = {
            "n_markets_sampled": len(stats),
            "trades_total_median": _median([s["trades_total"] for s in stats]),
            "volume_usd_median": _median([s["volume_usd"] for s in stats]),
            "unique_wallets_median": _median(
                [s["unique_wallets_in_fetched"] for s in stats]),
            "l5_share_median": _median(
                [s["l5_share_of_fetched_volume"] for s in stats]),
            "rewards_min_size_values": sorted(set(rw_min)),
            "rewards_max_spread_values": sorted(set(rw_spread)),
            "markets": stats,
        }

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "question": ("Mar 27: trader activation vs venue-liquidity regime "
                     "shift (BTC-15m)"),
        "selection_caveat": ("pre-era sample = windows L5 chose to trade; "
                             "if he picked livelier windows this biases "
                             "pre-era volume UP, so a post-era increase is "
                             "a lower bound"),
        "cohorts": cohorts,
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
