"""Driver: replay the transfer mechanics over fetched Polymarket sports
trades and emit analysis/l5/transfer_backtest_sports.json. THEORETICAL."""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.polymarket_l5 import sports_transfer_backtest as sb  # noqa: E402

SRC = Path("data/transfer_backtest/sports_trades.jsonl.gz")
OUT = Path("analysis/l5/transfer_backtest_sports.json")
MIN_SPAN_S = 1800.0
ONE = D("1")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def hold_outcome_proxy(prints, side, first_cost):
    """Final-print proxy settlement for a hold; None when ambiguous."""
    if not prints:
        return None
    last = prints[-1]["p_yes"]
    if last >= D("0.9"):
        won = side == "YES"
    elif last <= D("0.1"):
        won = side == "NO"
    else:
        return None
    return (ONE - D(first_cost)) if won else -D(first_cost)


def main() -> int:
    fams = defaultdict(list)
    for line in gzip.open(SRC, "rt", encoding="utf-8"):
        m = json.loads(line)
        fams[(m["tag"], (m.get("sportsMarketType") or "?").lower())].append(m)

    report = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "labeling": ("ALL THEORETICAL, print-only tier: no historical "
                           "book exists, maker joins proxied by traded "
                           "prices, v1 TTE gate not applicable to games; "
                           "optimistic AND strict are queue-blind upper "
                           "bounds; rewards not modeled"),
              "provenance": {
                  "events": "gamma-api.polymarket.com closed events, end 2026-08-31T00:00Z..2026-09-02T14:00Z",
                  "trades": "data-api.polymarket.com/trades per conditionId, complete via offset pagination"},
              "families": {}}

    for (tag, kind), markets in sorted(fams.items()):
        agg = {"markets": 0, "game_hours": 0.0, "fees_bps_seen": set()}
        per_mode = {m: defaultdict(int) for m in ("optimistic", "strict")}
        pair_costs = {m: [] for m in per_mode}
        comp_times = {m: [] for m in per_mode}
        hold_pnl = {m: D(0) for m in per_mode}
        hold_amb = {m: 0 for m in per_mode}
        flow_tot = {"slots": 0, "two_sided": 0, "empty": 0}
        for m in markets:
            prints = sb.normalize_yes_frame(m["trades"])
            if not prints:
                continue
            start = parse_iso(m.get("game_start")) or prints[0]["t"]
            start = max(start, prints[0]["t"])
            end = prints[-1]["t"]
            if end - start < MIN_SPAN_S:
                continue
            agg["markets"] += 1
            agg["game_hours"] += (end - start) / 3600.0
            agg["fees_bps_seen"].add((m.get("maker_fee_bps"),
                                      m.get("taker_fee_bps")))
            fl = sb.slot_flow(prints, start, end)
            for k in ("slots", "two_sided", "empty"):
                flow_tot[k] += fl[k]
            for mode in per_mode:
                for row in sb.replay_market(prints, start, end, mode):
                    per_mode[mode][row["status"]] += 1
                    if row["status"] == "MATCHED":
                        pair_costs[mode].append(D(row["pair_cost"]))
                        comp_times[mode].append(row["time_to_complement_s"])
                    if row["status"] == "ONE_SIDED_HOLD":
                        pnl = hold_outcome_proxy(prints, row["side"],
                                                 row["first_cost"])
                        if pnl is None:
                            hold_amb[mode] += 1
                        else:
                            hold_pnl[mode] += pnl
        if not agg["markets"]:
            continue
        fam = {"markets": agg["markets"],
               "game_hours": round(agg["game_hours"], 1),
               "clob_fees_bps_seen": sorted(str(x) for x in agg["fees_bps_seen"]),
               "flow": {**flow_tot,
                        "two_sided_pct": round(100 * flow_tot["two_sided"] / flow_tot["slots"], 1) if flow_tot["slots"] else None}}
        for mode in per_mode:
            c = per_mode[mode]
            slots = sum(c.values())
            fills = c["MATCHED"] + c["ONE_SIDED_HOLD"]
            fam[mode] = {
                "slots": slots,
                "first_leg_fill_rate": round(fills / slots, 4) if slots else None,
                "p_complement_given_first_fill": round(c["MATCHED"] / fills, 4) if fills else None,
                "one_sided_hold_rate": round(c["ONE_SIDED_HOLD"] / slots, 4) if slots else None,
                "matched": c["MATCHED"],
                "matched_per_game_hour": round(c["MATCHED"] / agg["game_hours"], 2),
                "median_pair_cost": str(sorted(pair_costs[mode])[len(pair_costs[mode]) // 2]) if pair_costs[mode] else None,
                "mean_time_to_complement_s": round(sum(comp_times[mode]) / len(comp_times[mode]), 1) if comp_times[mode] else None,
                "hold_pnl_theoretical_total": str(hold_pnl[mode]),
                "hold_outcomes_ambiguous": hold_amb[mode],
                "statuses": dict(c)}
        report["families"][f"{tag}:{kind}"] = fam

    OUT.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
