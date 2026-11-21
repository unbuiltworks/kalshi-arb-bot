"""Bounded-extension study (issue #167, offline/replay only — nothing armed).

Motivating live state (probe v1probe-20260902-02, HISTORICAL/LIVE label:
settled): first NO fill @0.50, state p45-55c|tte2-5m|imb1-10, v1
complete=0.4659 / extend=0.5342 -> ABSTAIN_EXTEND_CAPPED under the q=1
residual cap -> one-sided hold, settled -$0.51 (realized at Gemini
settlement). Question: what does L5's own history say happens after
"one-sided inventory + next relation EXTEND", and what would bounded
alternatives to the hard cap have done?

Part A — EXTEND-EPISODE PATHS (canonical sequence, systematic regime):
  anchor = each window's FIRST transition where inventory is one-sided
  (|residual| >= 1 share) and the next observed fill EXTENDS the surplus
  side. From the anchor, follow the window: P(complement before end),
  latency to first complement, same-side extensions before it, max
  interim imbalance (shares and $), final matched/residual, effective
  pair cost when the complement arrives. Split BTC/ETH x 5m/15m.

Part B — COUNTERFACTUAL POLICIES at q=1 scale (MODELED, never realized):
  applied at the anchor of every episode, using L5's own subsequent fills
  as the opportunity tape and window settlement labels for outcomes:
    hold   = current ABSTAIN_EXTEND_CAPPED: keep 1 share of the first
             side to settlement.
    extend1= allow exactly ONE bounded same-side extension at L5's next
             same-side price (inventory 2), then complete both sides at
             L5's subsequent deficit-side prices if they arrive pre-end,
             else settle the residual.
    force  = forced economic completion: complement at the reserve bound
             (1 - entry - 1c); counted filled only if L5's tape later
             prints the deficit side AT OR BELOW that price before end.
  Maker fees (Gemini formula) applied to modeled fills; settlement pays
  $1 to the winning side. All figures are per-episode expectations from
  historical labels — MODELED P&L, explicitly not realized.

Part C — FIRST-LEG RESTING HORIZON (Gemini tape, all-participant prints):
  empirical P(>=1 contra taker print within T seconds) for a maker
  resting at top-of-book, per family, T in {30,45,60,90,120,180}s —
  computed by sliding window starts over the print tape. This bounds
  fill probability vs rest time (queue position ignored: upper bound).
  Probe base rates so far: fills 1/3 at 30-45s rests.

    python3 -m src.polymarket_l5.extension_study [--tape-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import config
from .action_model import SYSTEMATIC_REGIME_EPOCH

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT = config.OUTPUT_DIR / "l5_extension_study.json"

REST_HORIZONS = (30, 45, 60, 90, 120, 180)
MIN_EDGE = 0.01


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _fee(price: float, qty: float = 1.0) -> float:
    """Gemini predictions maker fee: 0.30 * 0.07 * q * p * (1-p), per-cent
    ceil at cent granularity approximated continuously (study-level)."""
    return 0.30 * 0.07 * qty * price * (1.0 - price)


def _median(xs):
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 3) if xs else None


def _windows() -> Iterable[List[Dict[str, str]]]:
    with gzip.open(SEQUENCE_CSV, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def _relation_of(prev_resid: float, outcome: str, side: str) -> str:
    if side == "SELL":
        return "sell"
    if abs(prev_resid) < 1.0:
        return "open"
    surplus = "Up" if prev_resid > 0 else "Down"
    return "extend" if outcome == surplus else "complete"


def analyze() -> Dict[str, Any]:
    episodes: List[dict] = []
    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        if _f(rows[0]["timestamp"]) < SYSTEMATIC_REGIME_EPOCH:
            continue
        winner = rows[-1]["settlement_outcome"]
        if winner not in ("Up", "Down"):
            continue
        anchor_i = None
        for i in range(len(rows) - 1):
            resid = _f(rows[i]["residual_qty_signed"])
            rel = _relation_of(resid, rows[i + 1]["outcome"], rows[i + 1]["side"])
            if abs(resid) >= 1.0 and rel == "extend":
                anchor_i = i
                break
        if anchor_i is None:
            continue
        a = rows[anchor_i]
        surplus_side = "Up" if _f(a["residual_qty_signed"]) > 0 else "Down"
        deficit_side = "Down" if surplus_side == "Up" else "Up"
        a_ts = _f(a["timestamp"])
        entry_price = _f(a["price"])          # last fill price at anchor
        # follow forward
        ext_count = ext_qty = 0.0
        first_comp_ts = first_comp_price = None
        max_imb_sh = abs(_f(a["residual_qty_signed"]))
        max_imb_usd = 0.0
        comp_prices: List[float] = []
        same_prices: List[float] = []
        for r in rows[anchor_i + 1:]:
            if r["side"] != "BUY":
                continue
            if r["outcome"] == surplus_side:
                if first_comp_ts is None:
                    ext_count += 1
                    ext_qty += _f(r["quantity"])
                same_prices.append(_f(r["price"]))
            else:
                if first_comp_ts is None:
                    first_comp_ts = _f(r["timestamp"])
                    first_comp_price = _f(r["price"])
                comp_prices.append(_f(r["price"]))
            imb = abs(_f(r["residual_qty_signed"]))
            max_imb_sh = max(max_imb_sh, imb)
            max_imb_usd = max(max_imb_usd, imb * _f(r["price"]))
        last = rows[-1]
        pc_final = last["matched_pair_unit_cost"]
        episodes.append({
            "asset": a["asset"], "dur": a["duration_minutes"],
            "winner": winner, "surplus_side": surplus_side,
            "entry_price": entry_price,
            "tte_at_anchor": _f(a["seconds_to_settlement"]),
            "complemented": first_comp_ts is not None,
            "latency_s": (first_comp_ts - a_ts) if first_comp_ts else None,
            "ext_before_comp": ext_count, "ext_qty_before_comp": ext_qty,
            "max_imb_sh": max_imb_sh, "max_imb_usd": max_imb_usd,
            "final_matched": _f(last["matched_qty"]),
            "final_residual": abs(_f(last["residual_qty_signed"])),
            "pair_cost_final": _f(pc_final) if pc_final else None,
            "first_comp_price": first_comp_price,
            "next_same_price": same_prices[0] if same_prices else None,
        })

    def agg(eps: List[dict]) -> Dict[str, Any]:
        n = len(eps)
        if not n:
            return {"episodes": 0}
        comp = [e for e in eps if e["complemented"]]
        return {
            "episodes": n,
            "p_complement_before_end": round(len(comp) / n, 3),
            "latency_to_first_complement_s": {
                "median": _median([e["latency_s"] for e in comp]),
                "p90": _median(sorted(e["latency_s"] for e in comp)
                               [int(0.8 * len(comp)):]) if comp else None},
            "same_side_extensions_before_complement": {
                "median": _median([e["ext_before_comp"] for e in comp]),
                "p90_qty": _median(sorted(e["ext_qty_before_comp"]
                                          for e in comp)[int(0.8 * len(comp)):])
                if comp else None},
            "max_interim_imbalance": {
                "shares_median": _median([e["max_imb_sh"] for e in eps]),
                "usd_median": _median([e["max_imb_usd"] for e in eps]),
                "usd_p90": _median(sorted(e["max_imb_usd"] for e in eps)
                                   [int(0.8 * n):])},
            "final_matched_median": _median([e["final_matched"] for e in eps]),
            "final_residual_median": _median([e["final_residual"] for e in eps]),
            "pair_cost_when_complemented_median": _median(
                [e["pair_cost_final"] for e in comp
                 if e["pair_cost_final"] is not None]),
        }

    by_split = defaultdict(list)
    for e in episodes:
        by_split[f"{e['asset']}-{e['dur']}m"].append(e)

    # ---- Part B: counterfactual policies at q=1 (MODELED) ----
    def settle(side: str, winner: str, price: float) -> float:
        pay = 1.0 if side == winner else 0.0
        return pay - price - _fee(price)

    cf = {"hold": [], "extend1": [], "force": []}
    force_completed = 0
    for e in episodes:
        s, w, p = e["surplus_side"], e["winner"], e["entry_price"]
        cf["hold"].append(settle(s, w, p))
        # extend1: one extension at L5's next same-side price (if any),
        # then complete both at first two deficit prices if they arrived
        ext_p = e["next_same_price"]
        if ext_p is None:
            cf["extend1"].append(settle(s, w, p))
        elif e["complemented"]:
            cp = e["first_comp_price"]
            pair1 = 1.0 - p - cp - _fee(p) - _fee(cp)
            pair2 = 1.0 - ext_p - cp - _fee(ext_p) - _fee(cp)
            cf["extend1"].append(pair1 + pair2)     # optimistic: both complete
        else:
            cf["extend1"].append(settle(s, w, p) + settle(s, w, ext_p))
        # force: complement at reserve bound, filled only if tape printed
        # the deficit side at or below that price later in the window
        bound = 1.0 - p - MIN_EDGE
        if (e["complemented"] and e["first_comp_price"] is not None
                and e["first_comp_price"] <= bound):
            force_completed += 1
            cp = e["first_comp_price"]
            cf["force"].append(1.0 - p - cp - _fee(p) - _fee(cp))
        else:
            cf["force"].append(settle(s, w, p))

    def cf_agg(vals):
        n = len(vals)
        return {"episodes": n,
                "mean_modeled_usd_per_episode": round(sum(vals) / n, 4),
                "median": _median(vals),
                "p_loss": round(sum(1 for v in vals if v < 0) / n, 3)} if n else {}

    return {
        "part_a_extend_episodes": {
            "overall": agg(episodes),
            "splits": {k: agg(v) for k, v in sorted(by_split.items())},
        },
        "part_b_counterfactual_q1_MODELED": {
            "note": ("per-episode expectations from historical labels and "
                     "L5's own fills as the opportunity tape; extend1 is "
                     "OPTIMISTIC (assumes both completions fill at L5's "
                     "prices); NEVER realized P&L"),
            "hold_ABSTAIN_EXTEND_CAPPED": cf_agg(cf["hold"]),
            "one_bounded_extension": cf_agg(cf["extend1"]),
            "forced_economic_completion": cf_agg(cf["force"]),
            "force_completion_rate": round(force_completed /
                                           max(len(episodes), 1), 3),
        },
    }


# ---------------- Part C: resting-horizon from Gemini tape ----------------

PTRADE_RE = re.compile(r"^GEMI-([A-Z]+)(\d{2}M)(\d{10})-UP$")


def resting_horizon(tape_dir: str) -> Dict[str, Any]:
    prints = defaultdict(list)     # family -> [(ts, taker_side)]
    for fn in glob.glob(f"{tape_dir}/reflag_*.jsonl"):
        for line in open(fn):
            if '"ptrade"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = PTRADE_RE.match(str(r.get("market") or ""))
            if not m:
                continue
            ts = (r.get("t_ns") or 0) / 1e9
            # buyer_is_maker True => taker SOLD (fills a resting bid)
            prints[f"{m.group(1)}-{m.group(2)}"].append(
                (ts, "sell" if r.get("buyer_is_maker") else "buy"))
    out: Dict[str, Any] = {"note": (
        "P(>=1 contra taker print within T s) from all-participant tape, "
        "sliding starts every 15s across the tape span; UPPER bound on a "
        "top-of-book maker's fill probability (queue priority ignored)")}
    for fam, ps in sorted(prints.items()):
        ps.sort()
        if len(ps) < 10:
            continue
        t0, t1 = ps[0][0], ps[-1][0]
        starts = [t0 + i * 15 for i in range(int((t1 - t0) / 15))]
        sells = [t for t, s in ps if s == "sell"]
        buys = [t for t, s in ps if s == "buy"]
        fam_out = {"prints": len(ps), "span_h": round((t1 - t0) / 3600, 1)}
        import bisect
        for label, arr in (("maker_bid_fill(taker_sell)", sells),
                           ("maker_ask_fill(taker_buy)", buys)):
            probs = {}
            for T in REST_HORIZONS:
                hit = sum(1 for s in starts
                          if bisect.bisect_right(arr, s + T)
                          - bisect.bisect_right(arr, s) > 0)
                probs[f"{T}s"] = round(hit / max(len(starts), 1), 3)
            fam_out[label] = probs
        out[fam] = fam_out
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape-dir", default="data/ref_lag")
    args = ap.parse_args()
    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "labels": {"part_a": "HISTORICAL (L5 canonical sequence)",
                   "part_b": "MODELED counterfactual (never realized)",
                   "part_c": "HISTORICAL tape, upper-bound estimate"},
        "sources": [str(SEQUENCE_CSV), args.tape_dir],
        **analyze(),
        "part_c_resting_horizon": resting_horizon(args.tape_dir),
    }
    OUT.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
