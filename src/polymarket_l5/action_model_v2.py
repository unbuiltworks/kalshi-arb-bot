"""OFFLINE review: is the next-action model under-specified? (issue #167 pkg 2)

The live ex-ante benchmark shows the coarse v1 state (price band x tte x
imbalance) beats the marginal habit mix on log-prob but NOT on relation
top-1 — real but small signal. Question: does richer state close the gap,
or is L5 inherently stochastic at fill granularity?

Method (no-overfit guardrails, all offline; production model untouched):
  * TEMPORAL HOLDOUT: train on systematic-regime windows starting before
    Aug 31 00:00 UTC, validate on windows starting after. No validation
    row influences any count used to score it.
  * Candidate features are added ONE AT A TIME to the v1 base state, and
    then greedily combined; each variant is scored on the same held-out
    transitions with the same metrics as the live scorer (mean log-prob
    of the actual action; relation top-1; exact top-1), against the same
    marginal baseline.
  * SHRINKAGE, not raw counts: P(a|s_full) = (n_full*p_full + ALPHA*p_base)
    / (n_full + ALPHA) with backoff to the v1 base-state distribution,
    then to the training marginal — so rare rich states cannot memorize.
  * State-space cost is reported (distinct states, unseen-state rate on
    validation) so "improvement" bought by fragmentation is visible.

Candidate features (from the owner's list, computable from the canonical
sequence; maker/taker is excluded — it is unknown outside Aug 28-31 and
would leak coverage-era information):
  asset, horizon, pair-cost bucket, matched-share bucket, last-relation,
  last-size bucket, cadence (gap since previous fill), recent price move,
  window notional-so-far, fill-sequence position.

Output: analysis/l5/l5_action_model_v2_review.json + stdout table.
Read-only research; nothing here replaces the production model.

    python3 -m src.polymarket_l5.action_model_v2
"""
from __future__ import annotations

import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config
from .action_model import (SYSTEMATIC_REGIME_EPOCH, _bucket, _relation,
                           action_key, state_key, PRICE_BANDS, TTE_BUCKETS,
                           IMBALANCE_BUCKETS, SIZE_BUCKETS)

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT = config.OUTPUT_DIR / "l5_action_model_v2_review.json"

HOLDOUT_EPOCH = 1788134400            # 2026-08-31 00:00 UTC
ALPHA = 10.0                          # shrinkage strength toward base state
EPS = 1e-6

PAIRCOST_BUCKETS = ((0.90, "pc<90c"), (0.95, "pc90-95"), (1.00, "pc95-100"),
                    (1e9, "pc>=1"))
GAP_BUCKETS = ((2.0, "gap<2s"), (10.0, "gap2-10"), (30.0, "gap10-30"),
               (1e12, "gap>30"))
MATCHED_BUCKETS = ((0.5, "m<50"), (0.8, "m50-80"), (1e9, "m>=80"))
NOTIONAL_BUCKETS = ((25.0, "n<25"), (100.0, "n25-100"), (400.0, "n100-400"),
                    (1e12, "n>400"))
