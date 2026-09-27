"""PROFIT_LOCK_5 and COMPLEMENT_BIAS challengers. The frozen arm is NOT touched.

PROFIT_LOCK_5 latches once the portfolio's worst-case executable terminal P&L
across BOTH settlement outcomes reaches the threshold, then stops adding risk.
The floor is `settlement_independent_floor()`:

    floor = min(q_up, q_down) - cash_paid - fees + locked_completion_margin

attainable by holding to settlement whichever way it resolves. Resting orders are
charged conservatively at their own price with no credit to min(). No settlement
outcome, future price, future fill, or ex-post maximum is consulted anywhere.

THE DISTINCTION THE WHOLE EXERCISE TURNS ON, reported separately:
  A  TEMPORARY MARKED +$5 -- the best floor ever SEEN in the window
     (`peak_floor_seen`). Not a lock. Reported only to show how often a naive
     mark-based reading would have claimed one.
  B  EXECUTABLE / SETTLEMENT-INDEPENDENT LOCKED +$5 -- a floor that was
     actually reachable and held, either by holding (no trade) or by a
     depth-limited completion priced on the contemporaneous ladder. Only B counts.

Thresholds 2/3/4/5/6/7.50/10 are run as diagnostics. **$5 is the preregistered
business-target challenger and nothing is selected from the other six.**
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as S                       # noqa: E402
from src import pmus_fees as F                                    # noqa: E402
from scripts.audit_economic_complete import load, USABLE, audit   # noqa: E402

THETA, VARIANT, FILL = D("0.96"), "B", "queue_aware"
THRESHOLDS = (D(2), D(3), D(4), D(5), D(6), D("7.5"), D(10))
TARGET = D(5)
OUT = "analysis/l5/p1/p1_profit_lock.json"


def window_end_epoch(slug):
    import datetime as dt
    tail = slug.rsplit("-", 1)[-1]          # e.g. '2245z'
    day = slug.split("-15m-")[1][:10]
    t = dt.datetime.fromisoformat(
        "%sT%s:%s:00+00:00" % (day, tail[0:2], tail[2:4]))
    return t.timestamp() + 900


def rebate_for(rows, clip, variant, winner, **kw):
    a = audit(rows, clip, THETA, variant, winner, emit=True, fill_model=FILL)
    reb = D(0)
    for e in a["ledger"]:
        if e["action"].startswith("ACQ"):
            bp = e["acq_basis"]
            px = bp if e["side"] == "up" else (D(1) - bp)
            reb += F.maker_rebate(px, e["qty"], cents=True)
    return reb


def waterfall(rows_out):
    n = D(len(rows_out))
    g = lambda f: sum(x[f] for x in rows_out) / n
    net = g("shadow_trading_net")
    return {
        "ec_matched_gross": str(g("ec").quantize(D("0.0001"))),
        "passive_matched_gross": str(g("passive").quantize(D("0.0001"))),
        "matched_gross": str(g("matched").quantize(D("0.0001"))),
        "fees": str(g("fees").quantize(D("0.0001"))),
        "residual_pnl": str(g("resid").quantize(D("0.0001"))),
        "shadow_trading_net": str(net.quantize(D("0.0001"))),
        "expected_maker_rebate": str(g("rebate").quantize(D("0.0001"))),
        "shadow_modeled_net_including_expected_rebate": str(
            (net + g("rebate")).quantize(D("0.0001"))),
    }


def run(by, clip, *, lock=None, bias=None):
    out = []
    for slug, winner in USABLE.items():
        kw = {}
        if lock is not None:
            kw["profit_lock_usd"] = lock
        if bias is not None:
            kw["complement_bias_c"] = bias
        r = S.run_window(by[slug], clip=clip, theta=THETA, winner=winner,
                         variant=VARIANT, fill_model=FILL, **kw)
        ec = r["gross_margin"]
        reb = rebate_for(by[slug], clip, VARIANT, winner)
        lt = r.get("locked_at")
        out.append({
            "window": slug[-5:],
            "ec": ec, "passive": r["matched_pnl"] - ec,
            "matched": r["matched_pnl"], "fees": r["fees"],
            "resid": r["resid_pnl"],
            "shadow_trading_net": r["matched_pnl"] - r["fees"] + r["resid_pnl"],
            "rebate": reb,
            "peak_resid": r["peak_resid"],
            "terminal_resid": r["resid_qty"],
            "matched_qty": r["matched_qty"],
            "locked_at": lt,
            "lock_floor": r.get("lock_floor"),
            "lock_via": r.get("lock_via"),
            "lock_inventory": r.get("lock_inventory"),
            "lock_cancels": r.get("lock_cancels"),
            "lock_tte_s": ((window_end_epoch(slug) - S._epoch(lt))
                           if lt else None),
            "peak_floor_seen": r.get("peak_floor_seen"),
            "peak_mtm_seen": r.get("peak_mark_to_market_seen"),
        })
    return out


UNREACHABLE = D(10) ** 9          # trace the floor without ever latching


def main(argv):
    by = load()
    doc = {"role": "P1 CHALLENGERS -- diagnostic. The frozen n=25 arm (q in "
                   "{25,50}, variant B, theta 0.96) is byte-identical and "
                   "unchanged; verified by regenerating its ledger.",
           "causality": ("floor uses only contemporaneous inventory, basis, "
                         "fees and ladder. No settlement outcome, future price, "
                         "future fill or ex-post maximum anywhere."),
           "preregistered_target": "5.00",
           "selection": "none -- the other six thresholds are diagnostics only",
           "clips": {}}
    for clip in (D(25), D(50)):
        base = run(by, clip)
        trace = run(by, clip, lock=UNREACHABLE)
        peak = {x["window"]: x["peak_floor_seen"] for x in trace}
        peak_mtm = {x["window"]: x["peak_mtm_seen"] for x in trace}
        per_threshold = {}
        for th in THRESHOLDS:
            arm = run(by, clip, lock=th)
            hits = [x for x in arm if x["locked_at"]]
            lw = [x["shadow_trading_net"] for x in hits]
            per_threshold[str(th)] = {
                "lock_hit_rate": "%d of %d" % (len(hits), len(arm)),
                "waterfall_per_window": waterfall(arm),
                "usd_per_locked_window": str(
                    (sum(lw) / D(len(lw))).quantize(D("0.0001"))) if lw else None,
                "worst_window": str(min(x["shadow_trading_net"] for x in arm)
                                    .quantize(D("0.0001"))),
                "max_peak_directional_contracts": str(
                    max(x["peak_resid"] for x in arm)),
                "mean_matched_throughput_contracts": str(
                    (sum(x["matched_qty"] for x in arm) / D(len(arm)))
                    .quantize(D("0.01"))),
            }
        arm5 = run(by, clip, lock=TARGET)
        bias = run(by, clip, bias=D("0.02"))
        rows = []
        for b, a in zip(base, arm5):
            pk = peak[b["window"]]
            rows.append({
                "window": b["window"],
                "lock_became_executable": bool(a["locked_at"]),
                "first_lock_t": a["locked_at"],
                "tte_at_lock_s": (round(a["lock_tte_s"], 1)
                                  if a["lock_tte_s"] is not None else None),
                "inventory_at_lock": ({k: str(v) for k, v in
                                       (a["lock_inventory"] or {}).items()}
                                      or None),
                "lock_via": a["lock_via"],
                "orders_cancelled_at_lock": a["lock_cancels"],
                "guaranteed_net_at_lock": (str(a["lock_floor"].quantize(D("0.0001")))
                                           if a["lock_floor"] is not None else None),
                "final_net_frozen_arm": str(
                    b["shadow_trading_net"].quantize(D("0.0001"))),
                "final_net_profit_lock_5": str(
                    a["shadow_trading_net"].quantize(D("0.0001"))),
                "delta_vs_frozen": str(
                    (a["shadow_trading_net"] - b["shadow_trading_net"])
                    .quantize(D("0.0001"))),
                "B_peak_executable_floor": (
                    str(pk.quantize(D("0.0001"))) if pk is not None else None),
                "A_peak_mark_to_market": (
                    str(peak_mtm[b["window"]].quantize(D("0.0001")))
                    if peak_mtm[b["window"]] is not None else None),
                "A_marked_5_but_NOT_lockable": bool(
                    peak_mtm[b["window"]] is not None
                    and peak_mtm[b["window"]] >= TARGET
                    and (pk is None or pk < TARGET)),
                "peak_directional_contracts": str(b["peak_resid"]),
            })
        doc["clips"][str(clip)] = {
            "frozen_arm_waterfall": waterfall(base),
            "profit_lock_5_waterfall": waterfall(arm5),
            "complement_bias_2c_waterfall": waterfall(bias),
            "exposure": {
                "frozen_max_peak_contracts": str(max(x["peak_resid"] for x in base)),
                "lock5_max_peak_contracts": str(max(x["peak_resid"] for x in arm5)),
                "bias_max_peak_contracts": str(max(x["peak_resid"] for x in bias)),
            },
            "per_window": rows,
            "thresholds": per_threshold,
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
