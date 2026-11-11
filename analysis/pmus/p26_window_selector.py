"""P26 -- can an ACTIVE PMUS BTC15m window be identified without hindsight?

60.7% of closed windows carry no volume at all, so allocating hypothetical
capital uniformly across windows overstates capacity by roughly 2.5x and wastes
every action budget spent on a dead window. This is a VENUE SELECTOR -- which
window is worth watching -- and explicitly not a directional signal: it says
nothing about Up or Down and never chooses a side.

TWO CANDIDATE SIGNALS, AND THE RULE THEY BOTH OBEY.

  A. PRIOR-WINDOW STATE. Volume alternates almost perfectly across the
     history: `.V.V.V.V`. The previous window's outcome is known before the
     next window opens, so this predictor has ZERO latency and uses no future
     information at all.

  B. WITHIN-WINDOW ONSET. `sharesTraded` growth, a two-sided book, spread and
     depth, evaluated at time t using only snapshots strictly before t. This
     one costs latency, and the latency is the thing worth measuring.

Neither may consult anything after the instant it is evaluated at. A predictor
that peeks at the window's final volume is not a predictor.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from typing import Any, Dict, List, Optional


def load_history(path: str) -> List[dict]:
    d = json.load(open(path))
    rows = d["rows"] if isinstance(d, dict) else d
    rows = [r for r in rows if r.get("closed")]
    rows.sort(key=lambda r: r["start"])
    return rows


def confusion(pred: List[bool], truth: List[bool]) -> Dict[str, Any]:
    tp = sum(1 for p, t in zip(pred, truth) if p and t)
    fp = sum(1 for p, t in zip(pred, truth) if p and not t)
    fn = sum(1 for p, t in zip(pred, truth) if not p and t)
    tn = sum(1 for p, t in zip(pred, truth) if not p and not t)
    n = len(truth)
    return {
        "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / n, 4) if n else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else None,
        "false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else None,
        "base_rate_active": round(sum(truth) / n, 4) if n else None,
    }


def signal_a(rows: List[dict]) -> Dict[str, Any]:
    """Predict window i is ACTIVE from window i-1 being QUIET, and vice versa.

    Evaluated only where a previous window exists. Uses nothing but state that
    was already settled before the window opened, so its detection latency is
    zero -- the call can be made before the first quote.
    """
    truth = [r["shares"] > 0 for r in rows]
    pred, tru = [], []
    for i in range(1, len(truth)):
        pred.append(not truth[i - 1])            # alternation
        tru.append(truth[i])
    c = confusion(pred, tru)
    c["rule"] = "predict ACTIVE iff the immediately preceding window was QUIET"
    c["detection_latency_s"] = 0.0
    c["uses_future_information"] = False
    # the always-predict-active baseline, for honesty about lift
    base = confusion([True] * len(tru), tru)
    c["baseline_always_active"] = {k: base[k] for k in
                                   ("accuracy", "precision", "recall",
                                    "false_positive_rate")}
    return c


def runs(rows: List[dict]) -> Dict[str, Any]:
    """Is the alternation real, or would any streaky series look like this?"""
    seq = [r["shares"] > 0 for r in rows]
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    return {"length": len(seq), "transitions": flips,
            "transition_rate": round(flips / (len(seq) - 1), 4)
            if len(seq) > 1 else None,
            "note": "a rate near 1.0 is near-perfect alternation; 0.5 is what "
                    "an independent coin would give"}


def signal_b(tape_glob: str) -> Dict[str, Any]:
    """Within-window onset, from the live book tape.

    For each window: the elapsed seconds from the first snapshot to the first
    STRICT increase in sharesTraded, and to the first two-sided book. Both are
    evaluated forward-only.
    """
    import datetime as dt
    by: Dict[str, List[dict]] = {}
    for f in sorted(glob.glob(tape_glob)):
        for line in open(f):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "bbo" and r.get("slug"):
                by.setdefault(r["slug"], []).append(r)
    out = []
    for slug, rs in sorted(by.items()):
        rs.sort(key=lambda r: r["t"])
        t0 = dt.datetime.fromisoformat(rs[0]["t"].replace("Z", "+00:00"))

        def el(row):
            return (dt.datetime.fromisoformat(
                row["t"].replace("Z", "+00:00")) - t0).total_seconds()

        first_growth = None
        prev = None
        for r in rs:
            v = r.get("shares_traded")
            v = float(v) if v is not None else None
            if prev is not None and v is not None and v > prev:
                first_growth = round(el(r), 1)
                break
            if v is not None:
                prev = v
        two = next((round(el(r), 1) for r in rs if r.get("two_sided")), None)
        total = max((float(r.get("shares_traded") or 0) for r in rs),
                    default=0.0)
        out.append({
            "market": slug, "samples": len(rs),
            "observed_total_contracts": round(total, 1),
            "active": total > 0,
            "first_shares_growth_s": first_growth,
            "first_two_sided_s": two,
            "observed_span_s": round(el(rs[-1]), 1),
        })
    return {"windows": out,
            "caveat": ("sharesTraded refreshes on the gateway's ~30 s cache, "
                       "so an onset latency below ~30 s is not resolvable "
                       "here; these are UPPER BOUNDS on true onset")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history",
                    default="analysis/pmus/p24_pmus_btc15m_window_volumes.json")
    ap.add_argument("--tape", default="data/pmus_btc15m/*.jsonl")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rows = load_history(a.history)
    rep = {
        "label": "PMUS_BTC15M_WINDOW_SELECTOR_V1",
        "closed_windows": len(rows),
        "active": sum(1 for r in rows if r["shares"] > 0),
        "quiet": sum(1 for r in rows if r["shares"] == 0),
        "alternation": runs(rows),
        "signal_a_prior_window": signal_a(rows),
        "signal_b_within_window": signal_b(a.tape),
        "labels": "a VENUE SELECTOR; it never chooses a side and is not a "
                  "directional signal",
    }
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print("closed %d  active %d  quiet %d" % (rep["closed_windows"],
                                              rep["active"], rep["quiet"]))
    print("alternation:", rep["alternation"])
    a1 = rep["signal_a_prior_window"]
    print("\nSIGNAL A -- %s" % a1["rule"])
    print("  n=%d tp=%d fp=%d fn=%d tn=%d" % (a1["n"], a1["tp"], a1["fp"],
                                              a1["fn"], a1["tn"]))
    print("  accuracy %s  precision %s  recall %s  FPR %s  FNR %s"
          % (a1["accuracy"], a1["precision"], a1["recall"],
             a1["false_positive_rate"], a1["false_negative_rate"]))
    print("  base rate active %s   latency %ss   uses future info: %s"
          % (a1["base_rate_active"], a1["detection_latency_s"],
             a1["uses_future_information"]))
    print("  baseline (always predict ACTIVE): %s" % a1["baseline_always_active"])
    print("\nSIGNAL B -- within-window onset")
    for w in rep["signal_b_within_window"]["windows"]:
        print("  %-38s active=%-5s first_growth=%-7s first_two_sided=%-7s "
              "span=%ss" % (w["market"][-24:], w["active"],
                            w["first_shares_growth_s"],
                            w["first_two_sided_s"], w["observed_span_s"]))
    print("  caveat:", rep["signal_b_within_window"]["caveat"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
