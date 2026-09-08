"""Mapping B: a shadow-only translation of sports state into the frozen L5
key, and the pre-registered gate that alone could ever promote it.
(OWNER #167 comment 5583906276 items 1, 2, 4 and 5.)

Today every live sports decision keys `tte>10m`, 635 of 635, because
SPORTS_TTE_DEFAULT_S = 1e9 stands in for a settlement clock sports does not
serve. The frozen model is therefore asked about a bucket that, in L5's own
history, MEANS "the opening third of a 15-minute crypto window" -- while our
decision sits hours from resolution. That is a translation defect, not a
model defect, and this module fixes it in shadow only.

Two dimensions are translated. Neither invents a bucket:

  TIME       sport-native event progress (the venue's own period/score
             tokens, via src.pmus_event_progress.proximity) -> remaining
             fraction of the event -> the tte bucket L5 OCCUPIES at that
             remaining fraction of its own window. The fraction->bucket map
             is measured on L5's tape alone.

  INVENTORY  the scale-normalized imbalance of src.l5_inventory_translation,
             whose edges are likewise derived from L5's tape alone.

  PRICE      unchanged. The price band already transfers.

The bucket LABELS stay the frozen model's own, so mapping B keys the SAME
frozen model with the same identity -- only the assignment rule changes.
Nothing here is a literal seconds-to-settlement claim: a translated
`tte2-5m` means "as far through its event as L5 was with 2-5 minutes left",
and every row says so.

WHAT THIS MODULE MAY NOT DO. It may not change the live key, the live
shell, any cap, gate or band; it may not promote itself; and it may not
tune a threshold after seeing a result. The promotion gate below is frozen
with its own hash BEFORE any forward cohort is read, and the historical
comparison this module also runs is DESCRIPTIVE ONLY -- it re-reads
decisions already analysed and can never satisfy an out-of-sample gate.

The mini-window / local-regime clock is NOT used. Per the owner it stays a
logged candidate feature; the evidence did not establish it.

Read-only research. No network, no venue, nothing here trades.

    python3 -m src.l5_state_translation --logs dir --out analysis/l5
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.polymarket_l5.action_model import TTE_BUCKETS, _bucket
from src import l5_inventory_translation as invt

OUT_DIR = os.path.join("analysis", "l5")
CANONICAL = os.path.join(OUT_DIR, "l5_canonical_sequence.csv.gz")
MODEL_V2 = os.path.join(OUT_DIR, "l5_action_model_v2.json")
JSON_NAME = "l5_state_translation.json"
MD_NAME = "L5_STATE_TRANSLATION.md"

TTE_LABELS: Tuple[str, ...] = tuple(b for _, b in TTE_BUCKETS)
REMAINING_BINS = 20            # resolution of the fraction -> bucket map

try:
    from src.pmus_event_progress import proximity as venue_proximity
except Exception:                                       # noqa: BLE001
    venue_proximity = None                              # type: ignore


# --------------------------------------------------------------------------
# the time translation, measured on L5's tape alone
# --------------------------------------------------------------------------

def l5_remaining_fraction_map(csv_path: str = CANONICAL) -> dict:
    """For each of L5's own trades: what fraction of its window remained, and
    which tte bucket that trade keyed. Pooled over every window, so the map
    carries L5's own mix of 5- and 15-minute windows rather than a guess.

    Returns the modal tte bucket per remaining-fraction bin, plus the full
    distribution per bin so a thin or ambiguous bin is visible rather than
    silently modal."""
    bins: Dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    n_rows = n_used = 0
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            n_rows += 1
            tte = invt._f(r.get("seconds_to_settlement"))
            dur = invt._f(r.get("duration_minutes"))
            if tte is None or not dur or dur <= 0:
                continue
            span = dur * 60.0
            frac = tte / span
            if frac < 0.0 or frac > 1.0:
                # trades after settlement (negative tte) and any window whose
                # span is misreported are dropped, not clamped: clamping would
                # invent an observation at the boundary
                continue
            n_used += 1
            idx = min(int(frac * REMAINING_BINS), REMAINING_BINS - 1)
            bins[idx][_bucket(tte, TTE_BUCKETS)] += 1
    per_bin = {}
    for i in range(REMAINING_BINS):
        c = bins.get(i) or collections.Counter()
        n = sum(c.values())
        per_bin[str(i)] = {
            "remaining_fraction_from": round(i / REMAINING_BINS, 3),
            "remaining_fraction_to": round((i + 1) / REMAINING_BINS, 3),
            "n": n,
            "modal_tte_bucket": (c.most_common(1)[0][0] if n else None),
            "modal_purity": (round(c.most_common(1)[0][1] / n, 4) if n else None),
            "distribution": dict(sorted(c.items())),
        }
    spec = {
        "id": "L5_REMAINING_FRACTION_TO_TTE_v1",
        "derived_from": "l5_canonical_sequence.csv.gz, L5 rows only",
        "rule": ("pool L5's own trades by the fraction of their window still "
                 "remaining and take the tte bucket L5 actually keyed there; "
                 "sports progress is then read through L5's own clock"),
        "bins": REMAINING_BINS,
        "n_rows": n_rows, "n_used": n_used,
        "per_bin": per_bin,
        "not_a_claim": ("a translated bucket is NOT a seconds-to-settlement "
                        "claim about a sports market; it says 'as far through "
                        "its event as L5 was at that point of its window'"),
    }
    spec["sha256_16"] = hashlib.sha256(
        json.dumps({k: v for k, v in spec.items() if k != "sha256_16"},
                   sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return spec


def translate_tte(proximity: Optional[float], frac_map: Mapping[str, Any]
                  ) -> Tuple[Optional[str], str]:
    """(translated tte bucket, basis). `proximity` is the fraction of the
    EVENT elapsed, as the venue's own period tokens report it. Missing stays
    missing: an unknown event position is never translated into a bucket."""
    if proximity is None:
        return None, "no_event_progress_served"
    remaining = max(0.0, min(1.0, 1.0 - float(proximity)))
    idx = min(int(remaining * REMAINING_BINS), REMAINING_BINS - 1)
    row = (frac_map.get("per_bin") or {}).get(str(idx)) or {}
    bucket = row.get("modal_tte_bucket")
    if bucket is None:
        return None, f"l5_has_no_trades_at_remaining_fraction_bin_{idx}"
    return bucket, (f"event_progress->remaining={round(remaining, 3)}"
                    f"->l5_bin_{idx}(purity={row.get('modal_purity')})")


# --------------------------------------------------------------------------
# the pre-registered promotion gate -- frozen BEFORE any forward result
# --------------------------------------------------------------------------

PROMOTION_GATE: Dict[str, Any] = {
    "id": "L5_STATE_TRANSLATION_v1_PROMOTION_GATE",
    "owner_ref": "#167 comment 5583906276 item 5",
    "frozen_before": ("any forward paired-shadow observation is read. The "
                      "historical comparison in this module is descriptive "
                      "and explicitly cannot satisfy this gate."),
    "criterion": ("behavioural fidelity only. Realized or counterfactual P&L "
                  "is reported separately and is NOT an input to promotion, "
                  "in either direction."),
    "sample_minimums": {
        "forward_decisions_with_event_progress": 400,
        "distinct_markets": 40,
        "distinct_leagues": 4,
        "forward_fills": 60,
        "note": ("all four must be met on decisions observed AFTER this gate "
                 "was frozen; in-sample decisions do not count toward any of "
                 "them"),
    },
    "must_improve": {
        "state_support": ("median state_n under B strictly greater than under "
                          "A on the same decisions"),
        "off_distribution_rate": ("B's off-distribution rate at least 5 "
                                  "percentage points below A's"),
        "relation_distribution_distance": (
            "total variation between B's relation distribution and L5's "
            "canonical relation distribution at least 0.05 below A's, on the "
            "same decisions"),
    },
    "must_not_worsen": {
        "off_distribution_ceiling": ("B's off-distribution rate must not "
                                     "exceed A's under any split by league"),
        "no_new_unknown": ("decisions B cannot key at all (no event progress "
                           "served) must stay below 25%, and are counted as "
                           "failures of B, never dropped"),
    },
    "rollback": {
        "trigger": ("if promoted and any of: off-distribution rate rises "
                    "above the pre-promotion A baseline for 2 consecutive "
                    "hours; a league's event-progress coverage falls below "
                    "50%; or the translated bucket becomes degenerate "
                    "(one bucket >90% of decisions for 4 hours)"),
        "action": ("revert to mapping A immediately, no approval needed to "
                   "revert, and report the trigger"),
    },
    "explicitly_not_a_criterion": [
        "realized P&L", "counterfactual P&L", "fill rate",
        "the mini-window / local-regime clock, which stays a logged "
        "candidate and is not part of mapping B",
    ],
}
PROMOTION_GATE["sha256_16"] = hashlib.sha256(
    json.dumps(PROMOTION_GATE, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# the frozen model, read only
# --------------------------------------------------------------------------

def load_model(path: str = MODEL_V2) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    m = json.loads(raw)
    m["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    return m


def lookup(model: Mapping[str, Any], key: str) -> dict:
    """What the FROZEN model says at a key. Never mutated, never smoothed,
    and a key it has never seen returns support 0 rather than a guess."""
    acts = (model.get("base") or {}).get(key) or {}
    n = sum(acts.values())
    rel: collections.Counter = collections.Counter()
    for action, count in acts.items():
        rel[action.split("|")[0]] += count
    top = rel.most_common(1)[0][0] if rel else None
    return {"key": key, "state_n": n, "off_distribution": n == 0,
            "top_relation": top,
            "relation_p": {k: round(v / n, 4) for k, v in sorted(rel.items())}
                          if n else {}}


def canonical_relation_distribution(model: Mapping[str, Any]) -> Dict[str, float]:
    """L5's own relation mix over the whole frozen model -- the target the
    two mappings are scored against."""
    rel: collections.Counter = collections.Counter()
    for acts in (model.get("base") or {}).values():
        for action, count in acts.items():
            rel[action.split("|")[0]] += count
    n = sum(rel.values()) or 1
    return {k: v / n for k, v in rel.items()}


def _tv(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    keys = set(p) | set(q)
    return round(0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys), 4)


# --------------------------------------------------------------------------
# A vs B on the same decisions
# --------------------------------------------------------------------------

def paired_rows(rep: Mapping[str, Any], frac_map: Mapping[str, Any],
                edges: Sequence[float], model: Mapping[str, Any]) -> List[dict]:
    """One row per live decision carrying BOTH keys and both lookups. The A
    key is taken verbatim from the log, never recomputed, so mapping A cannot
    be quietly improved while scoring it."""
    from src.l5_sports_clock_study import selection_progress, latest_at, iso_epoch

    prog = selection_progress(rep.get("_selections") or [])
    ours = invt.attach_ratio(invt.live_decision_rows(rep))
    ratio_by = {(r["sequence"], r.get("idx")): r for r in ours}

    out: List[dict] = []
    for slug, m in (rep.get("per_market") or {}).items():
        for r in sorted(m.get("rows") or [], key=lambda x: x.get("idx") or 0):
            key_a = str(r.get("state") or "")
            if key_a.count("|") != 2:
                continue
            price_b, tte_a, inv_a = key_a.split("|")
            t = iso_epoch(r.get("t_utc"))
            prg = latest_at(prog.get(slug) or [], t) if t is not None else None
            prox = (prg or {}).get("proximity")
            tte_b, tte_basis = translate_tte(prox, frac_map)
            ir = ratio_by.get((slug, r.get("idx")))
            inv_b = invt.normalized_bucket(
                (ir or {}).get("imbalance_ratio"), edges)
            key_b = (f"{price_b}|{tte_b}|{inv_b}"
                     if tte_b and inv_b else None)
            out.append({
                "slug": slug, "idx": r.get("idx"), "t_utc": r.get("t_utc"),
                "key_a": key_a, "key_b": key_b,
                "tte_a": tte_a, "tte_b": tte_b, "tte_basis": tte_basis,
                "inv_a": inv_a, "inv_b": inv_b,
                "game_period": (prg or {}).get("period"),
                "event_proximity": prox,
                "imbalance_ratio": (ir or {}).get("imbalance_ratio"),
                "acted": r.get("acted"),
                "l5_sampled": r.get("l5_sampled"),
                "A": lookup(model, key_a),
                "B": lookup(model, key_b) if key_b else
                     {"key": None, "state_n": 0, "off_distribution": True,
                      "top_relation": None, "relation_p": {},
                      "unkeyable": True},
            })
    return out


def score_mapping(rows: Sequence[Mapping[str, Any]], side: str,
                  canonical: Mapping[str, float]) -> dict:
    """Support, off-distribution rate and relation-distribution distance for
    one mapping. Rows a mapping cannot key at all count AGAINST it -- they
    are its failures, not rows to drop."""
    ns = sorted(r[side]["state_n"] for r in rows)
    off = sum(1 for r in rows if r[side]["off_distribution"])
    unkeyable = sum(1 for r in rows if r[side].get("unkeyable"))
    rel: collections.Counter = collections.Counter()
    for r in rows:
        for k, p in (r[side]["relation_p"] or {}).items():
            rel[k] += p
    tot = sum(rel.values()) or 1.0
    mix = {k: v / tot for k, v in rel.items()}
    n = len(rows) or 1
    return {
        "decisions": len(rows),
        "median_state_n": (ns[len(ns) // 2] if ns else None),
        "off_distribution": off,
        "off_distribution_rate": round(off / n, 4),
        "unkeyable": unkeyable,
        "unkeyable_rate": round(unkeyable / n, 4),
        "relation_mix": {k: round(v, 4) for k, v in sorted(mix.items())},
        "relation_distance_to_l5": _tv(mix, canonical),
    }


def historical_ab(rep: Mapping[str, Any], frac_map: Mapping[str, Any],
                  edges: Sequence[float], model: Mapping[str, Any]) -> dict:
    rows = paired_rows(rep, frac_map, edges, model)
    canonical = canonical_relation_distribution(model)
    a = score_mapping(rows, "A", canonical)
    b = score_mapping(rows, "B", canonical)
    tte_b = collections.Counter(r["tte_b"] or "<unkeyable>" for r in rows)
    return {
        "status": "DESCRIPTIVE_ONLY",
        "why": ("these decisions were already observed and analysed. They "
                "show what the translation DOES; they cannot promote it, and "
                "the pre-registered gate requires decisions observed after "
                "the gate was frozen"),
        "n_rows": len(rows),
        "event_progress_coverage": round(
            sum(1 for r in rows if r["event_proximity"] is not None)
            / (len(rows) or 1), 4),
        "A": a, "B": b,
        "would_meet_must_improve_if_this_were_forward": {
            "state_support": (a["median_state_n"] is not None
                              and b["median_state_n"] is not None
                              and b["median_state_n"] > a["median_state_n"]),
            "off_distribution_rate": (a["off_distribution_rate"]
                                      - b["off_distribution_rate"]) >= 0.05,
            "relation_distribution_distance": (a["relation_distance_to_l5"]
                                               - b["relation_distance_to_l5"]) >= 0.05,
        },
        "translated_tte_occupancy": dict(sorted(tte_b.items())),
        "live_tte_occupancy": dict(sorted(
            collections.Counter(r["tte_a"] for r in rows).items())),
    }


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def build_report(log_paths: Sequence[str], canonical_csv: str = CANONICAL,
                 model_path: str = MODEL_V2) -> dict:
    from src.l5_sports_clock_study import build_ledger, input_manifest, expand_logs

    files = expand_logs(list(log_paths))
    rep = build_ledger(list(log_paths))
    model = load_model(model_path)
    frac_map = l5_remaining_fraction_map(canonical_csv)
    l5_rows = invt.attach_ratio(invt.l5_decision_rows(canonical_csv))
    edges_spec = invt.derive_ratio_edges(l5_rows)
    return {
        "question": ("OWNER #167 5583906276 items 1/2/4/5: what is the "
                     "defensible L5-equivalent state for a sports decision, "
                     "and does translating it change what the frozen model "
                     "is asked?"),
        "owner_ref": "#167 comment 5583906276",
        "read_only": ("no network, no venue, no orders. The live key, the "
                      "live shell, every cap, gate and band, the frozen "
                      "model and the frozen lookup are untouched."),
        "model_identity": {"file": os.path.basename(model_path),
                           "sha256_16": model["_sha256_16"],
                           "n_states": len(model.get("base") or {})},
        "inputs": {"live_logs": input_manifest(files),
                   "canonical_csv": os.path.basename(canonical_csv)},
        "time_translation": frac_map,
        "inventory_translation": edges_spec,
        "promotion_gate": PROMOTION_GATE,
        "historical_ab": historical_ab(rep, frac_map, edges_spec["edges"], model),
        "mini_window": ("not used in mapping B. Per the owner it remains a "
                        "logged candidate feature; the evidence did not "
                        "establish it as a superior clock."),
    }


def render_markdown(res: Mapping[str, Any]) -> str:
    ab = res["historical_ab"]
    L = [
        "# Mapping B — translating sports state into the frozen L5 key",
        "",
        f"_{res['owner_ref']}. {res['read_only']}_",
        "",
        f"Frozen model `{res['model_identity']['file']}` "
        f"sha `{res['model_identity']['sha256_16']}`, "
        f"{res['model_identity']['n_states']} states — unchanged, and keyed "
        f"by both mappings.",
        "",
        "## The two translations, each derived from L5's tape alone",
        "",
        f"- time: `{res['time_translation']['id']}` "
        f"sha `{res['time_translation']['sha256_16']}` — "
        f"{res['time_translation']['n_used']} L5 trades binned by remaining "
        f"fraction of their own window",
        f"- inventory: `{res['inventory_translation']['id']}` "
        f"sha `{res['inventory_translation']['sha256_16']}`",
        f"- price: unchanged",
        "",
        f"_{res['time_translation']['not_a_claim']}._",
        "",
        "## Pre-registered promotion gate",
        "",
        f"`{res['promotion_gate']['id']}` sha "
        f"`{res['promotion_gate']['sha256_16']}` — frozen "
        f"{res['promotion_gate']['frozen_before']}",
        "",
        f"Minimums: "
        + ", ".join(f"{k} {v}" for k, v in
                    res['promotion_gate']['sample_minimums'].items()
                    if k != "note"),
        "",
        f"## Historical A/B — {ab['status']}",
        "",
        f"_{ab['why']}._",
        "",
        f"{ab['n_rows']} decisions, event-progress coverage "
        f"{ab['event_progress_coverage']}.",
        "",
        "| | A (live mapping) | B (translated) |",
        "|---|---:|---:|",
        f"| median state support | {ab['A']['median_state_n']} | {ab['B']['median_state_n']} |",
        f"| off-distribution rate | {ab['A']['off_distribution_rate']} | {ab['B']['off_distribution_rate']} |",
        f"| unkeyable | {ab['A']['unkeyable_rate']} | {ab['B']['unkeyable_rate']} |",
        f"| relation distance to L5 | {ab['A']['relation_distance_to_l5']} | {ab['B']['relation_distance_to_l5']} |",
        "",
        "Time-bucket occupancy — live: "
        + json.dumps(ab["live_tte_occupancy"])
        + "; translated: " + json.dumps(ab["translated_tte_occupancy"]),
        "",
        f"_{res['mini_window']}_",
        "",
    ]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="l5_state_translation",
        description=("Shadow-only translation of sports state into the frozen "
                     "L5 key, with a pre-registered promotion gate. "
                     "Read-only; nothing here trades."))
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--canonical", default=CANONICAL)
    ap.add_argument("--model", default=MODEL_V2)
    a = ap.parse_args(argv)
    res = build_report(a.logs, canonical_csv=a.canonical, model_path=a.model)
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
