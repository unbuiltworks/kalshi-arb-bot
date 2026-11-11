"""Maker-fill toxicity attribution for the L5 study.

Question (pre-registered by the V2-replay review):

    Conditional on being filled as maker, what observable state predicts whether
    the fill is profitable after adverse selection (and, separately, rebates)?

Design constraints honored here:

- BUY-only sample: settlement P&L is additive per fill,
  ``pnl = shares * 1{outcome == winner} - dollars`` (identical accounting to
  ``derived_window_pnl``: winning quantity pays $1, cost basis is `dollars`).
- Features are computed strictly from state observable AT fill time (previous
  fills in the window, the fill's own price/size/side, clock). Nothing after
  the fill leaks into its features.
- Time-ordered split: train on the first TRAIN_FRACTION of windows (by window
  start), validate on the rest. Buckets are pre-registered below, not searched.
- Rebates are modeled separately and never folded into realized trading P&L.
  The modeled maker-rebate rate is an *empirical aggregate* (observed rebate
  rows / observed maker volume), applied flat; the true program formula is
  fee-proportional and unverified, so per-fill rebate numbers are indicative.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config

RECENT_START_TS = 1787903127  # start of the contiguous recent coverage era
TRAIN_FRACTION = 0.6
BURST_WINDOW_S = 3.0

# Empirical incentive aggregates measured from activity rows over this era
# (see L5_STRATEGY_FINDINGS.md section 2b). Flat-rate model, clearly labeled.
EMPIRICAL_MAKER_REBATE_PER_DOLLAR = 0.0072   # ~$1,008/day rebate / ~$139k/day maker volume
EMPIRICAL_TAKER_REBATE_PER_DOLLAR = 0.0025   # ~$150/day / ~$61k/day taker volume

# Pre-registered feature bucketings (edges chosen before looking at outcomes).
PRICE_BUCKETS = ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))
EXPIRY_FRACTION_BUCKETS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01))
IMBALANCE_BUCKETS = ((-1.01, -0.5), (-0.5, -0.1), (-0.1, 0.1), (0.1, 0.5), (0.5, 1.01))
STREAK_BUCKETS = ((0, 1), (1, 3), (3, 6), (6, 10_000))


def _f(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _bucket(value: Optional[float], edges: Sequence[Tuple[float, float]]) -> str:
    if value is None:
        return "na"
    for low, high in edges:
        if low <= value < high:
            return f"{low:g}..{high:g}"
    return "out"


def load_fills(trades_csv: Path) -> List[Dict[str, Any]]:
    """Recent-era BUY fills with window resolution, time-ordered per window."""
    rows = []
    with open(trades_csv, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ts = _f(row.get("timestamp"))
            if ts is None or ts < RECENT_START_TS:
                continue
            if (row.get("side") or "").upper() != "BUY":
                continue
            if row.get("outcome") not in ("Up", "Down"):
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r["window_id"], float(r["timestamp"]), r.get("transaction_hash") or ""))
    return rows


def load_taker_fingerprints(cache_root: Path) -> set:
    """Fingerprints of taker fills from the trades_takeronly raw cache."""
    from .cache import RawCache
    from .normalize import normalize_trade_item, fingerprint

    cache = RawCache(cache_root)
    out = set()
    for batch in cache.payload_batches("trades_takeronly"):
        for item in batch:
            record = normalize_trade_item(item, "trades_takeronly")
            if record is not None:
                out.add(fingerprint(record))
    return out


def _fingerprint_of_row(row: Dict[str, Any]) -> tuple:
    from .normalize import fingerprint

    return fingerprint(
        {
            "transaction_hash": row.get("transaction_hash", ""),
            "timestamp": int(float(row["timestamp"])),
            "condition_id": row.get("condition_id", ""),
            "token_id": row.get("token_id", ""),
            "side": row.get("side", ""),
            "price": _f(row.get("price")),
            "shares": _f(row.get("shares")),
        }
    )


def build_dataset(
    fills: Sequence[Dict[str, Any]],
    taker_fps: set,
    winners: Dict[str, str],
) -> List[Dict[str, Any]]:
    """One example per fill with observable-at-fill-time features and outcome."""
    examples: List[Dict[str, Any]] = []
    by_window: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in fills:
        by_window[row["window_id"]].append(row)

    for window_id, rows in by_window.items():
        winner = winners.get(window_id, "")
        if winner not in ("Up", "Down"):
            continue
        up_qty = down_qty = 0.0
        streak_side, streak_len = "", 0
        recent: List[Tuple[float, str, float]] = []  # (ts, outcome, price) history
        for row in rows:
            ts = float(row["timestamp"])
            price = _f(row.get("price")) or 0.0
            shares = _f(row.get("shares")) or 0.0
            dollars = _f(row.get("dollars"))
            dollars = dollars if dollars is not None else price * shares
            outcome = row["outcome"]
            wmin = _f(row.get("window_minutes")) or 5.0
            sfs = _f(row.get("seconds_from_window_start"))
            frac = None if sfs is None else max(0.0, min(1.0, sfs / (wmin * 60.0)))

            total = up_qty + down_qty
            imbalance = 0.0 if total <= 1e-12 else (up_qty - down_qty) / total
            residual_before = abs(up_qty - down_qty)
            up_after = up_qty + (shares if outcome == "Up" else 0.0)
            down_after = down_qty + (shares if outcome == "Down" else 0.0)
            reduces_residual = abs(up_after - down_after) < residual_before - 1e-12

            burst = sum(
                1 for (t, o, p) in recent
                if ts - t <= BURST_WINDOW_S and o == outcome and abs(p - price) < 1e-9
            )
            same_side_prices = [p for (t, o, p) in recent if o == outcome]
            price_drift = price - same_side_prices[-1] if same_side_prices else None

            is_taker = _fingerprint_of_row(row) in taker_fps
            pnl = shares * (1.0 if outcome == winner else 0.0) - dollars

            examples.append(
                {
                    "window_id": window_id,
                    "timestamp": ts,
                    "asset": row.get("asset", ""),
                    "window_minutes": int(wmin),
                    "is_maker": not is_taker,
                    "outcome": outcome,
                    "price": price,
                    "shares": shares,
                    "dollars": dollars,
                    "pnl": pnl,
                    "win": outcome == winner,
                    "frac_elapsed": frac,
                    "imbalance_signed_toward_fill": (
                        imbalance if outcome == "Up" else (None if imbalance is None else -imbalance)
                    ),
                    "reduces_residual": reduces_residual,
                    "burst_count": burst,
                    "streak_len_same_side": streak_len if streak_side == outcome else 0,
                    "price_drift_same_side": price_drift,
                }
            )

            up_qty, down_qty = up_after, down_after
            if outcome == streak_side:
                streak_len += 1
            else:
                streak_side, streak_len = outcome, 1
            recent.append((ts, outcome, price))
            if len(recent) > 50:
                recent.pop(0)
    examples.sort(key=lambda e: e["timestamp"])
    return examples


def _agg(examples: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    n = vol = pnl = 0.0
    for e in examples:
        n += 1
        vol += e["dollars"]
        pnl += e["pnl"]
    return {
        "n": int(n),
        "dollars": round(vol, 2),
        "pnl": round(pnl, 2),
        "pnl_per_dollar": round(pnl / vol, 5) if vol else None,
        "modeled_maker_rebate": round(vol * EMPIRICAL_MAKER_REBATE_PER_DOLLAR, 2),
    }


def bucket_report(
    examples: Sequence[Dict[str, Any]], feature: str, edges: Optional[Sequence] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{bucket: {train: agg, validate: agg}} for one pre-registered feature."""
    split_index = int(len(examples) * TRAIN_FRACTION)
    out: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for name, part in (("train", examples[:split_index]), ("validate", examples[split_index:])):
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in part:
            value = e.get(feature)
            if edges is not None:
                key = _bucket(value if not isinstance(value, bool) else float(value), edges)
            else:
                key = str(value)
            groups[key].append(e)
        for key, rows in groups.items():
            out[key][name] = _agg(rows)
    return dict(out)


