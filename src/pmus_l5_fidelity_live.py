"""Live SHADOW fields from the offline fidelity pass (OWNER 5553674206).

Read-only lookups over `analysis/l5/l5_fidelity_pass.json` that the live
session attaches to every decision and every order. None of them changes
what the engine does: the executed action is still the modal relation
under the unchanged safety shell, the executed size is still the
venue-minimum bounded micro size, and the venue is only ever touched
maker/post-only. What they add is the measurement the owner asked for:

* `stochastic`: the deterministic seeded draw from the SAME conditional
  distribution the modal choice came from (seed = sha256 of market slug,
  decision index, model sha), with its probability and rank -- so the
  live log can be scored against a stochastic policy without arming one.
* `l5_target_size`: L5's historical size for (state, relation), beside
  `executed_safe_size`, so the scale mismatch is measured, not hidden.
* `l5_target_aggressiveness`: L5's taker share at this time-to-settlement
  and relation, beside the live policy (maker only) and the PMUS fee
  economics a taker completion would have to clear.

Tiny, dependency-free (json + hashlib + the action-model buckets), so the
probe image carries no research code.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src.polymarket_l5.action_model import _bucket, TTE_BUCKETS

ARTIFACT = (Path(__file__).resolve().parents[1]
            / "analysis/l5/l5_fidelity_pass.json")
PMUS_TAKER_FEE_RATE = D("0.06")            # census: 0.06 * C * p * (1-p)

_cache: Optional[dict] = None


def load(path: Path = ARTIFACT) -> Optional[dict]:
    """The artifact, or None when the image does not carry it (every
    shadow field then reads `unavailable`; nothing else changes)."""
    global _cache
    if _cache is not None and _cache.get("_path") == str(path):
        return _cache
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    art = json.loads(raw)
    art["_path"] = str(path)
    art["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    _cache = art
    return art


def seed_material(slug: str, decision_idx: int, model_sha: str) -> str:
    return hashlib.sha256(f"{slug}|{decision_idx}|{model_sha}".encode()).hexdigest()


def sample_relation(relation_ranked: Any, seed: str) -> dict:
    """Deterministic inverse-CDF draw over the relation masses the
    engine already logs (`relation_ranked`: [{relation, p}, ...])."""
    rows = [(str(r.get("relation")), float(r.get("p") or 0.0))
            for r in (relation_ranked or []) if isinstance(r, Mapping)]
    if not rows:
        return {"sampled": None, "u": None, "p": None, "rank": None,
                "seed": seed[:16]}
    tot = sum(p for _, p in rows) or 1.0
    u = int(seed[:8], 16) / float(0x100000000)
    acc = 0.0
    chosen = rows[-1][0]
    for rel, p in sorted(rows):
        acc += p / tot
        if u < acc:
            chosen = rel
            break
    by_p = sorted(rows, key=lambda rp: -rp[1])
    rank = 1 + [r for r, _ in by_p].index(chosen)
    p = dict(rows).get(chosen, 0.0) / tot
    return {"sampled": chosen, "u": round(u, 6), "p": round(p, 4),
            "rank": rank, "seed": seed[:16],
            "labels": "SHADOW draw from the executed distribution; not acted on"}


def l5_target_size(art: Optional[Mapping[str, Any]], base_state: str,
                   relation: str) -> dict:
    if not art:
        return {"source": "unavailable"}
    sp = art.get("size_policy") or {}
    row = (sp.get("by_state_relation") or {}).get(f"{base_state}|{relation}")
    if row and int(row.get("n") or 0) >= 20:
        return {"source": "state|relation", **row}
    row = (sp.get("by_relation") or {}).get(relation)
    return {"source": "relation", **(row or {"n": 0})}


def l5_target_aggressiveness(art: Optional[Mapping[str, Any]], tte_s: float,
                             relation: str) -> dict:
    tb = _bucket(float(tte_s), TTE_BUCKETS)
    if not art:
        return {"tte_bucket": tb, "source": "unavailable"}
    ag = art.get("aggressiveness_policy") or {}
    row = (ag.get("p_taker_by_tte_relation") or {}).get(f"{tb}|{relation}")
    src = "tte|relation"
    if not row:
        row = (ag.get("p_taker_by_tte") or {}).get(tb) or {"n": 0, "p_taker": None}
        src = "tte"
    return {"tte_bucket": tb, "source": src, **row,
            "live_policy": "maker/post-only only; taker disabled"}


def taker_completion_economics(surplus_avg: Any, opp_ask: Any) -> dict:
    """What a TAKER completion at the current opposite ask would cost:
    pair cost, the PMUS taker fee at that price, and the edge left --
    reported for the promotion decision, never acted on here."""
    try:
        avg, ask = D(str(surplus_avg)), D(str(opp_ask))
    except Exception:
        return {"available": False}
    fee = PMUS_TAKER_FEE_RATE * ask * (D("1") - ask)
    pair = avg + ask
    edge = D("1") - pair - fee
    return {"available": True, "opp_ask": str(ask), "pair_cost": str(pair),
            "taker_fee_per_contract": str(fee.quantize(D("0.0001"))),
            "edge_after_fee": str(edge.quantize(D("0.0001"))),
            "fee_safe": bool(edge >= D("0.01")),
            "labels": "THEORETICAL; taker path disabled in the live engine"}


def shadow_fields(art: Optional[Mapping[str, Any]], *, slug: str,
                  decision_idx: int, model_sha: str, base_state: str,
                  relation: str, relation_ranked: Any, tte_s: float,
                  executed_qty: Any = None, executed_px: Any = None) -> dict:
    """Everything the live decision log attaches; pure function."""
    out = {
        "artifact_sha": (art or {}).get("_sha256_16"),
        "stochastic": sample_relation(
            relation_ranked, seed_material(slug, decision_idx, model_sha)),
        "l5_target_size": l5_target_size(art, base_state, relation),
        "l5_target_aggressiveness": l5_target_aggressiveness(art, tte_s, relation),
    }
    if executed_qty is not None:
        try:
            q, px = D(str(executed_qty)), D(str(executed_px or 0))
            out["executed_safe_size"] = {"contracts": str(q),
                                         "usd": str((q * px).quantize(D("0.0001")))}
            tgt = out["l5_target_size"].get("shares_p50")
            out["size_ratio_l5_over_ours"] = (
                round(float(tgt) / float(q), 1) if tgt and q > 0 else None)
        except Exception:
            out["executed_safe_size"] = {"contracts": str(executed_qty)}
    return out
