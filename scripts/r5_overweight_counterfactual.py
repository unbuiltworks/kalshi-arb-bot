"""Candidate overweight-extension rules over the 25 frozen windows.

CONTROL is the execution-aware one-fill-per-real-order rung,
$5.9220629591/window, not the $6.1167 historical baseline.

Every candidate is applied as a GATE ON THE ACQUISITION QUOTE ONLY, at the
frozen point in the loop, leaving completion, lock, hold and cap untouched.
No threshold is fitted: each candidate is a round number stated in advance,
or is taken from the L5 measurement.

SETTLEMENT is used only to price a window's outcome after every decision in
it has been made, exactly as the frozen replay already does. It is not a
feature.
"""
import statistics
from decimal import Decimal as D
from typing import Optional

from scripts.audit_economic_complete import load, USABLE
from src import pmus_exec_ladder as X
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

RUNG = "R2_ONE_FILL_PER_ORDER"          # the $5.9220629591 control
CLIP = E.CLIP                            # 50, the frozen sample's clip


def gate_none(resid, side, clip, matched):
    return True


def gate_abs(limit_clips):
    """Refuse a quote that would push |residual| past N clips."""
    def g(resid, side, clip, matched):
        if resid == 0:
            return True
        signed = clip if side == "up" else -clip
        after = resid + signed
        if abs(after) <= abs(resid):
            return True                  # reduces imbalance: always allowed
        return abs(after) <= limit_clips * clip
    return g


def gate_underweight_only(limit_clips):
    """Once |residual| >= N clips, quote ONLY the underweight side."""
    def g(resid, side, clip, matched):
        if resid == 0 or abs(resid) < limit_clips * clip:
            return True
        return (resid > 0) != (side == "up")
    return g


def gate_ratio(limit):
    """L5-DERIVED. Cap residual RELATIVE TO MATCHED, not in absolute size.

    Measured on 8,764 L5 BTC-15m imbalanced states: L5's residual/matched
    ratio is p50 0.214, p75 0.491, p90 1.087. He does NOT brake on absolute
    residual (extension rate is 52.2% at 0-1 clips and 52.2% at 10+ clips),
    but his residual is usually small relative to his matched book.
    """
    def g(resid, side, clip, matched):
        if resid == 0:
            return True
        signed = clip if side == "up" else -clip
        after = resid + signed
        if abs(after) <= abs(resid):
            return True
        if matched <= 0:
            return True                  # no matched book yet: unconstrained
        return (D(str(abs(after))) / D(str(matched))) <= D(str(limit))
    return g


def patched_engine(gate):
    """NativeEngine with the acquisition quote gated. Nothing else changes."""
    class Gated(E.NativeEngine):
        def _cap_permits(self, side, qty):
            if not super()._cap_permits(side, qty):
                return False
            return gate(self.bk.residual(), side, qty,
                        min(self.bk.q["up"], self.bk.q["down"]))
    return Gated


def run(gate, *, label):
    orig = X.E.NativeEngine
    X.E.NativeEngine = patched_engine(gate)
    try:
        rows = []
        for slug, w in USABLE.items():
            r = X.replay_window(TAPE[slug], winner=w, rung=RUNG, clip=CLIP)
            rows.append((slug, r))
    finally:
        X.E.NativeEngine = orig
    nets = [P.net_of(r) for _, r in rows]
    resid = [abs(D(str((r.get("bk") or {}).get("residual", 0)
                       if isinstance(r.get("bk"), dict) else 0)))
             for _, r in rows]
    n = len(nets)
    srt = sorted(nets)
    trimmed = srt[1:-1] if n > 2 else srt
    return {
        "label": label, "n": n,
        "mean": sum(nets, D(0)) / n,
        "median": srt[n // 2],
        "total": sum(nets, D(0)),
        "winning": sum(1 for x in nets if x > 0),
        "losing": sum(1 for x in nets if x < 0),
        "trimmed_mean": sum(trimmed, D(0)) / len(trimmed),
        "nets": nets,
    }


if __name__ == "__main__":
    TAPE = load()
    prospective = X.replay_window          # keep a handle for clarity
    cands = [
        ("1 current frozen behaviour", gate_none),
        ("2 no overweight ext > 1 clip", gate_abs(1)),
        ("3 no overweight ext > 2 clips", gate_abs(2)),
        ("4 underweight-only >= 1 clip", gate_underweight_only(1)),
        ("5 L5-derived ratio cap 0.50", gate_ratio("0.50")),
        ("5b L5-derived ratio cap 1.09", gate_ratio("1.087")),
    ]
    res = [run(g, label=lab) for lab, g in cands]
    print("CONTROL (execution-aware one-fill-per-order): 5.9220629591/window")
    print()
    print("%-32s %10s %10s %11s %8s %8s %11s"
          % ("candidate", "mean", "median", "total", "win", "lose",
             "trimmed"))
    for r in res:
        print("%-32s %10.4f %10.4f %11.4f %8d %8d %11.4f"
              % (r["label"], r["mean"], r["median"], r["total"],
                 r["winning"], r["losing"], r["trimmed_mean"]))
    print()
    base = res[0]["mean"]
    for r in res[1:]:
        print("%-32s delta vs frozen %+.4f/window" % (r["label"],
                                                      r["mean"] - base))
