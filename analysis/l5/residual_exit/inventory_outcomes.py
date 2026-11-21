"""Do L5's stranded-inventory behaviours predict better OUTCOMES?

`forensics.py` answers which states predict L5's next ACTION. That is
behavioural replication and it is already done; this module asks the question
it does not: conditional on state, does the action he takes leave the window
BETTER OFF in realized money, and does that survive a chronological holdout?

WHY THE WINDOW, NOT THE EPISODE, IS THE UNIT. An EXTEND grows the residual and
books no matched P&L; a COMPLETE books it. So per-episode net is mechanically
biased toward COMPLETE -- the extend's value shows up in a LATER episode of the
same window. All episodes in a window share one settlement, so the window is
the only unit at which the choice and its consequence are both present.
Window net reconciles to +$18,191.93 (matched +$162,257, residual -$144,065).

WHAT IS OBSERVED AND WHAT IS NOT. Every feature here is built from L5's own
prints. "L5 waited" is NOT observable: BTC15m has zero sells and no book, so a
quote that never filled leaves no trace, and `NONE_CARRIED` means only that no
fill followed. Nothing in this module treats the absence of a trade as a
decision. BTC distance from strike is also NOT computable for this corpus --
spot coverage is 2026-08-31/09-01 only against a corpus from 2026-03-19 -- so
strike distance, volatility and time enter only as the market price already
combines them, plus realized print-to-print movement.

This is observational. Within-state comparison reduces confounding; it does not
remove it. L5 chose using a book we cannot see.

No network, no venue, no orders, no limit changed.
"""
from __future__ import annotations

import collections
import json
import os
import statistics as st
from typing import Any, Dict, List, Mapping, Optional, Sequence

from analysis.l5.residual_exit import forensics as F

#: an EXTEND taken while the book is already this lopsided is an
#: "add to an already-overweight side". residual_rel = |res|/(up+dn).
OVERWEIGHT_REL = 0.50

#: chronological holdout fraction, same convention as forensics
TRAIN_FRAC = 0.70

EXTEND = "EXTEND"
COMPLEMENT = ("COMPLETE_PARTIAL", "COMPLETE_FLAT")
SWITCH = "SWITCH"


