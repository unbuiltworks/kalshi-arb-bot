"""CAPACITY COLLECTOR: real ladders over time, per duration, per size.

WHY A SNAPSHOT IS NOT ENOUGH, measured rather than assumed. At 00:21Z on
GEMI-BTC15M2610040030-UP the ladder was bid 0.63 x350 / ask 0.74 x350, so
the SIMULTANEOUS pair cost was 1.11 at q=1 AND at q=100 -- eleven cents
underwater at every size. A table of simultaneous pair costs therefore
measures something the strategy never does.

The continuous mechanism does not buy both legs at once. It OPENs one leg
and WAITS for the opposite side to come to it, so its economics are a
property of the ladder OVER TIME:

  * did an OPEN qualify at this size at all (displayed depth);
  * how long until the opposite ask fell to (ceiling - our leg's vwap);
  * what pair cost was actually reachable;
  * what was left unmatched when the window expired.

So this records a TIME SERIES of both durations' ladders with the
per-size walk economics attached, and the metrics are computed from it
afterwards. Nothing here trades, and nothing here prices a size the
displayed depth does not show.

DATASETS STAY SEPARATE: BTC15m and BTC5m write to their own files under
their own duration dataset roots, because a $/window figure cannot be
pooled across different window lengths.

READ-ONLY AND PUBLIC. `contract_depth` is an unauthenticated GET and
never raises; discovery is the public events list. No credential is read
and no order path is imported.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import capacity_ladder as CAP
from src.gemini_execution import gemini_durations as DUR
from src.gemini_execution import metadata as MD
from src.gemini_execution.ci15m_policy import POLICY

LABEL = "GEMINI_CAPACITY_TAPE_V1"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events() -> List[Dict[str, Any]]:
    try:
        from src.gemini_execution.r2_exec_runner import _active_events_public
        return _active_events_public() or []
    except Exception:                                          # noqa: BLE001
        return []


def live_markets(duration: str, now: float) -> List[Dict[str, Any]]:
    """Live contracts of ONE duration, soonest expiry first."""
    out = []
    for e in _events():
        tk = str(e.get("ticker") or "")
        if not DUR.matches(duration, tk):
            continue
        c = (e.get("contracts") or [{}])[0]
        exp = c.get("expiryDate")
        if not exp:
            continue
        try:
            ts = datetime.fromisoformat(
                str(exp).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        out.append({"ticker": tk, "symbol": "GEMI-%s-UP" % tk,
                    "expiry_ts": ts, "tte": ts - now})
    return sorted(out, key=lambda m: m["expiry_ts"])


def sample(duration: str, now: float, *,
           levels: int = 10) -> List[Dict[str, Any]]:
    """One tick: every live window of this duration, with per-size walks."""
    band = DUR.effective_open_band(duration)
    lo, hi = (band["band"] or (None, None))
    rows: List[Dict[str, Any]] = []
    for m in live_markets(duration, now):
        if m["tte"] <= 0:
            continue
        depth = MD.contract_depth(m["symbol"], levels=levels)
        if not depth.get("readable"):
            rows.append({"utc": _utc(), "duration": duration,
                         "ticker": m["ticker"], "tte": round(m["tte"], 1),
                         "readable": False,
                         "why": depth.get("why") or "depth unreadable"})
            continue
        cur = CAP.capacity_curve(depth,
                                 pair_ceiling=POLICY["MAX_PAIR_COST"])
        in_band = (lo is not None and lo <= m["tte"] <= hi)
        rows.append({
            "utc": _utc(), "duration": duration, "ticker": m["ticker"],
            "tte": round(m["tte"], 1), "readable": True,
            "in_frozen_open_band": bool(in_band),
            "frozen_open_band": [lo, hi],
            "band_basis": band["basis"],
            "ladder": cur["depth_shown"],
            "snapshot_age_s": depth.get("snapshot_age_s"),
            "sizes": [{
                "qty": r["qty"],
                "up_vwap": r["up"]["vwap"],
                "up_filled": r["up"]["filled"],
                "up_fully_executable": r["up"]["fully_executable"],
                "up_slippage_vs_touch": r["up"]["slippage_vs_touch"],
                "up_levels": r["up"]["n_levels_consumed"],
                "down_vwap": r["down"]["vwap"],
                "down_filled": r["down"]["filled"],
                "down_fully_executable": r["down"]["fully_executable"],
                "down_slippage_vs_touch": r["down"]["slippage_vs_touch"],
                "simultaneous_pair_cost": r["pair_cost"],
                "simultaneous_within_ceiling": r["pair_cost_within_ceiling"],
            } for r in cur["rows"]],
            "max_defensible_simultaneous_pair_qty":
                cur["max_defensible_pair_qty"],
            "labels": ("simultaneous_* is the SAME-INSTANT pair, recorded "
                       "for reference only. The continuous mechanism waits "
                       "for the complement, so its economics are computed "
                       "from this tape over time, not from one tick."),
        })
    return rows


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--durations", default="BTC15M,BTC5M")
    ap.add_argument("--seconds", type=float, default=3600.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--levels", type=int, default=10)
    a = ap.parse_args(argv)

    durations = [d.strip().upper() for d in a.durations.split(",")
                 if d.strip()]
    paths: Dict[str, str] = {}
    for d in durations:
        root = DUR.dataset_root(d)
        os.makedirs(root, exist_ok=True)
        paths[d] = os.path.join(
            root, "capacity_tape_%s.jsonl"
            % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    print(json.dumps({"label": LABEL, "durations": durations,
                      "paths": paths, "interval_s": a.interval,
                      "seconds": a.seconds,
                      "pair_ceiling": str(POLICY["MAX_PAIR_COST"]),
                      "bands": {d: DUR.effective_open_band(d)["band"]
                                for d in durations},
                      "labels": ("READ-ONLY public ladder tape. Separate "
                                 "file per duration; never pooled.")},
                     indent=2, default=str), flush=True)

    deadline = time.time() + a.seconds
    n = {d: 0 for d in durations}
    while time.time() < deadline:
        now = time.time()
        for d in durations:
            try:
                rows = sample(d, now, levels=a.levels)
            except Exception as exc:                           # noqa: BLE001
                rows = [{"utc": _utc(), "duration": d, "readable": False,
                         "why": "sample raised %s" % exc}]
            if rows:
                with open(paths[d], "a", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, sort_keys=True,
                                            default=str) + "\n")
                n[d] += len(rows)
        time.sleep(a.interval)
    print(json.dumps({"label": LABEL, "done_utc": _utc(), "rows": n,
                      "paths": paths}, indent=2, default=str))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
