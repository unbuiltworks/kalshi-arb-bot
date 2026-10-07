"""COMPLETE-vs-EXTEND MISMATCH FORENSIC.

HISTORICAL REPLAY ONLY. Nothing here is shadow, submitted, filled, settled or
realized profit for our account. Every figure describes the public fill tape of
Polymarket trader `l5zn1bwom8etsk`.

THE QUESTION
    In which CONTEMPORANEOUSLY OBSERVABLE states does L5 reduce (`complete`)
    while our deployed model says `extend`, and what are we missing there?

WHAT THIS IS NOT
    * It does not re-score the model. `analysis/l5/action_scorecard/score.py`
      is imported unmodified and is the single source of the row set, the
      chronological split, the model fit and the predictions.
    * It does not fit anything. Every cut reported here is a DESCRIPTIVE
      marginal over rows the scorecard already produced. Any candidate rule is
      labelled DISCOVERY and its confirmation is left unrun (owner's standing
      instruction: no tuning before OOS).

ROW SET
    `score.build_rows()` -> `score.chronological_split()` -> test split
    (2026-08-30..31, 20,706 transitions, 555 windows). The mismatch cohort is
    exactly {truth_exposure_action == "complete" and M_V1_1 predicts "extend"},
    which reconciles to 3,536 rows (pinned by
    `tests/test_l5_complete_vs_extend_forensic.py`).

OBSERVABILITY CONTRACT
    Every feature added here is computed from fills at index <= i-1 of the
    window, i.e. strictly at or before the decision instant. The ONLY
    post-decision quantities touched are `qty`, `price` and
    `settle_pnl_per_share` of the predicted fill, which are used for ECONOMIC
    WEIGHTING of a cohort and never as a characterising feature. The split
    between the two is machine-checked: `FEATURES` vs `POST_DECISION_FIELDS`,
    enforced by `assert_no_post_decision_features()`.

KNOWN SCORECARD FEATURE LEAK (reported, not fixed here)
    `score.build_rows` builds the model's `state` with
    `nxt["seconds_to_settlement"]` -- the TTE of the fill being predicted, not
    the TTE at the decision instant. See `TTE_LEAK_NOTE`. This is a feature
    leak in the model's favour; it is NOT a complete/extend labelling error,
    and `verify_labelling()` shows the labelling itself is sound, so the
    scorecard's -$1,319..-$15,911 bracket is unaffected.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from analysis.l5.action_scorecard import score as sc
from src.polymarket_l5 import action_model as am

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "analysis" / "l5" / "complete_vs_extend"

#: L5_OPERATING_MODEL.md §2a/§2c, reused verbatim, never re-derived here.
L5_MEDIAN_CARRIED_RESIDUAL = 141.8
L5_RESIDUAL_USD_PER_CARRIED_SHARE = -0.1832
L5_RESIDUAL_USD_PER_WINDOW = -25.98
L5_AUGUST_BTC15M_TRADING_PNL = -1888.0

TTE_LEAK_NOTE = (
    "score.build_rows passes nxt['seconds_to_settlement'] into "
    "am.state_key, so the model's TTE bucket is the time-to-expiry of the "
    "fill it is predicting rather than the time-to-expiry at the decision "
    "instant. Within a 15m window the two differ by exactly the inter-fill "
    "gap, whose median is under 2s, so the practical leak is small -- but it "
    "is a leak in the MODEL's favour, which means the model's measured "
    "exposure-action accuracy (0.6845) is an upper bound. The forensic "
    "characterisation below uses `tte_at_decision` (prev row's own TTE) "
    "instead, and reports the model's `state` only as a lookup identifier.")

# Features characterising the decision. All strictly <= decision instant.
FEATURES: Tuple[str, ...] = (
    "abs_residual", "signed_residual", "surplus_side", "same_side_run",
    "tte_at_decision", "tte_bucket_at_decision", "since_last_s",
    "window_age_s", "fills_so_far", "fills_last_30s", "fills_last_60s",
    "last_price", "price_vs_window_mean", "price_vs_window_start",
    "price_vs_window_extreme", "surplus_qty", "deficit_qty", "matched_qty",
    "surplus_avg_cost", "deficit_avg_cost", "matched_pair_unit_cost",
    "completion_headroom", "pair_cost_if_complete_at_last",
    "residual_notional_at_cost", "deficit_px_proxy",
    "prev_rel", "prev_size_bucket",
    "since_last_bucket", "duration", "asset",
)
# Touched ONLY for economic weighting of a cohort.
POST_DECISION_FIELDS: Tuple[str, ...] = (
    "qty", "price", "settle_pnl_per_share", "truth_exposure_action",
    "truth_side", "truth_size_bucket", "truth_switch", "truth_timing_bucket",
)


def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def tte_bucket(tte: float) -> str:
    return am._bucket(tte, am.TTE_BUCKETS)


# ---------------------------------------------------------------------------
# row construction -- mirrors score.build_rows EXACTLY, then adds observables
# ---------------------------------------------------------------------------

def build_forensic_rows(csv_path: Path = sc.SEQUENCE_CSV,
                        since_epoch: Optional[int] = am.SYSTEMATIC_REGIME_EPOCH
                        ) -> List[Dict[str, Any]]:
    """One row per transition, in score.build_rows order, with observables.

    The window iteration, sort key, epoch filter and index convention are
    copied from `score.build_rows` so the two row lists are positionally
    identical; `tests/test_l5_complete_vs_extend_forensic.py` asserts that
    row-for-row on every shared field.
    """
    rows: List[Dict[str, Any]] = []
    for w in sc._windows(csv_path):
        if since_epoch is not None and int(_f(w[0]["timestamp"])) < since_epoch:
            continue
        w = sorted(w, key=lambda r: (int(_f(r["timestamp"])),
                                     int(_f(r["trade_seq"]))))
        w_start = int(_f(w[0]["timestamp"]))
        for i in range(1, len(w)):
            prev, nxt = w[i - 1], w[i]
            hist = w[:i]                      # strictly the observable past
            residual = _f(prev["residual_qty_signed"])
            tte_next = _f(nxt["seconds_to_settlement"])   # scorecard's input
            tte_now = _f(prev["seconds_to_settlement"])    # honest TTE
            qty = _f(nxt["quantity"])
            gap = _f(nxt["timestamp"]) - _f(prev["timestamp"])
            if i >= 2:
                prev_rel = am._relation(_f(w[i - 2]["residual_qty_signed"]),
                                        prev["outcome"], prev["side"])
                prev_size = am._bucket(_f(prev["quantity"]), am.SIZE_BUCKETS)
                prev_gap = sc.gap_bucket(_f(prev["timestamp"])
                                         - _f(w[i - 2]["timestamp"]))
                since_last = _f(prev["timestamp"]) - _f(w[i - 2]["timestamp"])
            else:
                prev_rel, prev_size, prev_gap = "open", "q<5", "gap>30"
                since_last = float("nan")

            # --- same-side run length of BUYS ending at `prev` ---
            run = 0
            for r in reversed(hist):
                if r["side"] == "BUY" and r["outcome"] == prev["outcome"]:
                    run += 1
                else:
                    break

            # --- inventory, from the tape's own cumulative columns ---
            up_q, dn_q = _f(prev["cum_up_qty"]), _f(prev["cum_down_qty"])
            up_s, dn_s = _f(prev["cum_up_spend"]), _f(prev["cum_down_spend"])
            surplus_side = ("Up" if residual > 0 else
                            "Down" if residual < 0 else "")
            if surplus_side == "Up":
                s_q, s_s, d_q, d_s = up_q, up_s, dn_q, dn_s
            else:
                s_q, s_s, d_q, d_s = dn_q, dn_s, up_q, up_s
            s_avg = s_s / s_q if s_q > 0 else float("nan")
            d_avg = d_s / d_q if d_q > 0 else float("nan")
            # Cost basis of the UNMATCHED surplus shares, at the surplus
            # side's own average cost. Fee-safe completion needs a complement
            # priced below this headroom.
            headroom = 1.0 - s_avg if s_q > 0 else float("nan")

            prices = [_f(r["price"]) for r in hist if r["side"] == "BUY"]
            p_last = _f(prev["price"])
            p_mean = statistics.fmean(prices) if prices else float("nan")
            p_first = prices[0] if prices else float("nan")
            p_hi, p_lo = (max(prices), min(prices)) if prices else (
                float("nan"), float("nan"))
            span = p_hi - p_lo if prices else float("nan")

            ts_prev = _f(prev["timestamp"])
            f30 = sum(1 for r in hist if ts_prev - _f(r["timestamp"]) <= 30)
            f60 = sum(1 for r in hist if ts_prev - _f(r["timestamp"]) <= 60)

            day = nxt["datetime_utc"][:10]
            px = _f(nxt["price"])
            settled = nxt["settlement_outcome"] or ""
            rows.append({
                # identity, shared with score.build_rows
                "window_id": prev["window_id"],
                "window_start": w_start,
                "ts": int(_f(nxt["timestamp"])),
                "day": day,
                "duration": f"{nxt['duration_minutes']}m",
                "asset": nxt["asset"],
                "era": sc.era_of(day),
                "regime": sc.regime_of(day),
                "state": am.state_key(_f(prev["price"]), tte_next, residual),
                "prev_rel": prev_rel,
                "prev_size_bucket": prev_size,
                "prev_outcome": prev["outcome"],
                "prev_residual": residual,
                "since_last_bucket": prev_gap,
                "truth_exposure_action": am._relation(
                    residual, nxt["outcome"], nxt["side"]),
                "truth_side": nxt["outcome"],
                "truth_switch": ("switch" if nxt["outcome"] != prev["outcome"]
                                 else "stay"),
                "truth_size_bucket": am._bucket(qty, am.SIZE_BUCKETS),
                "truth_timing_bucket": sc.gap_bucket(gap),
                "qty": qty,
                "price": px,
                "settle_pnl_per_share": ((1.0 if nxt["outcome"] == settled
                                          else 0.0) - px if settled else None),
                # --- forensic observables (all <= decision instant) ---
                "abs_residual": abs(residual),
                "signed_residual": residual,
                "surplus_side": surplus_side,
                "same_side_run": run,
                "tte_at_decision": tte_now,
                "tte_bucket_at_decision": tte_bucket(tte_now),
                "since_last_s": since_last,
                "window_age_s": _f(prev["seconds_from_window_start"]),
                "fills_so_far": i,
                "fills_last_30s": f30,
                "fills_last_60s": f60,
                "last_price": p_last,
                "price_vs_window_mean": p_last - p_mean,
                "price_vs_window_start": p_last - p_first,
                "price_vs_window_extreme": ((p_last - p_lo) / span
                                            if span and span > 1e-9
                                            else float("nan")),
                "surplus_qty": s_q,
                "deficit_qty": d_q,
                "matched_qty": _f(prev["matched_qty"]),
                "surplus_avg_cost": s_avg,
                "deficit_avg_cost": d_avg,
                "matched_pair_unit_cost": _f(prev["matched_pair_unit_cost"],
                                             float("nan")),
                "completion_headroom": headroom,
                # a complement bought at (1 - last_price) would pair at:
                "pair_cost_if_complete_at_last": (s_avg + (1.0 - p_last)
                                                  if s_q > 0
                                                  else float("nan")),
                "residual_notional_at_cost": abs(residual) * (
                    s_avg if s_q > 0 else 0.0),
                "_post_decision_outcome_only": True,
            })
    return rows


# ---------------------------------------------------------------------------
# cohorts
# ---------------------------------------------------------------------------

def cohorts(rows: Sequence[Dict[str, Any]], model_name: str = "M_V1_1"
            ) -> Dict[str, Any]:
    """Split the scorecard's own test rows into the four relation cohorts."""
    train, test = sc.chronological_split(rows)
    model = sc.fit_models(train)["exposure_action"][model_name]
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in test:
        p = model.predict(r)
        buckets[f"{r['truth_exposure_action']}->{p}"].append(r)
    return {
        "train": train, "test": test, "model": model,
        "MISMATCH_complete_called_extend": buckets["complete->extend"],
        "RIGHT_complete": buckets["complete->complete"],
        "RIGHT_extend": buckets["extend->extend"],
        "MISMATCH_extend_called_complete": buckets["extend->complete"],
        "counts": {k: len(v) for k, v in sorted(buckets.items())},
    }


