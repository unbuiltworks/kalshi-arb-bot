#!/usr/bin/env python3
"""Paired-only vs paired + signal-gated selective underdog. Research only.

No network, no orders, no credentials, no exposure change. Every number here
is a REPLAY number: modelled fills against L5's own executed prints, never a
confirmed fill and never realized profit.

WHAT IS BEING COMPARED. Policy A is the deployed paired controller, taken
unchanged from analysis/l5/favorite_accumulation.py. Policy B is the same
controller plus one extra permitted action: a DIRECTIONAL underdog leg -- a
leg on the cheap side that does not pair and is not a completion -- allowed
only when the as-of probability model says that side is underpriced by more
than `threshold` after a conservative `margin`. Every cap, cutoff and ceiling
is identical between the two, and `test_l5_btc15m_signal_replay.py` asserts
that B with the signal switched off reproduces A exactly, window by window.

THE PRICE BASIS IS THE FORWARD PRINT, DELIBERATELY. A directional entry is
priced at the first print AT OR AFTER the decision, not the most recent one
before it. The most recent print is a price that has already happened: its
median age at a decision point is 6 s and its p90 is 36 s, and measuring an
edge against it credits us with the move that occurred since it printed.
On the stale basis the held-out gross edge reads +$0.0957/contract; on the
forward basis it reads +$0.0306 +/- $0.0565, and on the training half it
flips to -$0.0258. The stale basis is the "stale/public BBO opportunity"
CLAUDE.md forbids counting, so it is not used here.

Run: PYTHONPATH=. python3 analysis/l5/btc15m_signal_replay.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from analysis.l5.btc15m_asof import asof_snapshot, strike
from analysis.l5.btc15m_calibration_report import (OFFSETS, apply_logistic,
                                            clustered_mean, fit_logistic,
                                            load_candles, load_sources)
from analysis.l5.btc15m_probability import estimate_up
from analysis.l5.favorite_accumulation import (CASH_CAP, CLIP,
                                               COMPLETE_CUTOFF_S,
                                               ENTRY_CUTOFF_S, EXPOSURE_CAP,
                                               PAIR_CEILING, RESIDUAL_CAP,
                                               load_outcomes, load_trades,
                                               policy_paired)

#: Extra contracts of directional residual the signal may add, on top of the
#: paired controller's own residual cap. Deliberately small: the measured
#: residual economics are 7.4x worse than matched economics, so a signal has
#: to be very good before more residual is a good idea.
DIRECTIONAL_CAP = 1
#: Only the cheap side can be a "selective underdog".
UNDERDOG_MAX_PRICE = 0.50


class SignalBook:
    """Causal as-of probabilities, precomputed on the minute grid.

    A lookup returns the most recent probability computed at or before the
    decision, or None. None means NO DIRECTIONAL ACTION; there is no default
    probability and no fallback to 0.5.
    """

    def __init__(self, grid: Dict[str, List[Tuple[float, float]]]) -> None:
        self._g = {k: sorted(v) for k, v in grid.items()}

    @classmethod
    def build(cls, series, slugs: Sequence[str],
              sources: Dict[str, str],
              calibrate: Optional[Callable[[float], float]] = None) -> "SignalBook":
        """`sources` must carry each slug's own resolutionSource.

        A window whose stream is unknown or unrecognised gets NO signal: the
        settlement functional differs between the two streams, so guessing it
        would mis-price the strike and the horizon together.
        """
        grid: Dict[str, List[Tuple[float, float]]] = {}
        for slug in slugs:
            src = sources.get(slug, "")
            try:
                ws = float(slug.rsplit("-", 1)[1])
                strike(series, ws, src)
            except (ValueError, IndexError):
                continue
            pts: List[Tuple[float, float]] = []
            for off in OFFSETS:
                try:
                    p = estimate_up(asof_snapshot(series, ws, ws + off,
                                                  resolution_source=src))
                except ValueError:
                    continue
                pts.append((ws + off, calibrate(p) if calibrate else p))
            if pts:
                grid[slug] = pts
        return cls(grid)

    def __len__(self) -> int:
        return len(self._g)

    def p_up(self, slug: str, t_abs: float) -> Optional[float]:
        pts = self._g.get(slug)
        if not pts:
            return None
        best = None
        for ts, p in pts:
            if ts <= t_abs:
                best = p
            else:
                break
        return best


def _simulate(ts: List[Dict], *,
              signal: Optional[SignalBook] = None,
              threshold: float = 0.05,
              margin: float = 0.0,
              directional_cap: int = DIRECTIONAL_CAP,
              resid_cap: int = RESIDUAL_CAP,
              ceiling: float = PAIR_CEILING,
              participation: float = 1.0,
              max_actions: int = 8,
              complete_cutoff_s: float = COMPLETE_CUTOFF_S,
              fee_per_contract: float = 0.0,
              rescue: bool = False,
              rescue_allow: Optional[Callable[..., bool]] = None) -> Dict[str, Any]:
    """The deployed paired controller, plus signal-gated underdog entries.

    Derived line by line from favorite_accumulation._simulate; the only added
    branch is the `directional` one, and with `signal=None` the behaviour is
    identical. No cap, cutoff or ceiling is relaxed.
    """
    winner = ts[0]["winner"] if ts else None
    slug = ts[0]["slug"] if ts else ""
    up = dn = 0.0
    spend = 0.0
    acts = 0
    directional = 0
    rescued = 0
    fees = 0.0
    dir_rows: List[Dict[str, Any]] = []
    stride = max(1, int(round(1.0 / participation))) if participation > 0 else 10 ** 9
    for idx, t in enumerate(ts):
        if acts >= max_actions:
            break
        if idx % stride:
            continue
        px, oc, off = t["px"], t["outcome"], t["t_off"]
        held_same = up if oc == "Up" else dn
        held_other = dn if oc == "Up" else up
        reduces = held_other > held_same
        if reduces and off > complete_cutoff_s:
            continue
        if (not reduces) and off > ENTRY_CUTOFF_S:
            continue

        is_directional = False
        if not reduces:
            resid_after = abs((held_same + CLIP) - held_other)
            if resid_after > resid_cap:
                # The paired controller stops here. The signal may permit it
                # as a bounded directional leg, and only on the cheap side.
                if (signal is None or directional >= directional_cap
                        or px >= UNDERDOG_MAX_PRICE
                        or resid_after > resid_cap + directional_cap):
                    continue
                p_up = signal.p_up(slug, t["ws"] + off)
                if p_up is None:
                    continue
                p_side = p_up if oc == "Up" else 1.0 - p_up
                if max(0.0, p_side - margin) - px < threshold:
                    continue
                is_directional = True
        else:
            if held_other > 0:
                carried = spend / (up + dn) if (up + dn) > 0 else px
                if carried + px > ceiling:
                    # THE COMPLETION RESCUE, as deployed. src/l5_two_sided_probe
                    # refuses the cap and then re-asks whether completing beats
                    # carrying the residual to settlement. Replayed here: a
                    # contract costing px returns $1 if its side wins and the
                    # alternative is an unmatched unit, so completing is better
                    # whenever px plus fee is under $1. It earns ~nothing; it
                    # exists to avoid a residual worth -$0.27 measured.
                    if not rescue or px + fee_per_contract >= 1.0:
                        continue
                    # `rescue_allow`, when given, is the policy. Without it
                    # the branch accepts every complement under $1.00, which
                    # is an UPPER BOUND on what rescuing can do, not a
                    # policy: it assumes a complement is always there to be
                    # had. Policies that mean to be deployable pass one.
                    if rescue_allow is not None and not rescue_allow(
                            slug=slug, t_abs=t["ws"] + off, px=px,
                            up=up, dn=dn, spend=spend, off=off, side=oc):
                        continue
                    rescued += 1
        if spend + px * CLIP > CASH_CAP:
            continue
        if max(up, dn) + CLIP > EXPOSURE_CAP:
            continue
        if oc == "Up":
            up += CLIP
        else:
            dn += CLIP
        spend += px * CLIP
        fees += fee_per_contract * CLIP
        acts += 1
        if is_directional:
            directional += 1
            dir_rows.append({"slug": slug, "outcome": oc, "px": px,
                             "off": off, "won": (winner == oc)})

    matched = min(up, dn)
    resid_up, resid_dn = up - matched, dn - matched
    res_win = resid_up if winner == "Up" else resid_dn
    res_other = resid_dn if winner == "Up" else resid_up
    realized = matched * 1.0 + res_win - spend - fees
    other = matched * 1.0 + res_other - spend - fees
    return {"pairs": int(matched), "residual": resid_up + resid_dn,
            "residual_won": res_win, "residual_lost": res_other,
            "rescued": rescued,
            "spend": spend, "fees": fees, "actions": acts,
            "directional": directional, "directional_rows": dir_rows,
            "realized": realized, "if_other_outcome": other,
            "worst_case": min(realized, other)}


def policy_paired_plus_signal(ts, **kw) -> Dict[str, Any]:
    return _simulate(ts, **kw)


# ------------------------------------------------------------------ scoring
def max_drawdown(seq: Sequence[float]) -> float:
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for v in seq:
        cum += v
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def summarize(per_window: List[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any]:
    if not per_window:
        return {"windows": 0}
    per_window = sorted(per_window, key=lambda kv: kv[0])
    nets = [r["realized"] for _, r in per_window]
    byw = {str(ws): [r["realized"]] for ws, r in per_window}
    mu, ci, n = clustered_mean(byw)
    resid = sum(r["residual"] for _, r in per_window)
    resid_lost = sum(r["residual_lost"] for _, r in per_window)
    acts = sum(r["actions"] for _, r in per_window)
    dirn = sum(r["directional"] for _, r in per_window)
    dir_rows = [d for _, r in per_window for d in r["directional_rows"]]
    dir_win = sum(1 for d in dir_rows if d["won"])
    return {
        "windows": n,
        "net_total": sum(nets),
        "net_per_window": mu, "net_per_window_ci": ci,
        "max_drawdown": max_drawdown(nets),
        "worst_window": min(nets), "best_window": max(nets),
        "losing_windows": sum(1 for v in nets if v < 0),
        "pairs": sum(r["pairs"] for _, r in per_window),
        "actions": acts,
        "actions_per_window": acts / n if n else 0.0,
        "residual_contracts": resid,
        "residual_lost_contracts": resid_lost,
        "residual_lose_rate": (resid_lost / resid) if resid else None,
        "directional_legs": dirn,
        "directional_win_rate": (dir_win / len(dir_rows)) if dir_rows else None,
        "directional_mean_price": (sum(d["px"] for d in dir_rows) / len(dir_rows))
                                   if dir_rows else None,
        # STANDALONE AND COUNTERFACTUAL, not a contribution to net. A
        # directional leg that later gets matched settles as half of a pair
        # that pays $1 for BOTH legs, so crediting it a full $1 here would
        # double-count. The only non-double-counting comparison is the paired
        # per-window delta against policy A.
        "directional_standalone_net_if_unmatched": sum(
            (1.0 if d["won"] else 0.0) - d["px"] for d in dir_rows),
    }


def paired_delta(rows_a: Sequence[Tuple[float, Dict]],
                 rows_b: Sequence[Tuple[float, Dict]]) -> Dict[str, Any]:
    """Per-window B minus A, window-clustered. The deciding statistic.

    A and B are run on the SAME windows with the same caps, so the
    difference is paired and the only thing that varies is the signal gate.
    """
    a = {ws: r["realized"] for ws, r in rows_a}
    b = {ws: r["realized"] for ws, r in rows_b}
    per = {str(ws): [b[ws] - a[ws]] for ws in a if ws in b}
    mu, ci, n = clustered_mean(per)
    diffs = [v[0] for v in per.values()]
    return {"windows": n, "mean": mu, "ci": ci,
            "total": sum(diffs),
            "better": sum(1 for d in diffs if d > 0),
            "worse": sum(1 for d in diffs if d < 0),
            "unchanged": sum(1 for d in diffs if d == 0)}


def run(split: float = 0.60, participations: Sequence[float] = (1.0, 0.5, 0.25),
        thresholds: Sequence[float] = (0.03, 0.05, 0.10),
        margins: Sequence[float] = (0.0, 0.03)) -> Dict[str, Any]:
    series = load_candles()
    outcomes = load_outcomes()
    trades = load_trades(outcomes)
    byw: Dict[str, List[Dict]] = collections.defaultdict(list)
    for t in trades:
        byw[t["slug"]].append(t)

    # THE UNIVERSE IS WINDOWS THAT HAVE BOTH. A window with L5 prints but no
    # reference coverage cannot produce a signal, so B is identical to A
    # there: including it would dilute the comparison toward "no difference"
    # and hide whatever the signal does. The candle tape starts 2026-05-20
    # and the strike needs the minute before the open, so most of the
    # March-to-May windows drop out here. They are reported, not discarded
    # silently.
    sources = load_sources()
    probe = SignalBook.build(series, sorted(byw), sources)
    universe = sorted((s for s in byw if probe.p_up(s, float(s.rsplit("-", 1)[1]) + 780.0) is not None),
                      key=lambda s: float(s.rsplit("-", 1)[1]))
    dropped = len(byw) - len(universe)

    cut = int(len(universe) * split)
    train_slugs, test_slugs = universe[:cut], universe[cut:]

    # The recalibration map is fitted on the TRAIN windows only.
    fit_rows: List[Tuple[float, int]] = []
    for slug in train_slugs:
        y = 1 if outcomes[slug] == "Up" else 0
        fit_rows.extend((p, y) for _, p in probe._g.get(slug, ()))
    a, b = fit_logistic(fit_rows)
    book = SignalBook.build(series, universe, sources,
                            calibrate=lambda p: apply_logistic(p, a, b))

    out: Dict[str, Any] = {
        "provenance": {"candles": "data/btc15m/coinbase_1m.jsonl.gz",
                       "candle_source": "Coinbase 1m, PROXY for Chainlink",
                       "price_basis": "L5 executed prints, chronological",
                       "fills": "MODELLED, not confirmed fills",
                       "fees": "maker 0.0, as in the deployed configuration"},
        "platt_on_train": {"a": a, "b": b, "fit_rows": len(fit_rows)},
        "windows": {"with_l5_prints": len(byw), "universe": len(universe),
                    "dropped_no_reference": dropped,
                    "train": len(train_slugs), "test": len(test_slugs)},
        "results": {}, "delta": {},
    }
    for part in participations:
        for name, slugset in (("train", train_slugs), ("test", test_slugs)):
            rows_a = [(float(s.rsplit("-", 1)[1]),
                       _simulate(byw[s], signal=None, participation=part))
                      for s in slugset]
            out["results"]["A_paired|%s|part=%s" % (name, part)] = summarize(rows_a)
            for thr in thresholds:
                for mg in margins:
                    tag = "%s|part=%s|thr=%s|mg=%s" % (name, part, thr, mg)
                    rows_b = [(float(s.rsplit("-", 1)[1]),
                               _simulate(byw[s], signal=book, threshold=thr,
                                         margin=mg, participation=part))
                              for s in slugset]
                    out["results"]["B_signal|" + tag] = summarize(rows_b)
                    out["delta"][tag] = paired_delta(rows_a, rows_b)
    return out


def main() -> int:
    out = run()
    os.makedirs("analysis/l5/btc15m_signal", exist_ok=True)
    with open("analysis/l5/btc15m_signal/replay.json", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("windows: %s" % out["windows"])
    print("Platt fitted on train: a=%.4f b=%.4f over %d rows"
          % (out["platt_on_train"]["a"], out["platt_on_train"]["b"],
             out["platt_on_train"]["fit_rows"]))
    print()
    h = "%-40s %4s %9s %17s %9s %6s %6s %7s %9s"
    print(h % ("policy", "win", "net", "net/win +/- 95%", "maxDD", "pairs",
               "acts", "dirLegs", "residLose"))
    for k in sorted(out["results"]):
        s = out["results"][k]
        if not s.get("windows"):
            continue
        rl = s["residual_lose_rate"]
        print(h % (k, s["windows"], "%+.4f" % s["net_total"],
                   "%+.5f +/- %.5f" % (s["net_per_window"],
                                       s["net_per_window_ci"]),
                   "%+.4f" % s["max_drawdown"], s["pairs"], s["actions"],
                   s["directional_legs"], "n/a" if rl is None else "%.3f" % rl))
    print()
    print("PAIRED per-window delta, B minus A on the SAME windows "
          "(positive = signal helps)")
    dh = "%-34s %4s %18s %9s %6s %6s %6s"
    print(dh % ("variant", "win", "mean/win +/- 95%", "total", "bett", "wors",
                "same"))
    for k in sorted(out["delta"]):
        d = out["delta"][k]
        print(dh % (k, d["windows"],
                    "%+.5f +/- %.5f" % (d["mean"], d["ci"]),
                    "%+.4f" % d["total"], d["better"], d["worse"],
                    d["unchanged"]))
    print()
    print("wrote analysis/l5/btc15m_signal/replay.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