def stable_sign(report: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, str]:
    """Which buckets keep the same P&L-per-dollar sign in train AND validate."""
    verdicts = {}
    for key, parts in report.items():
        t = (parts.get("train") or {}).get("pnl_per_dollar")
        v = (parts.get("validate") or {}).get("pnl_per_dollar")
        if t is None or v is None:
            verdicts[key] = "insufficient"
        elif t > 0 and v > 0:
            verdicts[key] = "stable_positive"
        elif t < 0 and v < 0:
            verdicts[key] = "stable_negative"
        else:
            verdicts[key] = "unstable"
    return verdicts


def run(
    trades_csv: Optional[Path] = None,
    summary_csv: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    trades_csv = Path(trades_csv or (config.OUTPUT_DIR / config.TRADES_CSV))
    summary_csv = Path(summary_csv or (config.OUTPUT_DIR / config.SUMMARY_CSV))
    cache_root = Path(cache_root or config.RAW_DIR)

    winners = {}
    with open(summary_csv, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            winners[row["window_id"]] = row.get("winning_outcome") or ""

    fills = load_fills(trades_csv)
    taker_fps = load_taker_fingerprints(cache_root)
    examples = build_dataset(fills, taker_fps, winners)
    makers = [e for e in examples if e["is_maker"]]
    takers = [e for e in examples if not e["is_maker"]]

    label_note = ""
    if not taker_fps:
        label_note = "NO TAKER LABELS LOADED: maker set degenerates to all fills"

    features = {
        "price": bucket_report(makers, "price", PRICE_BUCKETS),
        "frac_elapsed": bucket_report(makers, "frac_elapsed", EXPIRY_FRACTION_BUCKETS),
        "market": bucket_report(makers, "asset"),
        "window_minutes": bucket_report(makers, "window_minutes"),
        "imbalance_toward_fill": bucket_report(
            makers, "imbalance_signed_toward_fill", IMBALANCE_BUCKETS
        ),
        "reduces_residual": bucket_report(makers, "reduces_residual"),
        "burst_count": bucket_report(makers, "burst_count", STREAK_BUCKETS),
        "streak_len_same_side": bucket_report(makers, "streak_len_same_side", STREAK_BUCKETS),
    }
    # Candidate acceptance policies over MAKER fills. A and B are composites of
    # individually stable buckets (chosen on train; the validate column is the
    # honest number). Unlike the pair-ceiling counterfactual, these are ex-ante
    # implementable: they correspond to which side/price you rest quotes on.
    def _policy(name, predicate):
        split = int(len(makers) * TRAIN_FRACTION)
        out = {"rule": name}
        for part_name, part in (("train", makers[:split]), ("validate", makers[split:])):
            kept = [e for e in part if predicate(e)]
            agg = _agg(kept)
            base = _agg(part)
            agg["kept_share_dollars"] = (
                round(agg["dollars"] / base["dollars"], 4) if base["dollars"] else None
            )
            agg["pnl_plus_modeled_rebate"] = round(agg["pnl"] + agg["modeled_maker_rebate"], 2)
            out[part_name] = agg
        return out

    def _imb(e):
        v = e.get("imbalance_signed_toward_fill")
        return 0.0 if v is None else v

    policies = [
        _policy("baseline_all_maker_fills", lambda e: True),
        _policy(
            "A_inventory_aware(reduces_residual or imbalance_toward_fill<-0.1)",
            lambda e: e["reduces_residual"] or _imb(e) < -0.1,
        ),
        _policy(
            "B_A_and_price>=0.30",
            lambda e: (e["reduces_residual"] or _imb(e) < -0.1) and e["price"] >= 0.30,
        ),
        _policy(
            "C_B_and_streak<3",
            lambda e: (e["reduces_residual"] or _imb(e) < -0.1)
            and e["price"] >= 0.30
            and e["streak_len_same_side"] < 3,
        ),
    ]

    by_duration = {
        f"{wm}m": {
            "maker": _agg([e for e in examples if e["is_maker"] and e["window_minutes"] == wm]),
            "taker": _agg([e for e in examples if not e["is_maker"] and e["window_minutes"] == wm]),
        }
        for wm in (5, 15)
    }

    result = {
        "era_start_ts": RECENT_START_TS,
        "train_fraction": TRAIN_FRACTION,
        "label_note": label_note,
        "counts": {
            "fills": len(examples),
            "maker": len(makers),
            "taker": len(takers),
            "maker_share_fills": round(len(makers) / len(examples), 4) if examples else None,
        },
        "aggregate": {"maker": _agg(makers), "taker": _agg(takers)},
        "rebate_model": {
            "maker_rebate_per_dollar": EMPIRICAL_MAKER_REBATE_PER_DOLLAR,
            "taker_rebate_per_dollar": EMPIRICAL_TAKER_REBATE_PER_DOLLAR,
            "basis": "empirical aggregate of observed rebate rows / observed volume; program formula unverified",
        },
        "by_duration": by_duration,
        "policy_evaluation": policies,
        "maker_features": features,
        "stability": {name: stable_sign(report) for name, report in features.items()},
    }
    out_path = Path(out_path or (config.OUTPUT_DIR / "l5_fill_toxicity.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":  # pragma: no cover
    summary = run()
    print(json.dumps(summary["counts"], indent=1))
    print(json.dumps(summary["aggregate"], indent=1))
