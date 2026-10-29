"""P5: what inventory scale does L5 actually run, in OUR units?

The question is not "does L5 hold more than we do" -- he has a larger
bankroll and that is not permission for anything. It is narrower: at
comparable price/TTE/residual states, is a 6-unit cap TRUNCATING the
oscillation his process depends on, or is 6 units already past where his
own inventory sits?

NORMALISATION. Our residual cap counts BEHAVIOURAL UNITS: residual divided
by the venue-minimum clip the engine trades (~1.7-1.9 contracts). L5's
analogue is his own typical clip in the same window, so his residual is
divided by the MEDIAN FILL SIZE of that window. Dividing by a global
constant would import his bankroll into the comparison, which is exactly
what must not happen.

    python3 analysis/l5/residual_cap/l5_scale.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.l5_streak_shadow import build_transitions            # noqa: E402
from src.polymarket_l5.action_model import (_bucket, _relation,  # noqa: E402
                                            IMBALANCE_BUCKETS, PRICE_BANDS,
                                            TTE_BUCKETS, state_key)
from src.polymarket_l5.action_model_v2 import _f, _windows     # noqa: E402

OUT = Path(__file__).resolve().parent / "l5_inventory_scale.json"

# the states our own band/cap decisions actually occurred in
OUR_STATES = ("p25-45c|tte>10m|imb10-50", "p10-25c|tte>10m|imb>50",
              "p<10c|tte>10m|imb10-50", "p45-55c|tte>10m|imb10-50",
              "p25-45c|tte>10m|imb>50")


def q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(int(p * len(s)), len(s) - 1)], 3)


def main() -> int:
    by_state = defaultdict(lambda: {"resid_units": [], "resid_qty": [],
                                    "resid_usd": [], "abs_inv": [],
                                    "matched": [], "same": 0, "n": 0})
    terminal = []
    matched_frac = []
    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        qtys = [_f(r["quantity"]) for r in rows if _f(r["quantity"]) > 0]
        if not qtys:
            continue
        unit = statistics.median(qtys)           # L5's own clip in THIS window
        if unit <= 0:
            continue
        last = rows[-1]
        up, dn = _f(last["cum_up_qty"]), _f(last["cum_down_qty"])
        m = _f(last["matched_qty"])
        terminal.append(abs(_f(last["residual_qty_signed"])) / unit)
        tot = up + dn
        if tot > 0:
            matched_frac.append(2 * m / tot)
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            resid = _f(prev["residual_qty_signed"])
            sk = state_key(_f(prev["price"]), _f(nxt["seconds_to_settlement"]),
                           resid)
            if sk not in OUR_STATES:
                continue
            b = by_state[sk]
            b["resid_units"].append(abs(resid) / unit)
            b["resid_qty"].append(abs(resid))
            b["resid_usd"].append(abs(resid) * _f(prev["price"]))
            b["abs_inv"].append((_f(prev["cum_up_qty"])
                                 + _f(prev["cum_down_qty"])) / unit)
            b["matched"].append(_f(prev["matched_qty"]) / unit)
            b["n"] += 1
            if nxt["outcome"] == prev["outcome"]:
                b["same"] += 1

    doc = {
        "labels": "L5 canonical history, normalised by HIS OWN per-window "
                  "median clip so his bankroll is not imported into our "
                  "units; read-only research",
        "our_live_cap_units": 6,
        "by_state": {},
        "window_level": {
            "terminal_residual_units": {
                "p50": q(terminal, 0.5), "p75": q(terminal, 0.75),
                "p90": q(terminal, 0.9), "max": round(max(terminal), 3),
                "n": len(terminal)},
            "matched_fraction_of_inventory": {
                "p50": q(matched_frac, 0.5), "p90": q(matched_frac, 0.9),
                "n": len(matched_frac)},
        },
    }
    for sk, b in sorted(by_state.items(), key=lambda kv: -kv[1]["n"]):
        n = b["n"] or 1
        doc["by_state"][sk] = {
            "n_decisions": b["n"],
            "residual_units": {"p50": q(b["resid_units"], 0.5),
                               "p75": q(b["resid_units"], 0.75),
                               "p90": q(b["resid_units"], 0.9),
                               "max": round(max(b["resid_units"]), 3)},
            "residual_shares": {"p50": q(b["resid_qty"], 0.5),
                                "p90": q(b["resid_qty"], 0.9)},
            "residual_usd": {"p50": q(b["resid_usd"], 0.5),
                             "p90": q(b["resid_usd"], 0.9)},
            "abs_inventory_units": {"p50": q(b["abs_inv"], 0.5),
                                    "p90": q(b["abs_inv"], 0.9)},
            "matched_units": {"p50": q(b["matched"], 0.5),
                              "p90": q(b["matched"], 0.9)},
            "p_next_fill_same_side": round(b["same"] / n, 4),
            "p_next_fill_opposite_side": round(1 - b["same"] / n, 4),
            "share_of_decisions_above_our_6_unit_cap": round(
                sum(1 for x in b["resid_units"] if x >= 6) / n, 4),
        }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
