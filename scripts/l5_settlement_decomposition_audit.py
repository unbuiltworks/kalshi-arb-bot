"""Read-only settlement-anchored audit of L5's btc-updown-15m inventory.

Answers one question: is L5's inventory imbalance a demonstrated economic
advantage, or a description of how its realized gains were distributed?

Four parts, in the order the account owner asked for them:

  1. completeness   -- which captures can carry a per-window answer at all
  2. realized       -- per-window ledger anchored on ACTUAL settlements
  3. decomposition  -- matched-pair economics vs residual inventory, and
                       four baselines the residual must beat to be an edge
  4. scaling        -- whole-contract PMUS replay at $5/$20/$50/$100/$250

Reads only files under data/. Submits nothing, cancels nothing, settles
nothing, and writes no state the executor reads.

  python3 -I scripts/l5_settlement_decomposition_audit.py
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import random
import statistics as st
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, Iterator, List, Optional, Tuple

PREFIX = "btc-updown-15m-"
ACTIVITY = ("data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz")
TRADES = "data/l5/raw/trades.jsonl"
MASTER = "data/l5_master/btc15m_sequences.jsonl"

TICK = D("0.01")
MIN_QTY = D("0.01")
THETA = {"maker": D("-0.0125"), "taker": D("0.0695"), "zero": D("0")}
BUDGETS = (5, 20, 50, 100, 250)
SPAN_DAYS = 165.5  # 2026-03-19 .. 2026-08-31, from the capture's own timestamps


# --------------------------------------------------------------------------
# 1. completeness


def _pages(path: str) -> Iterator[Dict[str, Any]]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            yield json.loads(line)


def capture_completeness() -> Dict[str, Any]:
    """Offset-paginated captures silently truncate. Report it, never assume it."""
    out = {}
    for path in (TRADES,) + ACTIVITY:
        if not os.path.exists(path):
            out[path] = {"present": False}
            continue
        pages = 0
        rows = 0
        offsets: List[Any] = []
        for page in _pages(path):
            pages += 1
            payload = page.get("payload") or []
            rows += len(payload) if isinstance(payload, list) else 0
            offsets.append((page.get("params") or {}).get("offset"))
        numeric = [o for o in offsets if isinstance(o, int)]
        out[path] = {
            "present": True,
            "pages": pages,
            "rows": rows,
            "max_offset": max(numeric) if numeric else None,
            # A single monotonically rising offset run means one query that ran
            # out of pages -- i.e. a hard tail cut, not a complete history.
            "single_offset_run": bool(numeric) and numeric == sorted(numeric),
        }
    return out


# --------------------------------------------------------------------------
# 2. realized ledger from actual settlements


def _blank_window() -> Dict[str, Any]:
    return {
        "legs": 0,
        "buy_up_sh": 0.0,
        "buy_dn_sh": 0.0,
        "buy_up_usd": 0.0,
        "buy_dn_usd": 0.0,
        "sell_sh": 0.0,
        "redeem_usd": 0.0,
        "redeem_outcomes": collections.Counter(),
        "rebate_usd": 0.0,
    }


def realized_windows() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
    """Per-window cash in, cash out, and the settlement the venue actually paid.

    Cash uses `usdcSize` -- the amount that moved -- never size*price, whose
    reported price is rounded and does not reconcile to the cashflow.
    """
    windows: Dict[str, Dict[str, Any]] = collections.defaultdict(_blank_window)
    counts: collections.Counter = collections.Counter()
    for path in ACTIVITY:
        if not os.path.exists(path):
            continue
        for page in _pages(path):
            for row in page.get("payload") or []:
                slug = row.get("slug") or ""
                if not slug.startswith(PREFIX):
                    continue
                kind = row.get("type")
                counts[kind] += 1
                w = windows[slug]
                size = float(row.get("size") or 0.0)
                usd = float(row.get("usdcSize") or 0.0)
                outcome = (row.get("outcome") or "").lower()
                if kind == "TRADE":
                    w["legs"] += 1
                    if row.get("side") == "SELL":
                        w["sell_sh"] += size
                        continue
                    if outcome == "up":
                        w["buy_up_sh"] += size
                        w["buy_up_usd"] += usd
                    elif outcome == "down":
                        w["buy_dn_sh"] += size
                        w["buy_dn_usd"] += usd
                    else:
                        counts["trade_unreadable_outcome"] += 1
                elif kind == "REDEEM":
                    w["redeem_usd"] += usd
                    if size > 0 or usd > 0:
                        w["redeem_outcomes"][outcome] += 1
                elif kind in ("MAKER_REBATE", "TAKER_REBATE", "REWARD"):
                    w["rebate_usd"] += usd
    return dict(windows), dict(counts)


def window_records(windows: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    recs = []
    for slug, w in windows.items():
        up, dn = w["buy_up_sh"], w["buy_dn_sh"]
        up_usd, dn_usd = w["buy_up_usd"], w["buy_dn_usd"]
        oc = w["redeem_outcomes"]
        winner = {"up": "Up", "down": "Down"}.get(max(oc, key=oc.get)) if oc else None
        avg_up = up_usd / up if up else None
        avg_dn = dn_usd / dn if dn else None
        bigger = "Up" if up > dn else ("Down" if dn > up else "tie")
        avg_big = avg_up if bigger == "Up" else (avg_dn if bigger == "Down" else None)
        pair_cost = (avg_up + avg_dn) if (avg_up is not None and avg_dn is not None) else None
        matched = min(up, dn)
        residual = abs(up - dn)
        spend = up_usd + dn_usd
        recs.append(
            {
                "slug": slug,
                "winner": winner,
                "up_sh": up,
                "dn_sh": dn,
                "avg_up": avg_up,
                "avg_dn": avg_dn,
                "avg_big": avg_big,
                "bigger": bigger,
                "pair_cost": pair_cost,
                "matched_sh": matched,
                "residual_sh": residual,
                "spend_usd": spend,
                "redeem_usd": w["redeem_usd"],
                "rebate_usd": w["rebate_usd"],
                "net_usd": w["redeem_usd"] + w["rebate_usd"] - spend,
                "sell_sh": w["sell_sh"],
                "legs": w["legs"],
                # matched pairs redeem $1 whichever way the window settles
                "matched_pnl": (matched * (1.0 - pair_cost)) if pair_cost is not None else None,
                "residual_pnl": (
                    residual * ((1.0 if bigger == winner else 0.0) - avg_big)
                    if avg_big is not None
                    else 0.0
                ),
            }
        )
    return recs


# --------------------------------------------------------------------------
# 3. baselines


def baselines(recs: List[Dict[str, Any]], *, seed: int = 20261016, trials: int = 400) -> Dict[str, Any]:
    """What the residual must beat. A residual that loses to a coin flip at the
    same prices and sizes is a cost, not an edge."""
    usable = [r for r in recs if r["pair_cost"] is not None and r["winner"] and r["bigger"] != "tie"]
    tot = lambda k: sum((r[k] or 0.0) for r in usable)
    matched_spend = sum(r["matched_sh"] * r["pair_cost"] for r in usable)
    residual_spend = sum(r["residual_sh"] * r["avg_big"] for r in usable)
    up_rate = sum(1 for r in usable if r["winner"] == "Up") / len(usable)
    rng = random.Random(seed)

    random_winner = []
    random_side = []
    for _ in range(trials):
        a = b = 0.0
        for r in usable:
            drawn = "Up" if rng.random() < up_rate else "Down"
            a += r["residual_sh"] * ((1.0 if r["bigger"] == drawn else 0.0) - r["avg_big"])
            side = "Up" if rng.random() < 0.5 else "Down"
            px = r["avg_up"] if side == "Up" else r["avg_dn"]
            b += r["residual_sh"] * ((1.0 if side == r["winner"] else 0.0) - px)
        random_winner.append(a)
        random_side.append(b)

    def _fixed(pick_dearer: bool) -> float:
        total = 0.0
        for r in usable:
            dearer = "Up" if r["avg_up"] > r["avg_dn"] else "Down"
            side = dearer if pick_dearer else ("Down" if dearer == "Up" else "Up")
            px = r["avg_up"] if side == "Up" else r["avg_dn"]
            total += r["residual_sh"] * ((1.0 if side == r["winner"] else 0.0) - px)
        return total

    cheaper = lambda r: "Up" if r["avg_up"] < r["avg_dn"] else "Down"
    return {
        "windows": len(usable),
        "spend_usd": tot("spend_usd"),
        "l5_actual_net": tot("net_usd"),
        "matched_only_net": tot("matched_pnl"),
        "matched_spend_usd": matched_spend,
        "residual_net": tot("residual_pnl"),
        "residual_spend_usd": residual_spend,
        "matched_sh": tot("matched_sh"),
        "residual_sh": tot("residual_sh"),
        "mean_pair_cost": st.mean(r["pair_cost"] for r in usable),
        "median_pair_cost": st.median(r["pair_cost"] for r in usable),
        "pairs_below_one_pct": 100.0 * sum(1 for r in usable if r["pair_cost"] < 1.0) / len(usable),
        "bigger_side_won_pct": 100.0 * sum(1 for r in usable if r["bigger"] == r["winner"]) / len(usable),
        "bigger_side_implied_pct": 100.0 * st.mean(r["avg_big"] for r in usable),
        "bigger_is_cheaper_pct": 100.0 * sum(1 for r in usable if r["bigger"] == cheaper(r)) / len(usable),
        "up_base_rate_pct": 100.0 * up_rate,
        "baseline_price_fair_net": 0.0,  # E = resid*(avg_big - avg_big), exactly zero
        "baseline_random_winner_net": st.mean(random_winner),
        "baseline_random_winner_sd": st.pstdev(random_winner),
        "baseline_random_side_net": st.mean(random_side),
        "baseline_random_side_sd": st.pstdev(random_side),
        "baseline_always_dearer_net": _fixed(True),
        "baseline_always_cheaper_net": _fixed(False),
    }


# --------------------------------------------------------------------------
# 4. whole-contract capital scaling


def _order_fee(theta: D, qty: D, price: D, *, cent_rounding: bool = True) -> D:
    """Kalshi/PMUS fee, per ORDER, rounded away from zero to the cent.

    `cent_rounding=False` gives the unrounded fee. The distinction is not
    cosmetic: on a 0.01-contract order the true maker rebate is ~$0.00003 and
    cent rounding inflates it ~300x, which is enough on its own to turn the
    small-budget scaling rows positive. Read both.
    """
    if qty <= 0:
        return D("0")
    raw = theta * qty * price * (D(1) - price)
    if not cent_rounding:
        return raw
    if raw == 0:
        return D("0")
    q = raw.quantize(D("0.01"), rounding=ROUND_UP if raw > 0 else ROUND_DOWN)
    if q == 0:
        q = D("0.01") if raw > 0 else D("-0.01")
    return q


def build_sequences() -> List[Dict[str, Any]]:
    """Derive the per-leg window sequences straight from data/l5/raw.

    MASTER is a 375MB derivation, so it is not tracked and may be absent. It
    is fully reproducible from the raw capture, which is the source of truth,
    so rebuild rather than skip section 4. Cash per leg is `usdcSize`; `cost`
    is the window's actual cash, not a price*qty reconstruction.
    """
    legs: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    cost: collections.Counter = collections.Counter()
    winner: Dict[str, Optional[str]] = {}
    for path in ACTIVITY:
        if not os.path.exists(path):
            continue
        for page in _pages(path):
            for row in page.get("payload") or []:
                slug = row.get("slug") or ""
                if not slug.startswith(PREFIX):
                    continue
                outcome = (row.get("outcome") or "").lower()
                side = {"up": "Up", "down": "Down"}.get(outcome)
                if row.get("type") == "TRADE" and row.get("side") == "BUY" and side:
                    legs[slug].append(
                        {
                            "t": float(row.get("timestamp") or 0),
                            "side": side,
                            "price": float(row.get("price") or 0.0),
                            "qty": float(row.get("size") or 0.0),
                        }
                    )
                    cost[slug] += float(row.get("usdcSize") or 0.0)
                elif row.get("type") == "REDEEM" and side:
                    if (float(row.get("size") or 0) > 0) or (float(row.get("usdcSize") or 0) > 0):
                        winner.setdefault(slug, side)
    out = []
    for slug, trades in legs.items():
        if winner.get(slug) not in ("Up", "Down") or cost[slug] <= 0:
            continue
        out.append(
            {
                "slug": slug,
                "winner": winner[slug],
                "cost": cost[slug],
                "trades": sorted(trades, key=lambda t: t["t"]),
            }
        )
    return out


def load_sequences() -> List[Dict[str, Any]]:
    if not os.path.exists(MASTER):
        return build_sequences()
    seqs = []
    for line in open(MASTER):
        r = json.loads(line)
        if r.get("trades") and (r.get("cost") or 0) > 0 and r.get("winner") in ("Up", "Down"):
            seqs.append(r)
    return seqs


def scale_replay(
    seqs: List[Dict[str, Any]], budget: int, mode: str, *, cent_rounding: bool = True
) -> Dict[str, Any]:
    """Replay each window's own leg sequence at a fixed cash budget.

    Prices and timing are L5's. Quantity is floored to the venue increment, so
    legs that no longer reach the minimum are DROPPED, not rounded up.
    """
    theta = THETA[mode]
    net = spend = fees = D("0")
    kept = dropped = windows = winning = 0
    matched = residual = D("0")
    for r in seqs:
        factor = D(str(budget)) / D(str(r["cost"]))
        up = dn = cash = fee_total = D("0")
        legs = 0
        for t in r["trades"]:
            qty = (D(str(t["qty"])) * factor).quantize(MIN_QTY, rounding=ROUND_DOWN)
            price = D(str(t["price"])).quantize(TICK)
            if qty < MIN_QTY or price <= 0 or price >= 1:
                dropped += 1
                continue
            legs += 1
            cash += qty * price
            fee_total += _order_fee(theta, qty, price, cent_rounding=cent_rounding)
            if t["side"] == "Up":
                up += qty
            else:
                dn += qty
        if legs == 0:
            continue
        windows += 1
        kept += legs
        payout = up if r["winner"] == "Up" else dn
        window_net = payout - cash - fee_total
        net += window_net
        spend += cash
        fees += fee_total
        matched += min(up, dn)
        residual += abs(up - dn)
        if window_net > 0:
            winning += 1
    rebate = -fees if fees < 0 else D("0")
    return {
        "budget": budget,
        "mode": mode,
        "cent_rounding": cent_rounding,
        "windows": windows,
        "legs_kept": kept,
        "legs_dropped": dropped,
        "spend_usd": float(spend),
        "fees_usd": float(fees),
        "rebate_usd": float(rebate),
        "trading_net_usd": float(net + fees),  # net before the fee/rebate line
        "net_usd": float(net),
        "pct_of_spend": float(net / spend * 100) if spend else 0.0,
        "per_day_usd": float(net) / SPAN_DAYS,
        "win_window_pct": 100.0 * winning / windows if windows else 0.0,
        "matched_sh": float(matched),
        "residual_sh": float(residual),
    }


def replay_calibration(seqs: List[Dict[str, Any]], actual_net: float, actual_spend: float) -> Dict[str, Any]:
    """A replay pays $1.00 for every winning share. The venue did not: some
    winning shares were never redeemed in the capture. Measure the gap rather
    than quietly inheriting it into the scaling table."""
    net = spend = D("0")
    for r in seqs:
        up = dn = cash = D("0")
        for t in r["trades"]:
            qty = D(str(t["qty"]))
            price = D(str(t["price"]))
            cash += qty * price
            if t["side"] == "Up":
                up += qty
            else:
                dn += qty
        payout = up if r["winner"] == "Up" else dn
        net += payout - cash
        spend += cash
    replay_pct = float(net / spend * 100) if spend else None
    actual_pct = 100.0 * actual_net / actual_spend if actual_spend else None
    return {
        "replay_net_usd": float(net),
        "replay_spend_usd": float(spend),
        "replay_pct_of_spend": replay_pct,
        "actual_pct_of_spend": actual_pct,
        "haircut": (actual_pct / replay_pct) if (replay_pct and actual_pct) else None,
    }


# --------------------------------------------------------------------------


def main() -> None:
    print("=" * 78)
    print("1. CAPTURE COMPLETENESS")
    print("=" * 78)
    for path, info in capture_completeness().items():
        if not info["present"]:
            print("  %-44s ABSENT" % path)
            continue
        print(
            "  %-44s pages %5d rows %7d max_offset %-7s %s"
            % (
                path,
                info["pages"],
                info["rows"],
                info["max_offset"],
                "SINGLE OFFSET RUN -> TAIL TRUNCATED" if info["single_offset_run"] else "time-sliced",
            )
        )

    windows, counts = realized_windows()
    recs = window_records(windows)
    print()
    print("=" * 78)
    print("2. REALIZED, ANCHORED ON ACTUAL SETTLEMENTS")
    print("=" * 78)
    print("  row types: %s" % counts)
    tot = lambda k: sum((r[k] or 0.0) for r in recs)
    settled = [r for r in recs if r["winner"]]
    print("  windows %d (settlement winner known for %d)" % (len(recs), len(settled)))
    print("  SELL shares across the whole history: %.2f" % tot("sell_sh"))
    print(
        "  spend $%.2f  redeemed $%.2f  rebates $%.2f  NET $%+.2f = %+.4f%% of spend"
        % (tot("spend_usd"), tot("redeem_usd"), tot("rebate_usd"), tot("net_usd"), 100 * tot("net_usd") / tot("spend_usd"))
    )
    print("  $%+.2f/day over %.1f days" % (tot("net_usd") / SPAN_DAYS, SPAN_DAYS))
    pos = sum(1 for r in recs if r["net_usd"] > 0)
    print("  profitable windows %d (%.1f%%)  median window $%+.2f" % (pos, 100 * pos / len(recs), st.median(r["net_usd"] for r in recs)))

    b = baselines(recs)
    print()
    print("=" * 78)
    print("3. DECOMPOSITION AND BASELINES  (%d both-sided settled windows)" % b["windows"])
    print("=" * 78)
    print("  mean pair cost %.4f  median %.4f  below $1.00 in %.1f%% of windows" % (b["mean_pair_cost"], b["median_pair_cost"], b["pairs_below_one_pct"]))
    print("  matched  %10.0f sh  spend $%11.2f  PnL $%+11.2f (%+.4f%%)" % (b["matched_sh"], b["matched_spend_usd"], b["matched_only_net"], 100 * b["matched_only_net"] / b["matched_spend_usd"]))
    print("  residual %10.0f sh  spend $%11.2f  PnL $%+11.2f (%+.4f%%)" % (b["residual_sh"], b["residual_spend_usd"], b["residual_net"], 100 * b["residual_net"] / b["residual_spend_usd"]))
    print("  sum $%+.2f vs actual net $%+.2f (unredeemed/rounding gap $%.2f)" % (b["matched_only_net"] + b["residual_net"], b["l5_actual_net"], b["matched_only_net"] + b["residual_net"] - b["l5_actual_net"]))
    print()
    print("  bigger inventory side won %.2f%% of windows, priced at %.2f%% implied" % (b["bigger_side_won_pct"], b["bigger_side_implied_pct"]))
    print("  bigger side was also the cheaper side %.2f%% of the time" % b["bigger_is_cheaper_pct"])
    print("  Up won %.2f%% of windows" % b["up_base_rate_pct"])
    print()
    print("  what the residual must beat, same shares / prices / timing:")
    print("    L5 actual residual                      $%+12.2f" % b["residual_net"])
    print("    price-fair expectation                   $%+12.2f" % b["baseline_price_fair_net"])
    print("    randomized winner, L5's side choice      $%+12.2f  (sd %.2f)" % (b["baseline_random_winner_net"], b["baseline_random_winner_sd"]))
    print("    randomized side, actual winners          $%+12.2f  (sd %.2f)" % (b["baseline_random_side_net"], b["baseline_random_side_sd"]))
    print("    always load the dearer side              $%+12.2f" % b["baseline_always_dearer_net"])
    print("    always load the cheaper side             $%+12.2f" % b["baseline_always_cheaper_net"])

    seqs = load_sequences()
    print()
    print("=" * 78)
    print("4. WHOLE-CONTRACT CAPITAL SCALING ON PMUS  (%d sequences)" % len(seqs))
    print("=" * 78)
    if not seqs:
        print("  SKIPPED: no sequences, from %s or from %s." % (MASTER, ACTIVITY[0]))
        return
    print("  source: %s" % (MASTER if os.path.exists(MASTER) else "rebuilt from " + ACTIVITY[0]))
    cal = replay_calibration(seqs, tot("net_usd"), tot("spend_usd"))
    print("  replay basis pays $1.00 per winning share: %+.4f%% of spend" % cal["replay_pct_of_spend"])
    print("  venue actually realized:                   %+.4f%% of spend" % cal["actual_pct_of_spend"])
    print("  -> replay overstates; haircut x%.3f applies to the trading line below" % cal["haircut"])
    print()
    print(
        "  %-7s %-6s %8s %9s %11s %10s %11s %10s %9s"
        % ("budget", "mode", "windows", "legs", "spend", "rebate", "trading", "net", "%spend")
    )
    rows = []
    for budget in BUDGETS:
        for mode in ("maker", "taker"):
            d = scale_replay(seqs, budget, mode)
            rows.append(d)
            print(
                "  $%-6d %-6s %8d %9d %11.2f %10.2f %11.2f %10.2f %+8.4f%%"
                % (budget, mode, d["windows"], d["legs_kept"], d["spend_usd"], d["rebate_usd"], d["trading_net_usd"], d["net_usd"], d["pct_of_spend"])
            )
    print()
    print("  after haircut (trading line x%.3f, rebate line unchanged):" % cal["haircut"])
    for d in rows:
        tr = 100 * d["trading_net_usd"] / d["spend_usd"]
        rb = 100 * d["rebate_usd"] / d["spend_usd"]
        fe = 100 * d["fees_usd"] / d["spend_usd"]
        adj = tr * cal["haircut"] + (rb if d["mode"] == "maker" else -fe)
        print("    $%-6d %-6s %+8.4f%% of spend   legs dropped %6d (%.1f%%)" % (d["budget"], d["mode"], adj, d["legs_dropped"], 100 * d["legs_dropped"] / (d["legs_kept"] + d["legs_dropped"])))

    print()
    print("  EXACT (unrounded) fees -- the figure that is not a cent-rounding artifact:")
    print("    %-7s %-6s %11s %10s %11s %10s" % ("budget", "mode", "fees", "trading", "net", "haircut%"))
    for budget in BUDGETS:
        for mode in ("maker", "taker"):
            d = scale_replay(seqs, budget, mode, cent_rounding=False)
            tr = 100 * d["trading_net_usd"] / d["spend_usd"]
            adj = tr * cal["haircut"] - 100 * d["fees_usd"] / d["spend_usd"]
            print(
                "    $%-6d %-6s %11.2f %11.2f %11.2f %+9.4f%%"
                % (budget, mode, d["fees_usd"], d["trading_net_usd"], d["net_usd"], adj)
            )


if __name__ == "__main__":
    main()
