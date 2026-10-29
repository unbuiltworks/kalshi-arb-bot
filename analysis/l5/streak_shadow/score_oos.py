"""P2: is the streak arm actually better, out of sample?

Fits BOTH arms on the training cohort only (canonical windows starting
before 2026-08-31 00:00 UTC) and scores them on the held-out cohort, which
no count used to score it has ever seen. The production artifact pools
train+valid -- correct for serving, useless for judging -- so nothing here
loads it.

Reported: log-loss, Brier, accuracy, calibration, action-distribution L1,
same-side probability calibration, and per-window trajectory similarity.
Accuracy is reported LAST and is never the promotion criterion: a cell
splitting 0.66/0.91 argmaxes the same way on both halves, so a stochastic
engine can be badly miscalibrated at unchanged accuracy. Action FREQUENCY
inside the cells P2 flagged is the measurement that matters.

    python3 analysis/l5/streak_shadow/score_oos.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_streak_shadow as ss            # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "oos_scores.json"
EPS = 1e-12

# the three cells P2 measured the largest within-key behaviour splits in
FOCUS_CELLS = ("p10-25c|tte2-5m|imb1-10",
               "p<10c|tte2-5m|imb10-50",
               "p10-25c|tte30-120s|imb10-50")


def _masses(model, t, arm):
    if arm == "frozen":
        d = ss.frozen_dist(model, t["base"], t["last_rel"], t["last_size"])
    else:
        d = ss.streak_dist(model, t["base"], t["last_rel"], t["last_size"],
                           t["streak"])
    return ss.relation_masses(d["dist"]), d


def _metrics(pairs):
    """pairs: [(predicted masses, actual relation)]"""
    ll = brier = 0.0
    hit = 0
    for p, actual in pairs:
        q = max(p.get(actual, 0.0), EPS)
        ll -= math.log(q)
        for r in ss.RELATIONS:
            brier += (p.get(r, 0.0) - (1.0 if r == actual else 0.0)) ** 2
        if max(ss.RELATIONS, key=lambda r: p.get(r, 0.0)) == actual:
            hit += 1
    n = len(pairs) or 1
    return {"logloss": round(ll / n, 5), "brier": round(brier / n, 5),
            "accuracy": round(hit / n, 4), "n": len(pairs)}


def _calibration(probs_labels, bins=10):
    """Reliability table for a binary probability (predicted, 0/1)."""
    buckets = defaultdict(lambda: [0.0, 0, 0])     # sum p, n, positives
    for p, y in probs_labels:
        b = min(int(p * bins), bins - 1)
        buckets[b][0] += p
        buckets[b][1] += 1
        buckets[b][2] += y
    rows, ece, tot = [], 0.0, sum(v[1] for v in buckets.values()) or 1
    for b in sorted(buckets):
        sp, n, pos = buckets[b]
        mean_p, frac = sp / n, pos / n
        ece += n * abs(mean_p - frac)
        rows.append({"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "n": n,
                     "mean_predicted": round(mean_p, 4),
                     "observed": round(frac, 4)})
    return {"bins": rows, "ece": round(ece / tot, 5)}


def main() -> int:
    train, valid = ss.build_transitions()
    model = ss.build_models(train)                 # TRAIN ONLY
    out = {"labels": "SHADOW ONLY; fitted on train, scored on holdout",
           "cohort": {"train_transitions": len(train),
                      "holdout_transitions": len(valid),
                      "holdout_epoch": ss.HOLDOUT_EPOCH
                      if hasattr(ss, "HOLDOUT_EPOCH") else None}}

    preds = {"frozen": [], "streak": []}
    same_side = {"frozen": [], "streak": []}
    per_window = defaultdict(lambda: {"actual": Counter(),
                                      "frozen": Counter(), "streak": Counter()})
    evidence = Counter()
    for t in valid:
        actual = t["action"].split("|")[0]
        for arm in ("frozen", "streak"):
            p, d = _masses(model, t, arm)
            preds[arm].append((p, actual))
            if arm == "streak":
                evidence["with_streak_evidence" if not d["backed_off"]
                         else "backed_off_to_production"] += 1
            # P(next fill is the SAME SIDE as the last one). `extend` always
            # buys the side already held; `complete` always buys the other;
            # `open` is not a same-side question at all and is excluded
            # rather than guessed.
            if actual in ("extend", "complete"):
                p_same = p.get("extend", 0.0) / max(
                    p.get("extend", 0.0) + p.get("complete", 0.0), EPS)
                same_side[arm].append((p_same, t["same_side"]))
            w = per_window[t["window"]]
            w[arm][max(ss.RELATIONS, key=lambda r: p.get(r, 0.0))] += 1
        per_window[t["window"]]["actual"][actual] += 1

    out["streak_evidence_on_holdout"] = dict(evidence)
    out["scores"] = {arm: _metrics(preds[arm]) for arm in preds}
    base = out["scores"]["frozen"]
    st = out["scores"]["streak"]
    out["delta"] = {
        "logloss_pct_better": round(100 * (base["logloss"] - st["logloss"])
                                    / base["logloss"], 3),
        "brier_pct_better": round(100 * (base["brier"] - st["brier"])
                                  / base["brier"], 3),
        "accuracy_pp": round(100 * (st["accuracy"] - base["accuracy"]), 3),
    }
    out["same_side_calibration"] = {
        arm: _calibration(same_side[arm]) for arm in same_side}

    # --- action-distribution L1 over the whole holdout
    actual_freq = Counter(t["action"].split("|")[0] for t in valid)
    n = sum(actual_freq.values()) or 1
    actual_freq = {r: actual_freq.get(r, 0) / n for r in ss.RELATIONS}
    out["action_distribution"] = {"actual": {k: round(v, 4)
                                             for k, v in actual_freq.items()}}
    for arm in preds:
        mean = {r: sum(p.get(r, 0.0) for p, _ in preds[arm]) / max(len(preds[arm]), 1)
                for r in ss.RELATIONS}
        out["action_distribution"][arm] = {k: round(v, 4) for k, v in mean.items()}
        out["action_distribution"][f"l1_{arm}"] = round(
            sum(abs(mean[r] - actual_freq[r]) for r in ss.RELATIONS), 4)

    # --- per-window trajectory similarity (top-1 sequence composition)
    traj = {}
    for arm in ("frozen", "streak"):
        ls = []
        for w in per_window.values():
            tot_a = sum(w["actual"].values()) or 1
            tot_p = sum(w[arm].values()) or 1
            ls.append(sum(abs(w["actual"].get(r, 0) / tot_a
                              - w[arm].get(r, 0) / tot_p)
                          for r in ss.RELATIONS))
        ls.sort()
        traj[arm] = {"windows": len(ls),
                     "mean_l1": round(sum(ls) / max(len(ls), 1), 4),
                     "median_l1": round(ls[len(ls) // 2], 4) if ls else None}
    out["trajectory_similarity"] = traj

    # --- THE MEASUREMENT THAT DECIDES IT: action frequency inside the
    #     cells where P2 found the largest behaviour splits
    focus = {}
    for cell in FOCUS_CELLS:
        sub = [t for t in valid if t["base"] == cell]
        by_streak = defaultdict(list)
        for t in sub:
            by_streak[t["streak"]].append(t)
        rows = {}
        for sk, ts in sorted(by_streak.items()):
            act = Counter(t["action"].split("|")[0] for t in ts)
            m = sum(act.values()) or 1
            row = {"n": len(ts),
                   "actual": {r: round(act.get(r, 0) / m, 4)
                              for r in ss.RELATIONS}}
            for arm in ("frozen", "streak"):
                mean = {r: 0.0 for r in ss.RELATIONS}
                for t in ts:
                    p, _ = _masses(model, t, arm)
                    for r in ss.RELATIONS:
                        mean[r] += p.get(r, 0.0)
                mean = {r: v / len(ts) for r, v in mean.items()}
                row[arm] = {r: round(v, 4) for r, v in mean.items()}
                row[f"l1_{arm}"] = round(
                    sum(abs(mean[r] - row["actual"][r])
                        for r in ss.RELATIONS), 4)
            rows[sk] = row
        focus[cell] = {"holdout_n": len(sub), "by_streak": rows,
                       "frequency_l1_all_streaks": {
                           arm: round(sum(r[f"l1_{arm}"] * r["n"]
                                          for r in rows.values())
                                      / max(sum(r["n"] for r in rows.values()), 1), 4)
                           for arm in ("frozen", "streak")}}
    out["focus_cells"] = focus

    # --- WHY P2 LOOKED STRONGER THAN THIS. P2 tested streak against the
    #     THREE-PART key (price band | tte | imbalance). Production does not
    #     use that key: its live lookup is base|last_rel|last_size, which
    #     already conditions on the previous relation. This block measures
    #     the same variable against the key P2 actually used, so the two
    #     results can be reconciled instead of contradicting each other.
    def _shrunk(counts, prior):
        from src import pmus_l5_persistence_v2 as pv2
        return pv2._shrink(counts, prior)

    base_counts = defaultdict(lambda: defaultdict(int))
    bs_counts = defaultdict(lambda: defaultdict(int))
    marg = defaultdict(int)
    for t in train:
        base_counts[t["base"]][t["action"]] += 1
        bs_counts[f"{t['base']}|{t['streak']}"][t["action"]] += 1
        marg[t["action"]] += 1
    mtot = sum(marg.values()) or 1
    marg_p = {a: c / mtot for a, c in marg.items()}
    p3 = {"base_only": [], "base_only_plus_streak": []}
    for t in valid:
        actual = t["action"].split("|")[0]
        bc = base_counts.get(t["base"])
        base_p = _shrunk(bc, marg_p) if bc else marg_p
        p3["base_only"].append((ss.relation_masses(base_p), actual))
        sc = bs_counts.get(f"{t['base']}|{t['streak']}")
        sp = _shrunk(sc, base_p) if sc else base_p
        p3["base_only_plus_streak"].append((ss.relation_masses(sp), actual))
    sc3 = {k: _metrics(v) for k, v in p3.items()}
    b3, s3 = sc3["base_only"], sc3["base_only_plus_streak"]
    out["p2_key_reconciliation"] = {
        "note": ("P2 scored streak against the three-part key; production "
                 "serves base|last_rel|last_size. Both are reported so the "
                 "earlier number and this one are comparable."),
        "scores": sc3,
        "logloss_pct_better": round(100 * (b3["logloss"] - s3["logloss"])
                                    / b3["logloss"], 3),
        "accuracy_pp": round(100 * (s3["accuracy"] - b3["accuracy"]), 3),
        "production_key_logloss_pct_better": out["delta"]["logloss_pct_better"],
    }

    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