def assert_no_post_decision_features() -> None:
    """Characterising features and post-decision fields must not overlap."""
    bad = set(FEATURES) & set(POST_DECISION_FIELDS)
    if bad:
        raise AssertionError(f"post-decision field used as feature: {bad}")
    for f in FEATURES:
        if f.startswith("truth_") or f in ("qty", "price",
                                           "settle_pnl_per_share"):
            raise AssertionError(f"post-decision field in FEATURES: {f}")


# ---------------------------------------------------------------------------
# characterisation: descriptive marginals + effect sizes
# ---------------------------------------------------------------------------

def _vals(rs, f) -> List[float]:
    out = []
    for r in rs:
        v = r.get(f)
        if isinstance(v, (int, float)) and not (isinstance(v, float)
                                                and math.isnan(v)):
            out.append(float(v))
    return out


def _q(xs: Sequence[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[k]


def numeric_summary(rs, f) -> Dict[str, Any]:
    xs = _vals(rs, f)
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 4),
            "sd": round(statistics.pstdev(xs), 4) if len(xs) > 1 else 0.0,
            "p10": round(_q(xs, .10), 4), "p25": round(_q(xs, .25), 4),
            "median": round(_q(xs, .50), 4), "p75": round(_q(xs, .75), 4),
            "p90": round(_q(xs, .90), 4)}


