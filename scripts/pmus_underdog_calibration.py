"""Underdog executable price x time calibration for PMUS BTC15m.

THE QUESTION. At predeclared checkpoints inside a window, does the executable
ASK of the CHEAPER outcome understate how often that outcome actually settles?
This is descriptive calibration. It selects no trading rule, searches no
timestamp, and tunes no boundary.

WHAT MAKES AN OBSERVATION LEGITIMATE, AND WHAT IS REFUSED.

  * Checkpoints are FIXED IN ADVANCE at 14, 12, 10, 8, 6, 5, 4, 3, 2 and 1
    minutes remaining, measured from the venue's own window end. Nothing was
    swept to find a good one.
  * ONE observation per window per checkpoint. The quote used is the LAST book
    state whose timestamp is at or before the checkpoint instant -- the most
    recent thing a trader could have seen. Every other quote inside the
    checkpoint is ignored, so a busy window cannot outvote a quiet one.
  * STRICTLY CAUSAL. A row stamped after the checkpoint is never used, so no
    future information enters. Staleness (checkpoint minus row timestamp) is
    recorded on every observation rather than assumed small.
  * NO FABRICATION. A window/checkpoint with no causal book row is EXCLUDED and
    counted as excluded. Nothing is filled in from a midpoint, a last trade, a
    settlementPrice, a later book state, or the settlement row. A missing quote
    is missing.

THE TWO EXECUTABLE PRICES, FROM ONE BOOK. The recorded book is the Yes/UP side.
A matched UP+DOWN pair pays exactly $1.00, so buying DOWN at the touch means
taking the other side of the displayed UP bid:

    UP   executable ask = ask         capacity = ask_qty
    DOWN executable ask = 1 - bid     capacity = bid_qty

That identity is why both sides' executable prices and both capacities come from
the same snapshot, and why UP_ask + DOWN_ask = 1 + (ask - bid) >= 1: the pair
excess IS the spread. A consequence worth stating: the cheaper side is NOT
always below 50c. When the spread is wide, both sides can exceed it. Those
observations fall OUTSIDE the predeclared buckets and are reported in their own
out-of-range row rather than folded into the 45-50c bucket.

FEES. `pmus_fees.taker_fee` at the venue cent, the treatment already established
in this programme. The fee is convex and cent-rounded, so it is computed on the
whole clip and never as a per-unit fee times quantity.

THIS IS NOT A MARTINGALE AND CONTAINS NO LOSS-DEPENDENT SIZING. The sizing model
below sets one stake per qualifying observation from that observation's own price
and the depth displayed at that moment. Nothing about it depends on whether
earlier windows won or lost.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import io
import json
import math
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_fees as FEES                                 # noqa: E402

ONE, ZERO = D(1), D(0)

#: Fixed in advance, in minutes remaining. Never searched.
CHECKPOINTS_MIN = (14, 12, 10, 8, 6, 5, 4, 3, 2, 1)

#: Predeclared price buckets, in cents, as [lo, hi).
BUCKETS = ((0, 10), (10, 15), (15, 20), (20, 25), (25, 30),
           (30, 35), (35, 40), (40, 45), (45, 50))

#: The fixed-profit target the sizing model solves for, in dollars of NET profit
#: on a settlement win. Declared here, not tuned.
TARGET_NET_WIN = D(5)

#: Standing per-window capital bound for the programme. Breaches are REPORTED,
#: not silently clipped, because the question of whether the target is reachable
#: at all is part of the answer.
CEILING = D(200)


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def _epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    if v in (None, ""):
        return None
    try:
        return dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def _dec(v):
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def load_settlements(path: str) -> dict:
    """slug -> {outcome, window_end_epoch, provenance}. Resolved windows only."""
    out = {}
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if r.get("outcome") not in ("UP", "DOWN"):
                continue
            # The venue's OWN window end is preferred over the slug-derived one.
            # They were verified equal on all 1,168 harvested rows, so this is a
            # provenance choice, not a correction.
            end = _epoch(r.get("venue_window_end")) or _epoch(r.get("window_end"))
            if end is None:
                continue
            out[r["slug"]] = {
                "outcome": r["outcome"],
                "window_end_epoch": end,
                "window_end": r.get("venue_window_end") or r.get("window_end"),
                "market_id": r.get("market_id"),
                "price_to_beat": r.get("price_to_beat"),
                "final_price": r.get("final_price"),
                "outcome_source": r.get("outcome_source"),
                "settlement_status": r.get("status"),
            }
    return out


def load_books(path: str) -> dict:
    """slug -> ascending list of usable book snapshots.

    A snapshot is usable only with a timestamp and BOTH sides quoted: a
    one-sided book has no executable price for the missing side, and guessing
    one is exactly the fabrication this study refuses.
    """
    by = collections.defaultdict(list)
    kept = dropped = 0
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if r.get("kind") != "book" or not r.get("slug"):
                continue
            t = _epoch(r.get("t"))
            bid, ask = _dec(r.get("bid")), _dec(r.get("ask"))
            if t is None or bid is None or ask is None:
                dropped += 1
                continue
            by[r["slug"]].append({
                "t": t, "bid": bid, "ask": ask,
                "bid_qty": _dec(r.get("bid_qty")),
                "ask_qty": _dec(r.get("ask_qty")),
                "status": r.get("status"),
                "ask_book_missing": r.get("ask_book_missing"),
            })
            kept += 1
    for v in by.values():
        v.sort(key=lambda x: x["t"])
    return {"books": dict(by), "kept": kept, "dropped_unusable": dropped}


def causal_quote(rows: list, at: float):
    """The newest snapshot at or before `at`, by binary search. None if none."""
    lo, hi, best = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid]["t"] <= at:
            best = rows[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def observe(books: dict, settle: dict) -> dict:
    """One causal observation per window per checkpoint."""
    obs, excl = [], collections.Counter()
    for slug, s in sorted(settle.items()):
        rows = books.get(slug)
        if not rows:
            excl["no_book_rows_for_window"] += len(CHECKPOINTS_MIN)
            continue
        end = s["window_end_epoch"]
        for m in CHECKPOINTS_MIN:
            at = end - m * 60.0
            q = causal_quote(rows, at)
            if q is None:
                excl["no_causal_quote_at_checkpoint"] += 1
                continue
            # Both executable prices from the one snapshot, per the identity
            # documented above.
            up_ask, up_cap = q["ask"], q["ask_qty"]
            dn_ask, dn_cap = ONE - q["bid"], q["bid_qty"]
            if up_ask <= dn_ask:
                cheap, cheap_px, cheap_cap = "UP", up_ask, up_cap
                other_px, other_cap = dn_ask, dn_cap
            else:
                cheap, cheap_px, cheap_cap = "DOWN", dn_ask, dn_cap
                other_px, other_cap = up_ask, up_cap
            if cheap_px <= ZERO or cheap_px >= ONE:
                # A degenerate touch carries no probability information and no
                # tradable price; excluded rather than bucketed.
                excl["degenerate_cheaper_price"] += 1
                continue
            obs.append({
                "slug": slug,
                "market_id": s["market_id"],
                "checkpoint_min_remaining": m,
                "checkpoint_epoch": at,
                "checkpoint_utc": dt.datetime.fromtimestamp(
                    at, dt.timezone.utc).isoformat(),
                "source_quote_epoch": q["t"],
                "source_quote_utc": dt.datetime.fromtimestamp(
                    q["t"], dt.timezone.utc).isoformat(),
                "staleness_s": round(at - q["t"], 3),
                "book_status": q["status"],
                "cheaper_outcome": cheap,
                "cheaper_executable_ask": str(cheap_px),
                "cheaper_touch_qty": (None if cheap_cap is None
                                      else str(cheap_cap)),
                "opposite_executable_ask": str(other_px),
                "opposite_touch_qty": (None if other_cap is None
                                       else str(other_cap)),
                "displayed_bid": str(q["bid"]),
                "displayed_ask": str(q["ask"]),
                "spread": str(q["ask"] - q["bid"]),
                "pair_cost": str(up_ask + dn_ask),
                "settled_outcome": s["outcome"],
                "underdog_won": (cheap == s["outcome"]),
                "window_end": s["window_end"],
                "settlement_source": s["outcome_source"],
                "settlement_status": s["settlement_status"],
                "price_to_beat": s["price_to_beat"],
                "final_price": s["final_price"],
            })
    return {"observations": obs, "exclusions": dict(excl)}


def wilson(k: int, n: int, z: float = 1.959963985):
    """Wilson score interval: it stays inside [0,1] and behaves at small n,
    which the normal interval does not -- and several cells here are small."""
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 4), round((c + h) / d, 4))


def bucket_of(px: D):
    cents = float(px) * 100.0
    for lo, hi in BUCKETS:
        if lo <= cents < hi:
            return "%d-%dc" % (lo, hi) if lo else "<10c"
    return "OUT_OF_RANGE_>=50c"


def cell_stats(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    pxs = [D(r["cheaper_executable_ask"]) for r in rows]
    wins = sum(1 for r in rows if r["underdog_won"])
    avg_px = sum(pxs) / n
    implied = avg_px                       # price IS the implied probability
    obs_rate = D(wins) / D(n)
    lo, hi = wilson(wins, n)

    # Hold-to-settlement P&L for $1 OF RISK, i.e. one dollar of premium paid.
    # Per contract at price p: win pays 1 - p, loss costs p. Normalising by the
    # premium paid makes cells at different prices comparable.
    gross = ZERO
    fees = ZERO
    for r in rows:
        p = D(r["cheaper_executable_ask"])
        qty = ONE / p                      # $1 of premium at this price
        gross += (ONE - p) * qty if r["underdog_won"] else -p * qty
        # The venue fee on a $1-premium clip. Reported separately from gross and
        # never netted into it silently.
        try:
            fees += D(str(FEES.taker_fee(p, qty, cents=True)))
        except Exception:                                      # noqa: BLE001
            pass
    return {
        "n": n,
        "avg_executable_ask": str(avg_px.quantize(D("0.0001"))),
        "underdog_wins": wins,
        "observed_win_pct": float((obs_rate * 100).quantize(D("0.01"))),
        "avg_price_implied_pct": float((implied * 100).quantize(D("0.01"))),
        "observed_minus_implied_pp": float(
            ((obs_rate - implied) * 100).quantize(D("0.01"))),
        "win_pct_ci95_wilson": [None if lo is None else round(lo * 100, 2),
                                None if hi is None else round(hi * 100, 2)],
        "implied_inside_ci": (None if lo is None
                             else bool(lo <= float(implied) <= hi)),
        "raw_pnl_per_dollar_risked": float(
            (gross / D(n)).quantize(D("0.0001"))),
        "fees_per_dollar_risked": float((fees / D(n)).quantize(D("0.0001"))),
        "net_pnl_per_dollar_risked": float(
            ((gross - fees) / D(n)).quantize(D("0.0001"))),
    }


def size_for_target(px: D, cap, target: D = TARGET_NET_WIN):
    """Smallest whole-contract size whose settlement WIN nets >= `target`.

    Solved by walking up from the fee-free lower bound, because the fee is
    convex and cent-rounded: dividing the target by a per-unit margin gives the
    wrong answer, and a closed form would have to re-derive the venue's rounding.
    The walk is bounded so a hopeless price cannot spin.
    """
    margin = ONE - px
    if margin <= ZERO:
        return None, None, "no_margin"
    q = int((target / margin)) or 1
    for _ in range(100000):
        try:
            fee = D(str(FEES.taker_fee(px, D(q), cents=True)))
        except Exception:                                      # noqa: BLE001
            return None, None, "fee_unavailable"
        if margin * D(q) - fee >= target:
            stake = (px * D(q)).quantize(D("0.01"))
            if cap is not None and D(q) > cap:
                return q, stake, "exceeds_touch_depth"
            if stake > CEILING:
                return q, stake, "exceeds_200_ceiling"
            return q, stake, "achievable"
        q += 1
    return None, None, "unreachable"


def sizing_model(obs: list) -> dict:
    """One stake per qualifying observation, from that observation's OWN price
    and the depth displayed at that moment.

    NOT a Martingale: no term anywhere depends on prior outcomes. Stakes are not
    increased after a loss and not reduced after a win.

    "Qualifying" is deliberately the weakest possible filter -- the price is
    inside the predeclared buckets and the target is reachable within the
    displayed touch and the ceiling. No probability cut, no time cut, no spread
    cut, because choosing one after seeing these outcomes is the optimisation
    this pass must not do.
    """
    per_cp = collections.defaultdict(list)
    for r in obs:
        per_cp[r["checkpoint_min_remaining"]].append(r)

    out = {}
    for m, rows in sorted(per_cp.items()):
        rows = sorted(rows, key=lambda r: r["checkpoint_epoch"])
        taken, blocked = [], collections.Counter()
        for r in rows:
            px = D(r["cheaper_executable_ask"])
            if bucket_of(px).startswith("OUT_OF_RANGE"):
                blocked["price_outside_predeclared_buckets"] += 1
                continue
            cap = (None if r["cheaper_touch_qty"] is None
                   else D(r["cheaper_touch_qty"]))
            q, stake, why = size_for_target(px, cap)
            if why != "achievable":
                blocked[why] += 1
                continue
            fee = D(str(FEES.taker_fee(px, D(q), cents=True)))
            net = ((ONE - px) * D(q) - fee) if r["underdog_won"] \
                else (-(px * D(q)) - fee)
            taken.append({**r, "size": q, "stake": str(stake),
                          "fee": str(fee), "net": net})

        if not taken:
            out["%dmin" % m] = {"qualifying": 0, "blocked": dict(blocked)}
            continue

        wins = sum(1 for t in taken if t["underdog_won"])
        nets = [t["net"] for t in taken]
        total = sum(nets)
        # Chronological drawdown and loss run. Reported because a positive total
        # with a deep trough is not a tradable result.
        cum = ZERO
        peak = ZERO
        maxdd = ZERO
        run = worst_run = 0
        for t in taken:
            cum += t["net"]
            peak = max(peak, cum)
            maxdd = max(maxdd, peak - cum)
            if t["underdog_won"]:
                run = 0
            else:
                run += 1
                worst_run = max(worst_run, run)
        days = len({r["slug"][len("cpc-btc-updown-15m-"):][:10] for r in taken})
        out["%dmin" % m] = {
            "qualifying": len(taken),
            "blocked": dict(blocked),
            "wins": wins, "losses": len(taken) - wins,
            "win_pct": round(100.0 * wins / len(taken), 2),
            "mean_required_stake": str((sum(D(t["stake"]) for t in taken)
                                        / D(len(taken))).quantize(D("0.01"))),
            "max_required_stake": str(max(D(t["stake"]) for t in taken)),
            "mean_size": round(sum(t["size"] for t in taken) / len(taken), 2),
            "min_size": min(t["size"] for t in taken),
            "max_size": max(t["size"] for t in taken),
            "net_pnl": str(total.quantize(D("0.01"))),
            "net_per_window": str((total / D(len(taken))).quantize(D("0.01"))),
            "days_covered": days,
            "net_per_day": (None if not days
                            else str((total / D(days)).quantize(D("0.01")))),
            "opportunities_per_day": (None if not days
                                      else round(len(taken) / days, 2)),
            "max_consecutive_losses": worst_run,
            "max_drawdown": str(maxdd.quantize(D("0.01"))),
            # Each window is one position held to settlement, so the capital at
            # risk at once is bounded by the stakes of windows whose lives
            # overlap. At one checkpoint per window and 15-minute windows, no two
            # positions of the SAME checkpoint overlap, so the aggregate is the
            # single largest stake. Stated explicitly because it would be wrong
            # to sum them.
            "max_simultaneous_capital": str(max(D(t["stake"]) for t in taken)),
            "aggregate_capital_if_all_checkpoints_traded_note":
                "this row is ONE checkpoint; trading several checkpoints in the "
                "same window would overlap and the capital would add",
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True,
                    help="derived book/trade rows (pmus_oos_derive_rows output)")
    ap.add_argument("--settlements", required=True,
                    help="settlement-history rows with venue-resolved outcomes")
    ap.add_argument("--label", default="EXECUTABLE")
    ap.add_argument("--out", default="analysis/l5/underdog_calibration")
    ap.add_argument("--dump-observations", default=None)
    a = ap.parse_args()

    settle = load_settlements(a.settlements)
    bk = load_books(a.books)
    books = bk["books"]

    # Only windows for which BOTH a resolved settlement and book rows exist can
    # contribute. The intersection is reported so the coverage is visible.
    both = sorted(set(books) & set(settle))
    settle_used = {s: settle[s] for s in both}

    res = observe(books, settle_used)
    obs = res["observations"]
    if not obs:
        print("NO OBSERVATIONS -- no window had a causal quote at any checkpoint")
        return 1

    # The calibration matrix: checkpoint x predeclared price bucket.
    cells = collections.defaultdict(list)
    for r in obs:
        cells[(r["checkpoint_min_remaining"],
               bucket_of(D(r["cheaper_executable_ask"])))].append(r)

    bucket_names = ["<10c"] + ["%d-%dc" % b for b in BUCKETS[1:]] \
        + ["OUT_OF_RANGE_>=50c"]
    matrix = {}
    for m in CHECKPOINTS_MIN:
        matrix["%dmin" % m] = {b: cell_stats(cells.get((m, b), []))
                               for b in bucket_names}
    # Pooled across checkpoints, per bucket: the broadness test. A real
    # mispricing should show in a bucket across many checkpoints, not in one cell.
    pooled_bucket = {b: cell_stats([r for r in obs
                                    if bucket_of(D(r["cheaper_executable_ask"]))
                                    == b])
                     for b in bucket_names}
    pooled_checkpoint = {"%dmin" % m: cell_stats(
        [r for r in obs if r["checkpoint_min_remaining"] == m])
        for m in CHECKPOINTS_MIN}

    stale = sorted(r["staleness_s"] for r in obs)
    doc = {
        "LABEL": "PMUS_BTC15M_UNDERDOG_PRICE_X_TIME_CALIBRATION",
        "sample": a.label,
        "DESCRIPTIVE_CALIBRATION_NOT_STRATEGY_SELECTION": True,
        "separate_from": ["178-window generating study",
                          "43-window executable holdout",
                          "settlement streak study", "Gemini"],
        "checkpoints_min_remaining": list(CHECKPOINTS_MIN),
        "price_buckets": bucket_names,
        "windows_with_resolved_settlement": len(settle),
        "windows_with_book_rows": len(books),
        "windows_usable_both": len(both),
        "book_rows_kept": bk["kept"],
        "book_rows_dropped_unusable": bk["dropped_unusable"],
        "observations": len(obs),
        "observations_possible": len(both) * len(CHECKPOINTS_MIN),
        "exclusions": res["exclusions"],
        "staleness_s": {
            "min": stale[0], "median": stale[len(stale) // 2],
            "p90": stale[int(len(stale) * 0.90)], "max": stale[-1],
        },
        "cheaper_side_counts": dict(collections.Counter(
            r["cheaper_outcome"] for r in obs)),
        "matrix_checkpoint_x_bucket": matrix,
        "pooled_by_bucket_all_checkpoints": pooled_bucket,
        "pooled_by_checkpoint_all_buckets": pooled_checkpoint,
        "sizing_model_target_net_win_usd": str(TARGET_NET_WIN),
        "sizing_model": sizing_model(obs),
        "labels": (
            "One causal observation per window per checkpoint: the newest book "
            "state at or before the checkpoint. No window/checkpoint without "
            "such a quote is filled in from a midpoint, last trade, "
            "settlementPrice or later book state -- it is excluded and counted. "
            "The cheaper side's price IS its implied probability. Capacity is "
            "the DISPLAYED TOUCH only; the tape carries no ladder, so larger "
            "size is unknowable, and no market impact, queue position, partial "
            "fill or adverse selection is modelled. Nothing here is a filled, "
            "settled or realized number. The sizing model contains no "
            "loss-dependent term and is not a Martingale."),
    }

    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "underdog_calibration_%s.json" % a.label.lower())
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    if a.dump_observations:
        with gzip.open(a.dump_observations, "wt", encoding="utf-8") as f:
            for r in obs:
                f.write(json.dumps(r, default=str) + "\n")
    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("matrix_checkpoint_x_bucket", "sizing_model",
                                   "pooled_by_bucket_all_checkpoints",
                                   "pooled_by_checkpoint_all_buckets")},
                     indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
