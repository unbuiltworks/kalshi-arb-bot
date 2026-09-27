"""Per-window money ledger and rolling readouts for the frozen BTC15m policy.

Read-only. Emits one row per usable window with every field the checkpoint-25
decision needs, plus rolling n=5/10/15/20/25 readouts and the residual-frequency
statistics that are the sole P0.

Policy is FROZEN: theta=0.96, variant B, queue-aware fills, preregistered q grid.
This module scores; it never selects.
"""
from __future__ import annotations

import json, math, os, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import pmus_completion_shadow as S           # noqa: E402
from src import pmus_fees as F                        # noqa: E402
from scripts.audit_economic_complete import load, USABLE, EXCLUDED, audit  # noqa: E402

THETA = D("0.96")
VARIANT = "B"
FILL_MODEL = "queue_aware"
OUT = "analysis/pmus/pmus_window_ledger.json"

#: largest clip size ever FILLED on an authenticated live PMUS order.
#: analysis/l5/r4_orders.jsonl: max filled_qty 5.31, max posted 5.60, capped by
#: executed_safe_size $2.4957. Anything above this is MODELED clip-size
#: extrapolation, not demonstrated executable capacity.
DEMONSTRATED_LIVE_CLIP = D("5.31")


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def window_row(rows, slug, winner, clip):
    r = S.run_window(rows, clip=clip, theta=THETA, winner=winner,
                     variant=VARIANT, fill_model=FILL_MODEL)
    a = audit(rows, clip, THETA, VARIANT, winner, emit=True,
              fill_model=FILL_MODEL)
    rq = r["resid_qty"]
    rp = r["resid_pnl"]
    # audit() reports final inventory; the residual side follows from its sign
    q = a["q"]
    d = q["up"] - q["down"]
    side = "up" if d > 0 else ("down" if d < 0 else None)
    won = rp > 0
    # a residual pays $1 if it wins and $0 if it loses, so basis follows exactly
    b = ((rq - rp) / rq) if (rq and won) else ((-rp / rq) if rq else D(0))
    reb = D(0)
    for e in a["ledger"]:
        if e["action"].startswith("ACQ"):
            bp = e["acq_basis"]
            px = bp if e["side"] == "up" else (D(1) - bp)
            reb += F.maker_rebate(px, e["qty"], cents=True)
    return {
        "window": slug[-5:], "settlement": winner,
        "terminal_residual_occurred": bool(rq),
        "residual_contracts": str(rq), "residual_basis": str(b.quantize(D("0.0001"))),
        "residual_side": side or "-", "residual_pnl": str(rp),
        "matched_contracts": str(r["completed_qty"]),
        "matched_gross": str(r["matched_pnl"]), "fees": str(-r["fees"]),
        "expected_maker_rebate": str(reb),
        "total_net": str(r["net"]),
        "realized_equivalent": str(r["net"] + reb),
    }


def rolling(ledger, ks=(5, 10, 15, 20, 25)):
    out = {}
    for k in ks:
        if len(ledger) < k:
            out["n=%d" % k] = "NOT YET AVAILABLE (%d of %d windows)" % (len(ledger), k)
            continue
        w = ledger[:k]
        g = lambda f: sum(D(x[f]) for x in w) / D(k)
        ev = sum(1 for x in w if x["terminal_residual_occurred"])
        lo, hi = wilson(ev, k)
        out["n=%d" % k] = {
            "matched_gross": str(g("matched_gross").quantize(D("0.0001"))),
            "fees": str(g("fees").quantize(D("0.0001"))),
            "residual_pnl": str(g("residual_pnl").quantize(D("0.0001"))),
            "expected_maker_rebate": str(g("expected_maker_rebate").quantize(D("0.0001"))),
            "total_net": str(g("total_net").quantize(D("0.0001"))),
            "realized_equivalent": str(g("realized_equivalent").quantize(D("0.0001"))),
            "residual_events": "%d of %d" % (ev, k),
            "residual_frequency": round(ev / k, 4),
            "wilson95": [round(lo, 4), round(hi, 4)],
        }
    return out


def main(argv):
    by = load()
    for clip in (D(25), D(50)):
        led = [window_row(by[s], s, w, clip) for s, w in USABLE.items()]
        led.sort(key=lambda x: x["window"])
        n = len(led)
        ev = sum(1 for x in led if x["terminal_residual_occurred"])
        lo, hi = wilson(ev, n)
        mg = sum(D(x["matched_gross"]) for x in led) / n
        fe = sum(D(x["fees"]) for x in led) / n
        rb = sum(D(x["expected_maker_rebate"]) for x in led) / n
        losses = [D(x["residual_pnl"]) for x in led if x["terminal_residual_occurred"]]
        # break-even and target residual frequency, given the non-residual lines
        before = mg + fe + rb
        sev = (sum(losses) / len(losses)) if losses else D(0)
        be = (before / -sev) if sev else None
        tgt = ((before - D(5)) / -sev) if sev else None
        doc = {
            "policy": {"theta": str(THETA), "variant": VARIANT,
                       "fill_model": FILL_MODEL, "clip": str(clip),
                       "frozen": True, "selects_nothing": True},
            "capacity": {
                "demonstrated_live_clip_contracts": str(DEMONSTRATED_LIVE_CLIP),
                "source": "analysis/l5/r4_orders.jsonl max filled_qty",
                "clip_is_modeled_extrapolation": clip > DEMONSTRATED_LIVE_CLIP,
                "extrapolation_multiple": str((clip / DEMONSTRATED_LIVE_CLIP).quantize(D("0.01"))),
            },
            "n_usable": n, "excluded": EXCLUDED,
            "per_window": led, "rolling": rolling(led),
            "residual_statistics": {
                "events": "%d of %d" % (ev, n),
                "frequency": round(ev / n, 4), "wilson95": [round(lo, 4), round(hi, 4)],
                "per_episode_losses": [str(x) for x in losses],
                "mean_episode_loss": str(sev.quantize(D("0.0001"))),
                "loss_per_window": str((sum(losses) / n).quantize(D("0.0001"))),
            },
            "p0_thresholds": {
                "net_before_residual": str(before.quantize(D("0.0001"))),
                "break_even_residual_frequency": (str(round(be, 4)) if be else None),
                "target_frequency_for_plus_5": (str(round(tgt, 4)) if tgt is not None else None),
                "note": ("break-even and target use the OBSERVED mean episode "
                         "severity; severity itself is uncertain at this n"),
            },
        }
        p = OUT.replace(".json", "_q%s.json" % int(clip))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        print("wrote %s  (n=%d)" % (p, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
