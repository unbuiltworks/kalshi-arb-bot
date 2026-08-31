"""Score a live canary session against the FROZEN same-window shadow benchmark.

Runbook definition (docs/KALSHI_CANARY_RUNBOOK.md): success is judged only by
real-vs-shadow on the SAME windows the canary quoted:
  1. capture ratio  = real filled notional / shadow filled notional
  2. fill quality   = realized c/$ on real fills vs shadow c/$ same windows
  3. ops cleanliness = no unexplained rejects, no deadman halts, no
     unsettled inventory

Input: the canary session log — either the local JSONL from
data/kalshi_canary/, or a file containing pasted Railway deploy logs (any
non-JSON lines are ignored; canary events are one JSON object per line).

Usage:
  python3 -m src.kalshi_maker.score_canary --canary-log <file> \
      [--tape-dir data/policy_d_port]
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

import httpx

from src.policy_d_port.shadow_eval import evaluate_market, resolve_winner


def parse_canary_log(path: str) -> Dict[str, Any]:
    events: List[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            # Railway prefixes timestamps; find the JSON object in the line.
            idx = line.find('{"')
            if idx < 0:
                continue
            try:
                e = json.loads(line[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and "kind" in e:
                events.append(e)
    quoted: Set[str] = set()
    fills: Dict[str, List[dict]] = defaultdict(list)
    settled: Dict[str, dict] = {}
    rejects: List[dict] = []
    deadman = 0
    live = False
    for e in events:
        k = e.get("kind")
        if k == "canary_start":
            live = bool(e.get("live"))
        elif k == "order_placed":
            quoted.add(e["market"])
        elif k == "dry_run_order":
            quoted.add(e["market"])
        elif k == "fill":
            fills[e["market"]].append(e)
        elif k == "window_settled":
            settled[e["market"]] = e
        elif k == "order_rejected":
            rejects.append(e)
        elif k == "deadman_halt":
            deadman += 1
    unsettled = [m for m in fills if m not in settled]
    return {"live": live, "quoted": sorted(quoted), "fills": dict(fills),
            "settled": settled, "rejects": rejects, "deadman": deadman,
            "unsettled": unsettled, "events": len(events)}


def shadow_for_windows(tape_dir: str, markets: Set[str]) -> Dict[str, dict]:
    by_market: Dict[str, List[dict]] = defaultdict(list)
    for path in sorted(glob.glob(f"{tape_dir}/tape_*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (e.get("venue") == "kalshi"
                        and str(e.get("market")) in markets):
                    by_market[e["market"]].append(e)
    out: Dict[str, dict] = {}
    with httpx.Client(timeout=10.0,
                      headers={"User-Agent": "arb-canary-score/1.0"}) as http:
        for market, rows in by_market.items():
            raw = evaluate_market(rows)   # {prints, bbo_snapshots, fills}
            winner, source = resolve_winner("kalshi", market, rows, http)
            fills = raw.get("fills", [])
            notional = sum(f["price"] * f["qty"] for f in fills)
            fees = sum(f.get("fee", 0.0) for f in fills)
            gross = sum((f["qty"] if f["side"] == winner else 0.0)
                        - f["price"] * f["qty"] for f in fills) if winner else None
            out[market] = {
                "shadow_fills": len(fills),
                "fill_dollars": round(notional, 4),
                "net_pnl": round(gross - fees, 4) if gross is not None else None,
                "winner": winner,
                "winner_source": source,
                "prints": raw.get("prints"),
            }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canary-log", required=True)
    parser.add_argument("--tape-dir", default="data/policy_d_port")
    parser.add_argument("--out", default="analysis/l5/canary_score.json")
    args = parser.parse_args()

    session = parse_canary_log(args.canary_log)
    shadows = shadow_for_windows(args.tape_dir, set(session["quoted"]))

    real_notional = 0.0
    real_realized = 0.0
    per_window = []
    for market in session["quoted"]:
        f = session["fills"].get(market, [])
        notional = sum(x["count"] * x["price"] for x in f)
        realized = session["settled"].get(market, {}).get("realized")
        sh = shadows.get(market, {})
        per_window.append({
            "market": market,
            "real_fills": len(f),
            "real_notional": round(notional, 4),
            "real_realized": realized,
            "shadow_fills": sh.get("shadow_fills"),
            "shadow_notional": sh.get("fill_dollars"),
            "shadow_net": sh.get("net_pnl"),
            "winner": sh.get("winner"),
        })
        real_notional += notional
        if realized is not None:
            real_realized += realized
    shadow_notional = sum(w["shadow_notional"] or 0 for w in per_window)
    shadow_net = sum(w["shadow_net"] or 0 for w in per_window)
    result = {
        "live_session": session["live"],
        "windows_quoted": len(session["quoted"]),
        "real_fills": sum(w["real_fills"] for w in per_window),
        "real_notional": round(real_notional, 2),
        "real_realized": round(real_realized, 4),
        "real_cents_per_dollar": round(100 * real_realized / real_notional, 2)
        if real_notional else None,
        "shadow_notional_same_windows": round(shadow_notional, 2),
        "shadow_net_same_windows": round(shadow_net, 4),
        "shadow_cents_per_dollar": round(100 * shadow_net / shadow_notional, 2)
        if shadow_notional else None,
        "capture_ratio": round(real_notional / shadow_notional, 4)
        if shadow_notional else None,
        "ops_clean": (not session["rejects"] and session["deadman"] == 0
                      and not session["unsettled"]),
        "rejects": len(session["rejects"]),
        "deadman_halts": session["deadman"],
        "unsettled_windows": session["unsettled"],
        "per_window": per_window,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_window"},
                     indent=1))
    for w in per_window:
        print(f'{w["market"]:34s} real_fills={w["real_fills"]:3d} '
              f'real=${w["real_notional"]:8.2f} realized={w["real_realized"]} '
              f'shadow_fills={w["shadow_fills"]} shadow_net={w["shadow_net"]}')


if __name__ == "__main__":
    main()
