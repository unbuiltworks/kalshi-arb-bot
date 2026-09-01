"""Action-by-action comparison foundation (issue #167 §6).

Answers, from the canonical per-trade dataset: "at this market state, what
did L5 do next?" — and provides the hook to ask "what would our clone have
done?" at the same states, so a candidate policy can be scored
action-by-action against L5's empirical behavior before it ever touches a
live venue.

State (deliberately coarse — sample size beats resolution):
  price_band        band of the last observed fill price (market-price proxy)
  tte_bucket        seconds-to-settlement at the moment the next action lands
  imbalance_bucket  magnitude of residual directional inventory (shares)

Action (what the next fill was):
  relation  "complete" buys the deficit side (reduces |residual|)
            "extend"   buys the surplus side (increases |residual|)
            "open"     buys from a flat book (no residual yet)
            "sell"     any SELL (vanishingly rare for L5: $2.18 lifetime)
  size_bucket  shares bucket of the next fill

The model is a plain dict of empirical counts/probabilities per state —
no fitting, no smoothing — built deterministically from
analysis/l5/l5_canonical_sequence.csv.gz and written to
analysis/l5/l5_action_model.json.

Policy hook: any callable ``policy(state: dict) -> action_key`` can be
scored with ``compare_policy`` (expected per-decision agreement with L5,
weighted by how often each state occurred). ``compare_action`` scores a
single observed live action, for the realtime mirror.

Read-only research. No network, nothing here trades.

    python3 -m src.polymarket_l5.action_model
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import config

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT_JSON = config.OUTPUT_DIR / "l5_action_model.json"

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
SIZE_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (5.0, "q<5"), (15.0, "q5-15"), (40.0, "q15-40"), (1e12, "q>40"),
)


def _bucket(value: float, edges: Tuple[Tuple[float, str], ...]) -> str:
    for hi, label in edges:
        if value < hi:
            return label
    return edges[-1][1]


def state_key(last_price: float, tte_s: float, residual_qty: float) -> str:
    """Discretized market state, the model's lookup key."""
    return "|".join((_bucket(last_price, PRICE_BANDS),
                     _bucket(tte_s, TTE_BUCKETS),
                     _bucket(abs(residual_qty), IMBALANCE_BUCKETS)))


def action_key(relation: str, quantity: float) -> str:
    return f"{relation}|{_bucket(quantity, SIZE_BUCKETS)}"


def _relation(prev_residual: float, outcome: str, side: str) -> str:
    if side == "SELL":
        return "sell"
    if abs(prev_residual) < 1.0:
        return "open"
    surplus_side = "Up" if prev_residual > 0 else "Down"
    return "extend" if outcome == surplus_side else "complete"


def _rows_by_window(csv_path: Path) -> Iterable[List[Dict[str, str]]]:
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        current: List[Dict[str, str]] = []
        wid = None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if current:
                    yield current
                current, wid = [], row["window_id"]
            current.append(row)
        if current:
            yield current


# Systematic-regime start (2026-03-27 00:00 UTC): the behavioral changepoint
# established by early_era.py. Pre-regime decisions (Mar 19-26, 122 of
# 69,768) are experimentation-era noise; excluding them makes the model the
# "primary training cohort" per issue #167.
SYSTEMATIC_REGIME_EPOCH = 1774569600