SEQ_BUCKETS = ((5.0, "seq<5"), (15.0, "seq5-15"), (1e12, "seq>15"))
MOVE_BUCKETS = ((-0.03, "dn"), (0.03, "flat"), (1e9, "up"))


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _windows():
    with gzip.open(SEQUENCE_CSV, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def build_transitions() -> Tuple[List[dict], List[dict]]:
    """One dict per (state-before, action) transition, with all candidate
    feature values precomputed. Split by window start time."""
    train, valid = [], []
    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        w0 = _f(rows[0]["timestamp"])
        if w0 < SYSTEMATIC_REGIME_EPOCH:
            continue
        dest = valid if w0 >= HOLDOUT_EPOCH else train
        spend = 0.0
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            spend += _f(prev["dollars"]) if prev["side"] == "BUY" else 0.0
            resid = _f(prev["residual_qty_signed"])
            pc = prev["matched_pair_unit_cost"]
            p3 = _f(rows[max(i - 2, 0)]["price"])
            t = {
                "base": state_key(_f(prev["price"]),
                                  _f(nxt["seconds_to_settlement"]), resid),
                "action": action_key(_relation(resid, nxt["outcome"],
                                               nxt["side"]),
                                     _f(nxt["quantity"])),
                "asset": prev["asset"],
                "horizon": prev["duration_minutes"],
                "paircost": _bucket(_f(pc), PAIRCOST_BUCKETS) if pc else "pc_none",
                "matched": _bucket(
                    2 * _f(prev["matched_qty"]) /
                    max(_f(prev["cum_up_qty"]) + _f(prev["cum_down_qty"]), 1e-9),
                    MATCHED_BUCKETS),
                "last_rel": _relation(
                    _f(rows[i - 1]["residual_qty_signed"]) if i else 0.0,
                    prev["outcome"], prev["side"]),
                "last_size": _bucket(_f(prev["quantity"]), SIZE_BUCKETS),
                "cadence": _bucket(
                    _f(nxt["timestamp"]) - _f(prev["timestamp"]), GAP_BUCKETS),
                "move": _bucket(_f(prev["price"]) - p3, MOVE_BUCKETS),
                "notional": _bucket(spend, NOTIONAL_BUCKETS),
                "seqpos": _bucket(float(i + 1), SEQ_BUCKETS),
            }
            dest.append(t)
    return train, valid


class Variant:
    """Base state + extra feature keys, with shrinkage backoff scoring."""

    def __init__(self, extras: Tuple[str, ...]):
        self.extras = extras
        self.full: Dict[tuple, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.base: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.marginal: Dict[str, int] = defaultdict(int)

    def key(self, t: dict) -> tuple:
        return (t["base"],) + tuple(t[e] for e in self.extras)

    def fit(self, train: List[dict]) -> None:
        for t in train:
            self.full[self.key(t)][t["action"]] += 1
            self.base[t["base"]][t["action"]] += 1
            self.marginal[t["action"]] += 1

    def dist(self, t: dict) -> Tuple[Dict[str, float], bool]:
        mtot = sum(self.marginal.values())
        marg = {a: c / mtot for a, c in self.marginal.items()}
        base_counts = self.base.get(t["base"])
        if base_counts:
            btot = sum(base_counts.values())
            base = {a: (base_counts.get(a, 0) + ALPHA * marg.get(a, 0.0)) /
                       (btot + ALPHA) for a in set(base_counts) | set(marg)}
        else:
            base = marg
        fc = self.full.get(self.key(t))
        if not fc:
            return base, True
        ftot = sum(fc.values())
        return ({a: (fc.get(a, 0) + ALPHA * base.get(a, 0.0)) / (ftot + ALPHA)
                 for a in set(fc) | set(base)}, False)

    def evaluate(self, valid: List[dict]) -> Dict[str, Any]:
        mtot = sum(self.marginal.values())
        marg = {a: c / mtot for a, c in self.marginal.items()}
        marg_modal = max(marg, key=marg.get)

        def rel_top(dist):
            agg = defaultdict(float)
            for a, p in dist.items():
                agg[a.split("|")[0]] += p
            return max(agg, key=agg.get)

        marg_rel_top = rel_top(marg)
        logp = logp_m = 0.0
        exact = exact_m = rel = rel_m = unseen = 0
        for t in valid:
            dist, backed_off = self.dist(t)
            unseen += backed_off
            a = t["action"]
            logp += math.log(dist.get(a, 0.0) + EPS)
            logp_m += math.log(marg.get(a, 0.0) + EPS)
            exact += max(dist, key=dist.get) == a
            exact_m += marg_modal == a
            r = a.split("|")[0]
            rel += rel_top(dist) == r
            rel_m += marg_rel_top == r
        n = len(valid)
        return {
            "extras": list(self.extras) or ["(v1 base)"],
            "train_states": len(self.full),
            "valid_unseen_state_pct": round(100 * unseen / n, 1),
            "mean_logp": round(logp / n, 4),
            "mean_logp_marginal": round(logp_m / n, 4),
            "exact_top1_pct": round(100 * exact / n, 1),
            "exact_top1_pct_marginal": round(100 * exact_m / n, 1),
            "relation_top1_pct": round(100 * rel / n, 1),
            "relation_top1_pct_marginal": round(100 * rel_m / n, 1),
        }


CANDIDATES = ("asset", "horizon", "paircost", "matched", "last_rel",
              "last_size", "cadence", "move", "notional", "seqpos")


def main() -> int:
    train, valid = build_transitions()
    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "holdout_epoch": HOLDOUT_EPOCH,
        "train_transitions": len(train),
        "valid_transitions": len(valid),
        "alpha_shrinkage": ALPHA,
        "note": ("temporal holdout; shrinkage backoff full->base->marginal; "
                 "maker/taker excluded (unknown outside Aug 28-31)"),
        "variants": [],
    }
    base = Variant(())
    base.fit(train)
    results = [base.evaluate(valid)]
    print(f"train={len(train)} valid={len(valid)}")
    for feat in CANDIDATES:
        v = Variant((feat,))
        v.fit(train)
        results.append(v.evaluate(valid))
    # greedy pairwise on top-3 single features by mean_logp
    singles = sorted(results[1:], key=lambda r: -r["mean_logp"])[:3]
    tried = set()
    for a in singles:
        for b in singles:
            fa, fb = a["extras"][0], b["extras"][0]
            if fa >= fb or (fa, fb) in tried:
                continue
            tried.add((fa, fb))
            v = Variant((fa, fb))
            v.fit(train)
            results.append(v.evaluate(valid))
    out["variants"] = results
    OUT.write_text(json.dumps(out, indent=1))
    hdr = f"{'variant':<26}{'states':>8}{'unseen%':>8}{'logp':>9}{'exact%':>8}{'rel%':>7}"
    print(hdr)
    for r in sorted(results, key=lambda r: -r["mean_logp"]):
        print(f"{'+'.join(r['extras']):<26}{r['train_states']:>8}"
              f"{r['valid_unseen_state_pct']:>8}{r['mean_logp']:>9}"
              f"{r['exact_top1_pct']:>8}{r['relation_top1_pct']:>7}")
    print(f"baselines: marginal logp={results[0]['mean_logp_marginal']} "
          f"exact={results[0]['exact_top1_pct_marginal']}% "
          f"rel={results[0]['relation_top1_pct_marginal']}%")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
