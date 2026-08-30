"""Causal replay of the toxicity policy at the CLONE's own inventory state.

The fill-toxicity study (analysis/l5/L5_MAKER_FILL_TOXICITY.md) evaluated the
inventory-aware quoting policy against L5's inventory path, which is circular:
the clone's decisions change its own state. This replay closes that gap:

- The clone holds its own InventoryState per window, at SCALE of L5's sizes.
- TAKER fills pass through unfiltered (they are deliberate actions the clone
  would also choose; L5's taker book is separately near-breakeven-to-positive).
- MAKER fills are accepted only if the policy passes at the clone's CURRENT
  state: the fill would reduce the clone's residual, or land on the clone's
  light side; plus the price floor; optionally the tape-streak guard. Tape
  features (streak) come from the full observed tape - the market is
  observable whether or not we quote - while inventory features come only
  from the clone's accepted fills.
- P&L is settlement-additive per accepted fill (identical accounting to
  derived_window_pnl for this BUY-only sample).

Honest limits, unchanged from the study: we assume the clone receives SCALE of
each observed fill it would have quoted for, at the same price and time. Queue
position, fill availability at reduced quoting breadth, and self-impact are not
modeled. The baseline (accept everything) must reconcile with the V2 replay's
-$250.43419 at 10% scale; that reconciliation is asserted in tests.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from .fill_toxicity import RECENT_START_TS, load_fills, load_taker_fingerprints, _fingerprint_of_row

SCALE = 0.10
PRICE_FLOOR = 0.30
IMBALANCE_LIGHT = -0.1
STREAK_MAX = 3
TRAIN_FRACTION = 0.6
EMPIRICAL_MAKER_REBATE_PER_DOLLAR = 0.0072
EPS = 1e-12


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class CloneState:
    __slots__ = ("up_qty", "down_qty")

    def __init__(self) -> None:
        self.up_qty = 0.0
        self.down_qty = 0.0

    def imbalance_toward(self, outcome: str) -> float:
        total = self.up_qty + self.down_qty
        if total <= EPS:
            return 0.0
        signed = (self.up_qty - self.down_qty) / total
        return signed if outcome == "Up" else -signed

    def reduces_residual(self, outcome: str, qty: float) -> bool:
        before = abs(self.up_qty - self.down_qty)
        up = self.up_qty + (qty if outcome == "Up" else 0.0)
        down = self.down_qty + (qty if outcome == "Down" else 0.0)
        return abs(up - down) < before - EPS

    def apply(self, outcome: str, qty: float) -> None:
        if outcome == "Up":
            self.up_qty += qty
        else:
            self.down_qty += qty


PolicyFn = Callable[[CloneState, str, float, float, int], bool]
TAPE_WINDOW = 30  # observed fills in the rolling tape-flow window
TAPE_LIGHT = -0.1


class TapeFlow:
    """Rolling share-weighted net direction of the OBSERVED tape (clone-observable)."""

    __slots__ = ("rows",)

    def __init__(self) -> None:
        self.rows: List[Tuple[str, float]] = []

    def add(self, outcome: str, shares: float) -> None:
        self.rows.append((outcome, shares))
        if len(self.rows) > TAPE_WINDOW:
            self.rows.pop(0)

    def imbalance_toward(self, outcome: str) -> float:
        total = sum(q for _, q in self.rows)
        if total <= EPS:
            return 0.0
        up = sum(q for o, q in self.rows if o == "Up")
        signed = (2.0 * up - total) / total
        return signed if outcome == "Up" else -signed


def policy_baseline(state: CloneState, outcome: str, price: float, qty: float, streak: int) -> bool:
    return True


def policy_a(state: CloneState, outcome: str, price: float, qty: float, streak: int) -> bool:
    return state.reduces_residual(outcome, qty) or state.imbalance_toward(outcome) < IMBALANCE_LIGHT


def policy_b(state: CloneState, outcome: str, price: float, qty: float, streak: int) -> bool:
    return policy_a(state, outcome, price, qty, streak) and price >= PRICE_FLOOR


def policy_c(state: CloneState, outcome: str, price: float, qty: float, streak: int) -> bool:
    return policy_b(state, outcome, price, qty, streak) and streak < STREAK_MAX


POLICIES: Dict[str, PolicyFn] = {
    "baseline_accept_all": policy_baseline,
    "A_clone_inventory_aware": policy_a,
    "B_A_and_price_floor_0.30": policy_b,
    "C_B_and_tape_streak_lt_3": policy_c,
}


def policy_d_tape(state: CloneState, outcome: str, price: float, qty: float, streak: int, *, tape: "TapeFlow") -> bool:
    return tape.imbalance_toward(outcome) < TAPE_LIGHT and price >= PRICE_FLOOR


policy_d_tape.wants_tape = True
POLICIES["D_tape_flow_contra_and_price_floor(EXPLORATORY)"] = policy_d_tape


def replay(
    fills_by_window: Dict[str, List[Dict[str, Any]]],
    winners: Dict[str, str],
    taker_fps: set,
    policy: PolicyFn,
    *,
    scale: float = SCALE,
    split_ts: Optional[float] = None,
) -> Dict[str, Any]:
    parts = {name: defaultdict(float) for name in ("train", "validate", "all")}

    def book(part: str, key: str, value: float) -> None:
        parts[part][key] += value
        if part != "all":
            parts["all"][key] += value

    for window_id, rows in fills_by_window.items():
        winner = winners.get(window_id, "")
        if winner not in ("Up", "Down"):
            continue
        clone = CloneState()
        tape = TapeFlow()
        streak_side, streak_len = "", 0
        for row in rows:
            ts = _f(row["timestamp"])
            part = "all" if split_ts is None else ("train" if ts < split_ts else "validate")
            outcome = row["outcome"]
            price = _f(row.get("price"))
            shares = _f(row.get("shares")) * scale
            dollars = _f(row.get("dollars")) * scale
            if shares <= 0:
                continue
            is_taker = _fingerprint_of_row(row) in taker_fps
            tape_streak = streak_len if streak_side == outcome else 0

            accept = True
            if not is_taker:
                if getattr(policy, "wants_tape", False):
                    accept = policy(clone, outcome, price, shares, tape_streak, tape=tape)
                else:
                    accept = policy(clone, outcome, price, shares, tape_streak)
            pnl = shares * (1.0 if outcome == winner else 0.0) - dollars

            if accept:
                clone.apply(outcome, shares)
                kind = "taker" if is_taker else "maker"
                book(part, f"{kind}_n", 1)
                book(part, f"{kind}_dollars", dollars)
                book(part, f"{kind}_pnl", pnl)
            else:
                book(part, "rejected_n", 1)
                book(part, "rejected_dollars", dollars)
                book(part, "rejected_pnl_foregone", pnl)

            # Tape evolves with every observed fill, accepted or not.
            tape.add(outcome, shares)
            if outcome == streak_side:
                streak_len += 1
            else:
                streak_side, streak_len = outcome, 1

    out: Dict[str, Any] = {}
    for part, acc in parts.items():
        maker_d, taker_d = acc["maker_dollars"], acc["taker_dollars"]
        total_pnl = acc["maker_pnl"] + acc["taker_pnl"]
        out[part] = {
            "maker_n": int(acc["maker_n"]),
            "taker_n": int(acc["taker_n"]),
            "rejected_n": int(acc["rejected_n"]),
            "maker_dollars": round(maker_d, 2),
            "taker_dollars": round(taker_d, 2),
            "turnover_dollars": round(maker_d + taker_d, 2),
            "maker_pnl": round(acc["maker_pnl"], 4),
            "taker_pnl": round(acc["taker_pnl"], 4),
            "trading_pnl": round(total_pnl, 4),
            "maker_pnl_per_dollar": round(acc["maker_pnl"] / maker_d, 5) if maker_d else None,
            "modeled_maker_rebate": round(maker_d * EMPIRICAL_MAKER_REBATE_PER_DOLLAR, 2),
            "trading_pnl_plus_modeled_rebate": round(
                total_pnl + maker_d * EMPIRICAL_MAKER_REBATE_PER_DOLLAR, 2
            ),
            "rejected_pnl_foregone": round(acc["rejected_pnl_foregone"], 2),
        }
    return out


def run(
    trades_csv: Optional[Path] = None,
    summary_csv: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    out_path: Optional[Path] = None,
    scale: float = SCALE,
) -> Dict[str, Any]:
    trades_csv = Path(trades_csv or (config.OUTPUT_DIR / config.TRADES_CSV))
    summary_csv = Path(summary_csv or (config.OUTPUT_DIR / config.SUMMARY_CSV))
    cache_root = Path(cache_root or config.RAW_DIR)

    winners = {
        row["window_id"]: row.get("winning_outcome") or ""
        for row in csv.DictReader(open(summary_csv, encoding="utf-8", newline=""))
    }
    fills = load_fills(trades_csv)
    taker_fps = load_taker_fingerprints(cache_root)

    by_window: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in fills:
        by_window[row["window_id"]].append(row)

    timestamps = sorted(_f(row["timestamp"]) for row in fills)
    split_ts = timestamps[int(len(timestamps) * TRAIN_FRACTION)] if timestamps else None

    results = {
        name: replay(by_window, winners, taker_fps, fn, scale=scale, split_ts=split_ts)
        for name, fn in POLICIES.items()
    }
    payload = {
        "scale": scale,
        "era_start_ts": RECENT_START_TS,
        "split_ts": split_ts,
        "train_fraction": TRAIN_FRACTION,
        "policy_constants": {
            "price_floor": PRICE_FLOOR,
            "imbalance_light": IMBALANCE_LIGHT,
            "streak_max": STREAK_MAX,
            "maker_rebate_per_dollar": EMPIRICAL_MAKER_REBATE_PER_DOLLAR,
        },
        "taker_fills_pass_through": True,
        "results": results,
    }
    out_path = Path(out_path or (config.OUTPUT_DIR / "l5_inventory_aware_replay.json"))
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    return payload


if __name__ == "__main__":  # pragma: no cover
    payload = run()
    for name, res in payload["results"].items():
        a = res["all"]
        print(f"{name}: turnover=${a['turnover_dollars']} pnl=${a['trading_pnl']} "
              f"maker_pnl/$={a['maker_pnl_per_dollar']} +rebate=${a['trading_pnl_plus_modeled_rebate']}")
