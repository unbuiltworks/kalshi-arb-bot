"""Persistence-v2 shadow arm — last_rel/last_size-conditioned L5 policy.

Issue #167 comment 5519823659. VERIFIED code-grounded first: the frozen
canonical `analysis/l5/l5_action_model.json` conditions ONLY on the v1
base state (price band x TTE x imbalance) — `last_rel` was emitted
alongside but never conditioned on, while the held-out review showed
`last_rel` is the dominant next-action feature (~68.9% relation top-1)
and base+last_rel+last_size the best exact variant. Per the directive
the frozen v1 is NOT overwritten; this module adds a SECOND, clearly
labeled arm:

* Artifact `analysis/l5/l5_action_model_v2.json`, built deterministically
  by `build_artifact()` from the SAME canonical sequence through the SAME
  transition/feature code as the held-out review
  (`action_model_v2.build_transitions` — train+holdout pooled, since the
  formulation was already validated out-of-sample there), with the exact
  review shrinkage: P(a|full) backs off full -> v1 base state -> marginal
  with ALPHA=10, so rare rich states cannot memorize.
* `v2_dist(base_state, last_rel, last_size)` returns that shrunk
  distribution plus provenance (state n, backed_off flag).
* `proposal(...)` converts the v2 recommended relation into a THEORETICAL
  action proposal — side, maker price zone, q=1 sports size (historical
  modal size bucket reported alongside), and next reevaluation timing
  from the historical cadence (modal gap bucket per state) — using only
  existing L5 historical evidence; no invented sports handicapping.

Everything here is read-only research/shadow: no network import, no
order path, nothing armed; all outputs THEORETICAL.

    python3 -m src.pmus_l5_persistence_v2      # (re)build the artifact
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

ARTIFACT = (Path(__file__).resolve().parents[1]
            / "analysis/l5/l5_action_model_v2.json")  # cwd-independent
ALPHA = 10.0
GAP_SECONDS = {"gap<2s": 2.0, "gap2-10": 10.0, "gap10-30": 30.0,
               "gap>30": 60.0}
# OWNER 5558477213 A/B + 5558859447 #5: the canonical state lookup shared
# by the Gemini shadow and the live probe -- outcome-price key (never
# mirrored to the YES scale), pre-fill = OPEN with off-distribution meaning
# the BASE state is absent, behavioral last_size AND a model-scale
# imbalance in the same shares. Rows record which version produced them.
CANONICAL_LOOKUP_VERSION = 3

_cache: Optional[dict] = None


def _key(base: str, last_rel: str, last_size: str) -> str:
    return f"{base}|{last_rel}|{last_size}"


def build_artifact(out: Path = ARTIFACT) -> dict:
    """Deterministic offline build from the canonical sequence, reusing
    the held-out review's exact transition/feature computation."""
    from src.polymarket_l5.action_model_v2 import build_transitions
    train, valid = build_transitions()
    rows = train + valid                     # formulation already validated
    full: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    base: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    marginal: dict[str, int] = defaultdict(int)
    cadence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in rows:
        k = _key(t["base"], t["last_rel"], t["last_size"])
        full[k][t["action"]] += 1
        base[t["base"]][t["action"]] += 1
        marginal[t["action"]] += 1
        cadence[k][t["cadence"]] += 1
    model = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "formulation": "v1 base state + last_rel + last_size, "
                       f"shrinkage alpha={ALPHA} (full->base->marginal)",
        "n_transitions": len(rows),
        "n_full_states": len(full),
        "labels": "THEORETICAL persistence-v2 arm; frozen v1 untouched",
        "full": {k: dict(v) for k, v in full.items()},
        "base": {k: dict(v) for k, v in base.items()},
        "marginal": dict(marginal),
        "cadence_modal": {k: max(v, key=v.get) for k, v in cadence.items()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=1, sort_keys=True))
    return model


def load_v2(path: Path = ARTIFACT) -> dict:
    global _cache
    if _cache is not None and _cache.get("_path") == str(path):
        return _cache
    raw = path.read_bytes()
    model = json.loads(raw)
    model["_path"] = str(path)
    model["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    _cache = model
    return model


def _shrink(counts: Mapping[str, int],
            prior: Mapping[str, float]) -> dict[str, float]:
    total = sum(counts.values())
    keys = set(counts) | set(prior)
    return {a: (counts.get(a, 0) + ALPHA * prior.get(a, 0.0)) /
            (total + ALPHA) for a in keys}


def v2_dist(model: Mapping[str, Any], base_state: str, last_rel: str,
            last_size: str) -> dict:
    """Shrunk next-action distribution: full -> base -> marginal."""
    mtot = sum(model["marginal"].values())
    marg = {a: c / mtot for a, c in model["marginal"].items()}
    base_counts = model["base"].get(base_state)
    prior = _shrink(base_counts, marg) if base_counts else marg
    key = _key(base_state, last_rel or "open", last_size or "q<5")
    fc = model["full"].get(key)
    if not fc:
        return {"dist": prior, "state": key, "n": 0, "backed_off": True}
    return {"dist": _shrink(fc, prior), "state": key,
            "n": sum(fc.values()), "backed_off": False}


def relation_mass(dist: Mapping[str, float]) -> dict[str, float]:
    agg: dict[str, float] = {}
    for a, p in dist.items():
        rel = a.split("|")[0]
        agg[rel] = agg.get(rel, 0.0) + p
    return agg


def proposal(model: Mapping[str, Any], base_state: str, last_rel: str,
             last_size: str, residual: int,
             clone_avg_cost: Optional[str] = None) -> dict:
    """THEORETICAL action proposal from the v2 recommended relation:
    side + maker price zone + q=1 sports size + next reevaluation timing,
    all mapped from existing historical evidence."""
    d = v2_dist(model, base_state, last_rel, last_size)
    rels = relation_mass(d["dist"])
    ranked = sorted(rels.items(), key=lambda kv: (-kv[1], kv[0]))
    rel = ranked[0][0]
    modal_action = max(d["dist"], key=d["dist"].get)
    if rel == "sell":
        side, zone = None, None            # L5 lifetime sells ~$2.18: no-op
    elif rel == "complete":
        side = "NO" if residual > 0 else ("YES" if residual < 0 else None)
        zone = (f"maker<=complement_bound({clone_avg_cost})"
                if clone_avg_cost else "maker_join_deficit_side_bid")
    else:                                  # open / extend
        side = "YES" if residual >= 0 else "NO"
        zone = "maker_join_current_bid"
    gap = model.get("cadence_modal", {}).get(d["state"], "gap2-10")
    return {
        "labels": "THEORETICAL proposal; nothing armed",
        "recommended_relation": rel,
        "relation_ranked": [{"relation": r, "p": round(p, 4)}
                            for r, p in ranked],
        "modal_action": modal_action,
        "persist_extend_p": round(rels.get("extend", 0.0), 4),
        "switch_complete_p": round(rels.get("complete", 0.0), 4),
        "side": side,
        "price_zone": zone,
        "size": "q=1 sports probe unit "
                f"(historical modal bucket {modal_action.split('|')[1]})",
        "next_reevaluation_s": GAP_SECONDS.get(gap, 10.0),
        "cadence_bucket": gap,
        "state": d["state"], "state_n": d["n"],
        "off_distribution": d["backed_off"],
    }


def main() -> int:
    model = build_artifact()
    print(json.dumps({"built": str(ARTIFACT),
                      "n_transitions": model["n_transitions"],
                      "n_full_states": model["n_full_states"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
