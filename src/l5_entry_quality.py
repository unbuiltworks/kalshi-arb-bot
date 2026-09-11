"""Which first legs should we never have taken?

OWNER 2026-09-11, P0: "If 75% of post-imbalance COMPLETE opportunities are
already ceiling-blocked, then many bad outcomes are effectively created on the
first fill... The question becomes: what first legs should we never have
taken?"

Run against `analysis/l5/ENTRY_QUALITY_PREREGISTRATION.md`, committed at
c2bcf7d4 before this module existed.

WHAT MAKES THIS STUDY DIFFERENT FROM THE REPAIRING-EXTEND ONE
-------------------------------------------------------------
Its counterfactual is EXACT. Refusing an entry deletes the episode, so both
the numerator and the denominator of residual incidence move deterministically
-- no fill is simulated and no trajectory modelled. The repairing-EXTEND study
could not answer the incidence question at all for exactly the opposite
reason: turning a stranded market into a paired one requires inventing fills
that never happened.

THE LABEL CARRIES NO SETTLEMENT HINDSIGHT
-----------------------------------------
STRANDED means residual != 0 when the episode closed. That is known without
the resolution side, without realized_usd, and without the final settlement
row. Those three are never read here, and a test walks this module's AST for
field READS to keep it that way -- checking for the WORDS fails on the
sentence you are reading.

THE PRIMARY FEATURE, AND WHY IT IS READABLE
-------------------------------------------
`complement_preflight` on an OPEN decision carries `chosen_px` (what we paid)
and `opp_ask` (what the other side cost at that instant). Their sum is the
pair cost implied at entry. It is on 91.7% of OPEN decisions, unlike
`opp_ask_authenticated` at 1.1%, which the last fill study had to report
UNAVAILABLE. Coverage is measured and reported rather than assumed.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import math
from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional

from src.l5_episode_ledger import iter_campaign_rows
from src.l5_residual_reconstruction import pair_decisions_to_orders

# A fill is a fill whether or not it filled the whole order.
FILLED = ("filled", "partial", "partially_filled")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                   # noqa: BLE001
        return None


def _is_filled(order: Optional[Mapping[str, Any]]) -> bool:
    if not order:
        return False
    q = _d(order.get("filled_qty"))
    return q is not None and q > 0


def sessions_by_market(paths: Iterable[str]) -> dict:
    """Every session_end row, grouped by slug, in timestamp order."""
    by: dict = {}
    for ts, obj in iter_campaign_rows(paths):
        if obj.get("kind") != "session_end":
            continue
        slug = obj.get("slug")
        if not slug:
            continue
        by.setdefault(slug, []).append((ts, obj))
    for slug in by:
        by[slug].sort(key=lambda p: p[0])
    return by


def entry_row(slug: str, sessions: list) -> Optional[dict]:
    """The state at the first leg that FILLED, and what the market became.

    The first fill is the one that created inventory, so a market whose
    earliest observed fill is a `complete` is one whose opening session we do
    not have. That is a coverage hole, not an entry to study, and it is
    reported as `excluded_no_open` rather than silently treated as an entry."""
    first = None
    for ts, s in sessions:
        for d, order in pair_decisions_to_orders(s):
            if not _is_filled(order):
                continue
            first = (ts, d, order)
            break
        if first:
            break
    if not first:
        return None

    ts, dec, order = first
    relation = str(order.get("relation") or dec.get("acted") or "")
    cp = dec.get("complement_preflight")
    cp = cp if isinstance(cp, Mapping) else {}
    chosen = _d(cp.get("chosen_px"))
    opp = _d(cp.get("opp_ask"))
    wire = _d(cp.get("wire_px"))
    inv = last_inventory(sessions)
    resid = _d((inv or {}).get("residual"))

    return {
        "slug": slug,
        "first_fill_utc": dec.get("t_utc") or ts,
        # ---- entry state, all knowable at the decision
        "relation": relation,
        "is_open": relation == "open",
        "side": order.get("side"),
        "entry_px": str(_d(order.get("px")) if order.get("px") else None),
        "chosen_px": str(chosen) if chosen is not None else None,
        "opp_ask": str(opp) if opp is not None else None,
        "entry_pair": (str(chosen + opp)
                       if chosen is not None and opp is not None else None),
        "ticks_from_wire": (float(chosen - wire) if chosen is not None
                            and wire is not None else None),
        "regime": cp.get("regime"),
        "state": dec.get("state"),
        "state_n": dec.get("state_n"),
        "persist_extend_p": dec.get("persist_extend_p"),
        "switch_complete_p": dec.get("switch_complete_p"),
        "planned_usd": ((dec.get("planned_safe_size") or {}).get("usd")),
        "filled_qty": order.get("filled_qty"),
        "category": category_of(slug),
        # ---- the label, and nothing downstream of it
        "residual_units": str(resid) if resid is not None else None,
        "label": (None if resid is None
                  else ("PAIRED" if resid == 0 else "STRANDED")),
    }


def last_inventory(sessions: list) -> Optional[dict]:
    for ts, s in reversed(sessions):
        inv = s.get("inventory") or s.get("resumed_inventory")
        if isinstance(inv, Mapping) and inv.get("up") is not None:
            return dict(inv)
    return None


def category_of(slug: str) -> str:
    """`aec-atp-x-y-date` -> `aec-atp`. The venue's own prefix grammar."""
    parts = str(slug).split("-")
    return "-".join(parts[:2]) if len(parts) > 2 else str(slug)


