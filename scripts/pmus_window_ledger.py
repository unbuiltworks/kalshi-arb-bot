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
    # --- the eight reported rows, in reporting order --------------------------
    # SIGN CONVENTION, stated because it is the whole point of this shape:
    #   `fees` is a POSITIVE COST MAGNITUDE and is SUBTRACTED.
    #   `residual_pnl` carries its own sign and is ADDED.
    # so the two identities hold literally as written:
    #   matched_gross - fees + residual_pnl        = shadow_trading_net
    #   shadow_trading_net + expected_maker_rebate = shadow_modeled_net
    ec = r["gross_margin"]                 # theta-gated completion economics
    passive = r["matched_pnl"] - ec        # ungated oscillator matching
    fees = r["fees"]                       # positive cost magnitude
    shadow_trading_net = r["matched_pnl"] - fees + rp
    return {
        "window": slug[-5:], "settlement": winner,
        "terminal_residual_occurred": bool(rq),
        "residual_contracts": str(rq), "residual_basis": str(b.quantize(D("0.0001"))),
        "residual_side": side or "-",
        "matched_contracts": str(r["completed_qty"]),
        # SHADOW only. Nothing on this row is credited and nothing is realized.
        "ec_matched_gross": str(ec),
        "passive_matched_gross": str(passive),
        "matched_gross": str(r["matched_pnl"]),
        "fees": str(fees),
        "residual_pnl": str(rp),
        "shadow_trading_net": str(shadow_trading_net),
        "expected_maker_rebate": str(reb),
        "shadow_modeled_net_including_expected_rebate": str(shadow_trading_net + reb),
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
            # same eight rows, same two identities, averaged per window
            "ec_matched_gross": str(g("ec_matched_gross").quantize(D("0.0001"))),
            "passive_matched_gross": str(g("passive_matched_gross").quantize(D("0.0001"))),
            "matched_gross": str(g("matched_gross").quantize(D("0.0001"))),
            "fees": str(g("fees").quantize(D("0.0001"))),
            "residual_pnl": str(g("residual_pnl").quantize(D("0.0001"))),
            "shadow_trading_net": str(g("shadow_trading_net").quantize(D("0.0001"))),
            "expected_maker_rebate": str(g("expected_maker_rebate").quantize(D("0.0001"))),
            "shadow_modeled_net_including_expected_rebate": str(
                g("shadow_modeled_net_including_expected_rebate").quantize(D("0.0001"))),
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
        before = mg - fe + rb
        sev = (sum(losses) / len(losses)) if losses else D(0)
        be = (before / -sev) if sev else None
        tgt = ((before - D(5)) / -sev) if sev else None
        doc = {
            "accounting": {
                "labels": "SHADOW / MODELED only. Nothing here is CREDITED or REALIZED.",
                "reporting_order": [
                    "ec_matched_gross", "passive_matched_gross", "matched_gross",
                    "fees", "residual_pnl", "shadow_trading_net",
                    "expected_maker_rebate",
                    "shadow_modeled_net_including_expected_rebate"],
                "sign_convention": ("`fees` is a POSITIVE COST MAGNITUDE and is "
                                    "SUBTRACTED; `residual_pnl` carries its own "
                                    "sign and is ADDED"),
                "identities": [
                    "ec_matched_gross + passive_matched_gross = matched_gross",
                    "matched_gross - fees + residual_pnl = shadow_trading_net",
                    ("shadow_trading_net + expected_maker_rebate = "
                     "shadow_modeled_net_including_expected_rebate")],
                "expected_vs_credited": (
                    "expected_maker_rebate is EXPECTED: modeled from the "
                    "published maker schedule against modeled maker fills. It is "
                    "not ELIGIBLE, not CREDITED and not REALIZED. Credited PMUS "
                    "liquidity rewards are $0 and BTC15m is in no liquidity "
                    "program. shadow_trading_net is the line that contains no "
                    "incentive term at all, and it is the line to read first."),
                "enforced_by": "tests/test_pmus_shadow_net_identity.py",
            },
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
                # NAMES CORRECTED at n=11: 2215z carried a residual that WON
                # (+2.25 -- UP at basis 0.9775 into an UP settlement), so this
                # set is no longer all losses and a mean over it is not a
                # "severity". The arithmetic is unchanged and still signed,
                # which is what break-even needs; only the labels were wrong.
                "per_episode_pnl": [str(x) for x in losses],
                "mean_episode_pnl": str(sev.quantize(D("0.0001"))),
                "episode_pnl_per_window": str((sum(losses) / n).quantize(D("0.0001"))),
                "episodes_that_lost": sum(1 for x in losses if x < 0),
                "episodes_that_won": sum(1 for x in losses if x > 0),
            },
            "p0_thresholds": {
                "net_before_residual": str(before.quantize(D("0.0001"))),
                "break_even_residual_frequency": (str(round(be, 4)) if be else None),
                "target_frequency_for_plus_5": (str(round(tgt, 4)) if tgt is not None else None),
                "note": ("break-even and target use the OBSERVED SIGNED mean "
                         "episode P&L, which from n=11 includes one winning "
                         "residual (2215z, +2.25). A winner raises the mean and "
                         "therefore RAISES break-even frequency, so this number "
                         "must not be read as a severity. The mean itself is "
                         "uncertain at this n."),
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
