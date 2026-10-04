"""Frozen Cell C at increasing size: capacity and execution economics.

Signals, thresholds, cadence, trail and timestamps are UNCHANGED. The only
thing that varies is order size, and the fill model is made honest about
what the tape can and cannot price:

- the displayed touch is filled at the touch;
- anything beyond it is **charged an impact** measured from this tape's own
  prints (1s mid move in the aggressor's direction), never quoted from a
  level the tape does not contain;
- the share of each order that exceeds displayed depth is reported, because
  past some size the replay is pricing more impact than evidence.
"""
from __future__ import annotations
import json, os, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)

#: measured on this tape: mean 1s mid move in the aggressor's direction is
#: 0.18-0.45c across every print-size bucket from <10 to >=1000 shares, and
#: the p90 is 2c. MEAN is the central charge, P90 the stress charge.
IMPACT_MEAN = D("0.0025")
IMPACT_P90 = D("0.0200")
ENVELOPES = (D(25), D(50), D(75), D(100), D(150), D(200))
BASE_PEAK = D("8.03")          # Cell C's observed peak capital at $1


def run(elig, alloc: D, impact: D):
    out = []
    for slug in sorted(elig):
        w, oc = elig[slug]
        r = DCA.replay_c(slug, w, oc, alloc, seed=False,
                         require_strengthening=False, exits_enabled=True,
                         name="C", impact=impact)
        for k in ("trajectory", "episodes", "allocation_log", "exit_log",
                  "intent_log", "missed_log", "fallback_log"):
            r.pop(k, None)
        out.append(r)
    return out


def summarize(rows, alloc, impact, label):
    n = len(rows)
    f = lambda k: sum(D(r[k]) for r in rows)
    nets = sorted(float(r["net_settlement_pnl"]) for r in rows)
    gross = f("directional_realized_pnl") + f("matched_pair_economics") \
        + f("settlement_residual_pnl")
    peak = max(D(r["max_capital"]) for r in rows)
    orders = sum(r["taker_orders"] for r in rows)
    over10 = sum(r["touch_depth_consumed_over_10pct"] for r in rows)
    return {
        "label": label, "alloc_usd": str(alloc), "impact": str(impact),
        "gross_before_costs": str(round(gross + f("impact_cost"), 4)),
        "slippage_depth_cost": str(round(f("impact_cost"), 4)),
        "taker_fees": str(round(f("fees"), 4)),
        "verified_maker_compensation": "0.0000",
        "gross_after_slippage": str(round(gross, 4)),
        "net": str(round(D(sum(nets)), 4)),
        "net_per_window": str(round(D(sum(nets)) / n, 4)),
        "median_window": str(round(D(statistics.median(nets)), 4)),
        "positive_windows": sum(1 for x in nets if x > 0),
        "LB1": str(round((D(sum(nets)) - D(nets[-1])) / (n - 1), 4)),
        "LB3": str(round((D(sum(nets)) - D(sum(nets[-3:]))) / (n - 3), 4)),
        "worst_window": str(round(D(nets[0]), 4)),
        "peak_capital": str(round(peak, 2)),
        "peak_residual_shares": str(round(
            max(D(r["max_residual"]) for r in rows), 1)),
        "turnover": str(round(f("acquisition_cost"), 2)),
        "shares_filled_beyond_touch": str(round(f("impact_shares"), 1)),
        "orders_over_10pct_of_touch_pct": (round(100.0 * over10 / orders, 1)
                                           if orders else None),
        "net_per_peak_capital": str(round(D(sum(nets)) / n / peak, 5)),
    }


def main() -> int:
    by = DCA.load(DCA.TAPE)
    elig = {}
    for slug, w in by.items():
        ok, oc, _ = DCA.eligible(slug, w)
        if ok:
            elig[slug] = (w, oc)
    out = {"base_peak_capital_at_1usd": str(BASE_PEAK), "ladder": []}
    for E in (D(1) * BASE_PEAK,) + ENVELOPES:
        alloc = (E / BASE_PEAK).quantize(D("0.0001"))
        for imp, nm in ((IMPACT_MEAN, "impact_mean_0.25c"),
                        (IMPACT_P90, "impact_p90_2c")):
            out["ladder"].append(summarize(run(elig, alloc, imp), alloc, imp,
                                           "env_%s_%s" % (E, nm)))
            print(out["ladder"][-1]["label"],
                  out["ladder"][-1]["net_per_window"], flush=True)
    json.dump(out, open("analysis/l5/favorite_dca/CELL_C_CAPACITY.json", "w"),
              indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