# ------------------------------------------------------------ the sample
def build(paths: Iterable[str]) -> dict:
    by = sessions_by_market(paths)
    rows, no_fill, no_label = [], 0, 0
    for slug, sessions in by.items():
        row = entry_row(slug, sessions)
        if row is None:
            no_fill += 1
            continue
        if row["label"] is None:
            no_label += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r["first_fill_utc"]))

    opens = [r for r in rows if r["is_open"]]
    with_pair = [r for r in opens if r["entry_pair"] is not None]
    return {
        "coverage": {
            "markets_with_sessions": len(by),
            "markets_with_a_fill": len(rows) + no_label,
            "excluded_no_fill": no_fill,
            "excluded_no_inventory": no_label,
            "filled_episodes_labelled": len(rows),
            "of_which_first_fill_was_open": len(opens),
            "excluded_no_open": len(rows) - len(opens),
            "with_entry_pair_computable": len(with_pair),
            "entry_pair_coverage": (round(len(with_pair) / len(opens), 4)
                                    if opens else None),
        },
        "rows": rows,
        "sample": with_pair,
    }


# ------------------------------------------------------------- the split
def split(sample: list) -> dict:
    """Chronological by first fill: earliest two thirds TRAIN, latest third
    HOLDOUT. Pre-registered §7 and not negotiable after the fact."""
    n = len(sample)
    cut = (n * 2) // 3
    return {"train": sample[:cut], "holdout": sample[cut:], "cut_index": cut}


# ------------------------------------------------------------- measuring
def incidence(rows: list) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for r in rows if r["label"] == "STRANDED") / len(rows), 4)


def describe(rows: list, field: str = "entry_pair") -> dict:
    """The feature's distribution, by label. No test statistic that assumes
    independence across decisions: these are one row per market already, so
    the unit IS independent, but n is small and the interval says so."""
    out = {}
    for lab in ("PAIRED", "STRANDED"):
        vals = sorted(float(r[field]) for r in rows
                      if r["label"] == lab and r.get(field) is not None)
        out[lab] = _summary(vals)
    return out


def _summary(vals: list) -> dict:
    if not vals:
        return {"n": 0}
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 1 else 0.0
    return {
        "n": n, "mean": round(mean, 4),
        "ci95": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        "min": round(vals[0], 4), "p25": round(_q(vals, .25), 4),
        "median": round(_q(vals, .50), 4), "p75": round(_q(vals, .75), 4),
        "max": round(vals[-1], 4),
        "share_at_or_above_par": round(
            sum(1 for v in vals if v >= 1.0) / n, 4),
    }


