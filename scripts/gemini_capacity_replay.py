"""CAPACITY REPLAY: the frozen policy over a real ladder tape, per size.

WHAT THIS ANSWERS. For each duration and each quantity, what the SAME
frozen continuous-inventory policy would have done against the ladder that
actually existed, and what it would have been worth.

THE ONE RULE THAT SHAPES EVERYTHING HERE: a quantity is priced at the
VWAP the displayed depth supports, never at the touch. The tape already
carries, per tick and per size, the walked UP ask VWAP and the walked DOWN
acquisition VWAP together with whether each leg was FULLY EXECUTABLE. A
size whose leg was not fully executable cannot trade at that tick -- it is
unavailable, not merely dear.

HOW THE SIZE ENTERS THE FROZEN POLICY, and nothing about the policy
changes. The policy decides on an UP bid and an UP ask. At size q the
executable UP ask is the walked ask VWAP, and the executable UP bid is
implied by the DOWN acquisition cost through the identity the book already
verifies:

    effective_ask(q) = vwap of the asks consumed buying q UP
    effective_bid(q) = 1 - (vwap of the bids consumed buying q DOWN)

Those two numbers are handed to `decide_once` unmodified. No threshold is
touched: .90, .96, the 5c EXTEND rule, the 2-EXTEND cap and NATIVE_UP are
the frozen policy's and are read from it.

FEES ARE SEPARATED, AND THE MEASURED ONE IS USED WHERE IT EXISTS. The only
authenticated fee this program has is from CI15M-4: a marketable limit
that RESTED 25.5s and filled as MAKER with fee_amount "0", aggressor
false. So maker fills are charged the MEASURED $0.00, and a taker charge
is reported SEPARATELY as a model, never merged into the measured figure.

SETTLEMENT IS READ, NOT MODELLED. Residual direction is paid off against
the venue's own published `resolutionSide` per window. A window whose
resolution cannot be read contributes its matched economics and reports
its residual as UNSETTLED rather than guessing a direction.

NOTHING HERE IS REALIZED P&L. This is a REPLAY over a public tape with no
order ever sent: the strict label is `historical replay`.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_execution import gemini_durations as DUR
from src.gemini_execution.ci15m_policy import POLICY
from src.gemini_execution.continuous_probe import COMPLETE, EXTEND, HOLD, OPEN

LABEL = "GEMINI_CAPACITY_REPLAY_V1"

#: The ONE authenticated fee measurement this program has.
MEASURED_MAKER_FEE = D("0.00")
#: Reported SEPARATELY as a model. CI15M-3 recorded 0.02 on a fill the
#: authenticated record later showed was maker, so no taker fee has ever
#: actually been measured on this account. It is a model, labelled.
MODELLED_TAKER_FEE = D("0.02")

WINDOWS_PER_DAY = {DUR.BTC15M: 96, DUR.BTC5M: 288}


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def load(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Tape rows grouped by window ticker, in time order."""
    by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("readable") and r.get("ticker"):
                by[r["ticker"]].append(r)
    for t in by:
        by[t].sort(key=lambda r: str(r.get("utc")))
    return dict(by)


def _size_row(tick: Dict[str, Any], qty: D) -> Optional[Dict[str, Any]]:
    for s in tick.get("sizes") or []:
        if _d(s.get("qty")) == qty:
            return s
    return None


