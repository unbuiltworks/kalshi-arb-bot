"""Why is R5F flat on 46.9% of decisions while L5 is flat on 0.9%?
(OWNER #167 comment 5583906276 item 3 -- the highest-priority fidelity test.)

The live state key's third dimension is the magnitude of directional
residual inventory, bucketed on ABSOLUTE share counts:

    flat  <1 share | imb1-10 | imb10-50 | imb>50

That is a scale-dependent feature shared between two operations whose
capital differs by orders of magnitude. L5 works a window with hundreds of
dollars; R5F works one market with a $7.50 at-risk cap. So the same
sentence -- "hold half a position of imbalance" -- lands in different
buckets purely because of size, and the frozen model is then asked a
question about a state we structurally never occupy.

This module does three things and refuses to blur them together:

  1. DECOMPOSE the gap. Three candidate causes are separated and each is
     measured: (a) dollar scale, (b) the behavioural-share adapter's reset
     at every completed pair, (c) the R5F shell itself (residual cap,
     $7.50, concurrency 1) making large imbalance UNREACHABLE. Only (a) is
     a representation defect. If (c) dominates, no rescaling can put us in
     L5's modal bucket, and this module says so rather than relabelling
     our way into it.

  2. Build a SCALE-NORMALIZED imbalance feature computable at decision
     time from state the operator already has:

         ratio = |residual_qty| x avg_cost(surplus side) / capital_scale

     `capital_scale` is the operator's OWN trailing median deployed
     capital per sequence (window for L5, market session for us), so it is
     causal, symmetric, and needs no knowledge of the other operator.

  3. Choose the bucket edges from L5'S OWN TAPE ALONE, by quantile
     matching: the ratio edges are the values at which L5's normalized
     decisions fall into the four buckets in EXACTLY the proportions its
     absolute-share decisions do. Nothing here is fitted to our data, so
     the translation cannot be tuned to flatter us. The bucket LABELS stay
     the frozen model's own labels -- only the assignment rule changes --
     so the frozen lookup is unchanged and still answers the question it
     was built to answer.

The honesty check that decides whether this is a re-expression or a
different feature: per-row agreement between the absolute and normalized
assignment ON L5'S OWN ROWS. High agreement means we re-expressed L5's
feature at another scale. Low agreement means we invented a new one, and
that is reported as such.

Read-only research. No network, no venue, nothing here trades, and the
frozen model, lookup and live R5F shell are untouched.

    python3 -m src.l5_inventory_translation --logs dir --out analysis/l5
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.polymarket_l5.action_model import IMBALANCE_BUCKETS, _bucket

OUT_DIR = os.path.join("analysis", "l5")
CANONICAL = os.path.join(OUT_DIR, "l5_canonical_sequence.csv.gz")
JSON_NAME = "l5_inventory_translation.json"
MD_NAME = "L5_INVENTORY_TRANSLATION.md"

# The buckets are the frozen model's own. We never rename them: the
# translation changes which decisions land in each, not what they mean.
BUCKET_LABELS: Tuple[str, ...] = tuple(b for _, b in IMBALANCE_BUCKETS)

# Trailing capital scale: the operator's own median deployed capital over
# the last N completed sequences. Causal by construction (a sequence only
# enters the estimator once it is finished, and never the current one).
SCALE_WINDOW = 50
SCALE_MIN_SEQUENCES = 5

# R5F shell constants, for the reachability arithmetic ONLY. This module
# does not read, set or change any of them on the live path.
R5F_PER_MARKET_CAP_USD = 7.50
R5F_RESIDUAL_UNIT_CAP = 6.0


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# one decision row per operator, in ONE shape, with state BEFORE the action
# --------------------------------------------------------------------------

def _imbalance_usd(up_qty: float, up_spend: float,
                   dn_qty: float, dn_spend: float) -> Tuple[float, float]:
    """(signed residual shares, |residual| valued at the surplus side's own
    average cost). One formula, used for both operators, from quantities
    and spends each already has at decision time."""
    resid = up_qty - dn_qty
    if resid == 0:
        return 0.0, 0.0
    if resid > 0:
        avg = (up_spend / up_qty) if up_qty else 0.0
    else:
        avg = (dn_spend / dn_qty) if dn_qty else 0.0
    return resid, abs(resid) * avg


def l5_decision_rows(csv_path: str = CANONICAL) -> List[dict]:
    """L5's decisions from the canonical per-trade tape: for each trade, the
    inventory state BEFORE it, plus the window it belongs to. Sequential and
    causal -- a row never reads its own action or any later one."""
    rows: List[dict] = []
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        prev_window = None
        before = (0.0, 0.0, 0.0, 0.0)      # up_qty, up_spend, dn_qty, dn_spend
        for r in csv.DictReader(fh):
            wid = r["window_id"]
            if wid != prev_window:
                prev_window, before = wid, (0.0, 0.0, 0.0, 0.0)
            resid, usd = _imbalance_usd(*before)
            rows.append({
                "operator": "l5",
                "sequence": wid,
                "t": _f(r.get("timestamp")),
                "residual_qty_before": resid,
                "imbalance_usd_before": usd,
                "deployed_usd_before": before[1] + before[3],
                "asset": r.get("asset"),
                "duration_minutes": r.get("duration_minutes"),
            })
            before = (_f(r.get("cum_up_qty")) or 0.0,
                      _f(r.get("cum_up_spend")) or 0.0,
                      _f(r.get("cum_down_qty")) or 0.0,
                      _f(r.get("cum_down_spend")) or 0.0)
    return rows


def live_decision_rows(rep: Mapping[str, Any]) -> List[dict]:
    """Our decisions from the R5F ledger, in the same shape. `state` is kept
    verbatim so the CURRENT bucket is the one the live key actually used --
    never recomputed here, so mapping A cannot be quietly improved."""
    rows: List[dict] = []
    for slug, m in (rep.get("per_market") or {}).items():
        for r in sorted(m.get("rows") or [], key=lambda x: x.get("idx") or 0):
            inv = r.get("inventory_before") or {}
            up, dn = _f(inv.get("up")) or 0.0, _f(inv.get("dn")) or 0.0
            spend = _f(inv.get("spent")) or 0.0
            # spends per side are not carried separately; recover them from
            # the averages the ledger does carry, and fall back to splitting
            # the total by quantity only when an average is missing
            au, ad = _f(inv.get("avg_up")), _f(inv.get("avg_dn"))
            up_spend = au * up if au is not None else (spend * (up / (up + dn)) if up + dn else 0.0)
            dn_spend = ad * dn if ad is not None else (spend * (dn / (up + dn)) if up + dn else 0.0)
            resid, usd = _imbalance_usd(up, up_spend, dn, dn_spend)
            state = str(r.get("state") or "")
            rows.append({
                "operator": "r5f",
                "sequence": slug,
                "t_utc": r.get("t_utc"),
                "idx": r.get("idx"),
                "residual_qty_before": resid,
                "imbalance_usd_before": usd,
                "deployed_usd_before": spend,
                "live_bucket": (state.split("|")[2]
                                if state.count("|") == 2 else None),
                "relation_acted": (r.get("order") or {}).get("relation"),
                "l5_sampled": r.get("l5_sampled"),
                "matched_before": _f((r.get("inventory_before") or {}).get("matched")),
            })
    return rows


# --------------------------------------------------------------------------
# capital scale: the operator's own trailing median, causal
# --------------------------------------------------------------------------

def sequence_capital(rows: Sequence[Mapping[str, Any]]) -> "collections.OrderedDict":
    """Deployed capital per sequence, in first-appearance order. A sequence's
    capital is its LAST observed deployed total, so it is only known once the
    sequence is over -- which is why the estimator below never uses the
    current one."""
    seen: "collections.OrderedDict[str, float]" = collections.OrderedDict()
    for r in rows:
        seen[r["sequence"]] = max(seen.get(r["sequence"], 0.0),
                                  r.get("deployed_usd_before") or 0.0)
    return seen


def trailing_scale(rows: Sequence[Mapping[str, Any]],
                   window: int = SCALE_WINDOW,
                   min_sequences: int = SCALE_MIN_SEQUENCES) -> Dict[str, Optional[float]]:
    """sequence -> the median deployed capital of the previous `window`
    COMPLETED sequences. None until `min_sequences` have completed; those
    rows are excluded from the normalized comparison and counted, never
    imputed."""
    caps = sequence_capital(rows)
    order = list(caps)
    out: Dict[str, Optional[float]] = {}
    history: List[float] = []
    for seq in order:
        if len(history) >= min_sequences:
            tail = history[-window:]
            med = statistics.median(tail)
            out[seq] = med if med > 0 else None
        else:
            out[seq] = None
        history.append(caps[seq])
    return out


def attach_ratio(rows: Sequence[Mapping[str, Any]], **kw) -> List[dict]:
    """|imbalance $| / trailing capital scale, per decision."""
    scale = trailing_scale(rows, **kw)
    out = []
    for r in rows:
        s = scale.get(r["sequence"])
        out.append({**r, "capital_scale_usd": s,
                    "imbalance_ratio": ((r["imbalance_usd_before"] / s)
                                        if s else None)})
    return out


# --------------------------------------------------------------------------
# the edges, chosen from L5's tape alone
# --------------------------------------------------------------------------

def absolute_bucket(residual_qty: float) -> str:
    return _bucket(abs(residual_qty), IMBALANCE_BUCKETS)


def _shares(labels: Iterable[str]) -> Dict[str, float]:
    c = collections.Counter(labels)
    n = sum(c.values()) or 1
    return {b: c.get(b, 0) / n for b in BUCKET_LABELS}


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    i = q * (len(sorted_vals) - 1)
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    frac = i - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def derive_ratio_edges(l5_rows: Sequence[Mapping[str, Any]]) -> dict:
    """Ratio edges such that L5's OWN normalized decisions fall into the four
    buckets in the same proportions as its absolute-share decisions.

    Derived from L5 only. Our data is not an input, so the translation
    cannot be tuned to make us resemble L5."""
    usable = [r for r in l5_rows if r.get("imbalance_ratio") is not None]
    target = _shares(absolute_bucket(r["residual_qty_before"]) for r in usable)
    vals = sorted(r["imbalance_ratio"] for r in usable)
    edges, cum = [], 0.0
    for label in BUCKET_LABELS[:-1]:
        cum += target[label]
        edges.append(_quantile(vals, cum))
    spec = {
        "id": "L5_INVENTORY_RATIO_EDGES_v1",
        "derived_from": "l5_canonical_sequence.csv.gz, L5 rows only",
        "rule": ("edges are the quantiles of L5's own imbalance-ratio "
                 "distribution at L5's own cumulative absolute-share bucket "
                 "shares, so the translated buckets reproduce L5's bucket "
                 "occupancy exactly on L5's tape"),
        "n_l5_rows_used": len(usable),
        "l5_absolute_share": {k: round(v, 4) for k, v in target.items()},
        "edges": [round(e, 8) for e in edges],
        "labels": list(BUCKET_LABELS),
    }
    spec["caveat_ties"] = (
        "quantile matching cannot split a tie: where the ratio distribution "
        "has atoms the reproduced shares are close, not exact, and the "
        "residual difference is reported rather than smoothed away")
    spec["sha256_16"] = hashlib.sha256(
        json.dumps({k: v for k, v in spec.items() if k != "sha256_16"},
                   sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return spec


def normalized_bucket(ratio: Optional[float], edges: Sequence[float]) -> Optional[str]:
    """The frozen model's own bucket label, assigned by scale-normalized
    imbalance instead of absolute shares."""
    if ratio is None:
        return None
    for e, label in zip(edges, BUCKET_LABELS[:-1]):
        if ratio < e:
            return label
    return BUCKET_LABELS[-1]


# --------------------------------------------------------------------------
# is this a re-expression of L5's feature, or a different feature?
# --------------------------------------------------------------------------

def agreement_on_l5(l5_rows: Sequence[Mapping[str, Any]],
                    edges: Sequence[float]) -> dict:
    """Per-row agreement between the absolute and the normalized assignment
    ON L5'S OWN ROWS. Matching marginal shares is guaranteed by construction
    and proves nothing; this does not. High agreement = we re-expressed L5's
    feature at another scale. Low agreement = we built a DIFFERENT feature,
    which would have to be justified on its own merits."""
    usable = [r for r in l5_rows if r.get("imbalance_ratio") is not None]
    conf: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    same = 0
    for r in usable:
        a = absolute_bucket(r["residual_qty_before"])
        b = normalized_bucket(r["imbalance_ratio"], edges)
        conf[a][b] += 1
        same += int(a == b)
    n = len(usable) or 1
    return {
        "n_rows": len(usable),
        "per_row_agreement": round(same / n, 4),
        "confusion_absolute_to_normalized": {
            a: dict(sorted(c.items())) for a, c in sorted(conf.items())},
        "reading": ("the marginal shares match by construction; only this "
                    "per-row figure says whether the normalized feature is "
                    "the same feature at another scale"),
    }


# --------------------------------------------------------------------------
# why we are flat: three causes, separated
# --------------------------------------------------------------------------

def flat_decomposition(rep: Mapping[str, Any]) -> dict:
    """Every R5F decision keyed `flat`, split by WHY the book was flat. The
    three causes need different fixes and only one of them is about scale."""
    cause: collections.Counter = collections.Counter()
    n_flat = n_total = 0
    for slug, m in (rep.get("per_market") or {}).items():
        for r in (m.get("rows") or []):
            state = str(r.get("state") or "")
            bucket = state.split("|")[2] if state.count("|") == 2 else None
            n_total += 1
            if bucket != "flat":
                continue
            n_flat += 1
            inv = r.get("inventory_before") or {}
            up, dn = _f(inv.get("up")) or 0.0, _f(inv.get("dn")) or 0.0
            if up == 0.0 and dn == 0.0:
                cause["empty_book_no_position_yet"] += 1
            elif up == dn:
                # a completed pair: the venue book is flat and the
                # behavioural share ledger is reset to start a fresh
                # sequence (the R4 netting fix), so the key reads flat
                cause["matched_pair_book_flat"] += 1
            else:
                cause["residual_below_one_share"] += 1
    return {
        "n_decisions": n_total,
        "n_flat": n_flat,
        "flat_share": round(n_flat / n_total, 4) if n_total else None,
        "causes": dict(sorted(cause.items())),
        "cause_share_of_flat": {k: round(v / n_flat, 4)
                                for k, v in sorted(cause.items())} if n_flat else {},
        "reading": ("empty_book -> we simply had not traded yet; "
                    "matched_pair_book_flat -> we HAD traded and paired back "
                    "to flat, which is the shell recycling, not a scale "
                    "artefact; residual_below_one_share -> we held real "
                    "directional inventory that the absolute-share bucket "
                    "calls flat, and THAT is the part a scale-normalized "
                    "feature can fix"),
    }


# --------------------------------------------------------------------------
# can we even get there? the shell's own arithmetic
# --------------------------------------------------------------------------

def reachability(r5f_rows: Sequence[Mapping[str, Any]],
                 l5_rows: Sequence[Mapping[str, Any]],
                 edges: Sequence[float],
                 cap_usd: float = R5F_PER_MARKET_CAP_USD) -> dict:
    """Under the R5F shell, the most one-sided a market can get is the
    per-market at-risk cap. Divided by our own trailing capital scale that
    gives the highest imbalance ratio the shell PERMITS -- and therefore the
    highest bucket the translated key can ever reach. If L5's modal bucket
    sits above it, no representation change can put us there, and saying so
    is the point of this function."""
    scales = [r["capital_scale_usd"] for r in r5f_rows
              if r.get("capital_scale_usd")]
    if not scales:
        return {"computable": False,
                "reason": "no completed sequences yet, so no capital scale"}
    med_scale = statistics.median(scales)
    max_ratio = cap_usd / med_scale
    top = normalized_bucket(max_ratio, edges)
    l5_usable = [r for r in l5_rows if r.get("imbalance_ratio") is not None]
    above = sum(1 for r in l5_usable if r["imbalance_ratio"] > max_ratio)
    observed = [r["imbalance_ratio"] for r in r5f_rows
                if r.get("imbalance_ratio") is not None]
    return {
        "computable": True,
        "r5f_median_capital_scale_usd": round(med_scale, 4),
        "per_market_cap_usd": cap_usd,
        "max_reachable_ratio": round(max_ratio, 6),
        "highest_reachable_bucket": top,
        "observed_max_ratio": round(max(observed), 6) if observed else None,
        "l5_decisions_above_our_ceiling": above,
        "l5_share_above_our_ceiling": (round(above / len(l5_usable), 4)
                                       if l5_usable else None),
        "reading": ("the ceiling is the shell's, not the representation's. "
                    "L5 decisions above it are unreachable at this capital "
                    "whatever the state key says, and raising capital to "
                    "reach them was explicitly ruled out"),
    }


# --------------------------------------------------------------------------
# trajectory, not just the marginal distribution
# --------------------------------------------------------------------------

def trajectory_stats(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Per-sequence inventory PATH. Two operators can share a marginal
    bucket distribution and still move through inventory completely
    differently, so the marginal alone is not the fidelity question."""
    by_seq: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_seq[r["sequence"]].append(r)
    per: List[dict] = []
    for seq, rs in by_seq.items():
        ratios = [r["imbalance_ratio"] for r in rs
                  if r.get("imbalance_ratio") is not None]
        signs = [1 if r["residual_qty_before"] > 0 else
                 (-1 if r["residual_qty_before"] < 0 else 0)
                 for r in rs]
        nz = [s for s in signs if s != 0]
        flips = sum(1 for a, b in zip(nz, nz[1:]) if a != b)
        per.append({
            "sequence": seq, "decisions": len(rs),
            "max_ratio": max(ratios) if ratios else None,
            "flat_share": round(sum(1 for r in rs
                                    if r["residual_qty_before"] == 0) / len(rs), 4),
            "sign_flips": flips,
        })
    maxes = [p["max_ratio"] for p in per if p["max_ratio"] is not None]
    return {
        "sequences": len(per),
        "decisions": len(rows),
        "median_decisions_per_sequence": (statistics.median(
            [p["decisions"] for p in per]) if per else None),
        "median_max_ratio": round(statistics.median(maxes), 6) if maxes else None,
        "median_flat_share": (round(statistics.median(
            [p["flat_share"] for p in per]), 4) if per else None),
        "median_sign_flips": (statistics.median(
            [p["sign_flips"] for p in per]) if per else None),
    }