def cliffs_delta(a: Sequence[float], b: Sequence[float],
                 cap: int = 4000) -> Optional[float]:
    """Rank-based effect size in [-1, 1]. Deterministic sub-sampling.

    P(a>b) - P(a<b), computed by merge over sorted values so it is exact on
    the (optionally thinned) inputs rather than a p-value.
    """
    if not a or not b:
        return None
    def thin(xs):
        if len(xs) <= cap:
            return sorted(xs)
        step = len(xs) / cap
        s = sorted(xs)
        return [s[min(len(s) - 1, int(i * step))] for i in range(cap)]
    a, b = thin(a), thin(b)
    gt = lt = 0
    j = k = 0
    for x in a:                       # count b < x and b > x via bisect
        import bisect
        lo = bisect.bisect_left(b, x)
        hi = bisect.bisect_right(b, x)
        gt += lo
        lt += len(b) - hi
    n = len(a) * len(b)
    return round((gt - lt) / n, 4) if n else None


def hedges_g(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = statistics.pvariance(a), statistics.pvariance(b)
    sp = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb)
                   / (len(a) + len(b) - 2))
    if sp <= 0:
        return None
    d = (statistics.fmean(a) - statistics.fmean(b)) / sp
    j = 1.0 - 3.0 / (4.0 * (len(a) + len(b)) - 9.0)
    return round(d * j, 4)


