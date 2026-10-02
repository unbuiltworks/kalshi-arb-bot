#!/usr/bin/env python3
"""Prospective scoring of the FROZEN signal `trade_imbalance >= 0.75 @ 2s`.

The signal is a candidate DIAGNOSTIC, not a strategy. Nothing here tunes it: the
threshold, horizon, ceilings and feature definition are imported from the
mechanism audit unchanged, and the cancel latency is the measured p90 RTT, fixed
in advance at 0.612 s.

One row per gate-passing window, appended cumulatively so each new pristine
window extends the table rather than replacing it. Windows are passed in
explicitly -- a window is only ever scored once it has passed the completeness
gate, and this script never selects its own sample.

Columns, as specified:
  baseline_net                  window net under the frozen baseline
  counterfactual_net            net if flagged lots cancellable at p90 were cancelled
  delta_net                     counterfactual - baseline
  p_flag_given_toxic
  p_flag_given_qualifying
  toxic_dollars_avoided         actual lot net removed, not a count
  qualifying_dollars_sacrificed
  frac_warnings_actionable      share of warnings with lead >= 0.612 s
  fills                         first-leg count (capacity)

Reported per ceiling, because selectivity differed by ceiling in the audit.

Diagnostic only. No rule, no sweep, no orders.
"""
from __future__ import annotations
import argparse, json, os
from decimal import Decimal as D

OUT = "analysis/l5/pmus_rewards/IMBALANCE_PROSPECTIVE_TABLE.json"
RTT_P90 = 0.612                      # measured, fixed in advance

import importlib.util
_a = importlib.util.spec_from_file_location("aud", "scripts/pmus_imbalance_mechanism_audit.py")
aud = importlib.util.module_from_spec(_a); _a.loader.exec_module(aud)


def score_window(sc, slug):
    rows = sc.get(slug) or []
    if not rows:
        return {"status": "NO_SCORABLE_ROWS"}
    dead = aud.pou.window_end(slug) - aud.DEADLINE_S
    out = {"scorable_trades": len(rows)}
    for c in aud.CEILINGS:
        lots = aud.lots_with_economics(rows, dead, c)
        base = sum(l["net"] for l in lots)
        tox_f = tox_n = qual_f = qual_n = 0
        avoided = D(0); sacrificed = D(0)
        a_n = s_n = too_late = 0
        leads = []
        for l in lots:
            f = aud.imbalance_series(rows, l["idx"], l["t"], l["side"])
            flagged = f["value"] is not None and f["value"] >= aud.IMB_THRESHOLD
            if l["label"] == "TOXIC":
                tox_f += flagged; tox_n += not flagged
            else:
                qual_f += flagged; qual_n += not flagged
            if not flagged:
                continue
            onset = aud.onset_before_fill(rows, l["idx"], l["t"], l["side"])
            if onset is None:
                continue
            lead = l["t"] - onset
            leads.append(lead)
            if lead >= RTT_P90:
                if l["label"] == "TOXIC":
                    avoided += l["net"]; a_n += 1
                else:
                    sacrificed += l["net"]; s_n += 1
            elif l["label"] == "TOXIC":
                too_late += 1
        n_t = tox_f + tox_n; n_q = qual_f + qual_n
        cf = base - (avoided + sacrificed)
        out[str(c)] = {
            "fills": len(lots),
            "n_toxic": n_t, "n_qualifying": n_q,
            "baseline_net": str(base),
            "counterfactual_net_p90": str(cf),
            "delta_net": str(cf - base),
            "p_flag_given_toxic": round(tox_f / n_t, 4) if n_t else None,
            "p_flag_given_qualifying": round(qual_f / n_q, 4) if n_q else None,
            "toxic_dollars_avoided": str(-avoided),
            "qualifying_dollars_sacrificed": str(sacrificed),
            "toxic_flagged_too_late": too_late,
            "avoided_n": a_n, "sacrificed_n": s_n,
            "frac_warnings_actionable_ge_0.612s":
                round(sum(1 for x in leads if x >= RTT_P90) / len(leads), 4) if leads else None,
            "warnings_n": len(leads),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("windows", nargs="+", help="gate-passing window slugs to score")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    table = {"signal": {"name": "trade_imbalance", "threshold": aud.IMB_THRESHOLD,
                        "horizon_s": aud.IMB_HORIZON_S, "frozen": True},
             "cancel_latency_s": RTT_P90,
             "status": "DIAGNOSTIC ONLY -- candidate, not a strategy",
             "windows": {}}
    if os.path.exists(a.out):
        try:
            table = json.load(open(a.out))
        except Exception:
            pass
    tr, bk = aud.pou.load()
    sc, _ = aud.pou.scorable(tr, bk)
    for w in a.windows:
        table["windows"][w] = score_window(sc, w)
        print(f"scored {w}")
    # cumulative totals per ceiling, recomputed from scratch each run
    tot = {}
    for c in aud.CEILINGS:
        cs = str(c); rows = [v[cs] for v in table["windows"].values() if cs in v]
        if not rows:
            continue
        tot[cs] = {
            "n_windows": len(rows),
            "fills": sum(r["fills"] for r in rows),
            "baseline_net": str(sum(D(r["baseline_net"]) for r in rows)),
            "counterfactual_net_p90": str(sum(D(r["counterfactual_net_p90"]) for r in rows)),
            "delta_net": str(sum(D(r["delta_net"]) for r in rows)),
            "toxic_dollars_avoided": str(sum(D(r["toxic_dollars_avoided"]) for r in rows)),
            "qualifying_dollars_sacrificed": str(sum(D(r["qualifying_dollars_sacrificed"]) for r in rows)),
            "windows_delta_positive": sum(1 for r in rows if D(r["delta_net"]) > 0),
            "windows_delta_negative": sum(1 for r in rows if D(r["delta_net"]) < 0),
        }
    table["cumulative"] = tot
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(table, open(a.out, "w"), indent=2, default=str)
    print(json.dumps(tot, indent=1))


if __name__ == "__main__":
    main()