# --------------------------------------------------------------------------
# the control that has to come before any conclusion: same unit of observation
# --------------------------------------------------------------------------

def like_for_like(r5f_rows: Sequence[Mapping[str, Any]],
                  l5_rows: Sequence[Mapping[str, Any]],
                  filled_keys: Sequence[Tuple[str, Any]]) -> dict:
    """L5's canonical tape has ONE ROW PER FILL. Ours has one row per
    DECISION, and most of our decisions never fill. Comparing the two
    directly counts our unfilled attempts as `flat` observations that L5's
    tape simply does not contain, so part of the headline gap is a unit
    mismatch rather than a behavioural difference. This restricts us to
    filled decisions -- L5's unit -- before anything is concluded.

    What survives the control is the real finding: imbalance accumulates
    with the NUMBER OF FILLS in a sequence, and that is where we differ."""
    keys = set(filled_keys)
    filled = [r for r in r5f_rows if (r["sequence"], r.get("idx")) in keys]

    def flat_share(rows):
        return (round(sum(1 for r in rows if r["residual_qty_before"] == 0)
                      / len(rows), 4) if rows else None)

    def fills_per_sequence(rows):
        seqs = collections.Counter(r["sequence"] for r in rows)
        return (round(sum(seqs.values()) / len(seqs), 2) if seqs else None)

    return {
        "r5f_all_decisions": {"n": len(r5f_rows),
                              "flat_share": flat_share(r5f_rows)},
        "r5f_filled_decisions_only": {"n": len(filled),
                                      "flat_share": flat_share(filled)},
        "l5_fills": {"n": len(l5_rows), "flat_share": flat_share(l5_rows)},
        "fills_per_sequence": {
            "l5": fills_per_sequence(l5_rows),
            "r5f": fills_per_sequence(filled),
        },
        "reading": ("a sequence's first fill is always flat, so the flat "
                    "share is close to 1/(fills per sequence). The gap that "
                    "survives the unit control is that arithmetic: L5 keeps "
                    "filling inside one window, we get a couple of fills per "
                    "market. That is an EXECUTION and market-selection "
                    "difference, not a dollar-scale one, and no state-key "
                    "change can manufacture fills we did not get"),
    }


