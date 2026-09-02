"""Validated-v1 L5 policy gate for the bounded Gemini q1 dynamic owner.

Plugs the EX-ANTE-VALIDATED v1 L5 state model (frozen `l5_action_model.json`
sha `a846e6bc96fc8079`, systematic regime Mar 27+; predeclared live gate
PASSED 2026-09-01, clustered 95% CI on the log-prob edge [+0.021, +0.099])
into the already-proven q1 executor shell as a pure DECISION FILTER. No
order transport lives here — placement, cancel, durable reservation,
exception cleanup, and settlement accounting remain the dynamic owner's
(`gemini_l5_dynamic_q1`) unchanged responsibility, with its fail-closed
invariants intact.

What the gate adds, all read-only:
  * IN-DISTRIBUTION CHECK — quote only when the current (price band, time
    to settlement, inventory imbalance) state is one the trained L5 model
    has seen. In 4h of live scoring L5 itself produced 0 off-distribution
    decisions, so leaving the envelope means the market state, not the
    venue, is unlike anything L5 traded — stand down.
  * RELATION SANITY — with flat inventory the model's modal relation must
    not be "sell" (it never is in-distribution; belt and braces).
  * PROVENANCE — every accept/reject can be emitted with the source model
    sha so the probe log is auditable against the research artifacts.

State-key discretization is vendored VERBATIM from
src/polymarket_l5/action_model.py on feature/l5-canonical (same bucket
edges, same key format) so gate lookups match the artifact exactly.

Disabled unless GEMINI_L5_V1_POLICY=1; the owner behaves identically to
its proven baseline when the flag is absent.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

POLICY_ENV = "GEMINI_L5_V1_POLICY"
GATE_PATH_ENV = "GEMINI_L5_V1_POLICY_GATE_FILE"
DEFAULT_GATE_PATH = Path("config/l5_v1_policy_gate.json")

# --- vendored verbatim from polymarket_l5.action_model (v1, frozen) ---
PRICE_BANDS: Tuple[Tuple[float, str], ...] = (
    (0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
    (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"),
    (10.0, "p>90c"),
)
TTE_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (30, "tte<30s"), (120, "tte30-120s"), (300, "tte2-5m"),
    (600, "tte5-10m"), (1e12, "tte>10m"),
)
IMBALANCE_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (1.0, "flat"), (10.0, "imb1-10"), (50.0, "imb10-50"), (1e12, "imb>50"),
)


def _bucket(value: float, edges: Tuple[Tuple[float, str], ...]) -> str:
    for hi, label in edges:
        if value < hi:
            return label
    return edges[-1][1]


def state_key(last_price: float, tte_s: float, residual_qty: float) -> str:
    return "|".join((_bucket(last_price, PRICE_BANDS),
                     _bucket(tte_s, TTE_BUCKETS),
                     _bucket(abs(residual_qty), IMBALANCE_BUCKETS)))
# --- end vendored block ---

_gate_cache: Optional[dict] = None


def enabled() -> bool:
    return os.getenv(POLICY_ENV, "").strip() in {"1", "true", "TRUE", "yes", "on"}


def load_gate() -> dict:
    global _gate_cache
    if _gate_cache is None:
        path = Path(os.getenv(GATE_PATH_ENV, "") or DEFAULT_GATE_PATH)
        _gate_cache = json.loads(path.read_text())
    return _gate_cache


def provenance() -> Mapping[str, Any]:
    g = load_gate()
    return {"model_sha256_16": g["source_model_sha256_16"],
            "training_since_epoch": g["training_regime_since_epoch"],
            "n_decisions": g["n_decisions"]}


def evaluate(mid_price: float, tte_s: float,
             residual_qty: float = 0.0) -> Mapping[str, Any]:
    """Pure decision: may the owner nominate a candidate in this state?

    mid_price    current market mid (or candidate YES price) in [0, 1]
    tte_s        seconds until window settlement
    residual_qty signed directional inventory in shares (0 pre-entry)
    """
    g = load_gate()
    sk = state_key(mid_price, tte_s, residual_qty)
    st = g["states"].get(sk)
    if st is None:
        return {"allow": False, "state": sk, "reason": "off_distribution",
                "model_sha256_16": g["source_model_sha256_16"]}
    if abs(residual_qty) < 1.0 and st["modal_relation"] == "sell":
        return {"allow": False, "state": sk, "reason": "modal_sell_flat",
                "model_sha256_16": g["source_model_sha256_16"]}
    return {"allow": True, "state": sk, "state_n": st["n"],
            "modal_relation": st["modal_relation"],
            "rel_probs": st["rel_probs"],
            "model_sha256_16": g["source_model_sha256_16"]}


def gate_candidate(bid: D, ask: D, tte_s: float) -> Mapping[str, Any]:
    """Convenience wrapper for the owner's PublicCandidate shape."""
    mid = float((D(str(bid)) + D(str(ask))) / 2)
    return evaluate(mid, tte_s, 0.0)