def replay_window(ticks: List[Dict[str, Any]], qty: D) -> Dict[str, Any]:
    """The frozen policy over ONE window at ONE size."""
    inv = POLICY["Inventory"]()
    n_extends = 0
    actions = 0
    acts: List[Dict[str, Any]] = []
    counts = {OPEN: 0, EXTEND: 0, COMPLETE: 0, HOLD: 0}
    first_open_t: Optional[float] = None
    first_complete_t: Optional[float] = None
    depth_levels = 0
    unavailable_ticks = 0
    in_band_ticks = 0

    for tk in ticks:
        s = _size_row(tk, qty)
        if s is None:
            continue
        if tk.get("in_frozen_open_band"):
            in_band_ticks += 1
        up_v, dn_v = _d(s.get("up_vwap")), _d(s.get("down_vwap"))
        if up_v is None or dn_v is None:
            continue
        # A LEG THE LADDER CANNOT SUPPLY IS UNAVAILABLE, not expensive.
        if not (s.get("up_fully_executable")
                and s.get("down_fully_executable")):
            unavailable_ticks += 1
            continue
        eff_ask = up_v
        eff_bid = D("1") - dn_v
        if eff_bid >= eff_ask:
            continue                       # crossed/locked at this size
        dec = POLICY["decide_once"](
            inv, bid=eff_bid, ask=eff_ask,
            bid_size=qty, ask_size=qty,
            tte=float(tk.get("tte") or 0.0), qty=qty,
            n_extends=n_extends, actions=actions)
        act = dec["action"]
        counts[act] = counts.get(act, 0) + 1
        if act == HOLD:
            continue
        side = dec["side"]
        px = dec["price"]
        # Treatment T: a capped marketable limit at the executable price,
        # which the depth above has already confirmed can be filled at q.
        inv.add(POLICY["make_fill"](float(tk.get("t") or 0.0), side,
                                    "taker", px, qty, "replay " + act))
        inv.assert_identities()
        actions += 1
        if act == EXTEND:
            n_extends += 1
        if act == OPEN and first_open_t is None:
            first_open_t = float(tk.get("t") or 0.0)
        if act == COMPLETE and first_complete_t is None:
            first_complete_t = float(tk.get("t") or 0.0)
        depth_levels += int((s.get("up_levels") or 0) if side == "UP" else 0)
        acts.append({"utc": tk.get("utc"), "tte": tk.get("tte"),
                     "action": act, "side": side, "price": str(px),
                     "qty": str(qty), "eff_bid": str(eff_bid),
                     "eff_ask": str(eff_ask)})

    matched = min(inv.q_up, inv.q_down)
    matched_cost = D("0")
    if matched > 0:
        matched_cost = matched * (inv.avg_up + inv.avg_down)
    return {
        "actions": acts, "counts": counts,
        "n_actions": len(acts),
        "ticks": len(ticks), "in_band_ticks": in_band_ticks,
        "unavailable_ticks": unavailable_ticks,
        "up_qty": str(inv.q_up), "up_cost": str(inv.c_up),
        "down_qty": str(inv.q_down), "down_cost": str(inv.c_down),
        "matched_qty": str(matched),
        "matched_cost": str(matched_cost),
        "matched_pair_cost": (None if matched == 0
                              else str(inv.avg_up + inv.avg_down)),
        "residual_side": inv.residual_side,
        "residual_qty": str(inv.residual),
        "residual_principal": str(
            inv.residual * (inv.avg_up if inv.residual_side == "UP"
                            else inv.avg_down) if inv.residual else D("0")),
        "time_to_first_complement_s": (
            None if (first_open_t is None or first_complete_t is None)
            else round(first_complete_t - first_open_t, 1)),
        "opened": counts.get(OPEN, 0) > 0,
        "depth_levels_consumed": depth_levels,
    }


