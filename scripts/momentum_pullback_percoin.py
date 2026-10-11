"""PER-COIN and PER-DURATION breakdown of the three preregistered candidates.

WHY THIS IS ITS OWN OUTPUT. The expanded run answers "does the candidate
survive out of sample" with one number per slice. It does not answer the
question that number provokes: was the original +$0.42/window a STRATEGY
result or a BTC result? A per-coin table answers it directly, and no
threshold is chosen from it -- the candidates are the three that were fixed
before the expansion, at the preregistered sizes only.

This is diagnosis, not selection. Picking the profitable coins out of this
table and calling the subset a strategy would be exactly the hindsight the
window split exists to prevent, and it is not done anywhere downstream.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.momentum_pullback_backtest import (  # noqa: E402
    load, run_window, summarise)
from scripts.momentum_pullback_expanded import (  # noqa: E402
    CADENCE_S, CANDIDATES, FILES, PER_DAY)

LABEL = "MOMENTUM_PULLBACK_PERCOIN_V1"
#: the smallest preregistered size, so the coin effect is not confounded
#: with a depth effect, plus one larger point to show it scales linearly.
SIZES = (2.0, 10.0)


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--durations", default="5m,15m,4h")
    a = ap.parse_args(argv)
    rep: Dict[str, Any] = {"label": LABEL, "cadence_s": CADENCE_S,
                           "sizes_usd": list(SIZES),
                           "purpose": ("diagnosis of the coin effect, NOT "
                                       "selection: no profitable subset is "
                                       "promoted to a strategy anywhere"),
                           "durations": {}}
    for dur in a.durations.split(","):
        dur = dur.strip()
        path = os.path.join(a.dir, FILES[dur][0]) if dur in FILES else None
        if not path or not os.path.exists(path):
            continue
        wins = load(path, coin=None)
        coins = sorted({s.split("-")[0] for s in wins})
        out: Dict[str, Any] = {"windows_per_day": PER_DAY[dur],
                               "coins": coins, "by_candidate": {}}
        for cname, ckw in CANDIDATES.items():
            per: Dict[str, Any] = {}
            for size in SIZES:
                tbl: Dict[str, Any] = {}
                for c in coins:
                    slugs = sorted(s for s in wins
                                   if s.split("-")[0] == c)
                    rows = [run_window(wins[s], cadence=CADENCE_S,
                                       ext_dollars=size, **ckw)
                            for s in slugs]
                    sm = summarise(rows, "%s/%s" % (cname, c))
                    tbl[c] = {
                        "windows": sm["windows"],
                        "net_per_window": sm["net_per_window"],
                        "net_per_window_after_2pct_fees":
                            sm["fees"]["net_per_window_with_sensitivity"],
                        "win_rate_pct": sm["win_rate_pct"],
                        "extension_realized_total":
                            sm["extension_realized_pnl"]["total"],
                        "extension_settlement_total":
                            sm["extension_settlement_pnl"]["total"],
                        "max_drawdown": sm["max_drawdown"],
                        "peak_capital_median": sm["peak_capital"]["median"],
                        "unsold_shares_total":
                            sm["unsold_shares_at_exit"]["total"],
                        "exits_nothing_sellable":
                            sm["exits_nothing_sellable"],
                        "no_trade_windows": sm["no_trade_windows"],
                        "by_flip_count": {
                            k: {"windows": v["windows"],
                                "net_mean": v["net"].get("mean")}
                            for k, v in sm["by_flip_count"].items()},
                    }
                nets = [v["net_per_window"] for v in tbl.values()
                        if v["net_per_window"] is not None]
                per["$%g" % size] = {
                    "by_coin": tbl,
                    "coins_positive": sum(1 for x in nets if x > 0),
                    "coins_total": len(nets),
                }
            out["by_candidate"][cname] = per
        rep["durations"][dur] = out
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  durations=%s" % (LABEL, ",".join(rep["durations"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