def window_rows(eps: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One row per window: observed behaviour plus the realized outcome."""
    by: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for e in eps:
        by[e["cid"]].append(e)
    out: List[Dict[str, Any]] = []
    for cid, es in by.items():
        es = sorted(es, key=lambda e: e["t"])
        a = es[0]
        acts = [e["next_action"] for e in es]
        n = len(es)
        matched = a["window_matched_pnl"] or 0.0
        resid = a["window_residual_pnl"] or 0.0
        fills = a["window_n_fills"] or 0
        ow = [e for e in es
              if e["next_action"] == EXTEND and e["residual_rel"] is not None
              and e["residual_rel"] >= OVERWEIGHT_REL]
        dps = [abs(e["dp_up_60s"]) for e in es if e["dp_up_60s"] is not None]
        out.append({
            "cid": cid,
            "window_start": a["window_start"],
            "n_episodes": n,
            "n_fills": fills,
            # --- observed behaviour -------------------------------------
            "extend_share": sum(1 for x in acts if x == EXTEND) / n,
            "complement_share": sum(1 for x in acts if x in COMPLEMENT) / n,
            "switch_share": sum(1 for x in acts if x == SWITCH) / n,
            "overweight_extends": len(ow),
            "overweight_extend_share": len(ow) / n,
            "max_residual_rel": max(e["residual_rel"] or 0 for e in es),
            "max_residual_abs": max(e["residual_abs"] or 0 for e in es),
            "max_run_len": max(e["same_side_run_len"] or 0 for e in es),
            "median_episode_dur_s": st.median([e["duration_s"] for e in es]),
            "mean_abs_dp60": (st.mean(dps) if dps else None),
            "carried": any(e["exit_mode"] == "CARRIED_TO_SETTLEMENT"
                           for e in es),
            # --- realized outcome ---------------------------------------
            "matched_pnl": matched,
            "residual_pnl": resid,
            "net": matched + resid,
            "net_per_fill": ((matched + resid) / fills) if fills else None,
            "settlement_residual_abs": abs(a["window_residual_signed"] or 0),
        })
    out.sort(key=lambda r: r["window_start"])
    return out


def split_chrono(rows: Sequence[Mapping[str, Any]], frac: float = TRAIN_FRAC):
    k = int(len(rows) * frac)
    return list(rows[:k]), list(rows[k:])


def _q(xs: Sequence[float], f: float) -> float:
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, round(f * (len(s) - 1))))]


def _bins(rows: Sequence[Mapping[str, Any]], feature: str,
          edges: Sequence[float]) -> List[tuple]:
    """Fixed edges so TRAIN and OOS bins are the SAME bins."""
    labels = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else float("inf")
        labels.append((lo, hi, f"{lo:g}-{hi:g}" if hi != float("inf")
                       else f">={lo:g}"))
    got = []
    for lo, hi, lbl in labels:
        sel = [r for r in rows
               if r[feature] is not None and lo <= r[feature] < hi]
        got.append((lbl, sel))
    return got


def outcome_table(train: Sequence[Mapping[str, Any]],
                  oos: Sequence[Mapping[str, Any]],
                  feature: str, edges: Sequence[float],
                  outcome: str = "net", min_n: int = 40) -> Dict[str, Any]:
    """Mean outcome by feature bin, TRAIN and OOS, with the OOS ordering."""
    res: Dict[str, Any] = {"feature": feature, "outcome": outcome,
                           "edges": list(edges), "bins": []}
    tb = dict((l, s) for l, s in _bins(train, feature, edges))
    ob = dict((l, s) for l, s in _bins(oos, feature, edges))
    for lbl in tb:
        t, o = tb[lbl], ob.get(lbl, [])
        tv = [r[outcome] for r in t if r[outcome] is not None]
        ov = [r[outcome] for r in o if r[outcome] is not None]
        res["bins"].append({
            "bin": lbl,
            "train_n": len(tv), "oos_n": len(ov),
            "train_mean": (round(st.mean(tv), 4) if len(tv) >= min_n else None),
            "oos_mean": (round(st.mean(ov), 4) if len(ov) >= min_n else None),
            "train_median": (round(st.median(tv), 4) if len(tv) >= min_n else None),
            "oos_median": (round(st.median(ov), 4) if len(ov) >= min_n else None),
        })
    usable = [b for b in res["bins"]
              if b["train_mean"] is not None and b["oos_mean"] is not None]
    res["usable_bins"] = len(usable)
    if len(usable) >= 3:
        res["spearman_train_vs_oos"] = F._spearman(
            [b["train_mean"] for b in usable], [b["oos_mean"] for b in usable])
        res["oos_spread"] = round(max(b["oos_mean"] for b in usable)
                                  - min(b["oos_mean"] for b in usable), 4)
        res["train_spread"] = round(max(b["train_mean"] for b in usable)
                                    - min(b["train_mean"] for b in usable), 4)
    return res


#: features and FIXED bin edges, chosen from TRAIN quantiles once and then
#: frozen here so the OOS table is scored on identical bins
FEATURES = {
    "extend_share":            [0.0, 0.4, 0.6, 0.75, 0.9],
    "complement_share":        [0.0, 0.05, 0.15, 0.25, 0.4],
    "switch_share":            [0.0, 0.1, 0.2, 0.3],
    "overweight_extend_share": [0.0, 0.05, 0.15, 0.3],
    "max_residual_rel":        [0.0, 0.1, 0.5, 0.95],
    "max_run_len":             [1, 2, 3, 5, 9],
    "n_fills":                 [1, 5, 15, 40, 100],
    "median_episode_dur_s":    [0, 15, 30, 60, 150],
    "mean_abs_dp60":           [0.0, 0.01, 0.02, 0.05],
}


def overweight_detail(eps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The specific question: what happens when L5 adds to an already
    overweight side? Episode-level, because the action is episode-level, and
    reported as the realized settlement outcome of the carried ones only --
    the sole episodes whose residual P&L is realized."""
    tr, oos = F.split_chrono(eps)
    out: Dict[str, Any] = {"overweight_rel_threshold": OVERWEIGHT_REL}
    for name, pool in (("train", tr), ("oos", oos)):
        ow = [e for e in pool if e["residual_rel"] is not None
              and e["residual_rel"] >= OVERWEIGHT_REL]
        nw = [e for e in pool if e["residual_rel"] is not None
              and e["residual_rel"] < OVERWEIGHT_REL]
        d: Dict[str, Any] = {}
        for lbl, grp in (("overweight", ow), ("not_overweight", nw)):
            acts = collections.Counter(e["next_action"] for e in grp)
            tot = sum(acts.values()) or 1
            carried = [e for e in grp
                       if e["exit_mode"] == "CARRIED_TO_SETTLEMENT"
                       and e["episode_residual_pnl"] is not None]
            d[lbl] = {
                "n": len(grp),
                "p_extend": round(acts[EXTEND] / tot, 4),
                "p_complement": round(sum(acts[a] for a in COMPLEMENT) / tot, 4),
                "p_switch": round(acts[SWITCH] / tot, 4),
                "p_none_carried": round(acts["NONE_CARRIED"] / tot, 4),
                "carried_n": len(carried),
                "carried_residual_pnl_sum": (
                    round(sum(e["episode_residual_pnl"] for e in carried), 2)
                    if carried else None),
                "carried_residual_pnl_mean": (
                    round(st.mean([e["episode_residual_pnl"] for e in carried]), 4)
                    if carried else None),
                "carried_p_profitable": (
                    round(sum(1 for e in carried
                              if e["episode_residual_pnl"] > 0) / len(carried), 4)
                    if carried else None),
            }
        out[name] = d
    return out


def transition_matrix(eps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """P(next action | state bucket), TRAIN and OOS, over the FOUR observed
    transitions. `NONE_CARRIED` is reported but is NOT a 'wait': it means no
    fill followed, which is indistinguishable from an unfilled resting quote.
    """
    tr, oos = F.split_chrono(eps)
    feats = ("same_side_run_len", "residual_abs", "dp_up_60s", "tte_s",
             "window_progress", "p_up_last_print", "pair_cost_last_prints",
             "residual_is_favored")
    out: Dict[str, Any] = {"actions": [EXTEND, *COMPLEMENT, SWITCH,
                                       "NONE_CARRIED"],
                           "none_carried_is_not_a_wait": True}
    for f in feats:
        rows = []
        buckets = sorted({F._bin(f, e) for e in eps} - {None},
                         key=lambda s: str(s))
        for b in buckets:
            r: Dict[str, Any] = {"bucket": b}
            for nm, pool in (("train", tr), ("oos", oos)):
                g = [e for e in pool if F._bin(f, e) == b]
                if len(g) < F.MIN_BIN:
                    r[nm] = None
                    continue
                c = collections.Counter(e["next_action"] for e in g)
                t = sum(c.values())
                r[nm] = {"n": t,
                         "extend": round(c[EXTEND] / t, 4),
                         "complement": round(
                             sum(c[a] for a in COMPLEMENT) / t, 4),
                         "switch": round(c[SWITCH] / t, 4),
                         "none_carried": round(c["NONE_CARRIED"] / t, 4)}
            rows.append(r)
        out[f] = rows
    return out


def report(cohort: str = "BTC15m") -> Dict[str, Any]:
    eps = F.build(cohort)
    rows = window_rows(eps)
    train, oos = split_chrono(rows)
    rep: Dict[str, Any] = {
        "cohort": cohort,
        "n_episodes": len(eps),
        "n_windows": len(rows),
        "train_windows": len(train), "oos_windows": len(oos),
        "split": "chronological on window_start, %.0f%% train" % (100 * TRAIN_FRAC),
        "unit": "window (one settlement per window)",
        "unobservable": [
            "L5 waited vs quoted-and-not-hit: identical in this data",
            "BTC distance from strike: spot coverage is 2026-08-31/09-01 only",
            "book state, queue position, maker/taker per fill",
        ],
        "reconciliation": {
            "window_matched_pnl_sum": round(sum(r["matched_pnl"] for r in rows), 2),
            "window_residual_pnl_sum": round(sum(r["residual_pnl"] for r in rows), 2),
            "window_net_sum": round(sum(r["net"] for r in rows), 2),
        },
        "outcome_tables": {}, "outcome_tables_per_fill": {},
        "overweight": overweight_detail(eps),
        "transitions": transition_matrix(eps),
    }
    for f, edges in FEATURES.items():
        rep["outcome_tables"][f] = outcome_table(train, oos, f, edges, "net")
        rep["outcome_tables_per_fill"][f] = outcome_table(
            train, oos, f, edges, "net_per_fill")
    return rep


def main() -> int:
    rep = report()
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "inventory_outcomes_btc15m.json")
    with open(dst, "w") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    print("wrote", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