def settle(rep: Dict[str, Any], resolution: Optional[str]) -> Dict[str, Any]:
    """Matched economics plus residual direction, fees separated."""
    matched = _d(rep["matched_qty"]) or D("0")
    mcost = _d(rep["matched_cost"]) or D("0")
    # A matched pair pays exactly 1.00 whichever way the market resolves.
    matched_pnl = matched * D("1") - mcost
    res_q = _d(rep["residual_qty"]) or D("0")
    res_prin = _d(rep["residual_principal"]) or D("0")
    res_side = rep["residual_side"]
    res_pnl: Optional[D] = None
    if res_q == 0:
        res_pnl = D("0")
    elif resolution is not None and res_side is not None:
        won = ((res_side == "UP" and resolution == "yes")
               or (res_side == "DOWN" and resolution == "no"))
        res_pnl = (res_q if won else D("0")) - res_prin
    n_fills = rep["n_actions"]
    fee_measured = D(n_fills) * MEASURED_MAKER_FEE
    fee_modelled = D(n_fills) * MODELLED_TAKER_FEE
    net_measured = (None if res_pnl is None
                    else matched_pnl + res_pnl - fee_measured)
    net_modelled = (None if res_pnl is None
                    else matched_pnl + res_pnl - fee_modelled)
    return {
        "matched_pair_pnl": str(matched_pnl),
        "residual_pnl": (None if res_pnl is None else str(res_pnl)),
        "residual_settled": res_pnl is not None and res_q > 0,
        "resolution_side": resolution,
        "n_fills": n_fills,
        "fees_measured_usd": str(fee_measured),
        "fees_modelled_taker_usd": str(fee_modelled),
        "net_with_measured_fees": (None if net_measured is None
                                   else str(net_measured)),
        "net_with_modelled_taker_fees": (None if net_modelled is None
                                         else str(net_modelled)),
        "labels": ("fees_measured uses the ONE authenticated observation "
                   "(CI15M-4: maker, fee_amount 0). The taker figure is a "
                   "MODEL and is never merged into it. A matched pair pays "
                   "1.00 either way; only the residual depends on "
                   "resolution."),
    }


def resolutions(tickers: List[str], duration: str) -> Dict[str, Optional[str]]:
    from src.gemini_execution import metadata as MD
    out: Dict[str, Optional[str]] = {}
    for t in tickers:
        try:
            r = MD.resolution_for("GEMI-%s-UP" % t)
            out[t] = (r.get("resolutionSide")
                      if r.get("verdict") == "SETTLED" else None)
        except Exception:                                      # noqa: BLE001
            out[t] = None
    return out