def filled_decision_keys(rep: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """(sequence, idx) of every R5F decision whose order actually filled."""
    out: List[Tuple[str, Any]] = []
    for slug, m in (rep.get("per_market") or {}).items():
        for r in (m.get("rows") or []):
            if (_f((r.get("order") or {}).get("filled_qty")) or 0.0) > 0:
                out.append((slug, r.get("idx")))
    return out


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def _bucket_shares(rows: Sequence[Mapping[str, Any]], edges: Sequence[float],
                   live_bucket: bool = False) -> dict:
    """Bucket occupancy under mapping A (absolute shares, what the live key
    uses today) and mapping B (scale-normalized)."""
    a_labels, b_labels = [], []
    for r in rows:
        a = (r.get("live_bucket") if live_bucket
             else absolute_bucket(r["residual_qty_before"]))
        if a:
            a_labels.append(a)
        b = normalized_bucket(r.get("imbalance_ratio"), edges)
        if b:
            b_labels.append(b)
    return {
        "A_absolute": {"n": len(a_labels),
                       "share": {k: round(v, 4)
                                 for k, v in _shares(a_labels).items()}},
        "B_normalized": {"n": len(b_labels),
                         "share": {k: round(v, 4)
                                   for k, v in _shares(b_labels).items()}},
        "rows_without_scale": sum(1 for r in rows
                                  if r.get("imbalance_ratio") is None),
    }


def _total_variation(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    return round(0.5 * sum(abs(p.get(b, 0.0) - q.get(b, 0.0))
                           for b in BUCKET_LABELS), 4)


def build_report(log_paths: Sequence[str], canonical: str = CANONICAL) -> dict:
    from src.l5_sports_clock_study import build_ledger, input_manifest, expand_logs

    files = expand_logs(list(log_paths))
    rep = build_ledger(list(log_paths))
    l5 = attach_ratio(l5_decision_rows(canonical))
    ours = attach_ratio(live_decision_rows(rep))
    edges_spec = derive_ratio_edges(l5)
    edges = edges_spec["edges"]

    l5_b = _bucket_shares(l5, edges)
    our_b = _bucket_shares(ours, edges, live_bucket=True)
    tv_a = _total_variation(our_b["A_absolute"]["share"], l5_b["A_absolute"]["share"])
    tv_b = _total_variation(our_b["B_normalized"]["share"], l5_b["B_normalized"]["share"])

    return {
        "question": ("OWNER #167 5583906276 item 3: why is R5F flat on ~47% "
                     "of decisions while L5 is flat on ~1%, how much of that "
                     "is dollar scale versus a genuinely different inventory "
                     "trajectory, and can a scale-normalized representation "
                     "carry L5's meaning at $7.50?"),
        "owner_ref": "#167 comment 5583906276 item 3",
        "read_only": ("no network, no venue, no orders; the frozen model, "
                      "the frozen lookup and the live R5F shell are untouched "
                      "by this module"),
        "inputs": {"live_logs": input_manifest(files),
                   "canonical_csv": os.path.basename(canonical)},
        "edges_spec": edges_spec,
        "is_it_the_same_feature": agreement_on_l5(l5, edges),
        "bucket_occupancy": {
            "l5": l5_b, "r5f": our_b,
            "distance_to_l5": {
                "A_absolute_total_variation": tv_a,
                "B_normalized_total_variation": tv_b,
                "closed_by_normalization": round(tv_a - tv_b, 4),
                "reading": ("total variation distance between our bucket "
                            "occupancy and L5's, 0 = identical, 1 = disjoint. "
                            "The difference is how much of the gap is pure "
                            "representation; whatever remains is trajectory "
                            "or shell, and no rescaling removes it"),
            },
        },
        "same_unit_control": like_for_like(ours, l5, filled_decision_keys(rep)),
        "why_we_are_flat": flat_decomposition(rep),
        "reachability_under_the_shell": reachability(ours, l5, edges),
        "trajectory": {"l5": trajectory_stats(l5), "r5f": trajectory_stats(ours)},
        "denominators": {
            "l5_flat_share_here": ("measured over FILLS on the canonical "
                                   "per-trade tape"),
            "l5_flat_share_0_009_quoted_elsewhere": (
                "the 0.9% in the sports-clock study is over the FROZEN "
                "MODEL's base transition counts, a different and smaller "
                "denominator (trained transitions, not raw fills). Both are "
                "correct for their own denominator and they are not "
                "interchangeable; every figure in this report uses the "
                "canonical-tape denominator"),
        },
        "not_claimed": [
            "no promotion: this module measures, it does not change the live key",
            "no capital increase is proposed to reach any bucket",
            "the mini-window/local-regime clock is not used here at all",
            "behaviour fidelity, not P&L, is the criterion this feeds",
        ],
    }


def render_markdown(res: Mapping[str, Any]) -> str:
    occ = res["bucket_occupancy"]
    d = occ["distance_to_l5"]
    flat = res["why_we_are_flat"]
    reach = res["reachability_under_the_shell"]
    same = res["is_it_the_same_feature"]
    L = [
        "# L5 inventory translation — is our imbalance state a scale artefact?",
        "",
        f"_{res['owner_ref']}. {res['read_only']}._",
        "",
        "## Bucket occupancy, ours against L5's",
        "",
        "| bucket | L5 A (absolute) | R5F A (live key) | L5 B (normalized) | R5F B (normalized) |",
        "|---|---:|---:|---:|---:|",
    ]
    for b in BUCKET_LABELS:
        L.append(f"| {b} | {occ['l5']['A_absolute']['share'].get(b)} | "
                 f"{occ['r5f']['A_absolute']['share'].get(b)} | "
                 f"{occ['l5']['B_normalized']['share'].get(b)} | "
                 f"{occ['r5f']['B_normalized']['share'].get(b)} |")
    L += ["",
          f"- distance to L5 under the live mapping A: **{d['A_absolute_total_variation']}**",
          f"- distance to L5 under the normalized mapping B: **{d['B_normalized_total_variation']}**",
          f"- closed by normalization: **{d['closed_by_normalization']}**",
          "", f"_{d['reading']}._", "",
          "## Is B the same feature as A, or a different one?", "",
          f"Per-row agreement on L5's own rows: **{same['per_row_agreement']}** "
          f"over {same['n_rows']} rows.", "", f"_{same['reading']}._", "",
          "## Why R5F is flat", "",
          f"{flat['n_flat']} of {flat['n_decisions']} decisions "
          f"({flat['flat_share']}) key `flat`. Split by cause:", ""]
    for k, v in flat["causes"].items():
        L.append(f"- `{k}`: {v} ({flat['cause_share_of_flat'].get(k)} of flat)")
    lfl = res["same_unit_control"]
    L += ["", f"_{flat['reading']}._", "",
          "## The unit control, before any conclusion", "",
          "| | n | flat share |", "|---|---:|---:|",
          f"| R5F, all decisions | {lfl['r5f_all_decisions']['n']} | "
          f"{lfl['r5f_all_decisions']['flat_share']} |",
          f"| R5F, filled decisions only (L5's unit) | "
          f"{lfl['r5f_filled_decisions_only']['n']} | "
          f"{lfl['r5f_filled_decisions_only']['flat_share']} |",
          f"| L5, fills | {lfl['l5_fills']['n']} | {lfl['l5_fills']['flat_share']} |",
          "",
          f"Fills per sequence — L5 **{lfl['fills_per_sequence']['l5']}**, "
          f"R5F **{lfl['fills_per_sequence']['r5f']}**.",
          "", f"_{lfl['reading']}._", "", "## What the shell permits", ""]
    if reach.get("computable"):
        L += [f"- our median capital scale: ${reach['r5f_median_capital_scale_usd']}",
              f"- per-market at-risk cap: ${reach['per_market_cap_usd']}",
              f"- highest imbalance ratio the shell permits: "
              f"**{reach['max_reachable_ratio']}** → bucket "
              f"**{reach['highest_reachable_bucket']}**",
              f"- highest we actually reached: {reach['observed_max_ratio']}",
              f"- L5 decisions above that ceiling: "
              f"{reach['l5_decisions_above_our_ceiling']} "
              f"({reach['l5_share_above_our_ceiling']})",
              "", f"_{reach['reading']}._"]
    else:
        L.append(f"- not computable: {reach.get('reason')}")
    L += ["", "## Inventory trajectory, not just the marginal", "",
          "| | L5 | R5F |", "|---|---:|---:|"]
    for k in ("sequences", "decisions", "median_decisions_per_sequence",
              "median_max_ratio", "median_flat_share", "median_sign_flips"):
        L.append(f"| {k} | {res['trajectory']['l5'].get(k)} | "
                 f"{res['trajectory']['r5f'].get(k)} |")
    L += ["", "## Not claimed", ""] + [f"- {s}" for s in res["not_claimed"]] + [""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="l5_inventory_translation",
        description=("Decompose the R5F/L5 inventory-state gap and derive a "
                     "scale-normalized imbalance feature from L5's tape "
                     "alone. Read-only; nothing here trades."))
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--canonical", default=CANONICAL)
    a = ap.parse_args(argv)

    res = build_report(a.logs, canonical=a.canonical)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, JSON_NAME), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
        fh.write("\n")
    md = render_markdown(res)
    with open(os.path.join(a.out, MD_NAME), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