def _q(vals: list, q: float) -> float:
    if not vals:
        return float("nan")
    i = (len(vals) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return vals[lo] if lo == hi else vals[lo] + (vals[hi] - vals[lo]) * (i - lo)


def auc(rows: list, field: str = "entry_pair") -> Optional[float]:
    """P(a stranded entry scores higher than a paired one), ties at half.
    0.5 is no separation, which is a pre-registered falsifier and not a
    disappointing result to be explained away."""
    pos = [float(r[field]) for r in rows
           if r["label"] == "STRANDED" and r.get(field) is not None]
    neg = [float(r[field]) for r in rows
           if r["label"] == "PAIRED" and r.get(field) is not None]
    if not pos or not neg:
        return None
    wins = sum((1.0 if p > q else 0.5 if p == q else 0.0)
               for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 4)


# ------------------------------------------------------- the rule, θ only
PAIRED_MEAN = D("0.2851")       # measured, R5F settled cohort
STRANDED_MEAN = D("-1.9120")    # measured, R5F settled cohort


def apply_threshold(rows: list, theta: float) -> dict:
    """Refuse the entry when entry_pair > θ. The retained set is what the
    engine would have traded; the discarded set is what it would not.

    Both counts are reported because a filter that retains four episodes has
    not solved incidence, it has stopped trading -- pre-registration §8-2."""
    keep = [r for r in rows if float(r["entry_pair"]) <= theta]
    drop = [r for r in rows if float(r["entry_pair"]) > theta]
    kept_paired = sum(1 for r in keep if r["label"] == "PAIRED")
    kept_stranded = len(keep) - kept_paired
    lost_paired = sum(1 for r in drop if r["label"] == "PAIRED")
    avoided = len(drop) - lost_paired
    all_paired = kept_paired + lost_paired
    delta = (D(kept_stranded - (len(rows) - all_paired)) * STRANDED_MEAN
             + D(-lost_paired) * PAIRED_MEAN)
    return {
        "theta": theta,
        "retained": len(keep), "discarded": len(drop),
        "retained_paired": kept_paired, "retained_stranded": kept_stranded,
        "paired_lost": lost_paired, "stranded_avoided": avoided,
        "paired_kept_share": (round(kept_paired / all_paired, 4)
                              if all_paired else None),
        "incidence": incidence(keep),
        # priced at the measured per-market means, which are estimates with
        # wide intervals -- a direction, never a forecast
        "delta_usd_indicative": str(delta.quantize(D("0.0001"))),
    }


def sweep(rows: list, thetas: Iterable[float]) -> list:
    return [apply_threshold(rows, t) for t in thetas]


def study(paths: Iterable[str], thetas: Optional[list] = None,
          open_holdout: bool = False) -> dict:
    built = build(paths)
    sample = built["sample"]
    sp = split(sample)
    thetas = thetas or [round(0.90 + 0.01 * i, 2) for i in range(16)]
    res = {
        "question": ("of the episodes whose first leg filled, does the state "
                     "at entry separate paired from stranded?"),
        "prereg": "analysis/l5/ENTRY_QUALITY_PREREGISTRATION.md @ c2bcf7d4",
        "coverage": built["coverage"],
        "baseline_incidence_all": incidence(sample),
        "train": {
            "n": len(sp["train"]),
            "incidence": incidence(sp["train"]),
            "entry_pair": describe(sp["train"]),
            "auc_entry_pair": auc(sp["train"]),
            "sweep": sweep(sp["train"], thetas),
        },
        "holdout": {"n": len(sp["holdout"]), "status": "SEALED"},
        "labels": {
            "counterfactual": ("exact: refusing an entry deletes the episode. "
                               "No fill simulated, no trajectory modelled."),
            "not_modelled": ("freed concurrency. A refused entry releases the "
                             "market slot; what would have been taken instead "
                             "is out of scope, and throughput is not a "
                             "break-even lever."),
            "delta_usd": ("indicative only: per-market means with wide "
                          "intervals, never a realized figure"),
        },
    }
    if open_holdout:
        res["holdout"] = {
            "n": len(sp["holdout"]),
            "status": "READ",
            "incidence": incidence(sp["holdout"]),
            "entry_pair": describe(sp["holdout"]),
            "auc_entry_pair": auc(sp["holdout"]),
            "sweep": sweep(sp["holdout"], thetas),
        }
    return res


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--open-holdout", action="store_true",
                    help="read the sealed holdout. Once, per §7.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    paths: list = []
    for pat in args.logs:
        paths.extend(sorted(_glob.glob(pat)) or [pat])
    res = study(paths, open_holdout=args.open_holdout)
    print(json.dumps(res, indent=2, default=str))
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(json.dumps(res, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