def run(path: str, duration: str, *,
        sizes: Optional[List[D]] = None) -> Dict[str, Any]:
    by = load(path)
    sizes = sizes or list(__import__(
        "src.gemini_execution.capacity_ladder",
        fromlist=["SIZES"]).SIZES)
    res = resolutions(sorted(by), duration)
    per_size: Dict[str, Any] = {}
    for q in sizes:
        windows = {}
        for t, ticks in by.items():
            rep = replay_window(ticks, q)
            windows[t] = {"replay": rep, "settlement": settle(rep, res.get(t))}
        n_win = len(windows)
        opened = [w for w in windows.values() if w["replay"]["opened"]]
        nets = [_d(w["settlement"]["net_with_measured_fees"])
                for w in windows.values()
                if w["settlement"]["net_with_measured_fees"] is not None]
        nets_t = [_d(w["settlement"]["net_with_modelled_taker_fees"])
                  for w in windows.values()
                  if w["settlement"]["net_with_modelled_taker_fees"]
                  is not None]
        total = sum(nets) if nets else None
        ttc = [w["replay"]["time_to_first_complement_s"]
               for w in windows.values()
               if w["replay"]["time_to_first_complement_s"] is not None]
        per_size[str(q)] = {
            "windows_seen": n_win,
            "windows_with_an_executable_open": len(opened),
            "open_rate": (None if not n_win else round(len(opened) / n_win, 4)),
            "n_open": sum(w["replay"]["counts"].get(OPEN, 0)
                          for w in windows.values()),
            "n_extend": sum(w["replay"]["counts"].get(EXTEND, 0)
                            for w in windows.values()),
            "n_complement": sum(w["replay"]["counts"].get(COMPLETE, 0)
                                for w in windows.values()),
            "n_hold": sum(w["replay"]["counts"].get(HOLD, 0)
                          for w in windows.values()),
            "unavailable_ticks": sum(w["replay"]["unavailable_ticks"]
                                     for w in windows.values()),
            "matched_qty_total": str(sum(
                (_d(w["replay"]["matched_qty"]) or D("0"))
                for w in windows.values())),
            "residual_qty_total": str(sum(
                (_d(w["replay"]["residual_qty"]) or D("0"))
                for w in windows.values())),
            "matched_pair_pnl_total": str(sum(
                (_d(w["settlement"]["matched_pair_pnl"]) or D("0"))
                for w in windows.values())),
            "residual_pnl_total": str(sum(
                (_d(w["settlement"]["residual_pnl"]) or D("0"))
                for w in windows.values()
                if w["settlement"]["residual_pnl"] is not None)),
            "fees_measured_total": str(sum(
                (_d(w["settlement"]["fees_measured_usd"]) or D("0"))
                for w in windows.values())),
            "fees_modelled_taker_total": str(sum(
                (_d(w["settlement"]["fees_modelled_taker_usd"]) or D("0"))
                for w in windows.values())),
            "net_total_measured_fees": (None if total is None else str(total)),
            "net_per_window": (None if (total is None or not n_win)
                               else str(total / n_win)),
            "net_per_eligible_window": (
                None if (total is None or not opened)
                else str(total / len(opened))),
            "net_per_window_modelled_taker": (
                None if not nets_t else str(sum(nets_t) / n_win)),
            "est_net_per_day": (
                None if (total is None or not n_win) else
                str((total / n_win) * WINDOWS_PER_DAY.get(duration, 0))),
            "windows_per_day_assumed": WINDOWS_PER_DAY.get(duration),
            "time_to_first_complement_s": {
                "n": len(ttc),
                "min": (min(ttc) if ttc else None),
                "max": (max(ttc) if ttc else None),
                "mean": (round(sum(ttc) / len(ttc), 1) if ttc else None)},
            "residual_unsettled_windows": sum(
                1 for w in windows.values()
                if (_d(w["replay"]["residual_qty"]) or D("0")) > 0
                and not w["settlement"]["residual_settled"]),
        }
    executable = [q for q in sizes
                  if (per_size[str(q)]["open_rate"] or 0) > 0]
    positive = [q for q in sizes
                if per_size[str(q)]["net_per_window"] is not None
                and D(per_size[str(q)]["net_per_window"]) > 0]
    return {
        "label": LABEL, "duration": duration, "tape": path,
        "windows": sorted(by), "n_windows": len(by),
        "effective_open_band": DUR.effective_open_band(duration),
        "resolutions": res,
        "per_size": per_size,
        "max_executable_qty": (None if not executable
                               else str(max(executable))),
        "max_defensible_positive_qty": (None if not positive
                                        else str(max(positive))),
        "accounting_label": "historical replay",
        "labels": ("NOTHING HERE IS REALIZED P&L. No order was sent; this "
                   "is the frozen policy over a public ladder tape. Sizes "
                   "are priced at the VWAP the displayed depth supports, "
                   "never at the touch."),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", default=DUR.BTC15M)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    path = a.tape
    if path is None:
        cands = sorted(glob.glob(os.path.join(
            DUR.dataset_root(a.duration), "capacity_tape_*.jsonl")))
        if not cands:
            print("no tape found for %s" % a.duration)
            return 2
        path = cands[-1]
    rep = run(path, a.duration)
    text = json.dumps(rep, indent=2, sort_keys=True, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    # A COMPACT TABLE, because the full object is large.
    print(json.dumps({k: rep[k] for k in
                      ("label", "duration", "tape", "n_windows",
                       "effective_open_band", "max_executable_qty",
                       "max_defensible_positive_qty", "accounting_label")},
                     indent=2, default=str))
    hdr = ("%4s %7s %6s %6s %6s %6s %9s %9s %9s %9s %10s"
           % ("q", "open%", "nOPEN", "nEXT", "nCMP", "unav",
              "matched", "pairPnL", "resPnL", "net/win", "est$/day"))
    print(hdr)
    for q, r in rep["per_size"].items():
        print("%4s %7s %6s %6s %6s %6s %9s %9s %9s %9s %10s"
              % (q,
                 ("%.0f%%" % (100 * (r["open_rate"] or 0))),
                 r["n_open"], r["n_extend"], r["n_complement"],
                 r["unavailable_ticks"], r["matched_qty_total"],
                 r["matched_pair_pnl_total"], r["residual_pnl_total"],
                 (r["net_per_window"] or "-")[:9],
                 (r["est_net_per_day"] or "-")[:10]))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
