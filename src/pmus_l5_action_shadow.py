"""READ-ONLY per-market L5 action shadow for the PMUS sports slate.

Issue #167 comments 5519672916 + 5519746304: historical L5 is a
PERSISTENT INCREMENTAL INVENTORY trader — repeated small same/nearby-side
buys, strong last-action persistence (`last_rel` is the strongest
next-action feature), temporary imbalance tolerance, later complements —
not a wait-for-instant-matched-pair trader. The current sports engine
reduces every market to the binary pair gate. This module adds the L5
view WITHOUT touching that gate: for every admitted market it maintains a
THEORETICAL per-market L5 clone state and emits the canonical frozen
action model's ranked next-action distribution alongside the pair-gate
row, plus action-by-action comparison fields.

What this is NOT: it does not trade, does not mutate anything, does not
change `ws_gate`, the supervisor's Q1_GATE_CLEARED admission, the q=1
executor, or any residual/account guard. It is the replay/shadow layer
the review requires BEFORE any live-semantics change.

Reused canon (nothing reinvented):
* `src.polymarket_l5.action_model` — `state_key` (price band x TTE x
  imbalance), `action_key`, relation semantics (open/extend/complete/
  sell), and the frozen trained model `analysis/l5/l5_action_model.json`
  (systematic regime, Mar 27+). The model file's sha256 prefix is
  reported with every emission so provenance is explicit.
* Theoretical fill semantics identical to `ws_gate`/`slot_replay`/the
  residual-completion shadow: a YES maker join at j fills on a
  SELL-direction print <= j; a NO maker join at p_no (YES-equivalent ask
  1-p_no) fills on a BUY-direction print >= 1-p_no. q=1 per simulated
  fill (the sports adapter's probe unit).

Relation vocabulary note (requested persist/extend/switch/complete/open):
the canonical model's relations are open/extend/complete/sell.
"persist" is an extend whose `last_rel` was also extend (same-side
continuation); "switch" is a complete whose `last_rel` was extend. Both
are derivable from the emitted (`last_rel`, ranked relations) pair — the
canonical vocabulary is emitted unchanged rather than renamed, so the
shadow stays comparable with every historical L5 artifact.

Sports TTE adapter limitation (stated, not hidden): in-game sports
markets have no fixed settlement clock on the tape, so without an
explicit settlement time the TTE feature collapses to the coarsest
bucket ("tte>10m"). Pass `settlement_t` when known.

All outputs are THEORETICAL and this module is structurally read-only:
no network-capable import, no transport, no order path.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src.polymarket_l5.action_model import state_key

MODEL_PATH = Path("analysis/l5/l5_action_model.json")
SPORTS_TTE_DEFAULT_S = 1e9          # no settlement clock -> coarsest bucket
TOP_ACTIONS = 6
LABELS = ("THEORETICAL per-market L5 clone shadow; frozen canonical "
          "action model; no order, no gate change, no realized economics")

_model_cache: Optional[dict] = None


def load_model(path: Path = MODEL_PATH) -> dict:
    """Frozen canonical model + provenance hash + pooled marginal."""
    global _model_cache
    if _model_cache is not None and _model_cache.get("_path") == str(path):
        return _model_cache
    raw = path.read_bytes()
    model = json.loads(raw)
    model["_path"] = str(path)
    model["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    pooled: dict[str, float] = {}
    total = 0.0
    for st in model["states"].values():
        for a, p in st["action_probs"].items():
            pooled[a] = pooled.get(a, 0.0) + st["n"] * p
        total += st["n"]
    model["_marginal"] = {a: v / total for a, v in pooled.items()}
    _model_cache = model
    return model


def _relation_ranked(dist: Mapping[str, float]) -> list[tuple[str, float]]:
    agg: dict[str, float] = {}
    for a, p in dist.items():
        rel = a.split("|")[0]
        agg[rel] = agg.get(rel, 0.0) + p
    return sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))


class CloneState:
    """Cumulative THEORETICAL L5 inventory for one market (q=1 fills)."""

    __slots__ = ("up_qty", "dn_qty", "cost_up", "cost_dn",
                 "last_rel", "last_fill_t", "n_fills", "last_px")

    def __init__(self) -> None:
        self.up_qty = self.dn_qty = 0
        self.cost_up = self.cost_dn = D("0")
        self.last_rel: Optional[str] = None
        self.last_fill_t: Optional[float] = None
        self.n_fills = 0
        self.last_px: Optional[D] = None       # last print seen (YES terms)

    @property
    def residual(self) -> int:
        return self.up_qty - self.dn_qty

    @property
    def matched(self) -> int:
        return min(self.up_qty, self.dn_qty)

    def pair_cost(self) -> Optional[str]:
        if self.matched == 0:
            return None
        return str((self.cost_up / max(self.up_qty, 1)
                    + self.cost_dn / max(self.dn_qty, 1)))

    def record_fill(self, side_up: bool, price: D, t: float) -> None:
        rel = ("open" if self.up_qty + self.dn_qty == 0 else
               ("extend" if (self.residual > 0) == side_up and
                self.residual != 0 else "complete"))
        if side_up:
            self.up_qty += 1
            self.cost_up += price
        else:
            self.dn_qty += 1
            self.cost_dn += price
        self.last_rel = rel
        self.last_fill_t = t
        self.n_fills += 1


def _proposed_side_up(relation: str, residual: int) -> Optional[bool]:
    """Canonical relation -> which side the clone works next.
    open: flat book, take the YES side of the current join; extend: the
    surplus side; complete: the deficit side; sell: abstain (L5's
    lifetime SELL volume is $2.18 — the shadow never simulates sells)."""
    if relation == "open":
        return True
    if relation == "extend":
        return residual >= 0
    if relation == "complete":
        return residual < 0 if residual != 0 else None
    return None


def simulate_clone(trades: list[Mapping[str, Any]],
                   model: Mapping[str, Any],
                   settlement_t: Optional[float] = None) -> CloneState:
    """Fold the recorded trade tape through a modal-relation clone.

    Sequentially, at each print: consult the frozen model at the clone's
    current state; the top-1 relation proposes a side; the proposal is a
    q=1 maker join AT THE PRINT-ADJACENT LEVEL and fills only when the
    print itself crosses it under the standard theoretical semantics
    (SELL print fills a YES join at >= print px is NOT allowed — the join
    is the prevailing bid side, so a SELL print at px fills a YES join at
    >= px; a BUY print at px fills a NO join whose YES-equivalent ask is
    <= px). Prints that do not cross leave state untouched."""
    st = CloneState()
    for tr in trades:
        px_raw = tr.get("px")
        direction = tr.get("dir")
        t = tr.get("t", 0.0)
        if not px_raw or direction not in ("buy", "sell"):
            continue
        try:
            px = D(str(px_raw))
        except Exception:
            continue
        tte = (max(settlement_t - t, 0.0) if settlement_t
               else SPORTS_TTE_DEFAULT_S)
        # state BEFORE this print: last previously-seen price (this print
        # as the cold-start fallback), current simulated residual
        sk = state_key(float(st.last_px if st.last_px is not None else px),
                       tte, float(st.residual))
        row = model["states"].get(sk)
        dist = row["action_probs"] if row else model["_marginal"]
        relation = _relation_ranked(dist)[0][0]
        side_up = _proposed_side_up(relation, st.residual)
        if side_up is not None:
            # theoretical crossing: a SELL print fills a resting YES-side
            # join; a BUY print fills a resting NO-side join (whose
            # YES-equivalent ask the print reaches)
            if side_up and direction == "sell":
                st.record_fill(True, px, t)
            elif not side_up and direction == "buy":
                st.record_fill(False, (D("1") - px), t)
        st.last_px = px
    return st


def market_shadow_row(s: Mapping[str, Any], now: float,
                      gate: Mapping[str, Any],
                      settlement_t: Optional[float] = None,
                      model: Optional[Mapping[str, Any]] = None) -> dict:
    """One market's L5 shadow + action-by-action comparison fields."""
    model = model or load_model()
    trades = [t for t in s.get("trades", []) if t.get("px")]
    st = simulate_clone(trades, model, settlement_t)
    last_px = None
    for t in reversed(trades):
        last_px = t.get("px")
        if last_px:
            break
    if last_px is None:
        last_px = s.get("bid") or "0.5"
    tte = (max(settlement_t - now, 0.0) if settlement_t
           else SPORTS_TTE_DEFAULT_S)
    sk = state_key(float(D(str(last_px))), tte, float(st.residual))
    row = model["states"].get(sk)
    dist = row["action_probs"] if row else None
    ranked_src = dist or model["_marginal"]
    rel_ranked = _relation_ranked(ranked_src)
    l5_next_rel = rel_ranked[0][0]

    gate_status = gate.get("gate_status")
    gate_next = ("q1_first_leg_candidate"
                 if gate_status == "Q1_GATE_CLEARED" else "wait")
    return {
        "labels": LABELS,
        "model_sha256_16": model["_sha256_16"],
        "l5_state": sk,
        "off_distribution": dist is None,
        "state_n": row["n"] if row else 0,
        "last_rel": st.last_rel,
        "sim_inventory": {"up": st.up_qty, "dn": st.dn_qty,
                          "matched": st.matched, "residual": st.residual,
                          "pair_unit_cost": st.pair_cost(),
                          "n_fills": st.n_fills},
        "next_action_ranked": [
            {"action": a, "p": round(p, 4)} for a, p in
            sorted(ranked_src.items(), key=lambda kv: -kv[1])[:TOP_ACTIONS]],
        "next_relation_ranked": [
            {"relation": r, "p": round(p, 4)} for r, p in rel_ranked],
        "tte_adapter": ("settlement_clock" if settlement_t
                        else "sports_default_coarsest_bucket"),
        "comparison": {
            "pair_gate_status": gate_status,
            "pair_gate_next": gate_next,
            "l5_next_relation": l5_next_rel,
            "divergent": (l5_next_rel in ("extend", "complete", "open")
                          and gate_next == "wait"),
            "note": ("divergent=True means the L5 policy would be working "
                     "inventory here while the pair shell waits; persist="
                     "extend with last_rel=extend, switch=complete with "
                     "last_rel=extend"),
        },
    }


def slate_shadow(state: Mapping[str, Any], now: float,
                 gates: Mapping[str, Mapping[str, Any]],
                 model: Optional[Mapping[str, Any]] = None) -> dict:
    """Shadow rows for every tracked market; per-market failures are
    contained so one bad market can never cost the status line."""
    try:
        model = model or load_model()
    except Exception as exc:
        return {"error": f"model_unavailable:{type(exc).__name__}"}
    rows = {}
    divergent = 0
    for slug, s in state.items():
        try:
            row = market_shadow_row(s, now, gates.get(slug, {}),
                                    model=model)
            rows[slug] = row
            divergent += bool(row["comparison"]["divergent"])
        except Exception as exc:
            rows[slug] = {"error": str(exc)[:80]}
    return {"model_sha256_16": model["_sha256_16"],
            "markets": rows, "divergent_total": divergent,
            "labels": LABELS}
