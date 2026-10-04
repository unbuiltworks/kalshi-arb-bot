"""Capital envelopes for FAVORITE_DCA A / Am / B / C, rule unchanged.

The strategy rule is NOT touched. The only quantity that varies is the
per-period allocation notional, and it is set by a rule that reads CAPITAL
and never P&L:

    alloc(E) = E / (peak capital measured at alloc = $1)

so the size for each envelope is fixed by the capital target, then the
replay is run and the **measured** peak capital is reported, which can come
out under the envelope when displayed depth binds.

Depth is the real limit on this, and the tape shows TOP OF BOOK only. Every
pass therefore reports the largest fraction of the displayed touch a single
order consumed and how many orders took more than 10% of it. Beyond the
displayed touch this replay has nothing to say, so a pass whose orders
routinely eat the touch is reported as not defensible rather than scaled on.

The efficiency metric the target names -- settled net P&L / peak capital --
is computed per envelope, with the honest note that it is meaningless when
net is negative.
"""
from __future__ import annotations
import json, os, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                            # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)

ENVELOPES = [D(25), D(50), D(75), D(100), D(150), D(200)]
TARGET = D(5)


VARIANT_KW = {"C": {}, "D0": {"seed": False},
              "D1": {"seed": False, "opposite_rest": True}}


def run(elig, variant: str, alloc: D):
    out = []
    for slug in sorted(elig):
        w, oc, _ = elig[slug]
        r = (DCA.replay_c(slug, w, oc, alloc, name=variant,
                          **VARIANT_KW[variant]) if variant in VARIANT_KW
             else DCA.replay(slug, w, oc, variant, alloc))
        r.pop("trajectory", None)
        out.append(r)
    return out


def summarize(rows, alloc: D, envelope=None):
    n = len(rows)
    net = sum(D(r["net_settlement_pnl"]) for r in rows)
    peak_cap = max(D(r["max_capital"]) for r in rows)
    gross_out = sum(D(r.get("gross_cash_out") or r["acquisition_cost"])
                    for r in rows)
    # turnover is PER WINDOW -- gross cash out in that window over that
    # window's own peak capital -- then averaged. Dividing a 28-window cash
    # total by one window's peak would read as 27x turnover when the
    # strategy never recycles a cent.
    turn = []
    for r in rows:
        pk = D(r["max_capital"])
        if pk > 0:
            turn.append(D(r.get("gross_cash_out")
                          or r["acquisition_cost"]) / pk)
    dfr = [r["touch_depth_consumed_max"] for r in rows
           if r["touch_depth_consumed_max"]]
    nets = sorted(float(r["net_settlement_pnl"]) for r in rows)
    orders = sum(r["taker_orders"] for r in rows)
    return {
        "envelope": (str(envelope) if envelope else "baseline"),
        "alloc_usd": str(round(alloc, 4)),
        "net_per_window": str(round(net / n, 4)),
        "net_total": str(round(net, 3)),
        "median_window_net": str(round(D(statistics.median(
            [float(r["net_settlement_pnl"]) for r in rows])), 4)),
        "windows_net_positive": sum(
            1 for r in rows if D(r["net_settlement_pnl"]) > 0),
        "peak_capital_worst_window": str(round(peak_cap, 2)),
        "capital_turnover_per_window_mean": (
            str(round(sum(turn) / len(turn), 3)) if turn else None),
        "max_directional_residual_shares": str(round(
            max(D(r["max_residual"]) for r in rows), 1)),
        "max_drawdown_worst_window": str(round(
            max(D(r["max_drawdown_from_peak"]) for r in rows), 2)),
        "fees": str(round(sum(D(r["fees"]) for r in rows), 3)),
        "profitable_exits": sum(r.get("profitable_exits", 0) for r in rows),
        "exits": sum(r.get("exits", 0) for r in rows),
        "settlement_residual_shares": str(round(sum(
            abs(D(r["residual_terminal"])) for r in rows), 1)),
        "settlement_contingent_exposure_worst_window_usd": str(round(
            max(D(r["max_residual"]) for r in rows), 1)),
        # the tests the \$5 objective now requires explicitly
        "p_net_ge_5": str(round(D(sum(1 for x in nets if x >= 5)) / n, 4)),
        "p_net_lt_0": str(round(D(sum(1 for x in nets if x < 0)) / n, 4)),
        "pct_windows_5plus": str(round(
            D(100 * sum(1 for x in nets if x >= 5)) / n, 1)),
        "pct_windows_positive": str(round(
            D(100 * sum(1 for x in nets if x > 0)) / n, 1)),
        "worst_window": str(round(D(nets[0]), 3)),
        "leave_best_1_net_per_window": str(round(
            (D(sum(nets)) - D(nets[-1])) / (n - 1), 4)),
        "leave_best_3_net_per_window": str(round(
            (D(sum(nets)) - D(sum(nets[-3:]))) / (n - 3), 4)),
        # liquidity participation: the share of the DISPLAYED touch a
        # typical order consumed, averaged over every taker order in the
        # sample. The tape has nothing below the touch, so this is the only
        # honest participation measure available.
        "liquidity_participation_mean_frac_of_touch": (str(round(
            sum(D(r["touch_depth_frac_sum"]) for r in rows) / orders, 4))
            if orders else None),
        "orders_over_10pct_of_touch_pct": str(round(
            D(100 * sum(r["touch_depth_consumed_over_10pct"] for r in rows))
            / orders, 1)) if orders else None,
        "net_per_peak_capital": (str(round(net / n / peak_cap, 4))
                                 if peak_cap > 0 else None),
        "touch_depth_consumed_max": (max(dfr) if dfr else None),
        "orders_over_10pct_of_touch": sum(
            r["touch_depth_consumed_over_10pct"] for r in rows),
        "taker_orders": sum(r["taker_orders"] for r in rows),
    }


def main() -> int:
    by = DCA.load(DCA.TAPE)
    elig = {}
    for slug, w in by.items():
        ok, oc, why = DCA.eligible(slug, w)
        if ok:
            elig[slug] = (w, oc, why)
    res = {"windows": len(elig), "target_net_per_window": str(TARGET),
           "variants": {}}
    for variant in ("A", "Am", "B", "C", "D0", "D1"):
        base = run(elig, variant, D(1))
        b = summarize(base, D(1))
        peak1 = D(b["peak_capital_worst_window"])
        rows = {"baseline": b, "envelopes": []}
        for E in ENVELOPES:
            alloc = (E / peak1).quantize(D("0.0001"))
            r = run(elig, variant, alloc)
            rows["envelopes"].append(summarize(r, alloc, E))
        npw = D(b["net_per_window"])
        rows["capital_for_5_per_window"] = (
            "UNREACHABLE_AT_ANY_CAPITAL_net_per_window_is_negative"
            if npw <= 0 else
            str(round(peak1 * (TARGET / npw), 2)))
        res["variants"][variant] = rows
        print(variant, "baseline net/win", b["net_per_window"],
              "peak cap", b["peak_capital_worst_window"], flush=True)
    out = "analysis/l5/favorite_dca/CAPITAL_ENVELOPES.json"
    json.dump(res, open(out, "w"), indent=1)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