def build_model(csv_path: Path = SEQUENCE_CSV,
                since_epoch: Optional[int] = None) -> Dict[str, Any]:
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    n_decisions = n_windows = 0
    for rows in _rows_by_window(csv_path):
        if since_epoch is not None and rows and \
                int(float(rows[0]["timestamp"])) < since_epoch:
            continue
        n_windows += 1
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        for prev, nxt in zip(rows, rows[1:]):
            try:
                last_price = float(prev["price"])
                residual = float(prev["residual_qty_signed"] or 0.0)
                tte = float(nxt["seconds_to_settlement"])
                qty = float(nxt["quantity"])
            except (TypeError, ValueError):
                continue
            sk = state_key(last_price, tte, residual)
            ak = action_key(_relation(residual, nxt["outcome"], nxt["side"]), qty)
            counts[sk][ak] += 1
            n_decisions += 1

    states: Dict[str, Any] = {}
    for sk, acts in counts.items():
        n = sum(acts.values())
        probs = {a: round(c / n, 4) for a, c in
                 sorted(acts.items(), key=lambda kv: -kv[1])}
        states[sk] = {"n": n, "action_probs": probs,
                      "modal_action": next(iter(probs))}
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(csv_path),
        "since_epoch": since_epoch,
        "n_windows": n_windows,
        "n_decisions": n_decisions,
        "n_states": len(states),
        "buckets": {
            "price_bands": [l for _, l in PRICE_BANDS],
            "tte_buckets": [l for _, l in TTE_BUCKETS],
            "imbalance_buckets": [l for _, l in IMBALANCE_BUCKETS],
            "size_buckets": [l for _, l in SIZE_BUCKETS],
        },
        "states": states,
        "note": ("Empirical next-action distributions over L5's canonical "
                 "sequence. Coverage is L5's visited states only — a state "
                 "L5 never reached returns None, which is itself a signal "
                 "that a clone has wandered off-distribution."),
    }


def next_action_stats(model: Dict[str, Any], state: str) -> Optional[Dict[str, Any]]:
    """What L5 did next from this state, or None if never observed."""
    return model["states"].get(state)


def compare_action(model: Dict[str, Any], state: str,
                   action: str) -> Dict[str, Any]:
    """Score one observed/proposed action against L5's distribution."""
    st = model["states"].get(state)
    if st is None:
        return {"state": state, "observed_by_l5": False, "n": 0,
                "action": action, "probability": None, "rank": None,
                "modal_action": None}
    probs = st["action_probs"]
    rank = (list(probs).index(action) + 1) if action in probs else None
    return {"state": state, "observed_by_l5": True, "n": st["n"],
            "action": action, "probability": probs.get(action, 0.0),
            "rank": rank, "modal_action": st["modal_action"]}


PolicyHook = Callable[[str], Optional[str]]
"""A clone policy: given a state key, the action it would take (or None to
abstain). Deliberately the same key vocabulary as the model, so any policy —
rule-based, replayed, or live-shadow — plugs in unchanged."""


def compare_policy(model: Dict[str, Any], policy: PolicyHook) -> Dict[str, Any]:
    """Expected action-by-action agreement between a policy and L5.

    For each state, weighted by how often L5 faced it: the probability that
    L5's next action equals the policy's choice (exact action key), and
    separately at relation level only (complete/extend/open/sell), which is
    the behaviorally meaningful axis.
    """
    total = agree_exact = agree_relation = abstained = 0.0
    per_state: List[Dict[str, Any]] = []
    for sk, st in model["states"].items():
        n = st["n"]
        total += n
        choice = policy(sk)
        if choice is None:
            abstained += n
            continue
        p_exact = st["action_probs"].get(choice, 0.0)
        rel = choice.split("|")[0]
        p_rel = sum(p for a, p in st["action_probs"].items()
                    if a.split("|")[0] == rel)
        agree_exact += n * p_exact
        agree_relation += n * p_rel
        per_state.append({"state": sk, "n": n, "policy_action": choice,
                          "p_exact": round(p_exact, 4),
                          "p_relation": round(p_rel, 4),
                          "l5_modal": st["modal_action"]})
    per_state.sort(key=lambda r: -r["n"])
    covered = total - abstained
    return {
        "decisions_weighted": int(total),
        "policy_abstained_weight": int(abstained),
        "expected_agreement_exact": round(agree_exact / covered, 4) if covered else None,
        "expected_agreement_relation": round(agree_relation / covered, 4) if covered else None,
        "top_states": per_state[:20],
    }


def main() -> int:
    model = build_model(since_epoch=SYSTEMATIC_REGIME_EPOCH)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(model, indent=1))
    top = sorted(model["states"].items(), key=lambda kv: -kv[1]["n"])[:12]
    print(f"decisions={model['n_decisions']} states={model['n_states']} "
          f"windows={model['n_windows']}")
    for sk, st in top:
        print(f"  {sk:<38} n={st['n']:>6}  modal={st['modal_action']} "
              f"p={st['action_probs'][st['modal_action']]}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
