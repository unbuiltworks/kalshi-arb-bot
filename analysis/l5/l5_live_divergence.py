#!/usr/bin/env python3
"""L5's REAL trades vs our engine's gates, from the restored v2 feed.

WHY THIS EXISTS. Issue #194 item 7 asks for a continuous comparison of L5's
real trades against our engine's decisions on side, price, size, timing,
inventory and economics. Until 2026-11-05 that question was unanswerable from
production: Polymarket retired Data API v1, `l5_activity` swallowed the HTTP
410, and the executor ingested ZERO rows while counting 2,013 feed errors. The
v2 migration restored the feed, so this measures the divergence directly
instead of inferring it from replay.

WHAT IT MEASURES, against the DEPLOYED gate values rather than code defaults:
  * timing   -- seconds into the 900s window, vs entry/complete cutoffs
  * price    -- own-price, vs the opening band
  * size     -- contracts, vs our opening clip
  * economics-- whether L5's own cheapest UP + cheapest DOWN clears our pair
                ceiling in each window

WHAT IT IS NOT. These are L5's FILLS, not his resting orders, and our band
applies to our own opening QUOTE. A divergence here is not evidence that
closing it is profitable: the 522-window replay has our residual landing on
the losing side 81% of the time against L5's 57%, so copying late or wide
behaviour could amplify losses rather than earn his P&L. This file reports
the gap and draws no promotion conclusion.

READ-ONLY. No order, no submission, no ledger write.
"""
from __future__ import annotations

import collections
import datetime as dt
import sys
import time
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_pmus_realtime_observer as OBS      # noqa: E402

#: THE DEPLOYED VALUES, read from L5_EFFECTIVE_RUNTIME_CONFIG on deployment
#: 1f4fe0e3 at 2026-11-05T21:24:23Z -- not code defaults, because the two
#: have diverged before and the band in force is what decides.
WINDOW_S = 900.0
ENTRY_CUTOFF_S = 480.0
COMPLETE_CUTOFF_S = 600.0
BAND_LO, BAND_HI = D("0.30"), D("0.60")
PAIR_CEILING = D("0.96")
OPENING_CLIP = D("1")

BTC15M_PREFIX = "btc-updown-15m-"


def _own(row) -> D | None:
    try:
        return D(str(row.get("price")))
    except Exception:                                          # noqa: BLE001
        return None


def _offset(row) -> float | None:
    """Seconds into the window. The v2 slug carries the window start epoch."""
    try:
        ws = float(str(row.get("slug")).rsplit("-", 1)[1])
        off = float(row.get("timestamp")) - ws
    except Exception:                                          # noqa: BLE001
        return None
    return off if 0.0 <= off <= WINDOW_S + 60.0 else None


def collect(limit: int = 500):
    rows = OBS.l5_activity(limit=limit)
    if rows is None:
        raise SystemExit("FEED FAILED: %s" % (OBS.last_feed_failure(),))
    return [r for r in rows
            if str(r.get("slug") or "").startswith(BTC15M_PREFIX)]


def report(btc) -> dict:
    out: dict = {"btc15m_rows": len(btc)}
    if not btc:
        return out

    prices = [p for p in (_own(r) for r in btc) if p is not None]
    offs = sorted(o for o in (_offset(r) for r in btc) if o is not None)
    sizes = []
    for r in btc:
        try:
            sizes.append(D(str(r.get("size"))))
        except Exception:                                      # noqa: BLE001
            pass

    # ---- timing, which the 2026-11-05 sample made the dominant gap
    if offs:
        n = len(offs)
        out["timing"] = {
            "median_s": offs[n // 2],
            "p90_s": offs[int(0.9 * n)],
            "after_entry_cutoff_frac": round(
                sum(1 for o in offs if o > ENTRY_CUTOFF_S) / n, 4),
            "after_complete_cutoff_frac": round(
                sum(1 for o in offs if o > COMPLETE_CUTOFF_S) / n, 4),
            "entry_cutoff_s": ENTRY_CUTOFF_S,
        }

    # ---- price against the opening band in force
    if prices:
        m = len(prices)
        ps = sorted(prices)
        out["price"] = {
            "median_own": str(ps[m // 2]),
            "below_band_frac": round(
                sum(1 for p in prices if p < BAND_LO) / m, 4),
            "above_band_frac": round(
                sum(1 for p in prices if p > BAND_HI) / m, 4),
            "inside_band_frac": round(
                sum(1 for p in prices if BAND_LO <= p <= BAND_HI) / m, 4),
            "band": [str(BAND_LO), str(BAND_HI)],
        }

    # ---- size against our clip
    if sizes:
        zs = sorted(sizes)
        out["size"] = {"median_contracts": str(zs[len(zs) // 2]),
                       "our_clip": str(OPENING_CLIP),
                       "ratio_to_our_clip": str(
                           (zs[len(zs) // 2] / OPENING_CLIP).quantize(D("0.1")))}

    # ---- economics: is a pair inside our ceiling even available?
    wins = collections.defaultdict(list)
    for r in btc:
        wins[str(r.get("slug"))].append(r)
    feasible = infeasible = 0
    for rs in wins.values():
        ups = [p for p in (_own(x) for x in rs
                           if str(x.get("outcome", "")).upper().startswith("UP"))
               if p is not None]
        dns = [p for p in (_own(x) for x in rs
                           if not str(x.get("outcome", "")).upper().startswith("UP"))
               if p is not None]
        if not ups or not dns:
            continue
        if min(ups) + min(dns) <= PAIR_CEILING:
            feasible += 1
        else:
            infeasible += 1
    out["pair_economics"] = {
        "windows_both_sides": feasible + infeasible,
        "cheapest_pair_within_ceiling": feasible,
        "cheapest_pair_above_ceiling": infeasible,
        "ceiling": str(PAIR_CEILING),
    }
    out["windows_sampled"] = len(wins)
    newest = max((float(r.get("timestamp") or 0) for r in btc), default=0)
    if newest:
        out["newest_trade_utc"] = dt.datetime.fromtimestamp(
            newest, dt.timezone.utc).isoformat()
        out["newest_trade_age_s"] = round(time.time() - newest, 1)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(report(collect()), indent=2))
