"""TWO-SIDED BASE + FAVORITE MOMENTUM + PULLBACK EXIT.

THIS IS NOT THE STRATEGY THAT WAS REJECTED. The hold-to-settlement
favorite/flip rule lost because after a leadership flip it held both legs
each bought above 0.50, so every matched pair cost more than $1.00 and
the matched portion was a guaranteed loss. That test deliberately had NO
exit rule, so its -$0.395/window result says nothing about a strategy
whose whole point is selling the extension back. It is kept here only as
arm 1, the negative control.

THE HYPOTHESIS HERE is that the intra-window price move itself can be
harvested: establish a small two-sided base, extend into the favored side
while it strengthens, and SELL THE EXTENSION when it retreats -- rather
than accumulating expensive shares and holding them into settlement.

THE SELL IS THE WHOLE STRATEGY, SO THE SELL IS MODELLED HONESTLY:
  * every BUY walks the historical ASK ladder, upward, paying each level;
  * every SELL walks the historical BID ladder, downward, receiving each
    level -- a displayed 0.65 is not 0.65 of proceeds unless the bid
    depth is there;
  * the pullback is measured on the BID, not on the mid or the ask,
    because the bid is what we could actually sell into. Measuring the
    high on an unachievable price would manufacture the entire edge.
  * a one-sided book cannot be sold into at all, which the dataset's own
    README warns is common late in a window.

NO MARK-TO-MARKET IS EVER CALLED REALIZED. Extension P&L is realized only
by an executed walk down the bid ladder. Anything still held at expiry
settles at the venue's own `is_winning_outcome` and is reported as
settlement P&L, separately.

SEPARATE ACCOUNTING, because they are different businesses: BASE
inventory (two-sided, held to settlement) and EXTENSION inventory
(directional, sold on the pullback) never share a ledger line.

THRESHOLDS ARE CHOSEN ON TRAIN ONLY. The 33 windows are split by window;
the pullback threshold is selected on the training windows and then
reported on the held-out ones. Selecting it on all 33 would be the
hindsight this design exists to avoid.

THE AMBIGUITY, IMPLEMENTED BOTH WAYS rather than guessed: the spec says
exit "the directional extension", leaving the base alone. That is the
primary arm. "Liquidate everything including the base" is a materially
different strategy, so it is run as its OWN labelled variant and never
merged.

Data: Zenodo 22084977 updown_15m.parquet, CC-BY-4.0, sha256
d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959.

Accounting label: historical replay on third-party public data. No order
was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "MOMENTUM_PULLBACK_BACKTEST_V1"
PARQUET_SHA256 = ("d86191506e416026f460338b1b238c628b3fcee37f468e646b3a"
                  "2256bd0be959")
PULLBACKS = (0.02, 0.03, 0.05)
FEE_PER_DOLLAR_SENSITIVITY = 0.02
TRAIN_FRAC = 0.6


def load(path: str, coin: Optional[str] = "btc"
         ) -> Dict[str, Dict[str, Any]]:
    """Both outcomes' FULL bid and ask ladders, second by second.

    `coin=None` loads EVERY coin in the file. The default stays "btc" so the
    original 33-window run reproduces byte-for-byte.
    """
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=[
        "market_slug", "coin", "token_id", "outcome", "is_winning_outcome",
        "seconds_to_close", "ask_prices", "ask_sizes", "bid_prices",
        "bid_sizes", "best_bid", "best_ask"])
    d = t.to_pydict()
    wins: Dict[str, Dict[str, Any]] = {}
    for i in range(len(d["market_slug"])):
        if coin is not None and d["coin"][i] != coin:
            continue
        slug = d["market_slug"][i]
        w = wins.setdefault(slug, {"slug": slug, "winning_outcome": None,
                                   "ticks": {}})
        out = str(d["outcome"][i])
        stc = d["seconds_to_close"][i]
        if stc is None:
            continue
        # THE PRODUCER'S OWN FLAG decides the winner. `winner` is
        # "yes"/"no" while `outcome` is "Up"/"Down"; mapping that string
        # myself is what made every window a 100% loss in the first
        # favorite/flip run.
        if bool(d["is_winning_outcome"][i]):
            w["winning_outcome"] = out
        w["ticks"].setdefault(round(float(stc), 0), {})[out] = {
            "ask_prices": list(d["ask_prices"][i] or []),
            "ask_sizes": list(d["ask_sizes"][i] or []),
            "bid_prices": list(d["bid_prices"][i] or []),
            "bid_sizes": list(d["bid_sizes"][i] or []),
            "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
        }
    return wins


def _mid(s: Dict[str, Any]) -> Optional[float]:
    b, a = s.get("best_bid"), s.get("best_ask")
    return None if (b is None or a is None) else (b + a) / 2.0


def buy_dollars(side: Dict[str, Any], dollars: float) -> Dict[str, Any]:
    """Walk the ASK ladder upward. No midpoint, no touch-equals-fill."""
    remaining, shares, spent = dollars, 0.0, 0.0
    for p, s in sorted(zip(side.get("ask_prices") or [],
                           side.get("ask_sizes") or []),
                       key=lambda t: t[0]):
        if remaining <= 1e-12 or p is None or s is None or p <= 0:
            break
        take = min(float(s), remaining / p)
        if take <= 0:
            continue
        shares += take
        spent += take * p
        remaining -= take * p
    return {"shares": shares, "spent": spent,
            "vwap": (spent / shares if shares else None),
            "shortfall": max(0.0, dollars - spent)}


def sell_shares(side: Dict[str, Any], shares: float) -> Dict[str, Any]:
    """Walk the BID ladder DOWNWARD. This is where a marked profit dies:
    the best bid holds only its own size, and the rest is sold lower."""
    remaining, sold, proceeds = shares, 0.0, 0.0
    for p, s in sorted(zip(side.get("bid_prices") or [],
                           side.get("bid_sizes") or []),
                       key=lambda t: -t[0]):
        if remaining <= 1e-12 or p is None or s is None or p <= 0:
            break
        take = min(float(s), remaining)
        if take <= 0:
            continue
        sold += take
        proceeds += take * p
        remaining -= take
    return {"sold": sold, "proceeds": proceeds,
            "vwap": (proceeds / sold if sold else None),
            "unsold": max(0.0, shares - sold)}


def run_window(w: Dict[str, Any], *, cadence: float, base_dollars: float,
               ext_dollars: float, pullback: Optional[float],
               use_base: bool, liquidate_base_too: bool = False
               ) -> Dict[str, Any]:
    """One window. `pullback=None` means hold extensions to settlement."""
    stcs = sorted(w["ticks"], reverse=True)
    winner = w["winning_outcome"]
    if not stcs or winner is None:
        return {"slug": w["slug"], "skipped": True,
                "why": "no ticks or no winning outcome flag"}

    base_q: Dict[str, float] = collections.defaultdict(float)
    base_c: Dict[str, float] = collections.defaultdict(float)
    ext_q: Dict[str, float] = collections.defaultdict(float)
    ext_c: Dict[str, float] = collections.defaultdict(float)
    ext_realized = 0.0
    gross_bought = 0.0
    n_ext = n_exits = 0
    peak_bid: Dict[str, Optional[float]] = {}
    lead_flips = 0
    last_lead: Optional[str] = None
    peak_capital = 0.0
    peak_dir_exposure = 0.0
    unsold_at_exit = 0.0
    # ---- INSTRUMENTATION ONLY. None of these feed a decision; they are
    # added so slippage and bid depth are MEASURED rather than asserted.
    entry_slippage = 0.0       # paid above the displayed ask touch
    exit_slippage = 0.0        # received below the displayed bid touch
    exit_touch_proceeds = 0.0  # what touch-equals-fill would have claimed
    exit_bid_depth_shares: List[float] = []
    exit_bid_depth_dollars: List[float] = []
    exit_bid_levels: List[float] = []
    n_exits_partial = 0
    n_exits_nothing = 0
    events: List[Dict[str, Any]] = []

    # ---- BASE: the first instant both sides are buyable
    if use_base:
        for stc in stcs:
            tick = w["ticks"][stc]
            if len(tick) < 2:
                continue
            got = {o: buy_dollars(tick[o], base_dollars) for o in tick}
            if all(g["shares"] > 0 for g in got.values()):
                for o, g in got.items():
                    base_q[o] += g["shares"]
                    base_c[o] += g["spent"]
                    gross_bought += g["spent"]
                events.append({"stc": stc, "kind": "BASE",
                               "cost": round(sum(g["spent"]
                                                 for g in got.values()), 4)})
                break

    targets: List[float] = []
    t = stcs[0]
    while t > 0:
        targets.append(t)
        t -= cadence

    for want in targets:
        avail = [s for s in stcs if s <= want]
        if not avail:
            continue
        stc = max(avail)
        tick = w["ticks"][stc]
        px = {o: _mid(s) for o, s in tick.items()}
        live = {o: p for o, p in px.items() if p is not None}
        fav = max(live, key=lambda o: live[o]) if live else None
        if fav is not None and live[fav] <= 0.50:
            fav = None
        if fav is not None:
            if last_lead is not None and fav != last_lead:
                lead_flips += 1
            last_lead = fav

        # ---- EXIT FIRST: a pullback or a leadership loss closes the
        # extension before anything new is added, so one tick never both
        # tops up and unwinds the same position.
        for side_name in [o for o in list(ext_q) if ext_q[o] > 1e-9]:
            s = tick.get(side_name)
            if s is None:
                continue
            bid = s.get("best_bid")
            if bid is not None:
                prev = peak_bid.get(side_name)
                peak_bid[side_name] = bid if prev is None else max(prev, bid)
            hi = peak_bid.get(side_name)
            lost_lead = (fav != side_name)
            retreated = (pullback is not None and hi is not None
                         and bid is not None and (hi - bid) >= pullback)
            if not (retreated or lost_lead):
                continue
            got = sell_shares(s, ext_q[side_name])
            bsz = [float(x) for x in (s.get("bid_sizes") or [])
                   if x is not None]
            bpx = [float(x) for x in (s.get("bid_prices") or [])
                   if x is not None]
            exit_bid_depth_shares.append(sum(bsz))
            exit_bid_depth_dollars.append(sum(
                px_ * sz_ for px_, sz_ in zip(bpx, bsz)))
            exit_bid_levels.append(float(len(bsz)))
            if got["sold"] <= 0:
                # THE VENUE'S OWN NO-LIQUIDITY STATE, in the historical
                # tape: we wanted out and the bid side could not take any
                # of it. Counted, never silently skipped.
                n_exits_nothing += 1
                continue
            if got["unsold"] > 1e-9:
                n_exits_partial += 1
            if bid is not None:
                exit_touch_proceeds += got["sold"] * bid
                exit_slippage += got["sold"] * bid - got["proceeds"]
            basis = (ext_c[side_name] / ext_q[side_name]) * got["sold"]
            ext_realized += got["proceeds"] - basis
            ext_c[side_name] -= basis
            ext_q[side_name] -= got["sold"]
            unsold_at_exit += got["unsold"]
            n_exits += 1
            peak_bid[side_name] = None
            events.append({"stc": stc, "kind": "EXIT", "side": side_name,
                           "sold": round(got["sold"], 4),
                           "proceeds": round(got["proceeds"], 4),
                           "basis": round(basis, 4),
                           "reason": ("PULLBACK" if retreated
                                      else "LEADERSHIP_LOST"),
                           "unsold": round(got["unsold"], 4)})
            if liquidate_base_too and base_q[side_name] > 1e-9:
                g2 = sell_shares(s, base_q[side_name])
                if g2["sold"] > 0:
                    b2 = (base_c[side_name] / base_q[side_name]) * g2["sold"]
                    ext_realized += g2["proceeds"] - b2
                    base_c[side_name] -= b2
                    base_q[side_name] -= g2["sold"]
                    events.append({"stc": stc, "kind": "EXIT_BASE",
                                   "side": side_name,
                                   "sold": round(g2["sold"], 4),
                                   "proceeds": round(g2["proceeds"], 4)})

        # ---- EXTEND: favored AND strengthening on the bid we could exit
        if fav is not None:
            s = tick[fav]
            bid = s.get("best_bid")
            hi = peak_bid.get(fav)
            strengthening = (ext_q[fav] <= 1e-9 or hi is None or bid is None
                             or bid >= hi)
            if strengthening:
                got = buy_dollars(s, ext_dollars)
                if got["shares"] > 0:
                    ask = s.get("best_ask")
                    if ask is not None:
                        entry_slippage += got["spent"] - got["shares"] * ask
                    ext_q[fav] += got["shares"]
                    ext_c[fav] += got["spent"]
                    gross_bought += got["spent"]
                    n_ext += 1
                    if bid is not None:
                        prev = peak_bid.get(fav)
                        peak_bid[fav] = (bid if prev is None
                                         else max(prev, bid))
                    events.append({"stc": stc, "kind": "EXTEND",
                                   "side": fav,
                                   "shares": round(got["shares"], 4),
                                   "cost": round(got["spent"], 4),
                                   "vwap": got["vwap"]})
        held = sum(base_c.values()) + sum(ext_c.values())
        peak_capital = max(peak_capital, held)
        peak_dir_exposure = max(peak_dir_exposure, sum(ext_c.values()))

    # ---- SETTLEMENT on whatever is still held, read from the venue flag
    base_matched = (min(base_q.values()) if len(base_q) > 1 else 0.0)
    base_cost = sum(base_c.values())
    base_payout = base_q.get(winner, 0.0)
    base_pnl = base_payout - base_cost
    base_pair_cost = None
    if base_matched > 0 and len(base_q) > 1:
        base_pair_cost = sum(base_c[o] / base_q[o] for o in base_q)
    ext_held_cost = sum(ext_c.values())
    ext_settle = ext_q.get(winner, 0.0) - ext_held_cost
    total_net = base_pnl + ext_realized + ext_settle
    return {
        "slug": w["slug"], "winner": winner,
        "lead_flips": lead_flips,
        "n_extensions": n_ext, "n_exits": n_exits,
        "base_cost": round(base_cost, 4),
        "base_matched_shares": round(base_matched, 4),
        "base_pair_cost": (None if base_pair_cost is None
                           else round(base_pair_cost, 4)),
        "base_pnl_settlement": round(base_pnl, 4),
        "extension_realized_pnl": round(ext_realized, 4),
        "extension_held_to_settlement_cost": round(ext_held_cost, 4),
        "extension_settlement_pnl": round(ext_settle, 4),
        "gross_bought": round(gross_bought, 4),
        "peak_capital": round(peak_capital, 4),
        "peak_directional_exposure": round(peak_dir_exposure, 4),
        "unsold_shares_at_exit": round(unsold_at_exit, 4),
        "no_trade_window": (n_ext == 0),
        "entry_slippage_dollars": round(entry_slippage, 4),
        "exit_slippage_dollars": round(exit_slippage, 4),
        "exit_touch_only_proceeds": round(exit_touch_proceeds, 4),
        "exit_bid_depth_shares_mean": (
            None if not exit_bid_depth_shares
            else round(statistics.fmean(exit_bid_depth_shares), 2)),
        "exit_bid_depth_dollars_mean": (
            None if not exit_bid_depth_dollars
            else round(statistics.fmean(exit_bid_depth_dollars), 2)),
        "exit_bid_levels_mean": (
            None if not exit_bid_levels
            else round(statistics.fmean(exit_bid_levels), 2)),
        "n_exits_partial": n_exits_partial,
        "n_exits_nothing_sellable": n_exits_nothing,
        "total_net_zero_fees": round(total_net, 4),
        "fee_sensitivity": round(gross_bought
                                 * FEE_PER_DOLLAR_SENSITIVITY, 4),
        "total_net_with_fee_sensitivity": round(
            total_net - gross_bought * FEE_PER_DOLLAR_SENSITIVITY, 4),
        "events": events,
    }


def _st(xs: Sequence[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "worst": round(s[0], 4),
            "p10": round(s[max(0, len(s) // 10)], 4),
            "median": round(statistics.median(s), 4),
            "p90": round(s[min(len(s) - 1, 9 * len(s) // 10)], 4),
            "best": round(s[-1], 4), "mean": round(statistics.fmean(s), 4),
            "total": round(sum(s), 4)}


def _dd(nets: Sequence[float]) -> float:
    cum = peak = dd = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 4)


def summarise(rows: List[Dict[str, Any]], arm: str) -> Dict[str, Any]:
    rows = [r for r in rows if not r.get("skipped")]
    nets = [r["total_net_zero_fees"] for r in rows]
    n = len(rows)
    by_flip: Dict[str, Any] = {}
    for key, pred in (("0_flips", lambda f: f == 0),
                      ("1_flip", lambda f: f == 1),
                      ("2_flips", lambda f: f == 2),
                      ("3+_flips", lambda f: f >= 3)):
        sub = [r for r in rows if pred(r["lead_flips"])]
        by_flip[key] = {
            "windows": len(sub),
            "net": _st([r["total_net_zero_fees"] for r in sub]),
            "extension_realized": _st([r["extension_realized_pnl"]
                                       for r in sub]),
            "base_pnl": _st([r["base_pnl_settlement"] for r in sub]),
        }
    return {
        "arm": arm, "windows": n,
        "net_per_window": (None if not n else round(sum(nets) / n, 4)),
        "est_net_per_day": (None if not n
                            else round(sum(nets) / n * 96, 2)),
        "win_rate_pct": (None if not n else round(
            100.0 * sum(1 for x in nets if x > 0) / n, 1)),
        "net_distribution": _st(nets),
        "max_drawdown": _dd(nets),
        "base_pnl_settlement": _st([r["base_pnl_settlement"] for r in rows]),
        "base_pair_cost": _st([r["base_pair_cost"] for r in rows]),
        "extension_realized_pnl": _st([r["extension_realized_pnl"]
                                       for r in rows]),
        "extension_settlement_pnl": _st([r["extension_settlement_pnl"]
                                         for r in rows]),
        "peak_capital": _st([r["peak_capital"] for r in rows]),
        "peak_directional_exposure": _st([r["peak_directional_exposure"]
                                          for r in rows]),
        "n_extensions": _st([float(r["n_extensions"]) for r in rows]),
        "n_exits": _st([float(r["n_exits"]) for r in rows]),
        "unsold_shares_at_exit": _st([r["unsold_shares_at_exit"]
                                      for r in rows]),
        "lead_flips": _st([float(r["lead_flips"]) for r in rows]),
        "no_trade_windows": sum(1 for r in rows if r.get("no_trade_window")),
        "entry_slippage_dollars": _st([r["entry_slippage_dollars"]
                                       for r in rows]),
        "exit_slippage_dollars": _st([r["exit_slippage_dollars"]
                                      for r in rows]),
        "exit_slippage_pct_of_touch": (
            None if not sum(r["exit_touch_only_proceeds"] for r in rows)
            else round(100.0 * sum(r["exit_slippage_dollars"] for r in rows)
                       / sum(r["exit_touch_only_proceeds"] for r in rows), 3)),
        "exit_bid_depth_shares_mean": _st([r["exit_bid_depth_shares_mean"]
                                           for r in rows]),
        "exit_bid_depth_dollars_mean": _st([r["exit_bid_depth_dollars_mean"]
                                            for r in rows]),
        "exit_bid_levels_mean": _st([r["exit_bid_levels_mean"]
                                     for r in rows]),
        "exits_partial": sum(r["n_exits_partial"] for r in rows),
        "exits_nothing_sellable": sum(r["n_exits_nothing_sellable"]
                                      for r in rows),
        "fees": {"base_case": 0.0,
                 "why_zero": ("no authenticated Polymarket fee measurement "
                              "exists in this repository"),
                 "sensitivity_per_dollar": FEE_PER_DOLLAR_SENSITIVITY,
                 "net_per_window_with_sensitivity": (
                     None if not n else round(
                         sum(r["total_net_with_fee_sensitivity"]
                             for r in rows) / n, 4))},
        "by_flip_count": by_flip,
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--cadence", type=float, default=60.0)
    ap.add_argument("--base", type=float, default=1.00)
    ap.add_argument("--ext", type=float, default=1.00)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    wins = load(a.parquet)
    slugs = sorted(wins)
    cut = int(len(slugs) * TRAIN_FRAC)
    train, test = slugs[:cut], slugs[cut:]

    def arm(name: str, **kw) -> Dict[str, Any]:
        return {
            "train": summarise([run_window(wins[s], cadence=a.cadence,
                                           **kw) for s in train], name),
            "test": summarise([run_window(wins[s], cadence=a.cadence,
                                          **kw) for s in test], name),
            "all33": summarise([run_window(wins[s], cadence=a.cadence,
                                           **kw) for s in slugs], name),
        }

    arms: Dict[str, Any] = {}
    # ARM 1 -- the NEGATIVE CONTROL: no base, no exit, hold everything.
    arms["1_control_hold_to_settlement"] = arm(
        "favorite/flip hold-to-settlement (negative control)",
        base_dollars=0.0, ext_dollars=a.ext, pullback=None, use_base=False)
    # ARM 2 -- momentum WITHOUT a two-sided base, with the pullback exit.
    for pb in PULLBACKS:
        arms["2_momentum_no_base_pb%gc" % (pb * 100)] = arm(
            "favorite momentum, no base, %gc pullback exit" % (pb * 100),
            base_dollars=0.0, ext_dollars=a.ext, pullback=pb,
            use_base=False)
    # ARM 3 -- the PROPOSED strategy: base + momentum + pullback exit.
    for pb in PULLBACKS:
        arms["3_base_momentum_pb%gc" % (pb * 100)] = arm(
            "two-sided base + momentum + %gc pullback exit" % (pb * 100),
            base_dollars=a.base, ext_dollars=a.ext, pullback=pb,
            use_base=True)
    # ARM 4 -- THE OTHER READING of "cash out once it dips": liquidate the
    # base as well. A materially different strategy, labelled, not merged.
    for pb in PULLBACKS:
        arms["4_liquidate_all_pb%gc" % (pb * 100)] = arm(
            "base + momentum, %gc pullback liquidates BASE TOO" % (pb * 100),
            base_dollars=a.base, ext_dollars=a.ext, pullback=pb,
            use_base=True, liquidate_base_too=True)

    # THRESHOLD CHOSEN ON TRAIN ONLY
    cand = {k: v for k, v in arms.items() if k.startswith("3_")}
    pick = max(cand, key=lambda k: (cand[k]["train"]["net_per_window"]
                                    or -9e9))
    rep = {
        "label": LABEL, "parquet_sha256": PARQUET_SHA256,
        "cadence_s": a.cadence, "base_dollars": a.base,
        "ext_dollars": a.ext,
        "n_windows": len(slugs),
        "split": {"train_windows": len(train), "test_windows": len(test),
                  "train_frac": TRAIN_FRAC,
                  "rule": "split BY WINDOW; the pullback threshold is "
                          "selected on TRAIN and reported on TEST"},
        "selected_on_train": pick,
        "selected_test_net_per_window": cand[pick]["test"]["net_per_window"],
        "execution": ("BUYS walk the ask ladder upward; SELLS walk the BID "
                      "ladder downward; the pullback high is measured on "
                      "the BID because that is what we could sell into"),
        "realized_discipline": ("extension P&L is realized ONLY by an "
                                "executed walk down the bid ladder; nothing "
                                "held is called realized"),
        "ambiguity_resolved_both_ways": (
            "arm 3 exits the EXTENSION only, leaving the base, which is "
            "the specification. Arm 4 liquidates the BASE as well -- a "
            "materially different strategy, reported separately and never "
            "merged."),
        "arms": arms,
        "accounting_label": "historical replay on third-party public data",
        "separation": ("not the frozen .96 Gemini control and not the L5 "
                       "inference model"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  windows=%d  cadence=%gs base=$%.2f ext=$%.2f"
          % (LABEL, len(slugs), a.cadence, a.base, a.ext))
    print("split: train %d / test %d (by window)\n" % (len(train), len(test)))
    print("%-34s %9s %9s %9s %8s %9s %9s"
          % ("arm", "trainNet", "testNet", "all33Net", "win%", "extReal",
             "maxDD"))
    for k, v in arms.items():
        a33 = v["all33"]
        print("%-34s %9s %9s %9s %8s %9s %9s"
              % (k[:34], v["train"]["net_per_window"],
                 v["test"]["net_per_window"], a33["net_per_window"],
                 a33["win_rate_pct"],
                 a33["extension_realized_pnl"].get("mean"),
                 a33["max_drawdown"]))
    print("\nSELECTED ON TRAIN: %s -> held-out net/window %s"
          % (pick, cand[pick]["test"]["net_per_window"]))
    sel = cand[pick]["all33"]
    print("\nSELECTED ARM, all 33 windows, decomposed")
    for k in ("base_pnl_settlement", "base_pair_cost",
              "extension_realized_pnl", "extension_settlement_pnl",
              "peak_capital", "peak_directional_exposure", "n_extensions",
              "n_exits", "unsold_shares_at_exit", "lead_flips"):
        print("   %-32s %s" % (k, sel[k]))
    print("   fees: base %s | with %g/$ sensitivity net/win %s"
          % (sel["fees"]["base_case"], FEE_PER_DOLLAR_SENSITIVITY,
             sel["fees"]["net_per_window_with_sensitivity"]))
    print("\n   BY LEADERSHIP FLIPS")
    for k, v in sel["by_flip_count"].items():
        print("      %-9s windows %-3d net mean %-9s extReal mean %s"
              % (k, v["windows"], v["net"].get("mean"),
                 v["extension_realized"].get("mean")))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
