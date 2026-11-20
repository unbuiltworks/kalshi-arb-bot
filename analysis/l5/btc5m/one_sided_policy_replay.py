"""The EXISTING controller, with and without the one-sided rule, replayed
over the 187 recorded windows. Matched and residual results kept apart.

Every decision is `src.l5_btc5m_controller.decide` under the 17:05Z live
window's effective configuration (book-relative two-sided quoting, clip
$0.05, side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20,
entry-EV gate WAIVED as it was live). One decision every 10 s; quotes are
replaced at each decision; a quote FILLS only if the side's ask reaches
its price within those 10 s (THROUGH), in full. Maker fee 0, no rebate, no
incentive: none is credited to this account on these markets.

THIS IS AN ARITHMETIC REPLAY ON PUBLIC QUOTES WITH AN ASSUMED FILL RULE.
The entry-EV gate is waived, sizes and queue are unknown, and our own
orders are assumed not to move the market. It can show which line a
difference comes from. It is NOT an executable profit.
"""
import os
import random
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_attribution as A          # noqa: E402
from src import l5_btc5m_controller as C           # noqa: E402
from analysis.l5.btc5m.one_sided_value import load, quote, p_side  # noqa

STEP_S = 10.0


def params(control):
    return C.Params(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=D("0.05"),
        hedge_enabled=False, reserve_mode="NONE", max_exposure_usd=D("0.60"),
        straddle_book_relative=True, straddle_fill_model_measured=True,
        straddle_pair_ceiling=D("0.99"), straddle_repeat_enabled=True,
        straddle_max_side_usd=D("0.30"), straddle_max_unmatched_usd=D("0.10"),
        straddle_requote_ticks=2, straddle_max_window_loss_usd=D("0.20"),
        straddle_leg_max_age_s=240.0, straddle_ev_waiver=C.EV_WAIVER_TOKEN,
        straddle_one_sided_control=control)


def run_window(rows, control):
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    fills, refused, nxt = [], 0, 0.0
    par = params(control)
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt:
            continue
        nxt = t + STEP_S
        ub, ua = quote(r, "UP")
        if ub is None or ua is None:
            continue
        p = r["p_up"]
        pu = p["value"] if p.get("ok") and p.get("value") is not None \
            else None
        d = C.decide(
            book={"bid": str(ub), "ask": str(ua), "age_s": 0.1},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                       for s in C.SIDES},
            resting=[], params=par,
            p_up=None if pu is None else str(round(pu, 6)),
            p_source="VENUE_INDEX", p_age_s=r["index"].get("age_s"))
        osv = (d.get("evaluation") or {}).get("one_sided") or {}
        refused += sum(1 for v in osv.values()
                       if v and v.get("allowed") is False)
        for o in d["orders"]:
            if o.get("op", "PLACE") != "PLACE":
                continue
            side, px, q = o["side"], D(str(o["price"])), D(str(o["qty"]))
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > STEP_S:
                    break
                _b, a = quote(rows[j], side)
                if a is not None and D(str(a)) <= px:
                    inv[side][0] += q
                    inv[side][1] += q * px
                    fills.append({"side": side, "qty": q, "cost": q * px,
                                  "fee": "0"})
                    break
    att = A.attribute(fills, rows[0]["settled_outcome"] == "UP")
    return {"fills": len(fills), "refused": refused,
            "matched": att["matched"]["pnl"],
            "residual": att["unmatched"]["pnl"], "net": att["net_usd"],
            "cost": sum((f["cost"] for f in fills), D(0))}


def stat(vals, seed=3, n=4000):
    vals = [float(v) for v in vals]
    k = len(vals)
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(vals) for _ in range(k)) / k for _ in range(n))
    return sum(vals) / k, ms[int(.025 * n)], ms[int(.975 * n)], sum(vals)


def main():
    data = load()
    names = list(data)
    res = {c: {m: run_window(data[m], c) for m in names}
           for c in (False, True)}
    P = print
    for label, ws in (("ALL", names), ("FIRST HALF", names[:len(names)//2]),
                      ("SECOND HALF", names[len(names)//2:])):
        P("== %s: %d windows, per window in USD ==" % (label, len(ws)))
        for c in (False, True):
            r = res[c]
            P("  control %-3s fills %5d  legs refused %4d  cost $%.2f" % (
                "ON" if c else "OFF", sum(r[m]["fills"] for m in ws),
                sum(r[m]["refused"] for m in ws),
                sum(r[m]["cost"] for m in ws)))
            for k in ("matched", "residual", "net"):
                m_, lo, hi, tot = stat([r[m][k] for m in ws])
                P("     %-9s mean %+.5f [%+.5f,%+.5f]  total %+.4f" % (
                    k, m_, lo, hi, tot))
            P("     fees 0.0000  credited incentives 0.0000")
        m_, lo, hi, tot = stat([res[True][m]["net"] - res[False][m]["net"]
                                for m in ws])
        P("  PAIRED ON-OFF net: mean %+.5f [%+.5f,%+.5f]  total %+.4f  "
          "windows changed %d" % (m_, lo, hi, tot, sum(
              1 for m in ws if res[True][m]["net"] != res[False][m]["net"])))
        m_, lo, hi, tot = stat([res[True][m]["residual"]
                                - res[False][m]["residual"] for m in ws])
        P("  PAIRED ON-OFF residual: mean %+.5f [%+.5f,%+.5f]" % (
            m_, lo, hi))
    return 0


if __name__ == "__main__":
    sys.exit(main())
