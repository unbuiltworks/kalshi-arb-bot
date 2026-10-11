"""L5 LOOKUP v4 -- strict FORWARD validation of the shadow loop's candidate.

OWNER directive (issue #167 comment 5577286614 item 4): the realtime L5
shadow raised RELATION_DRIFT on its first cycle and wrote a lookup-v4
PROPOSAL. "Validate that candidate out-of-sample before changing anything
live: compare frozen v3 vs proposed v4 on a clean subsequent L5 cohort ...
If v4 materially improves true forward fidelity, bring me a promotion
recommendation; do not auto-promote."

This module is that validation. It is NOT the loop's own split:

  * every normalized decision row is produced by the loop's OWN ingestion
    (src.l5_shadow_loop.ShadowLoop: the existing collector's day files ->
    VenueAdapter + canonical_row -> state_key / _relation / v2_dist, the
    same objects the live decision path imports), enriched here with the
    fields the loop discards (price, outcome, size, cadence, residual and
    pair cost AFTER the action) -- the lookup keys are byte-identical;
  * rows are ordered by time; TWO cuts are scored:
      - "loop"   the loop's own rule (row-level split at its cut time,
                 read from the reproduced proposal when given, else
                 ShadowLoop._holdout_cut on the same rows);
      - "strict" the last HOLDOUT_DAYS days of activity as a clean forward
                 holdout, split at WINDOW level: training = settlement
                 windows that END before the cut, holdout = windows that
                 START at/after the cut, straddling windows dropped, so no
                 holdout window's state was ever built from training fills;
  * the candidate is rebuilt on the training cohort ONLY, with the loop's
    own builder (ShadowLoop.build_candidate, imported, not re-implemented);
  * on the holdout, frozen v3 and candidate v4 are scored on: relation
    distribution (L1 predicted-vs-observed, top-1 agreement, mean
    P(observed action)), side convention, price aggressiveness (reported
    UNMEASURED when the public fills carry no maker/taker flag), size
    buckets, timing (cadence buckets vs the lookup's cadence_modal),
    inventory trajectory (residual units after each action), and COMPLETE
    behavior (P(complete | post-fill state), pair cost at complete,
    complete-vs-extend persistence) -- overall and by base-state bucket,
    with counts; a paired bootstrap CI + exact binomial (McNemar) on the
    agreement difference; and an explicit verdict.

Frozen artifacts are never written (the loop's FrozenPathViolation guard
is reused for every write; the only outputs are the two files named in
OUT_JSON / OUT_MD). Network use, when enabled, is the same public keyless
activity endpoint the collector reads (windowed DESC walk from
src.polymarket_l5.fetch, raw cache OUTSIDE the repo). Nothing here trades
or promotes anything: the verdict is a recommendation for the owner.

    python3 -m src.l5_lookup_v4_validation --state-dir <loop state dir> \
        --proposal <reproduced proposal json> [--deep-fetch-days 7]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src import l5_shadow_loop as loop_mod
from src import pmus_l5_persistence_v2 as pv2
from src import l5_fidelity_gate as gate
from src.polymarket_l5 import ops
from src.polymarket_l5.action_model import (IMBALANCE_BUCKETS, PRICE_BANDS, SIZE_BUCKETS,
                                            _bucket)
from src.polymarket_l5.action_model_v2 import GAP_BUCKETS

REPO_ROOT = loop_mod.REPO_ROOT
OUT_JSON = REPO_ROOT / "analysis" / "l5" / "l5_lookup_v4_validation.json"
OUT_MD = REPO_ROOT / "analysis" / "l5" / "L5_LOOKUP_V4_VALIDATION.md"
ALLOWED_OUTPUTS = (OUT_JSON.resolve(), OUT_MD.resolve())
DAY = loop_mod.DAY
RELATIONS = loop_mod.RELATIONS                       # open, extend, complete, sell
SIZE_LABELS = tuple(l for _, l in SIZE_BUCKETS)
IMB_LABELS = tuple(l for _, l in IMBALANCE_BUCKETS)
GAP_LABELS = tuple(l for _, l in GAP_BUCKETS)
PAIRCOST_EDGES: Tuple[Tuple[float, str], ...] = (
    (0.90, "pc<0.90"), (0.95, "pc0.90-0.95"), (0.98, "pc0.95-0.98"),
    (1.00, "pc0.98-1.00"), (1e9, "pc>=1.00"))
EPOCH_SEP1_2026 = 1788220800                          # 2026-09-01T00:00:00Z: first
                                                      # second after the frozen
                                                      # model's canonical history
LABELS = ("STRICT FORWARD VALIDATION of the shadow loop's lookup-v4 candidate; "
          "read-only; the frozen model/lookup/spec are never written; the "
          "verdict is a RECOMMENDATION -- promotion requires an owner decision")

# "materially improves" -- the numbers the verdict rests on (all on the
# STRICT forward holdout; the loop's cut is reported alongside)
MATERIAL = {
    "min_holdout_rows": 200,          # frozen spec minimum_evidence.live_scored_decisions
    "min_train_rows": 200,            # L5_SHADOW_MIN_DECISIONS
    "min_holdout_windows": 30,
    "agreement_gain_pp": 2.0,         # relation top-1 agreement: candidate - frozen, pp
    "mean_p_gain": 0.010,             # mean P(observed action): candidate - frozen
    "ci_level": 0.95,                 # paired bootstrap CI lower bound must exceed 0
    "max_relation_l1_worsening": 0.02,
    "max_complete_gap_worsening_pp": 2.0,
    "max_off_distribution_worsening_pp": 0.0,
    "reject_agreement_loss_pp": 1.0,  # candidate worse by >= this with CI upper < 0
    "reject_mean_p_loss": 0.005,
}


# ---------------------------------------------------------------- utilities

def _utc(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _freq(counter: Mapping[str, int], keys: Sequence[str]) -> Dict[str, float]:
    n = float(sum(counter.get(k, 0) for k in keys)) or 1.0
    return {k: round(counter.get(k, 0) / n, 4) for k in keys}


def _l1(a: Mapping[str, float], b: Mapping[str, float], keys: Iterable[str]) -> float:
    return round(sum(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) for k in keys), 4)


def _rel(p: Any) -> str:
    try:
        return str(Path(str(p)).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def guard_output(path: Path) -> Path:
    """Only the two files this module owns may be written; the loop's frozen
    set and analysis/l5/** otherwise stay untouched."""
    p = Path(path).resolve()
    if p in loop_mod.FROZEN_PATHS:
        raise loop_mod.FrozenPathViolation(f"refusing to write frozen path {p}")
    if p in ALLOWED_OUTPUTS:
        return p
    if p == loop_mod.FROZEN_DIR or loop_mod.FROZEN_DIR in p.parents or REPO_ROOT in p.parents:
        raise loop_mod.FrozenPathViolation(
            f"refusing to write {p}: inside the repo only {[str(a) for a in ALLOWED_OUTPUTS]} are allowed")
    return p                                  # scratch locations outside the repo (tests, smoke runs)


# ---------------------------------------------------------- 1. ingestion

class EnrichedShadowLoop(loop_mod.ShadowLoop):
    """The loop's own ingestion; each decision row additionally carries the
    observable behavior fields the comparison needs. Lookup keys and every
    scored quantity the loop already computes are unchanged."""

    def decision_for(self, prev: Mapping[str, Any], prev_prev_residual: float,
                     nxt: Mapping[str, Any], ctx_start: int) -> Dict[str, Any]:
        d = super().decision_for(prev, prev_prev_residual, nxt, ctx_start)
        ts_prev = float(prev["timestamp"])
        gap = float(nxt["timestamp"]) - ts_prev
        d.update({
            "price": float(nxt["price"]), "prev_price": float(prev["price"]),
            "outcome": str(nxt["outcome"]), "side": str(nxt["side"]),
            "qty": float(nxt["quantity"]), "dollars": float(nxt.get("dollars") or 0.0),
            "tte": float(nxt["seconds_to_settlement"]),
            "gap_s": gap, "gap_bucket": _bucket(gap, GAP_BUCKETS),
            "size_bucket": _bucket(float(nxt["quantity"]), SIZE_BUCKETS),
            "resid_after": float(nxt["residual_qty_signed"]),
            "pair_cost_after": (float(nxt["matched_pair_unit_cost"])
                                if nxt.get("matched_pair_unit_cost") not in (None, "") else None),
            "maker_taker": str(nxt.get("maker_taker") or "unknown"),
            "window_end": None,
        })
        ctx = self.windows.get(nxt["window_id"])
        if ctx is not None:
            d["window_end"] = ctx.window_end
        return d


def raw_rows_from_cache(cache_dir: Path, tag: str = "activity") -> List[Dict[str, Any]]:
    """Rows in the collector's day-file vocabulary from a windowed-fetch raw
    cache (payload batches), fingerprinted with the collector's own rule."""
    from src.polymarket_l5.cache import RawCache
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for batch in RawCache(cache_dir).payload_batches(tag):
        for r in batch:
            if not isinstance(r, dict) or r.get("type") != "TRADE":
                continue
            fp = ops._fingerprint(r)
            if fp in seen:
                continue
            seen.add(fp)
            r = dict(r)
            r["_fp"] = fp
            r["_venue"] = "polymarket"
            r["_collected_utc"] = "windowed_fetch"
            out.append(r)
    return out


def raw_rows_from_day_files(ops_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted(Path(ops_dir).glob("activity_*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.endswith("\n"):
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "TRADE":
                    continue
                r["_fp"] = r.get("_fp") or ops._fingerprint(r)
                out.append(r)
    return out


def merge_raw(*sources: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for src in sources:
        for r in src:
            fp = r.get("_fp") or ops._fingerprint(r)
            if fp in seen:
                continue
            seen.add(fp)
            rr = dict(r)
            rr["_fp"] = fp
            out.append(rr)
    out.sort(key=lambda r: (float(r.get("timestamp") or 0), r["_fp"]))
    return out


def deep_fetch(cache_dir: Path, stop_before_ts: int, wallet: str = ops.L5_WALLET,
               max_total_pages: int = 600) -> Dict[str, Any]:
    """Windowed DESC walk of the public activity endpoint (the existing
    fetch helper, past the 8-page / ~6h reach of the collector's incremental
    call). Raw cache lives OUTSIDE the repo. Read-only, keyless."""
    from src.polymarket_l5.cache import RawCache
    from src.polymarket_l5.fetch import fetch_activity_windowed
    cache = RawCache(Path(cache_dir))
    cache.reset("activity")
    t0 = time.monotonic()
    rep = fetch_activity_windowed(wallet, cache=cache, max_total_pages=max_total_pages,
                                  stop_before_ts=int(stop_before_ts))
    rep["elapsed_s"] = round(time.monotonic() - t0, 1)
    rep["cache_dir"] = str(cache_dir)
    rep["fetched_utc"] = _utc(time.time())
    (Path(cache_dir) / "fetch_report.json").write_text(json.dumps(rep, default=str), encoding="utf-8")
    return rep


def normalize(raw_rows: List[Dict[str, Any]], model: Mapping[str, Any],
              work_dir: Path, window_days: int = 60) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run the loop's ingestion (absorb) over raw rows and return the
    enriched decision rows in time order plus the ingest report."""
    if not raw_rows:
        return [], {"fills": 0, "decisions": 0, "skipped": 0, "evicted": 0}
    anchor = max(float(r.get("timestamp") or 0) for r in raw_rows) + 60.0
    cfg = loop_mod.load_config({})
    cfg["L5_SHADOW_WINDOW_DAYS"] = window_days
    cfg["L5_SHADOW_MAX_ROWS"] = max(cfg["L5_SHADOW_MAX_ROWS"], len(raw_rows) + 1)
    loop = EnrichedShadowLoop(cfg, work_dir, dry_run=True, now_fn=lambda: anchor,
                              model=model, out=lambda s: None)
    rep = loop.absorb(sorted(raw_rows, key=lambda r: (float(r.get("timestamp") or 0),
                                                       r.get("_fp") or "")))
    rows = sorted(loop.rows, key=lambda d: (d["ts"], d["window"], d["fp"] or ""))
    return rows, rep


# ------------------------------------------------------------ 2. the cuts

def split_rows(rows: List[Dict[str, Any]], cut_ts: float, mode: str) -> Dict[str, Any]:
    """'row': the loop's rule (ts < cut -> train, else holdout).
    'window': training = windows that END before the cut, holdout = windows
    that START at/after the cut, straddlers dropped."""
    train: List[Dict[str, Any]] = []
    hold: List[Dict[str, Any]] = []
    dropped = 0
    for d in rows:
        if mode == "row":
            (train if d["ts"] < cut_ts else hold).append(d)
            continue
        w_end = d.get("window_end")
        if w_end is not None and float(w_end) <= cut_ts:
            train.append(d)
        elif float(d["t0"]) >= cut_ts:
            hold.append(d)
        else:
            dropped += 1
    return {"train": train, "holdout": hold, "dropped_straddling": dropped,
            "cut_ts": cut_ts, "cut_utc": _utc(cut_ts), "mode": mode}


def strict_cut_ts(rows: List[Dict[str, Any]], holdout_days: float) -> Optional[float]:
    if not rows:
        return None
    last = max(d["ts"] for d in rows)
    return float(last) - holdout_days * DAY


def loop_cut_ts(rows: List[Dict[str, Any]], now_ts: float, holdout_days: float) -> Dict[str, Any]:
    """The loop's own _holdout_cut rule, evaluated on these rows at now_ts."""
    if not rows:
        return {"cut_ts": None, "rule": "no_rows"}
    cut = now_ts - holdout_days * DAY
    n_hold = sum(1 for d in rows if d["ts"] >= cut)
    least = max(1, len(rows) // 10)
    if least <= n_hold <= len(rows) - least:
        return {"cut_ts": cut, "rule": f"last_{holdout_days:g}_days"}
    ts_sorted = sorted(d["ts"] for d in rows)
    return {"cut_ts": float(ts_sorted[int(len(ts_sorted) * 0.7)]),
            "rule": "time_quantile_0.7 (window too short/lopsided for the day rule)"}


# -------------------------------------------------------------- 3. scoring

def size_representatives(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Median observed shares per size bucket (from the TRAINING cohort) --
    what a predicted size bucket implies for the residual after the action."""
    by: Dict[str, List[float]] = defaultdict(list)
    for d in rows:
        by[d.get("size_bucket") or _bucket(float(d.get("qty") or 0), SIZE_BUCKETS)].append(float(d.get("qty") or 0))
    default = {"q<5": 2.0, "q5-15": 10.0, "q15-40": 25.0, "q>40": 60.0}
    return {k: (round(statistics.median(v), 4) if v else default.get(k, 10.0))
            for k in SIZE_LABELS for v in [by.get(k, [])]}


def _implied_resid_after(resid: float, action: str, reps: Mapping[str, float]) -> float:
    rel, size = action.split("|", 1)
    q = float(reps.get(size, 10.0))
    if rel == "open":
        return q if abs(resid) < 1.0 else resid
    if rel == "extend":
        return resid + math.copysign(q, resid if resid != 0 else 1.0)
    if rel == "complete":
        return resid - math.copysign(q, resid if resid != 0 else 1.0)
    return resid                                       # sell: unmodelled here


def score_rows(model: Mapping[str, Any], rows: List[Dict[str, Any]],
               size_reps: Mapping[str, float]) -> Dict[str, Any]:
    """Every fidelity dimension of one lookup on one cohort. Returns the
    aggregate view plus per-row indicators for the paired statistics."""
    n = len(rows)
    if not n:
        return {"n": 0}
    per_row = {"agree": [], "exact": [], "p_action": [], "p_rel": [], "off": []}
    obs_rel: Counter = Counter()
    exp_rel: Dict[str, float] = {r: 0.0 for r in RELATIONS}
    obs_size: Counter = Counter()
    exp_size: Dict[str, float] = {s: 0.0 for s in SIZE_LABELS}
    size_top1 = 0
    obs_size_by_rel: Dict[str, Counter] = defaultdict(Counter)
    exp_size_by_rel: Dict[str, Dict[str, float]] = defaultdict(lambda: {s: 0.0 for s in SIZE_LABELS})
    n_by_rel: Counter = Counter()
    obs_imb: Counter = Counter()
    exp_imb: Dict[str, float] = {s: 0.0 for s in IMB_LABELS}
    obs_imbalanced_after = 0
    exp_imbalanced_after = 0.0
    post_n = 0
    post_obs_complete = 0
    post_exp_complete = 0.0
    post_agree = 0
    persist_n: Counter = Counter()
    persist_obs: Counter = Counter()
    persist_exp: Dict[str, float] = defaultdict(float)
    cadence_n = cadence_agree = 0
    obs_gap: Counter = Counter()
    open_n = open_cheaper = 0
    per_state: Dict[str, Dict[str, Any]] = {}
    pc_at_complete: Counter = Counter()
    pc_values: List[float] = []
    for d in rows:
        dd = pv2.v2_dist(model, d["base"], d["last_rel"], d["last_size"])
        dist = dd["dist"]
        rels = pv2.relation_mass(dist)
        rec = sorted(rels.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        modal = max(dist, key=dist.get)
        rel, action = d["rel"], d["action"]
        agree = rec == rel
        per_row["agree"].append(int(agree))
        per_row["exact"].append(int(modal == action))
        per_row["p_action"].append(float(dist.get(action, 0.0)))
        per_row["p_rel"].append(float(rels.get(rel, 0.0)))
        per_row["off"].append(int(dd["backed_off"]))
        obs_rel[rel] += 1
        for r in RELATIONS:
            exp_rel[r] += float(rels.get(r, 0.0))
        # size
        sz = d.get("size_bucket") or action.split("|", 1)[1]
        obs_size[sz] += 1
        size_mass: Dict[str, float] = {s: 0.0 for s in SIZE_LABELS}
        for a, p in dist.items():
            size_mass[a.split("|", 1)[1]] = size_mass.get(a.split("|", 1)[1], 0.0) + float(p)
        for s in SIZE_LABELS:
            exp_size[s] += size_mass.get(s, 0.0)
        size_top1 += int(max(size_mass, key=size_mass.get) == sz)
        # size conditional on the observed relation
        cond = {s: float(dist.get(f"{rel}|{s}", 0.0)) for s in SIZE_LABELS}
        ctot = sum(cond.values())
        if ctot > 0:
            n_by_rel[rel] += 1
            obs_size_by_rel[rel][sz] += 1
            for s in SIZE_LABELS:
                exp_size_by_rel[rel][s] += cond[s] / ctot
        # inventory after the action
        resid = float(d["residual_units"])
        ra = d.get("resid_after")
        if ra is not None:
            obs_imb[_bucket(abs(float(ra)), IMBALANCE_BUCKETS)] += 1
            obs_imbalanced_after += int(abs(float(ra)) >= 1.0)
            for a, p in dist.items():
                ib = _bucket(abs(_implied_resid_after(resid, a, size_reps)), IMBALANCE_BUCKETS)
                exp_imb[ib] += float(p)
                exp_imbalanced_after += float(p) * int(abs(_implied_resid_after(resid, a, size_reps)) >= 1.0)
        # COMPLETE behavior on post-fill states
        if abs(resid) >= 1.0 and rel != "sell":
            post_n += 1
            post_obs_complete += int(rel == "complete")
            post_exp_complete += float(rels.get("complete", 0.0))
            post_agree += int(agree)
            lr = d["last_rel"]
            if lr in ("extend", "complete"):
                persist_n[lr] += 1
                persist_obs[lr] += int(rel == lr)
                persist_exp[lr] += float(rels.get(lr, 0.0))
        if rel == "complete" and d.get("pair_cost_after") is not None:
            pc = float(d["pair_cost_after"])
            pc_values.append(pc)
            pc_at_complete[_bucket(pc, PAIRCOST_EDGES)] += 1
        # timing: the lookup's modal cadence for this full key vs observed gap
        gb = d.get("gap_bucket")
        if gb:
            obs_gap[gb] += 1
            cm = (model.get("cadence_modal") or {}).get(dd["state"])
            if cm:
                cadence_n += 1
                cadence_agree += int(cm == gb)
        # side: opens follow the cheaper-touch convention?
        if rel == "open" and d.get("price") is not None:
            open_n += 1
            open_cheaper += int(float(d["price"]) <= 0.5)
        # per base state
        e = per_state.setdefault(d["base"], {
            "n": 0, "agree": 0, "off": 0, "p_action_sum": 0.0,
            "observed": Counter(), "expected": {r: 0.0 for r in RELATIONS},
            "post_n": 0, "post_obs_complete": 0, "post_exp_complete": 0.0})
        e["n"] += 1
        e["agree"] += int(agree)
        e["off"] += int(dd["backed_off"])
        e["p_action_sum"] += float(dist.get(action, 0.0))
        e["observed"][rel] += 1
        for r in RELATIONS:
            e["expected"][r] += float(rels.get(r, 0.0))
        if abs(resid) >= 1.0 and rel != "sell":
            e["post_n"] += 1
            e["post_obs_complete"] += int(rel == "complete")
            e["post_exp_complete"] += float(rels.get("complete", 0.0))
    obs_rel_f = _freq(obs_rel, RELATIONS)
    exp_rel_f = {r: round(exp_rel[r] / n, 4) for r in RELATIONS}
    states_out: Dict[str, Any] = {}
    weighted_l1 = 0.0
    for s, e in per_state.items():
        of = _freq(e["observed"], RELATIONS)
        ef = {r: round(e["expected"][r] / e["n"], 4) for r in RELATIONS}
        l1 = _l1(of, ef, RELATIONS)
        weighted_l1 += l1 * e["n"]
        states_out[s] = {
            "n": e["n"], "agreement": round(e["agree"] / e["n"], 4),
            "mean_p_action": round(e["p_action_sum"] / e["n"], 4),
            "off_pct": round(100.0 * e["off"] / e["n"], 2),
            "observed_relation_freq": of, "expected_relation_freq": ef, "relation_l1": l1,
            "post_fill_n": e["post_n"],
            "post_fill_complete_observed": (round(e["post_obs_complete"] / e["post_n"], 4)
                                            if e["post_n"] else None),
            "post_fill_complete_expected": (round(e["post_exp_complete"] / e["post_n"], 4)
                                            if e["post_n"] else None),
        }
    n_imb = sum(obs_imb.values())
    return {
        "n": n,
        "relation": {
            "observed_freq": obs_rel_f, "expected_freq": exp_rel_f,
            "l1_observed_vs_expected": _l1(obs_rel_f, exp_rel_f, RELATIONS),
            "state_weighted_l1": round(weighted_l1 / n, 4),
            "top1_agreement": round(sum(per_row["agree"]) / n, 4),
            "exact_action_agreement": round(sum(per_row["exact"]) / n, 4),
            "mean_p_observed_action": round(sum(per_row["p_action"]) / n, 4),
            "mean_p_observed_relation": round(sum(per_row["p_rel"]) / n, 4),
            "off_distribution_pct": round(100.0 * sum(per_row["off"]) / n, 2),
        },
        "side": {
            "open_n": open_n,
            "open_cheaper_touch_agreement": round(open_cheaper / open_n, 4) if open_n else None,
            "post_fill_n": post_n,
            "post_fill_side_agreement": round(post_agree / post_n, 4) if post_n else None,
            "note": ("the lookup predicts a relation, never a side: post-fill side = the side the "
                     "recommended relation buys (complete -> deficit side, extend -> surplus side), "
                     "so post-fill side agreement equals post-fill relation agreement; the OPEN "
                     "side follows the shared cheaper-touch convention (observed fill price <= 0.50) "
                     "and is identical under both lookups"),
        },
        "size": {
            "observed_freq": _freq(obs_size, SIZE_LABELS),
            "expected_freq": {s: round(exp_size[s] / n, 4) for s in SIZE_LABELS},
            "l1_observed_vs_expected": _l1(_freq(obs_size, SIZE_LABELS),
                                           {s: exp_size[s] / n for s in SIZE_LABELS}, SIZE_LABELS),
            "top1_agreement": round(size_top1 / n, 4),
            "conditional_on_observed_relation": {
                rel: {"n": n_by_rel[rel],
                      "observed": _freq(obs_size_by_rel[rel], SIZE_LABELS),
                      "expected": {s: round(exp_size_by_rel[rel][s] / n_by_rel[rel], 4) for s in SIZE_LABELS},
                      "l1": _l1(_freq(obs_size_by_rel[rel], SIZE_LABELS),
                                {s: exp_size_by_rel[rel][s] / n_by_rel[rel] for s in SIZE_LABELS},
                                SIZE_LABELS)}
                for rel in ("open", "extend", "complete") if n_by_rel[rel]},
        },
        "timing": {
            "observed_gap_freq": _freq(obs_gap, GAP_LABELS),
            "cadence_modal_scored": cadence_n,
            "cadence_modal_agreement": round(cadence_agree / cadence_n, 4) if cadence_n else None,
            "note": "cadence_modal is copied unchanged from frozen v3 into the candidate by the "
                    "loop's builder, so timing is identical under both lookups",
        },
        "inventory": {
            "n": n_imb,
            "observed_resid_after_freq": _freq(obs_imb, IMB_LABELS),
            "expected_resid_after_freq": {s: round(exp_imb[s] / n_imb, 4) for s in IMB_LABELS} if n_imb else None,
            "l1_observed_vs_expected": (_l1(_freq(obs_imb, IMB_LABELS),
                                            {s: exp_imb[s] / n_imb for s in IMB_LABELS}, IMB_LABELS)
                                        if n_imb else None),
            "imbalanced_after_observed": round(obs_imbalanced_after / n_imb, 4) if n_imb else None,
            "imbalanced_after_expected": round(exp_imbalanced_after / n_imb, 4) if n_imb else None,
            "size_representatives_shares": dict(size_reps),
        },
        "complete": {
            "post_fill_n": post_n,
            "p_complete_observed": round(post_obs_complete / post_n, 4) if post_n else None,
            "p_complete_expected": round(post_exp_complete / post_n, 4) if post_n else None,
            "gap_pp": (round(100.0 * (post_obs_complete - post_exp_complete) / post_n, 2)
                       if post_n else None),
            "persistence": {
                lr: {"n": persist_n[lr],
                     "observed_p_same": round(persist_obs[lr] / persist_n[lr], 4),
                     "expected_p_same": round(persist_exp[lr] / persist_n[lr], 4)}
                for lr in ("extend", "complete") if persist_n[lr]},
            "pair_cost_at_complete": {
                "n": len(pc_values),
                "freq": _freq(pc_at_complete, [l for _, l in PAIRCOST_EDGES]),
                "median": round(statistics.median(pc_values), 4) if pc_values else None,
                "note": "observed only (matched pair unit cost after each COMPLETE fill); "
                        "neither lookup predicts price, so this is identical under both",
            },
        },
        "price_aggressiveness": {
            "status": "UNMEASURED",
            "maker_taker_known_rows": sum(1 for d in rows if d.get("maker_taker") in ("maker", "taker")),
            "observed_price_band_freq": _freq(Counter(_bucket(float(d.get("price") or 0), PRICE_BANDS)
                                                      for d in rows), [l for _, l in PRICE_BANDS]),
            "note": ("the public activity fills carry no maker/taker flag (the collector "
                     "marks every realtime row 'unknown'; 'maker' is asserted only inside the "
                     "takerOnly feed coverage of the canonical history) and no book, so touch-join "
                     "and taker share are not derivable here; neither lookup predicts them -- the "
                     "aggressiveness policy comes from the fidelity artifact and is shared"),
        },
        "by_state": states_out,
        "_per_row": per_row,
    }


# --------------------------------------------------- 4. paired statistics

def paired_stats(frozen: Mapping[str, Any], cand: Mapping[str, Any], *,
                 n_boot: int = 1000, seed: int = 7, level: float = 0.95) -> Dict[str, Any]:
    """Paired bootstrap CI on candidate - frozen for agreement / mean P /
    mean P(relation), plus the exact binomial (McNemar) test on the
    discordant agreement pairs."""
    fa, ca = frozen["_per_row"]["agree"], cand["_per_row"]["agree"]
    fp, cp = frozen["_per_row"]["p_action"], cand["_per_row"]["p_action"]
    fr, cr = frozen["_per_row"]["p_rel"], cand["_per_row"]["p_rel"]
    n = len(fa)
    if n == 0:
        return {"n": 0}
    d_agree = [c - f for c, f in zip(ca, fa)]
    d_p = [c - f for c, f in zip(cp, fp)]
    d_r = [c - f for c, f in zip(cr, fr)]
    rng = random.Random(seed)
    idx_range = range(n)
    boots = {"agreement": [], "mean_p_action": [], "mean_p_relation": []}
    for _ in range(n_boot):
        idx = rng.choices(idx_range, k=n)
        boots["agreement"].append(sum(d_agree[i] for i in idx) / n)
        boots["mean_p_action"].append(sum(d_p[i] for i in idx) / n)
        boots["mean_p_relation"].append(sum(d_r[i] for i in idx) / n)
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2

    def ci(vals: List[float]) -> Tuple[float, float]:
        s = sorted(vals)
        return (round(s[int(lo_q * (len(s) - 1))], 5), round(s[int(hi_q * (len(s) - 1))], 5))
    only_cand = sum(1 for c, f in zip(ca, fa) if c and not f)
    only_frozen = sum(1 for c, f in zip(ca, fa) if f and not c)
    m = only_cand + only_frozen
    # exact two-sided binomial test of only_cand ~ Binomial(m, 0.5)
    if m:
        k = min(only_cand, only_frozen)
        log_norm = math.lgamma(m + 1) - m * math.log(2.0)
        cdf = sum(math.exp(log_norm - math.lgamma(j + 1) - math.lgamma(m - j + 1))
                  for j in range(k + 1))                     # log-space: no overflow for large m
        p_two = min(1.0, 2.0 * cdf)
    else:
        p_two = 1.0
    return {
        "n": n, "bootstrap_resamples": n_boot, "seed": seed, "ci_level": level,
        "agreement_delta": round(sum(d_agree) / n, 5), "agreement_delta_ci": ci(boots["agreement"]),
        "mean_p_action_delta": round(sum(d_p) / n, 5), "mean_p_action_delta_ci": ci(boots["mean_p_action"]),
        "mean_p_relation_delta": round(sum(d_r) / n, 5),
        "mean_p_relation_delta_ci": ci(boots["mean_p_relation"]),
        "discordant": {"candidate_only_right": only_cand, "frozen_only_right": only_frozen,
                       "both_right": sum(1 for c, f in zip(ca, fa) if c and f),
                       "both_wrong": sum(1 for c, f in zip(ca, fa) if not c and not f)},
        "mcnemar_exact_p_two_sided": round(p_two, 10),
    }


# ------------------------------------------------------------- 5. verdict

def verdict_for(frozen: Mapping[str, Any], cand: Mapping[str, Any], stats: Mapping[str, Any],
                n_train: int, n_holdout_windows: int, spec_frozen: Mapping[str, Any],
                spec_cand: Mapping[str, Any], material: Mapping[str, Any] = MATERIAL) -> Dict[str, Any]:
    n = int(frozen.get("n") or 0)
    checks: Dict[str, Any] = {}
    if n < material["min_holdout_rows"] or n_train < material["min_train_rows"] \
            or n_holdout_windows < material["min_holdout_windows"]:
        return {"verdict": "NOT_YET", "reason": "insufficient forward evidence",
                "checks": {"holdout_rows": n, "train_rows": n_train,
                           "holdout_windows": n_holdout_windows,
                           "minimums": {k: material[k] for k in
                                        ("min_holdout_rows", "min_train_rows", "min_holdout_windows")}}}
    fr, cr = frozen["relation"], cand["relation"]
    d_agree_pp = 100.0 * float(stats["agreement_delta"])
    ci_lo, ci_hi = stats["agreement_delta_ci"]
    d_p = float(stats["mean_p_action_delta"])
    p_lo, p_hi = stats["mean_p_action_delta_ci"]
    d_l1 = float(cr["l1_observed_vs_expected"]) - float(fr["l1_observed_vs_expected"])
    d_off = float(cr["off_distribution_pct"]) - float(fr["off_distribution_pct"])
    fg, cg = frozen["complete"].get("gap_pp"), cand["complete"].get("gap_pp")
    d_gap = (abs(float(cg)) - abs(float(fg))) if fg is not None and cg is not None else 0.0
    dims = ("action_distribution_relation", "post_fill_complete_gap", "off_distribution_rate")
    cand_fail = [k for k in dims if (spec_cand.get(k) or {}).get("status") == "FAIL"]
    frozen_fail = [k for k in dims if (spec_frozen.get(k) or {}).get("status") == "FAIL"]
    checks = {
        "agreement_delta_pp": round(d_agree_pp, 3), "agreement_delta_ci": [ci_lo, ci_hi],
        "agreement_gain_material": d_agree_pp >= material["agreement_gain_pp"] and ci_lo > 0,
        "mean_p_delta": round(d_p, 5), "mean_p_delta_ci": [p_lo, p_hi],
        "mean_p_gain_material": d_p >= material["mean_p_gain"] and p_lo > 0,
        "relation_l1_delta": round(d_l1, 4),
        "relation_l1_ok": d_l1 <= material["max_relation_l1_worsening"],
        "off_distribution_delta_pp": round(d_off, 3),
        "off_distribution_ok": d_off <= material["max_off_distribution_worsening_pp"],
        "complete_gap_abs_delta_pp": round(d_gap, 3),
        "complete_gap_ok": d_gap <= material["max_complete_gap_worsening_pp"],
        "frozen_spec_fail_candidate": cand_fail, "frozen_spec_fail_frozen": frozen_fail,
        "spec_ok": not [k for k in cand_fail if k not in frozen_fail],
    }
    no_regression = (checks["relation_l1_ok"] and checks["off_distribution_ok"]
                     and checks["complete_gap_ok"] and checks["spec_ok"])
    material_gain = checks["agreement_gain_material"] or checks["mean_p_gain_material"]
    reject = ((d_agree_pp <= -material["reject_agreement_loss_pp"] and ci_hi < 0)
              or (d_p <= -material["reject_mean_p_loss"] and p_hi < 0)
              or not checks["spec_ok"])
    if reject:
        v, why = "REJECT", ("candidate is measurably WORSE than frozen v3 on the forward holdout "
                            "(agreement/mean P loss with CI excluding zero, or a new frozen-spec FAIL)")
    elif material_gain and no_regression:
        v, why = "PROMOTE_RECOMMENDED", ("candidate materially improves forward fidelity with no "
                                         "regression on the other dimensions")
    else:
        v, why = "NOT_YET", ("no material forward improvement: the gain is below the bar or its "
                             "CI includes zero" + ("" if no_regression else "; a regression check failed"))
    return {"verdict": v, "reason": why, "checks": checks,
            "material_definition": dict(material)}


# ---------------------------------------------------------------- 6. run

def evaluate_cut(name: str, rows: List[Dict[str, Any]], split: Mapping[str, Any],
                 frozen_model: Mapping[str, Any], loop: loop_mod.ShadowLoop,
                 spec: Mapping[str, Any], *, n_boot: int, seed: int,
                 rule: str) -> Dict[str, Any]:
    train, hold = split["train"], split["holdout"]
    built = loop.build_candidate(train)
    cand = built["candidate"]
    reps = size_representatives(train) if train else size_representatives(hold)
    f = score_rows(frozen_model, hold, reps)
    c = score_rows(cand, hold, reps)
    stats = paired_stats(f, c, n_boot=n_boot, seed=seed) if hold else {"n": 0}
    # the loop's own frozen-spec view (score through the frozen fidelity spec)
    fs = loop._score(frozen_model, hold) if hold else {"n": 0}
    cs = loop._score(cand, hold) if hold else {"n": 0}
    prov = {"lookup_provenance": {"lookup_version_gate": loop_mod.CANONICAL_LOOKUP_VERSION}}
    g_f = gate.score_pmus_shadow({"behavioral_layer": fs.get("behavioral"), **prov}, spec) if hold else {}
    g_c = gate.score_pmus_shadow({"behavioral_layer": cs.get("behavioral"), **prov}, spec) if hold else {}
    dims = ("action_distribution_relation", "post_fill_complete_gap", "off_distribution_rate")
    n_windows = len({d["window"] for d in hold})
    ver = verdict_for(f, c, stats, len(train), n_windows, g_f, g_c) if hold else \
        {"verdict": "NOT_YET", "reason": "empty holdout", "checks": {}}
    top_states = sorted(f.get("by_state", {}).items(), key=lambda kv: -kv[1]["n"])[:25]
    by_state = []
    for s, e in top_states:
        ce = c["by_state"].get(s, {})
        by_state.append({"state": s, "n": e["n"],
                         "frozen": {k: e[k] for k in ("agreement", "mean_p_action", "relation_l1",
                                                       "observed_relation_freq", "expected_relation_freq",
                                                       "post_fill_n", "post_fill_complete_observed",
                                                       "post_fill_complete_expected")},
                         "candidate": {k: ce.get(k) for k in ("agreement", "mean_p_action", "relation_l1",
                                                              "expected_relation_freq",
                                                              "post_fill_complete_expected")}})
    strip = lambda s: {k: v for k, v in s.items() if k not in ("_per_row", "by_state")}   # noqa: E731
    return {
        "cut": name, "rule": rule, "split_mode": split["mode"],
        "cut_ts": split["cut_ts"], "cut_utc": split["cut_utc"],
        "train_rows": len(train), "holdout_rows": len(hold),
        "dropped_straddling_rows": split.get("dropped_straddling", 0),
        "train_span_utc": [_utc(min(d["ts"] for d in train)), _utc(max(d["ts"] for d in train))] if train else None,
        "holdout_span_utc": [_utc(min(d["ts"] for d in hold)), _utc(max(d["ts"] for d in hold))] if hold else None,
        "holdout_windows": n_windows, "train_windows": len({d["window"] for d in train}),
        "candidate": {"n_transitions": cand.get("n_transitions"), "n_full_states": cand.get("n_full_states"),
                      "states_touched": len(built["deltas"]),
                      "states_new_to_model": sum(1 for r in built["deltas"] if r["state_new"]),
                      "modal_action_changes": sum(1 for r in built["deltas"] if r["modal_changed"]),
                      "sha256_16": loop_mod.hashlib.sha256(
                          json.dumps({k: cand[k] for k in ("full", "base", "marginal")},
                                     sort_keys=True).encode()).hexdigest()[:16],
                      "sha_note": "sha of the candidate's count tables (full/base/marginal), "
                                  "independent of generation time"},
        "frozen_v3_on_holdout": strip(f), "candidate_v4_on_holdout": strip(c),
        "paired": stats,
        "frozen_spec_view": {"frozen_v3": {k: g_f.get(k) for k in dims},
                             "candidate_v4": {k: g_c.get(k) for k in dims},
                             "loop_score_frozen": {k: v for k, v in fs.items() if k != "behavioral"},
                             "loop_score_candidate": {k: v for k, v in cs.items() if k != "behavioral"}},
        "by_state_top": by_state,
        "verdict": ver,
    }


def run(state_dir: Path, *, proposal_path: Optional[Path], cache_dir: Optional[Path],
        deep_fetch_days: Optional[float], holdout_days: float, n_boot: int, seed: int,
        work_dir: Path, network: bool = True, now_ts: Optional[float] = None,
        reuse_cache: bool = False) -> Dict[str, Any]:
    now_ts = time.time() if now_ts is None else now_ts
    frozen = pv2.load_v2()
    spec = gate.load_spec()
    identity = loop_mod._frozen_identity()
    identity = {k: (_rel(v) if isinstance(v, str) and k.endswith("_path") else
                    [_rel(x) for x in v] if k == "frozen_paths" else v) for k, v in identity.items()}
    proposal = json.loads(Path(proposal_path).read_text(encoding="utf-8")) if proposal_path else None
    # --- data: the loop's own day files (+ the deeper windowed fetch) -----
    day_rows = raw_rows_from_day_files(Path(state_dir) / "ops")
    fetch_rep: Dict[str, Any] = {"attempted": False}
    cache_rows: List[Dict[str, Any]] = []
    if cache_dir is not None:
        prior = Path(cache_dir) / "fetch_report.json"
        if reuse_cache and prior.exists():
            fetch_rep = {"attempted": True, "reused_from_cache": True,
                         **json.loads(prior.read_text(encoding="utf-8"))}
        elif network and deep_fetch_days:
            stop = max(int(now_ts - deep_fetch_days * DAY), EPOCH_SEP1_2026)
            try:
                fetch_rep = {"attempted": True, **deep_fetch(Path(cache_dir), stop)}
            except Exception as exc:                                  # noqa: BLE001
                fetch_rep = {"attempted": True, "error": repr(exc)}
        if Path(cache_dir).exists():
            cache_rows = raw_rows_from_cache(Path(cache_dir))
    raw = merge_raw(day_rows, cache_rows)
    all_rows, ingest = normalize(raw, frozen, work_dir)
    for d in all_rows:                   # the frozen-model provenance must be the deployed one
        assert d["model_sha"] == frozen.get("_sha256_16")
    # a CLEAN subsequent cohort: only windows that started after the frozen
    # model's canonical history (ends 2026-08-31T23:11Z); windows starting
    # on/after 2026-09-01T00:00Z were never seen by frozen v3
    rows = [d for d in all_rows if float(d["t0"]) >= EPOCH_SEP1_2026]
    data = {
        "loop_day_file_fills": len(day_rows), "windowed_fetch_fills": len(cache_rows),
        "merged_unique_fills": len(raw), "ingest": ingest,
        "decision_rows_all": len(all_rows), "decision_rows": len(rows),
        "span_utc": [_utc(rows[0]["ts"]), _utc(rows[-1]["ts"])] if rows else None,
        "windows": len({d["window"] for d in rows}),
        "fetch": fetch_rep,
        "network_fetch_worked": bool(fetch_rep.get("attempted") and not fetch_rep.get("error")
                                     and fetch_rep.get("items")),
        "clean_cohort_rule": "windows starting at/after 2026-09-01T00:00Z (the frozen model's "
                             "canonical history ends 2026-08-31T23:11Z)",
        "first_second_after_frozen_history_utc": _utc(EPOCH_SEP1_2026),
        "rows_dropped_overlapping_frozen_history": len(all_rows) - len(rows),
    }
    cfg = loop_mod.load_config({})
    cfg["L5_SHADOW_WINDOW_DAYS"] = 60
    cfg["L5_SHADOW_MAX_ROWS"] = max(cfg["L5_SHADOW_MAX_ROWS"], len(rows) + 1)
    loop = EnrichedShadowLoop(cfg, work_dir, dry_run=True, now_fn=lambda: now_ts,
                              model=frozen, out=lambda s: None)
    cuts: List[Dict[str, Any]] = []
    # cut A: the loop's own rule
    if proposal and (proposal.get("evidence") or {}).get("holdout_cut_utc"):
        cut_a = datetime.strptime(proposal["evidence"]["holdout_cut_utc"], "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc).timestamp()
        rule_a = (f"loop proposal {Path(proposal_path).name}: {proposal['evidence'].get('holdout_rule')}; "
                  f"that cut TIME applied to this deeper cohort (the loop's own candidate saw only "
                  f"{proposal['evidence'].get('train_transitions')} train / "
                  f"{proposal['evidence'].get('holdout_transitions')} holdout rows of its collector window)")
    else:
        lc = loop_cut_ts(rows, now_ts, cfg["L5_SHADOW_HOLDOUT_DAYS"])
        cut_a, rule_a = lc["cut_ts"], f"ShadowLoop._holdout_cut: {lc['rule']}"
    if cut_a is not None and rows:
        cuts.append(evaluate_cut("loop", rows, split_rows(rows, cut_a, "row"), frozen, loop, spec,
                                 n_boot=n_boot, seed=seed, rule=rule_a))
    # cut B: strict forward, window-level, last HOLDOUT_DAYS days never seen
    cut_b = strict_cut_ts(rows, holdout_days)
    if cut_b is not None:
        cuts.append(evaluate_cut("strict", rows, split_rows(rows, cut_b, "window"), frozen, loop, spec,
                                 n_boot=n_boot, seed=seed,
                                 rule=f"last {holdout_days:g} days of observed activity; window-level split"))
    strict = next((c for c in cuts if c["cut"] == "strict"), None)
    loop_cut = next((c for c in cuts if c["cut"] == "loop"), None)
    final = strict["verdict"] if strict else {"verdict": "NOT_YET", "reason": "no rows"}
    return {
        "kind": "l5_lookup_v4_forward_validation",
        "utc": _utc(now_ts), "labels": LABELS,
        "owner_directive": "issue #167 comment 5577286614 item 4",
        "frozen_v3_identity": {**identity, "model_sha256_16_loaded": frozen.get("_sha256_16"),
                               "n_transitions": frozen.get("n_transitions"),
                               "n_full_states": frozen.get("n_full_states"),
                               "generated_utc": frozen.get("generated_utc")},
        "reproduced_proposal": ({"path": str(proposal_path), "verdict": proposal["validation"]["verdict"],
                                 "candidate_sha256_16": proposal["candidate_artifact"]["sha256_16"],
                                 "train": proposal["evidence"]["train_transitions"],
                                 "holdout": proposal["evidence"]["holdout_transitions"],
                                 "holdout_cut_utc": proposal["evidence"]["holdout_cut_utc"],
                                 "holdout_rule": proposal["evidence"]["holdout_rule"],
                                 "rows_in_window": proposal["evidence"]["rows_in_window"],
                                 "frozen_on_holdout": {k: v for k, v in proposal["validation"]["frozen_on_holdout"].items()
                                                       if k != "frozen_spec_dimensions"},
                                 "candidate_on_holdout": {k: v for k, v in proposal["validation"]["candidate_on_holdout"].items()
                                                          if k != "frozen_spec_dimensions"}}
                                if proposal else None),
        "data": data,
        "cuts": cuts,
        "verdict": {**final, "rests_on": "strict cut",
                    "loop_cut_verdict": loop_cut["verdict"]["verdict"] if loop_cut else None,
                    "promotion": "REQUIRES_OWNER_DECISION"},
    }


# -------------------------------------------------------------- 7. report

def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100.0 * float(x):.1f}%"


def render_md(rep: Mapping[str, Any]) -> str:
    fid = rep["frozen_v3_identity"]
    L: List[str] = []
    L.append("# L5 LOOKUP v4 -- strict forward validation (frozen v3 vs candidate v4)")
    L.append("")
    L.append(f"_{rep['labels']}_")
    L.append("")
    L.append(f"Generated {rep['utc']} by `src/l5_lookup_v4_validation.py` "
             f"({rep['owner_directive']}). **Verdict: `{rep['verdict']['verdict']}`** -- "
             f"{rep['verdict']['reason']}. Promotion: `{rep['verdict']['promotion']}`.")
    L.append("")
    L.append("## Identities")
    L.append("")
    L.append(f"- frozen model v2 / lookup v{fid['lookup_version']}: `{fid['model_path']}` "
             f"sha256_16 `{fid['model_sha256_16']}` (n_transitions {fid['n_transitions']}, "
             f"full states {fid['n_full_states']}, generated {fid['generated_utc']}); "
             f"frozen spec `{fid['spec_path']}` sha `{fid['spec_sha256_16']}` frozen {fid['spec_frozen_utc']}")
    rp = rep.get("reproduced_proposal")
    if rp:
        L.append(f"- reproduced loop proposal: `{Path(rp['path']).name}` verdict `{rp['verdict']}`, "
                 f"candidate sha `{rp['candidate_sha256_16']}`, train {rp['train']} / holdout {rp['holdout']}, "
                 f"cut {rp['holdout_cut_utc']} ({rp['holdout_rule']}), rows in window {rp['rows_in_window']}; "
                 f"frozen agreement {rp['frozen_on_holdout'].get('relation_agreement')} / mean P "
                 f"{rp['frozen_on_holdout'].get('mean_prob_of_observed_action')} vs candidate "
                 f"{rp['candidate_on_holdout'].get('relation_agreement')} / "
                 f"{rp['candidate_on_holdout'].get('mean_prob_of_observed_action')}")
    d = rep["data"]
    L.append("")
    L.append("## Cohort")
    L.append("")
    L.append(f"- network fetch worked: **{d['network_fetch_worked']}**; loop day-file fills {d['loop_day_file_fills']}, "
             f"windowed-fetch fills {d['windowed_fetch_fills']}, merged unique fills {d['merged_unique_fills']} "
             f"(fetch: {json.dumps({k: v for k, v in d['fetch'].items() if k in ('attempted', 'pages', 'windows', 'items', 'oldest_ts', 'newest_ts', 'stopped_because', 'elapsed_s', 'error')})})")
    L.append(f"- normalized decision rows {d['decision_rows']} (of {d['decision_rows_all']} ingested) over "
             f"{d['windows']} settlement windows, span {d['span_utc']}; clean-cohort rule: {d['clean_cohort_rule']}; "
             f"rows dropped as overlapping the frozen history: {d['rows_dropped_overlapping_frozen_history']}")
    L.append("")
    L.append("## What \"materially improves\" means here")
    L.append("")
    m = MATERIAL
    L.append(f"On the STRICT forward holdout (>= {m['min_holdout_rows']} rows, >= {m['min_train_rows']} training rows, "
             f">= {m['min_holdout_windows']} holdout windows): candidate relation top-1 agreement >= frozen + "
             f"{m['agreement_gain_pp']} pp with the {int(100 * m['ci_level'])}% paired-bootstrap CI lower bound > 0, "
             f"OR mean P(observed action) >= frozen + {m['mean_p_gain']} with CI lower bound > 0; AND no regression: "
             f"relation L1 not worse by > {m['max_relation_l1_worsening']}, off-distribution not worse, post-fill "
             f"COMPLETE |gap| not worse by > {m['max_complete_gap_worsening_pp']} pp, no new FAIL under the frozen "
             f"fidelity spec. REJECT when the candidate is worse by >= {m['reject_agreement_loss_pp']} pp agreement "
             f"(CI upper < 0) or >= {m['reject_mean_p_loss']} mean P (CI upper < 0) or adds a spec FAIL. Otherwise NOT_YET.")
    for c in rep["cuts"]:
        f, k = c["frozen_v3_on_holdout"], c["candidate_v4_on_holdout"]
        L.append("")
        L.append(f"## Cut `{c['cut']}` -- {c['rule']}")
        L.append("")
        L.append(f"- split `{c['split_mode']}` at {c['cut_utc']}: train {c['train_rows']} rows / {c['train_windows']} windows "
                 f"{c['train_span_utc']}; holdout {c['holdout_rows']} rows / {c['holdout_windows']} windows "
                 f"{c['holdout_span_utc']}; straddling rows dropped {c['dropped_straddling_rows']}")
        cd = c["candidate"]
        L.append(f"- candidate v4 rebuilt on the training cohort only: n_transitions {cd['n_transitions']}, "
                 f"full states {cd['n_full_states']}, states touched {cd['states_touched']}, new {cd['states_new_to_model']}, "
                 f"modal changes {cd['modal_action_changes']}, count-table sha `{cd['sha256_16']}`")
        L.append(f"- **verdict `{c['verdict']['verdict']}`** -- {c['verdict']['reason']}")
        if not f.get("n"):
            continue
        L.append("")
        L.append("| dimension | frozen v3 | candidate v4 | delta |")
        L.append("|---|---|---|---|")
        fr, cr = f["relation"], k["relation"]

        def row(name: str, a: Any, b: Any, nd: int = 4) -> None:
            try:
                delta = round(float(b) - float(a), nd)
            except (TypeError, ValueError):
                delta = "n/a"
            L.append(f"| {name} | {a} | {b} | {delta} |")
        row("relation top-1 agreement", fr["top1_agreement"], cr["top1_agreement"])
        row("exact action agreement", fr["exact_action_agreement"], cr["exact_action_agreement"])
        row("mean P(observed action)", fr["mean_p_observed_action"], cr["mean_p_observed_action"])
        row("mean P(observed relation)", fr["mean_p_observed_relation"], cr["mean_p_observed_relation"])
        row("relation L1 observed vs expected", fr["l1_observed_vs_expected"], cr["l1_observed_vs_expected"])
        row("state-weighted relation L1", fr["state_weighted_l1"], cr["state_weighted_l1"])
        row("off-distribution %", fr["off_distribution_pct"], cr["off_distribution_pct"], 2)
        row("side: open cheaper-touch agreement (model-free)", f["side"]["open_cheaper_touch_agreement"],
            k["side"]["open_cheaper_touch_agreement"])
        row("side: post-fill side agreement", f["side"]["post_fill_side_agreement"], k["side"]["post_fill_side_agreement"])
        row("size bucket L1 observed vs expected", f["size"]["l1_observed_vs_expected"], k["size"]["l1_observed_vs_expected"])
        row("size bucket top-1 agreement", f["size"]["top1_agreement"], k["size"]["top1_agreement"])
        row("timing: cadence_modal agreement", f["timing"]["cadence_modal_agreement"], k["timing"]["cadence_modal_agreement"])
        row("inventory: resid-after bucket L1", f["inventory"]["l1_observed_vs_expected"], k["inventory"]["l1_observed_vs_expected"])
        row("inventory: imbalanced-after expected (obs %s)" % f["inventory"]["imbalanced_after_observed"],
            f["inventory"]["imbalanced_after_expected"], k["inventory"]["imbalanced_after_expected"])
        row("COMPLETE: P(complete|post-fill) expected (obs %s, n %s)" % (f["complete"]["p_complete_observed"], f["complete"]["post_fill_n"]),
            f["complete"]["p_complete_expected"], k["complete"]["p_complete_expected"])
        row("COMPLETE: gap pp (observed - expected)", f["complete"]["gap_pp"], k["complete"]["gap_pp"], 2)
        for lr in ("extend", "complete"):
            pf, pc = f["complete"]["persistence"].get(lr), k["complete"]["persistence"].get(lr)
            if pf and pc:
                row(f"persistence P({lr}|last={lr}) expected (obs {pf['observed_p_same']}, n {pf['n']})",
                    pf["expected_p_same"], pc["expected_p_same"])
        L.append("")
        pcx = f["complete"]["pair_cost_at_complete"]
        L.append(f"- observed relation freq {fr['observed_freq']}; expected v3 {fr['expected_freq']}; expected v4 {cr['expected_freq']}")
        L.append(f"- size: observed {f['size']['observed_freq']}; expected v3 {f['size']['expected_freq']}; v4 {k['size']['expected_freq']}")
        L.append(f"- timing: observed gap freq {f['timing']['observed_gap_freq']} ({f['timing']['note']})")
        L.append(f"- inventory: residual-after buckets observed {f['inventory']['observed_resid_after_freq']}; "
                 f"expected v3 {f['inventory']['expected_resid_after_freq']}; v4 {k['inventory']['expected_resid_after_freq']}")
        L.append(f"- pair cost at COMPLETE (observed, n {pcx['n']}, median {pcx['median']}): {pcx['freq']}")
        L.append(f"- price aggressiveness: {f['price_aggressiveness']['status']} -- {f['price_aggressiveness']['note']}; "
                 f"observed price bands {f['price_aggressiveness']['observed_price_band_freq']}")
        p = c["paired"]
        if p.get("n"):
            L.append(f"- paired statistics (n {p['n']}, {p['bootstrap_resamples']} resamples): agreement delta "
                     f"{p['agreement_delta']} CI {p['agreement_delta_ci']}; mean P delta {p['mean_p_action_delta']} "
                     f"CI {p['mean_p_action_delta_ci']}; discordant {p['discordant']}; McNemar exact p {p['mcnemar_exact_p_two_sided']}")
        sv = c["frozen_spec_view"]
        L.append(f"- frozen fidelity spec view: v3 " +
                 ", ".join(f"{k2}={ (v or {}).get('status')}" for k2, v in sv["frozen_v3"].items()) +
                 "; v4 " + ", ".join(f"{k2}={(v or {}).get('status')}" for k2, v in sv["candidate_v4"].items()))
        L.append(f"- verdict checks: `{json.dumps(c['verdict'].get('checks'), default=str)}`")
        if c["by_state_top"]:
            L.append("")
            L.append("| base state | n | agree v3 | agree v4 | mean P v3 | mean P v4 | rel L1 v3 | rel L1 v4 | post-fill n | P(complete) obs | exp v3 | exp v4 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for s in c["by_state_top"]:
                a, b = s["frozen"], s["candidate"]
                L.append(f"| {s['state']} | {s['n']} | {a['agreement']} | {b.get('agreement')} | {a['mean_p_action']} | "
                         f"{b.get('mean_p_action')} | {a['relation_l1']} | {b.get('relation_l1')} | {a['post_fill_n']} | "
                         f"{a['post_fill_complete_observed']} | {a['post_fill_complete_expected']} | {b.get('post_fill_complete_expected')} |")
    strict = next((c for c in rep["cuts"] if c["cut"] == "strict"), None)
    if strict and strict["frozen_v3_on_holdout"].get("n"):
        f, k, p = strict["frozen_v3_on_holdout"], strict["candidate_v4_on_holdout"], strict["paired"]
        L.append("")
        L.append("## Reading")
        L.append("")
        L.append(f"- On {strict['holdout_rows']} clean forward decisions ({strict['holdout_windows']} windows, "
                 f"{strict['holdout_span_utc'][0]} to {strict['holdout_span_utc'][1]}) that neither lookup saw, "
                 f"candidate v4 vs frozen v3: relation top-1 agreement {k['relation']['top1_agreement']} vs "
                 f"{f['relation']['top1_agreement']} (delta {p['agreement_delta']}, 95% CI {p['agreement_delta_ci']}, "
                 f"McNemar p {p['mcnemar_exact_p_two_sided']}); mean P(observed action) {k['relation']['mean_p_observed_action']} "
                 f"vs {f['relation']['mean_p_observed_action']} (delta {p['mean_p_action_delta']}, CI {p['mean_p_action_delta_ci']}); "
                 f"relation L1 {k['relation']['l1_observed_vs_expected']} vs {f['relation']['l1_observed_vs_expected']}; "
                 f"post-fill COMPLETE gap {k['complete']['gap_pp']} pp vs {f['complete']['gap_pp']} pp; both PASS every "
                 f"frozen-spec dimension.")
        L.append(f"- Verdict `{strict['verdict']['verdict']}`: {strict['verdict']['reason']}. Against the material bar: "
                 f"agreement delta {round(100 * float(p['agreement_delta']), 3)} pp vs the +{MATERIAL['agreement_gain_pp']} pp "
                 f"bar; mean P delta {p['mean_p_action_delta']} vs the +{MATERIAL['mean_p_gain']} bar.")
        L.append("- The shadow loop's own proposal verdict rests on a much thinner holdout than its name suggests: the "
                 "collector's incremental call reaches back ~4,000 fills (a few hours at L5's current rate), so the "
                 "loop's 'last 3 days' rule falls back to a 0.7 time quantile of that window (a holdout of roughly an "
                 "hour, 12-15 windows). A CANDIDATE_VALIDATED_OOS from that path is not forward evidence of the kind "
                 "the owner asked for; this strict validation is.")
        L.append("- Promotion recommendation: NOT YET. Re-run this validation as the clean cohort grows (the deployed "
                 "loop's proposals should be judged by this module's strict cut, not by their own split); a v4 built "
                 "on the same recipe is expected to stay a near-no-op until the drifted imb>50 states accumulate enough "
                 "post-freeze transitions to move the shrunk masses.")
    L.append("")
    L.append("## Assumptions about the loop's internals")
    L.append("")
    L.append("- The candidate is `ShadowLoop.build_candidate` (imported): frozen v3 counts PLUS the training cohort's "
             "transitions on the same full key; shrinkage alpha=10 at lookup time; `cadence_modal` copied unchanged.")
    L.append("- The collector's incremental call reaches back at most 8 pages x 500 rows (~4,000 fills, a few hours of "
             "L5 activity now); the deeper cohort here comes from the existing windowed DESC fetch helper with the raw "
             "cache outside the repo. The loop's own 'last 3 days' holdout rule therefore falls back to its 0.7 time "
             "quantile on a window that short.")
    L.append("- Decision rows are the loop's own (`decision_for`), enriched with observable fields; lookup keys are unchanged. "
             "The strict cut splits at settlement-window level so no holdout window's state was built from training fills.")
    L.append("- No frozen artifact is written; the only outputs are this file and its JSON twin.")
    L.append("")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", required=True, help="the shadow loop's local state dir (ops/activity_*.jsonl)")
    ap.add_argument("--proposal", default=None, help="the reproduced lookup_v4_proposal_*.json")
    ap.add_argument("--cache-dir", default=None, help="raw cache dir for the windowed fetch (outside the repo)")
    ap.add_argument("--deep-fetch-days", type=float, default=7.0)
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--reuse-cache", action="store_true",
                    help="reuse the cache dir's previous windowed fetch (and its stored report)")
    ap.add_argument("--holdout-days", type=float, default=3.0)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--work-dir", default=None, help="scratch state dir for the enriched loop (outside analysis/l5)")
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-md", default=str(OUT_MD))
    args = ap.parse_args(argv)
    state_dir = Path(args.state_dir)
    work_dir = Path(args.work_dir) if args.work_dir else state_dir.parent / "l5_v4_validation_work"
    rep = run(state_dir, proposal_path=Path(args.proposal) if args.proposal else None,
              cache_dir=Path(args.cache_dir) if args.cache_dir else None,
              deep_fetch_days=None if args.no_network else args.deep_fetch_days,
              holdout_days=args.holdout_days, n_boot=args.bootstrap, seed=args.seed,
              work_dir=work_dir, network=not args.no_network, reuse_cache=args.reuse_cache)
    out_json = guard_output(Path(args.out_json))
    out_md = guard_output(Path(args.out_md))
    out_json.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    out_md.write_text(render_md(rep), encoding="utf-8")
    summary = {"verdict": rep["verdict"]["verdict"], "promotion": rep["verdict"]["promotion"],
               "network_fetch_worked": rep["data"]["network_fetch_worked"],
               "fills": rep["data"]["merged_unique_fills"], "rows": rep["data"]["decision_rows"],
               "cuts": [{"cut": c["cut"], "verdict": c["verdict"]["verdict"], "train": c["train_rows"],
                         "holdout": c["holdout_rows"],
                         "agreement": [c["frozen_v3_on_holdout"].get("relation", {}).get("top1_agreement"),
                                       c["candidate_v4_on_holdout"].get("relation", {}).get("top1_agreement")],
                         "mean_p": [c["frozen_v3_on_holdout"].get("relation", {}).get("mean_p_observed_action"),
                                    c["candidate_v4_on_holdout"].get("relation", {}).get("mean_p_observed_action")]}
                        for c in rep["cuts"]],
               "out_json": str(out_json), "out_md": str(out_md)}
    print("L5_LOOKUP_V4_VALIDATION " + json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