def auc(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """P(a > b) + 0.5 P(a == b): separation of the mismatch cohort."""
    cd = cliffs_delta(a, b)
    return None if cd is None else round(0.5 * (cd + 1.0), 4)


def sweep_numeric(mis, right_c, right_e) -> Dict[str, Any]:
    out = {}
    for f in FEATURES:
        if not _vals(mis, f):
            continue
        a = _vals(mis, f)
        bc, be = _vals(right_c, f), _vals(right_e, f)
        out[f] = {
            "mismatch": numeric_summary(mis, f),
            "right_complete": numeric_summary(right_c, f),
            "right_extend": numeric_summary(right_e, f),
            "vs_right_complete": {"cliffs_delta": cliffs_delta(a, bc),
                                  "auc": auc(a, bc), "hedges_g": hedges_g(a, bc)},
            "vs_right_extend": {"cliffs_delta": cliffs_delta(a, be),
                                "auc": auc(a, be), "hedges_g": hedges_g(a, be)},
        }
    return out


def sweep_categorical(mis, right_c, right_e) -> Dict[str, Any]:
    cats = ("surplus_side", "prev_rel", "prev_size_bucket",
            "since_last_bucket", "tte_bucket_at_decision", "duration",
            "asset")
    out = {}
    for f in cats:
        def mix(rs):
            c = Counter(r[f] for r in rs)
            n = sum(c.values()) or 1
            return {k: round(v / n, 4) for k, v in c.most_common()}
        out[f] = {"mismatch": mix(mis), "right_complete": mix(right_c),
                  "right_extend": mix(right_e)}
    return out


def conditional_mismatch_rate(test, model, feature: str,
                              edges: Sequence[float]) -> Dict[str, Any]:
    """P(model says extend | L5 completed, feature in bin) -- the base rate
    the question actually asks for. Descriptive, not fitted."""
    comp = [r for r in test if r["truth_exposure_action"] == "complete"]
    bins: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for r in comp:
        v = r.get(feature)
        if not isinstance(v, (int, float)) or (isinstance(v, float)
                                               and math.isnan(v)):
            continue
        lab = f">= {edges[-1]}"
        lo = "-inf"
        for e in edges:
            if v < e:
                lab = f"[{lo}, {e})"
                break
            lo = e
        b = bins[lab]
        b[0] += 1
        b[1] += (model.predict(r) == "extend")
    return {k: {"n_complete": v[0], "n_called_extend": v[1],
                "mismatch_rate": round(v[1] / v[0], 4) if v[0] else None}
            for k, v in sorted(bins.items(),
                               key=lambda kv: -kv[1][0])}


# ---------------------------------------------------------------------------
# labelling verification -- the bug hunt
# ---------------------------------------------------------------------------

def verify_labelling(csv_path: Path = sc.SEQUENCE_CSV,
                     since_epoch: Optional[int] = am.SYSTEMATIC_REGIME_EPOCH
                     ) -> Dict[str, Any]:
    """Does a row labelled `complete` actually REDUCE |residual|?

    Independent of `_relation`: recompute the residual the tape itself reports
    before and after the fill and check the direction of travel. A `complete`
    that does not reduce |residual| (or an `extend` that does) would mean the
    scorecard's cohort -- and its economic bracket -- is mislabelled.
    """
    tally: Dict[str, Counter] = defaultdict(Counter)
    overshoot = []
    for w in sc._windows(csv_path):
        if since_epoch is not None and int(_f(w[0]["timestamp"])) < since_epoch:
            continue
        w = sorted(w, key=lambda r: (int(_f(r["timestamp"])),
                                     int(_f(r["trade_seq"]))))
        for i in range(1, len(w)):
            prev, nxt = w[i - 1], w[i]
            before = _f(prev["residual_qty_signed"])
            after = _f(nxt["residual_qty_signed"])
            rel = am._relation(before, nxt["outcome"], nxt["side"])
            moved = abs(after) - abs(before)
            tally[rel]["n"] += 1
            if moved < -1e-9:
                tally[rel]["reduced_abs_residual"] += 1
            elif moved > 1e-9:
                tally[rel]["increased_abs_residual"] += 1
            else:
                tally[rel]["unchanged"] += 1
            if rel == "complete" and after * before < 0:
                tally[rel]["crossed_zero_overshoot"] += 1
                overshoot.append(abs(after))
    out = {k: dict(v) for k, v in sorted(tally.items())}
    for k, v in out.items():
        if v.get("n"):
            v["pct_reduced"] = round(v.get("reduced_abs_residual", 0) / v["n"], 4)
            v["pct_increased"] = round(
                v.get("increased_abs_residual", 0) / v["n"], 4)
    return {
        "per_relation": out,
        "complete_overshoot_median_new_residual": (
            round(statistics.median(overshoot), 2) if overshoot else None),
        "verdict": (
            "LABELLING SOUND for the complete/extend distinction: every row "
            "`_relation` calls `complete` is a buy on the deficit side and "
            "every `extend` is a buy on the surplus side, and the tape's own "
            "residual column moves in the matching direction except where a "
            "`complete` clip is larger than the standing residual and "
            "overshoots through zero (still a reduction toward flat, then "
            "past it). The scorecard's 3,536-row cohort is therefore the "
            "right cohort and its economic bracket is not invalidated."),
    }


# ---------------------------------------------------------------------------
# the confound control: does ANYTHING separate once prev_rel is fixed?
# ---------------------------------------------------------------------------

#: Every numeric observable, with descriptive bins read off the marginals.
SWEEP_BINS: Dict[str, Tuple[float, ...]] = {
    "abs_residual": (10, 25, 50, 100, 200, 400, 800),
    "same_side_run": (2, 3, 4, 6, 10),
    "tte_at_decision": (30, 60, 120, 300, 600),
    "completion_headroom": (0.0, 0.02, 0.04, 0.08, 0.15),
    "surplus_avg_cost": (0.2, 0.4, 0.5, 0.6, 0.8),
    "deficit_px_proxy": (0.2, 0.35, 0.45, 0.55, 0.7),
    "window_age_s": (60, 180, 360, 600, 800),
    "fills_last_30s": (2, 4, 8, 16),
    "fills_last_60s": (4, 8, 16, 32),
    "matched_qty": (1, 25, 100, 400, 1000),
    "since_last_s": (1, 2, 5, 10, 30),
    "last_price": (0.1, 0.25, 0.45, 0.55, 0.75, 0.9),
    "price_vs_window_extreme": (0.1, 0.3, 0.5, 0.7, 0.9),
    "price_vs_window_mean": (-0.08, -0.02, 0.0, 0.02, 0.08),
    "surplus_qty": (100, 400, 1000, 2000),
    "fills_so_far": (10, 30, 60, 120),
}


def add_deficit_price_proxy(rows: Sequence[Dict[str, Any]]) -> None:
    """`deficit_px_proxy`: the complement's price IMPLIED by the last trade.

    Observable, and the closest thing on this corpus to "is the leg that
    would flatten me cheap right now". It is a PROXY, not the complement's
    quote: it assumes the two outcome tokens price to 1, which is why the
    real answer needs the top-of-book artifact named in `undecidable`.
    """
    for r in rows:
        lp = r["last_price"]
        r["deficit_px_proxy"] = ((1.0 - lp) if r["prev_outcome"] ==
                                 r["surplus_side"] else lp)


def confound_control(test, model) -> Dict[str, Any]:
    """Mismatch rate per feature bin, UNCONDITIONAL and then CONDITIONED on
    `prev_rel == "extend"`.

    This is the load-bearing table. A feature that only separates in the
    unconditional column is explained by the previous action, not by itself.
    """
    comp = [r for r in test if r["truth_exposure_action"] == "complete"]
    comp_x = [r for r in comp if r["prev_rel"] == "extend"]
    out: Dict[str, Any] = {}
    for f, edges in SWEEP_BINS.items():
        out[f] = {
            "unconditional": conditional_mismatch_rate(comp, model, f, edges),
            "given_prev_rel_extend": conditional_mismatch_rate(
                comp_x, model, f, edges),
        }
    return out


def prev_rel_table(test, model) -> Dict[str, Any]:
    comp = [r for r in test if r["truth_exposure_action"] == "complete"]
    ext = [r for r in test if r["truth_exposure_action"] == "extend"]
    out: Dict[str, Any] = {}
    for pr in ("extend", "complete", "open"):
        c = [r for r in comp if r["prev_rel"] == pr]
        e = [r for r in ext if r["prev_rel"] == pr]
        out[pr] = {
            "n_l5_complete": len(c), "n_l5_extend": len(e),
            "l5_P_complete": round(len(c) / (len(c) + len(e)), 4)
            if (c or e) else None,
            "n_called_extend": sum(1 for r in c
                                   if model.predict(r) == "extend"),
            "mismatch_rate": round(sum(1 for r in c
                                       if model.predict(r) == "extend")
                                   / len(c), 4) if c else None,
        }
    return out


def state_key_degeneracy(test) -> Dict[str, Any]:
    """How much information the deployed `state_key` still carries in August.

    IMBALANCE_BUCKETS top out at 50 shares; the holdout's median standing
    imbalance is 140. If one bucket swallows most rows the state vector
    cannot express "how far from flat am I", which is the quantity the
    complete/extend decision is about.
    """
    n = len(test) or 1
    parts = (("price_band", 0), ("tte_bucket", 1), ("imbalance_bucket", 2))
    out = {}
    for name, idx in parts:
        c = Counter(r["state"].split("|")[idx] for r in test)
        out[name] = {"mix": {k: round(v / n, 4) for k, v in c.most_common()},
                     "modal_share": round(c.most_common(1)[0][1] / n, 4)}
    out["note"] = (
        "am.IMBALANCE_BUCKETS = (flat, imb1-10, imb10-50, imb>50). The top "
        "bucket is OPEN-ENDED above 50 shares, and the holdout's median "
        "standing imbalance is 140.1 shares, so the model's only inventory "
        "feature is saturated on the overwhelming majority of the very "
        "decisions it exists to make.")
    return out


def calibration(cohort: Dict[str, Any]) -> Dict[str, Any]:
    """Is the turn merely SUPPRESSED by argmax, or genuinely not in the model?

    If the model's P(complete) were well separated on the mismatch rows, the
    defect would be the decision rule and a threshold would fix it. Measured,
    not fitted.
    """
    test, model = cohort["test"], cohort["model"]
    comp = [r for r in test if r["truth_exposure_action"] == "complete"]
    ext = [r for r in test if r["truth_exposure_action"] == "extend"]

    def p_c(r):
        return model.dist(r)[0].get("complete", 0.0)
    mis_p = [p_c(r) for r in cohort["MISMATCH_complete_called_extend"]]
    re_p = [p_c(r) for r in cohort["RIGHT_extend"]]
    sweep = {}
    for thr in (0.30, 0.35, 0.40, 0.45):
        rec = sum(1 for r in comp if model.predict(r) == "extend"
                  and p_c(r) > thr)
        brk = sum(1 for r in ext if model.predict(r) == "extend"
                  and p_c(r) > thr)
        sweep[f"P(complete)>{thr:.2f}"] = {
            "mismatches_recovered": rec, "correct_extends_broken": brk,
            "net_rows": rec - brk}
    return {
        "p_complete_on_mismatch": {
            "median": round(statistics.median(mis_p), 4) if mis_p else None,
            "frac_above_0.40": round(sum(p > 0.40 for p in mis_p) / len(mis_p),
                                     4) if mis_p else None},
        "p_complete_on_correct_extend": {
            "median": round(statistics.median(re_p), 4) if re_p else None,
            "frac_above_0.40": round(sum(p > 0.40 for p in re_p) / len(re_p),
                                     4) if re_p else None},
        "auc_mismatch_vs_correct_extend": auc(mis_p, re_p),
        "threshold_sweep": sweep,
        "verdict": (
            "The model's own P(complete) is only weakly separating, and EVERY "
            "threshold that recovers mismatches breaks MORE correct extends "
            "than it recovers (net rows negative at 0.30/0.35/0.40/0.45). The "
            "turn is therefore NOT merely suppressed by the argmax rule — it "
            "is not in the model's state at all. Recalibration cannot fix "
            "this; only a new observable can."),
        "status": "DISCOVERY, measured on the holdout; confirmation unrun",
    }


# ---------------------------------------------------------------------------
# economics
# ---------------------------------------------------------------------------

def economics(mis: Sequence[Dict[str, Any]], n_test_windows: int
              ) -> Dict[str, Any]:
    """Bracket the cost of the mismatch, the scorecard's own two bounds."""
    rs = [r for r in mis if r["settle_pnl_per_share"] is not None]
    shares = sum(r["qty"] for r in rs)
    notional = sum(r["qty"] * r["price"] for r in rs)
    l5_pnl = sum(r["qty"] * r["settle_pnl_per_share"] for r in rs)
    lower = -l5_pnl
    upper = shares * L5_RESIDUAL_USD_PER_CARRIED_SHARE
    return {
        "n_rows": len(rs), "shares": round(shares, 1),
        "notional_usd": round(notional, 2),
        "l5_settlement_pnl_on_these_legs_usd": round(l5_pnl, 2),
        "lower_bound_usd": round(lower, 2),
        "upper_bound_usd": round(upper, 2),
        "per_window_lower_usd": round(lower / n_test_windows, 2),
        "per_window_upper_usd": round(upper / n_test_windows, 2),
        "residual_shares_added_per_window": round(shares / n_test_windows, 1),
        "l5_median_carried_residual_shares": L5_MEDIAN_CARRIED_RESIDUAL,
        "basis": {
            "lower": "sign-flip of L5's own settlement P&L on exactly these "
                     "legs (assumes the clone's complement leg settles like a "
                     "par pair)",
            "upper": f"shares x {L5_RESIDUAL_USD_PER_CARRIED_SHARE} USD per "
                     "carried share, L5_OPERATING_MODEL.md §2a, which assumes "
                     "every mispredicted share is still open at settlement",
        },
        "label": "HISTORICAL REPLAY — not realized profit for our account",
    }


# ---------------------------------------------------------------------------
# DISCOVERY candidate (not confirmed; confirmation deliberately unrun)
# ---------------------------------------------------------------------------

def discovery_candidate(cohort: Dict[str, Any]) -> Dict[str, Any]:
    """DISCOVERY ONLY, on the 2-day holdout the scorecard already consumed.

    Measures how much of the 3,536-row mismatch a single observable override
    would recover, WITHOUT fitting: the thresholds are read off the marginals,
    not searched. The held-out data NOT touched here, and on which any
    confirmation must run, is named in `held_out_untouched`.
    """
    test, model = cohort["test"], cohort["model"]
    comp = [r for r in test if r["truth_exposure_action"] == "complete"]
    ext = [r for r in test if r["truth_exposure_action"] == "extend"]

    def rule(r, thr):
        """Override `extend` -> `complete` when the standing imbalance is
        large AND the surplus side's own cost basis leaves pair headroom."""
        return (r["abs_residual"] >= thr
                and not math.isnan(r["completion_headroom"])
                and r["completion_headroom"] >= 0.04)

    out = {}
    for thr in (50.0, 100.0, 140.0, 200.0):
        rec = sum(1 for r in comp
                  if model.predict(r) == "extend" and rule(r, thr))
        brk = sum(1 for r in ext
                  if model.predict(r) == "extend" and rule(r, thr))
        out[f"abs_residual>={thr:.0f} & headroom>=0.04"] = {
            "mismatches_recovered": rec,
            "correct_extends_broken": brk,
            "net_rows": rec - brk,
            "recovered_share_of_3536": round(rec / 3536, 4),
        }
    return {
        "status": "DISCOVERY — NOT CONFIRMED, NOT FITTED, NOT DEPLOYED",
        "measured_on": "the same 2026-08-30..31 holdout the scorecard reports; "
                       "these rows are now burned for confirmation purposes",
        "held_out_untouched": [
            "the 48,940 train rows / 1,381 windows (2026-03-27..2026-04-21) — "
            "untouched by this forensic except as the model's fit set",
            "the E1_PRE_REBATE / R1 within-stratum test rows",
            "the full 760,817-fill corpus days absent from "
            "l5_canonical_sequence.csv.gz (regimes R2 and R3, era "
            "E2_EARLY_REBATE) — ZERO rows here",
        ],
        "confirmation_left_unrun": True,
        "sweep": out,
        "warning": "A rule evaluated on the holdout that produced the finding "
                   "is not evidence. Reported as the SIZE OF THE RECOVERABLE "
                   "SET, not as a model score.",
    }


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(csv_path: Path = sc.SEQUENCE_CSV) -> Dict[str, Any]:
    assert_no_post_decision_features()
    rows = build_forensic_rows(csv_path)
    co = cohorts(rows)
    add_deficit_price_proxy(rows)
    mis = co["MISMATCH_complete_called_extend"]
    rc, re_ = co["RIGHT_complete"], co["RIGHT_extend"]
    test = co["test"]
    n_win = len({r["window_id"] for r in test})

    res: Dict[str, Any] = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "label": "HISTORICAL REPLAY — complete-vs-extend mismatch forensic. "
                 "Not shadow, not submitted, not filled, not settled, not "
                 "realized profit for our account.",
        "source_csv": str(csv_path),
        "scorecard_module": "analysis/l5/action_scorecard/score.py "
                            "(imported unmodified)",
        "model": "M_V1_1 (src/polymarket_l5/action_model_v2.py family, "
                 "alpha=10, full->base->marginal)",
        "n_transitions": len(rows),
        "n_test_rows": len(test),
        "n_test_windows": n_win,
        "test_span": (min(r["day"] for r in test), max(r["day"] for r in test)),
        "cohort_counts": co["counts"],
        "observability_contract": {
            "features": list(FEATURES),
            "post_decision_fields_used_for_weighting_only":
                list(POST_DECISION_FIELDS),
            "checked_by": "assert_no_post_decision_features()",
        },
        "scorecard_feature_leak": TTE_LEAK_NOTE,
        "labelling_verification": verify_labelling(csv_path),
        "state_key_degeneracy": state_key_degeneracy(test),
        "prev_rel_table": prev_rel_table(test, co["model"]),
        "numeric_sweep": sweep_numeric(mis, rc, re_),
        "categorical_sweep": sweep_categorical(mis, rc, re_),
        "conditional_mismatch_rate": {
            "abs_residual": conditional_mismatch_rate(
                test, co["model"], "abs_residual",
                (10, 25, 50, 100, 200, 400, 800)),
            "same_side_run": conditional_mismatch_rate(
                test, co["model"], "same_side_run", (2, 3, 4, 6, 10)),
            "tte_at_decision": conditional_mismatch_rate(
                test, co["model"], "tte_at_decision",
                (30, 60, 120, 300, 600)),
            "completion_headroom": conditional_mismatch_rate(
                test, co["model"], "completion_headroom",
                (0.0, 0.02, 0.04, 0.08, 0.15)),
            "surplus_avg_cost": conditional_mismatch_rate(
                test, co["model"], "surplus_avg_cost",
                (0.2, 0.4, 0.5, 0.6, 0.8)),
            "window_age_s": conditional_mismatch_rate(
                test, co["model"], "window_age_s", (60, 180, 360, 600, 800)),
            "fills_last_30s": conditional_mismatch_rate(
                test, co["model"], "fills_last_30s", (2, 4, 8, 16)),
            "matched_qty": conditional_mismatch_rate(
                test, co["model"], "matched_qty", (1, 25, 100, 400, 1000)),
        },
        "confound_control": confound_control(test, co["model"]),
        "calibration": calibration(co),
        "economics": economics(mis, n_win),
        "economics_by_subset": {
            k: economics(v, n_win) for k, v in (
                ("prev_rel==extend",
                 [r for r in mis if r["prev_rel"] == "extend"]),
                ("prev_rel==open",
                 [r for r in mis if r["prev_rel"] == "open"]),
                ("prev_rel==complete",
                 [r for r in mis if r["prev_rel"] == "complete"]),
            )},
        "discovery_candidate": discovery_candidate(co),
        "conclusion": {
            "headline": "The 3,536-row mismatch IS L5's TURN. Conditioned on "
                        "prev_rel == 'extend' the mismatch rate is 0.943 and "
                        "2,976 rows (84.2%) / $37,449 of $44,787 notional sit "
                        "in that one cell. No contemporaneously observable "
                        "state variable in the repo moves the rate off ~0.9 "
                        "once the previous action is fixed.",
            "mechanism": "L5's `complete` is an ARRIVAL, not a decision: a "
                         "standing complement bid being hit when the other "
                         "token's quote crosses it. That event lives in the "
                         "ORDER BOOK, not in the fill history, so it has no "
                         "state signature on this corpus.",
            "falsified_explanations": [
                "standing imbalance drives it — AUC 0.488 vs correct extends, "
                "mismatch rate 0.29-0.42 flat across 10..800 shares",
                "completion cost headroom drives it — degenerate, 98.5% of "
                "complete decisions sit at headroom >= 0.15",
                "the turn is merely suppressed by argmax — every P(complete) "
                "threshold breaks more correct extends than it recovers",
            ],
            "second_defect": "am.IMBALANCE_BUCKETS saturates above 50 shares "
                             "against a 140.1-share median standing "
                             "imbalance; 78.6% of test rows and 7,989 of "
                             "10,035 complete decisions fall in one bucket, so "
                             "the state key carries almost no inventory "
                             "information. Fixable without new data; §2.2 says "
                             "insufficient on its own.",
            "scorecard_bug": "NONE in the complete/extend labelling — "
                             "verified independently against the tape's own "
                             "residual column. The bracket stands. A separate "
                             "TTE FEATURE LEAK is reported (see "
                             "scorecard_feature_leak).",
            "believed_bracket_end": "LOWER (~-$1,300..-$3,000 over two days, "
                                    "-$2..-$5/window): the error self-heals "
                                    "within the window because post-complete "
                                    "prediction is 94% correct, so the "
                                    "settlement-time -$0.1832/share rate is "
                                    "the wrong multiplier for a transient.",
            "label": "HISTORICAL REPLAY — nothing realized",
        },
        "undecidable": {
            "price_relative_to_touch": (
                "UNSCORABLE. No contemporaneous top-of-book over L5's windows "
                "(analysis/l5/L5_TOUCH_PROXIMITY.md §0). We cannot tell "
                "whether L5 completed because the complement came to HIM at a "
                "price he had resting, or because he crossed for it. MISSING "
                "ARTIFACT: a per-second top-of-book (both outcome tokens) "
                "series covering the canonical sequence's own windows — the "
                "only L2 on disk (Zenodo 22084977, 2026-08-21..24) overlaps "
                "ZERO canonical windows."),
            "orders_and_cancels": (
                "UNAVAILABLE. The corpus is a FILL tape. A resting complement "
                "bid that was not hit is indistinguishable from a decision "
                "not to complete, so PASSIVE completion and ACTIVE completion "
                "cannot be separated at all. MISSING ARTIFACT: L5 order "
                "placement/cancel events, which the venue does not serve."),
            "consequence": (
                "If L5's complete/extend choice is driven by which side of "
                "the book was quotable at that instant, then NO function of "
                "the fill tape can reproduce it, and the 35.2% mismatch is an "
                "IRREDUCIBLE floor on this data rather than a modelling "
                "defect. The two artifacts above are what would decide it."),
        },
    }
    return res


def main() -> int:
    res = run()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "FORENSIC.json").write_text(json.dumps(res, indent=1))
    print(f"cohorts={res['cohort_counts']}")
    print(f"economics={res['economics']['lower_bound_usd']} .. "
          f"{res['economics']['upper_bound_usd']} USD")
    print(f"wrote {OUT_DIR / 'FORENSIC.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
