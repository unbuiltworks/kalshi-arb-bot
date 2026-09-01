"""Early-era cohort study: launch, refinement, and ramp (issue #167 follow-ups).

Answers the owner's Mar/Apr hypotheses with data, keeping fact vs inference
explicit:

  A. weekly_cohorts  — Mar 16 → May 31 from the FULL-history ledger windows
     (cashflow-grade: windows/day, notional, both-sides %, matched %, pair
     cost, pair vs residual economics, first-leg price/TTE, opposite-side
     gap, BTC/5m mix) plus dated incentive rows where source-supported.
  B. three_era       — explicit behavioral comparison of
     pre-transition (Mar 19–25) / transition (Mar 26) / systematic
     (Mar 27 → Apr 25, the span with complete per-fill sequencing).
  C. regime_similarity — state-distribution distances (total variation over
     the action-model state vocabulary) between early cohorts and the
     Apr and Aug sequenced cohorts: does Mar 27+ already look like the
     mature system?
  D. scaling_decomposition — is the ramp (a) same policy at larger
     size/more windows, (b) improved pair completion, (c) more incentive
     capture, or (d) a behavioral regime change? Measured, not asserted.
  E. refinement      — Mar 30–Apr 2 vs Apr 6–10 at fill-sequence level:
     first-leg price/TTE, opposite-side gap distribution, pair-cost
     trajectory through the window, residual share and outcome, size,
     price bands.

Maker/taker honesty: the takerOnly feed only covers Aug 28–31, so
maker/taker is UNKNOWN for the entire early era; nothing here infers it.

Offline and deterministic — reads only committed artifacts:
  analysis/l5/l5_ledger_windows.csv.gz, l5_ledger.json,
  l5_canonical_sequence.csv.gz.

    python3 -m src.polymarket_l5.early_era
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config
from .action_model import state_key, _relation

LEDGER_WINDOWS = config.OUTPUT_DIR / "l5_ledger_windows.csv.gz"
LEDGER_JSON = config.OUTPUT_DIR / "l5_ledger.json"
SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT = config.OUTPUT_DIR / "l5_early_era.json"

RAMP_END = "2026-05-31"

ERAS = (
    ("pre_transition", "2026-03-19", "2026-03-25"),
    ("transition", "2026-03-26", "2026-03-26"),
    ("systematic", "2026-03-27", "2026-04-25"),
)
REFINEMENT = (("mar30_apr2", "2026-03-30", "2026-04-02"),
              ("apr6_10", "2026-04-06", "2026-04-10"))
SIMILARITY_COHORTS = (
    ("mar19_26", "2026-03-19", "2026-03-26"),
    ("mar27_apr4", "2026-03-27", "2026-04-04"),
    ("apr5_25", "2026-04-05", "2026-04-25"),
    ("aug28_sep1", "2026-08-28", "2026-09-01"),
)


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 4) if xs else None


def _pct(xs: List[float], q: float) -> Optional[float]:
    xs = sorted(xs)
    return round(xs[min(int(q * len(xs)), len(xs) - 1)], 4) if xs else None


def load_ledger_windows() -> List[Dict[str, Any]]:
    rows = []
    with gzip.open(LEDGER_WINDOWS, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("is_updown") != "True":
                continue
            r["_day"] = _day(_f(r["first_ts"]))
            rows.append(r)
    return rows


def _window_stats(rows: List[Dict[str, Any]], income_rows: List[dict],
                  lo: str, hi: str) -> Dict[str, Any]:
    """Cashflow-grade cohort stats from ledger windows within [lo, hi] days."""
    sel = [r for r in rows if lo <= r["_day"] <= hi]
    if not sel:
        return {"n_windows": 0}
    days = sorted({r["_day"] for r in sel})
    buys = [_f(r["buy_usdc"]) for r in sel]
    both = [r for r in sel
            if _f(r["buy_up_qty"]) > 0 and _f(r["buy_down_qty"]) > 0]
    tot_q = sum(_f(r["buy_up_qty"]) + _f(r["buy_down_qty"]) for r in sel)
    matched = sum(_f(r["matched_qty"]) for r in sel)
    pcs = [_f(r["pair_cost"]) for r in sel if r["pair_cost"]]
    gaps = [_f(r["opposite_gap_s"]) for r in sel if r["opposite_gap_s"]]
    turnover = sum(buys)
    inc = defaultdict(float)
    lo_ts = datetime.strptime(lo, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    hi_ts = datetime.strptime(hi, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 86400
    for row in income_rows:
        if lo_ts <= row["ts"] < hi_ts:
            inc[row["type"]] += row["usdc"]
    return {
        "n_windows": len(sel),
        "n_days_active": len(days),
        "windows_per_active_day": round(len(sel) / len(days), 1),
        "n_fills": int(sum(_f(r["n_fills"]) for r in sel)),
        "fills_per_window_median": _median([_f(r["n_fills"]) for r in sel]),
        "turnover_usdc": round(turnover, 2),
        "window_buy_usdc_median": _median(buys),
        "window_buy_usdc_p90": _pct(buys, 0.90),
        "both_sides_pct": round(100 * len(both) / len(sel), 1),
        "matched_pct_qty": round(2 * matched / tot_q, 4) if tot_q else None,
        "pair_cost_median": _median(pcs),
        "pair_cost_p25": _pct(pcs, 0.25),
        "pair_cost_p75": _pct(pcs, 0.75),
        "opposite_gap_s_median": _median(gaps),
        "first_price_median": _median([_f(r["first_price"]) for r in sel
                                       if r["first_price"]]),
        "first_tte_s_median": _median([_f(r["first_tte_s"]) for r in sel
                                       if r["first_tte_s"]]),
        "net_trading_cash": round(sum(_f(r["net_trading_cash"]) for r in sel), 2),
        "pair_locked": round(sum(_f(r["pair_locked"]) for r in sel), 2),
        "residual_and_timing": round(
            sum(_f(r["residual_and_timing"]) for r in sel), 2),
        "incentives_usdc": {k: round(v, 2) for k, v in sorted(inc.items())},
        "incentives_total": round(sum(inc.values()), 2),
        "incentive_cents_per_turnover_dollar": round(
            100 * sum(inc.values()) / turnover, 3) if turnover else None,
        "trading_cents_per_turnover_dollar": round(
            100 * sum(_f(r["net_trading_cash"]) for r in sel) / turnover, 3)
            if turnover else None,
        "btc_share_windows": round(100 * sum(1 for r in sel
                                             if r["asset"] == "BTC") / len(sel), 1),
        "m5_share_windows": round(100 * sum(1 for r in sel
                                            if r["horizon_m"] == "5") / len(sel), 1),
    }


# ---------- canonical-sequence cohort machinery ----------

def _seq_windows(lo: str, hi: str) -> Iterable[List[Dict[str, str]]]:
    with gzip.open(SEQUENCE_CSV, "rt", newline="", encoding="utf-8") as fh:
        cur: List[Dict[str, str]] = []
        wid = None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur and lo <= _day(_f(cur[0]["timestamp"])) <= hi:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur and lo <= _day(_f(cur[0]["timestamp"])) <= hi:
            yield cur


def _seq_cohort(lo: str, hi: str) -> Dict[str, Any]:
    """Fill-sequence-level cohort stats (only valid inside sequenced spans)."""
    n_windows = 0
    fills_per_win: List[float] = []
    first_price: List[float] = []
    first_tte: List[float] = []
    opp_gap: List[float] = []
    traj: Dict[str, List[float]] = {"q25": [], "q50": [], "q75": [], "final": []}
    sizes: List[float] = []
    residual_share: List[float] = []
    resid_on_winner = resid_labelled = 0
    bands = Counter()
    hours = Counter()
    both_sides = 0
    for rows in _seq_windows(lo, hi):
        n_windows += 1
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        fills_per_win.append(len(rows))
        first = rows[0]
        first_price.append(_f(first["price"]))
        first_tte.append(_f(first["seconds_to_settlement"]))
        hours[datetime.fromtimestamp(
            _f(first["timestamp"]), tz=timezone.utc).hour] += 1
        first_out = first["outcome"]
        for r in rows[1:]:
            if r["outcome"] != first_out:
                opp_gap.append(_f(r["timestamp"]) - _f(first["timestamp"]))
                break
        # pair-cost trajectory at quartiles of the fill sequence
        for label, frac in (("q25", 0.25), ("q50", 0.50), ("q75", 0.75),
                            ("final", 1.0)):
            idx = min(int(frac * len(rows)) - 1 if frac == 1.0
                      else int(frac * len(rows)), len(rows) - 1)
            pc = rows[idx]["matched_pair_unit_cost"]
            if pc:
                traj[label].append(_f(pc))
        last = rows[-1]
        up_q, dn_q = _f(last["cum_up_qty"]), _f(last["cum_down_qty"])
        if up_q > 0 and dn_q > 0:
            both_sides += 1
        tot = up_q + dn_q
        if tot > 0:
            residual_share.append(abs(_f(last["residual_qty_signed"])) / tot)
        winner = last["settlement_outcome"]
        rside = last["residual_side"]
        if winner in ("Up", "Down") and rside:
            resid_labelled += 1
            if rside == winner:
                resid_on_winner += 1
        for r in rows:
            sizes.append(_f(r["quantity"]))
            p = _f(r["price"])
            b = ("<5c" if p < 0.05 else "5-15c" if p < 0.15 else
                 "15-85c" if p < 0.85 else "85-95c" if p < 0.95 else ">95c")
            bands[b] += 1
    top_hours = [h for h, _ in hours.most_common(6)]
    return {
        "n_windows": n_windows,
        "fills_per_window_median": _median(fills_per_win),
        "both_sides_pct": round(100 * both_sides / n_windows, 1) if n_windows else None,
        "first_price_median": _median(first_price),
        "first_tte_s_median": _median(first_tte),
        "opposite_gap_s": {"median": _median(opp_gap), "p25": _pct(opp_gap, .25),
                           "p75": _pct(opp_gap, .75), "p90": _pct(opp_gap, .90)},
        "pair_cost_trajectory_median": {k: _median(v) for k, v in traj.items()},
        "fill_size_median": _median(sizes),
        "residual_share_of_qty_median": _median(residual_share),
        "residual_on_winner_pct": round(100 * resid_on_winner / resid_labelled, 1)
            if resid_labelled else None,
        "residual_labelled_windows": resid_labelled,
        "price_band_fill_mix_pct": {b: round(100 * c / max(sum(bands.values()), 1), 1)
                                    for b, c in bands.most_common()},
        "top_activity_hours_utc": top_hours,
        "maker_taker": "UNKNOWN for this span (takerOnly feed covers Aug 28-31 only)",
    }


def _state_distribution(lo: str, hi: str) -> Tuple[Counter, Counter, int]:
    states: Counter = Counter()
    relations: Counter = Counter()
    n = 0
    for rows in _seq_windows(lo, hi):
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        for prev, nxt in zip(rows, rows[1:]):
            try:
                sk = state_key(_f(prev["price"]),
                               _f(nxt["seconds_to_settlement"]),
                               _f(prev["residual_qty_signed"]))
                rel = _relation(_f(prev["residual_qty_signed"]),
                                nxt["outcome"], nxt["side"])
            except (TypeError, ValueError):
                continue
            states[sk] += 1
            relations[rel] += 1
            n += 1
    return states, relations, n


def _tv(a: Counter, b: Counter) -> Optional[float]:
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return None
    keys = set(a) | set(b)
    return round(0.5 * sum(abs(a[k] / na - b[k] / nb) for k in keys), 4)


def main() -> int:
    windows = load_ledger_windows()
    ledger = json.loads(LEDGER_JSON.read_text())
    income_rows = ledger.get("income_rows") or []
    income_note = ("income_rows sum ${:.0f} vs income_total ${:.0f} — "
                   "rows are the source-dated stream".format(
                       sum(r["usdc"] for r in income_rows),
                       ledger.get("income_total", 0)))

    # A. weekly cohorts Mar 16 -> May 31 (full-history cashflow grade)
    weekly: Dict[str, Any] = {}
    import datetime as _dt
    d = _dt.date(2026, 3, 16)
    while d <= _dt.date.fromisoformat(RAMP_END):
        lo, hi = d.isoformat(), (d + _dt.timedelta(days=6)).isoformat()
        weekly[f"{lo}..{hi}"] = _window_stats(windows, income_rows, lo, hi)
        d += _dt.timedelta(days=7)

    # B. three-era comparison (ledger + sequence where sequenced)
    three_era: Dict[str, Any] = {}
    for name, lo, hi in ERAS:
        three_era[name] = {
            "span": f"{lo}..{hi}",
            "ledger": _window_stats(windows, income_rows, lo, hi),
            "sequence": _seq_cohort(lo, hi),
        }

    # C. regime similarity over action-model state vocabulary
    dists: Dict[str, Any] = {}
    coh: Dict[str, Tuple[Counter, Counter, int]] = {}
    for name, lo, hi in SIMILARITY_COHORTS:
        coh[name] = _state_distribution(lo, hi)
        dists[name] = {"decisions": coh[name][2],
                       "relation_mix": {k: round(v / max(coh[name][2], 1), 3)
                                        for k, v in coh[name][1].most_common()}}
    pairs = {}
    names = [c[0] for c in SIMILARITY_COHORTS]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs[f"{a}__vs__{b}"] = {
                "tv_states": _tv(coh[a][0], coh[b][0]),
                "tv_relations": _tv(coh[a][1], coh[b][1]),
            }

    # D. scaling decomposition: early-systematic vs late-ramp
    early = _window_stats(windows, income_rows, "2026-03-27", "2026-04-10")
    late = _window_stats(windows, income_rows, "2026-05-11", "2026-05-31")
    def _ratio(k):
        a, b = early.get(k), late.get(k)
        return round(b / a, 2) if a and b else None
    scaling = {
        "early_cohort": "2026-03-27..2026-04-10", "late_cohort": "2026-05-11..2026-05-31",
        "windows_per_day_ratio": _ratio("windows_per_active_day"),
        "window_notional_ratio": _ratio("window_buy_usdc_median"),
        "turnover_ratio": _ratio("turnover_usdc"),
        "matched_pct_early_late": [early.get("matched_pct_qty"),
                                   late.get("matched_pct_qty")],
        "pair_cost_median_early_late": [early.get("pair_cost_median"),
                                        late.get("pair_cost_median")],
        "incentive_cents_per_dollar_early_late": [
            early.get("incentive_cents_per_turnover_dollar"),
            late.get("incentive_cents_per_turnover_dollar")],
        "trading_cents_per_dollar_early_late": [
            early.get("trading_cents_per_turnover_dollar"),
            late.get("trading_cents_per_turnover_dollar")],
    }

    # E. refinement: Mar 30-Apr 2 vs Apr 6-10 (fill-sequence level)
    refinement: Dict[str, Any] = {}
    for name, lo, hi in REFINEMENT:
        refinement[name] = {
            "span": f"{lo}..{hi}",
            "ledger": _window_stats(windows, income_rows, lo, hi),
            "sequence": _seq_cohort(lo, hi),
        }

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [str(LEDGER_WINDOWS), str(LEDGER_JSON), str(SEQUENCE_CSV)],
        "income_note": income_note,
        "maker_taker_caveat": ("takerOnly coverage is Aug 28-31 only; "
                               "maker/taker is UNKNOWN for Mar-May"),
        "weekly_cohorts": weekly,
        "three_era": three_era,
        "regime_similarity": {"cohorts": dists, "pairwise_distance": pairs,
                              "metric": "total variation (0=identical, 1=disjoint)"},
        "scaling_decomposition": scaling,
        "refinement": refinement,
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"weekly_weeks": len(weekly),
                      "regime_similarity": pairs,
                      "scaling": scaling}, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
