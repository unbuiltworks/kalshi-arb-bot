"""BTC15m resting-bid hypothesis test, on the two-sided recorder tape.

READ-ONLY SHADOW. There is no transport import, no signer and no order path in
this module. Nothing it prints is submitted, filled, settled or realized.

THE QUESTION
------------
Does resting simultaneous UP and DOWN maker orders at (or one tick behind) the
best bid generate repeatable profitable BTC15m inventory?

WHAT THE VENUE ACTUALLY IS -- read this before reading any number below
----------------------------------------------------------------------
On Gemini there is no separate BTC15m `-DOWN` instrument: the recorder probed
for one and the venue answered `-1013 Invalid stream name` every time. The
DOWN book is DERIVED from the UP ladder:

    DOWN bid = 1 - UP ask        DOWN ask = 1 - UP bid

So "rest a DOWN bid at the best DOWN bid" IS "rest an UP sell at the best UP
ask". Arm B is therefore not two independent bids in two books -- it is a
two-sided quote on ONE book, and its completed pair costs
`UP_bid + (1 - UP_ask) = 1 - spread`. The hypothesis is a market-making
hypothesis, and the edge it is reaching for is the single-leg spread.

FILL STANDARD -- no touch=fill, no invented queue priority
----------------------------------------------------------
A resting order fills only on venue print evidence, and the tiers are reported
separately and never merged:

  THROUGH    an aggressive print strictly through our price. Price-time
             priority cannot leave us behind it, so this is a fill.
  DEPLETION  aggressive prints AT our price whose cumulative quantity since
             placement exceeds the size displayed at our price when we placed
             (our queue position upper bound). Defensible.
  OPTIMISTIC any same-price print regardless of the queue. Reported as a
             BOUND and NEVER counted as a fill or in any economic line.

Aggressor side is taken from the venue's own `buyer_is_maker` flag on the
print, not inferred: `buyer_is_maker=True` means the resting side was the
buyer, so the aggressor SOLD and a resting BUY can fill.

A re-peg (cancel and replace) RESETS both the queue position and the
accumulated depletion credit. That is the cost of repricing, and arm D exists
to measure it.

ARMS (identical windows, identical tape, identical accounting)
-------------------------------------------------------------
  A   the canonical engine. Not re-implemented here: run
      `analysis/gemini/info_edge/btc15m_shadow_policy.py` on the same tape dir
      (see REPORT.md) and read its settled total. This module refuses to
      reproduce it so there is no second engine.
  B   two-sided maker AT the touch, re-pegged when the touch moves.
  C   two-sided maker ONE TICK BEHIND the touch, re-pegged.
  D1  B's prices, HELD -- never re-pegged, queue priority preserved.
  D2  B's prices, re-pegged, and INVENTORY-AWARE: once one leg is filled the
      other leg is never quoted at a price that would push the matched pair
      cost above MAX_PAIR_COST.

FEES AND REBATES mirror `analysis/gemini/favorite_inventory/fi_core.py`
exactly (maker ceil(0.0175*C*P*(1-P)), taker ceil(0.07*C*P*(1-P)), rebate
floor(0.021*C*P*(1-P)) only inside 0.20<=P<=0.80). `--verify-fi-core PATH`
asserts agreement against that module rather than trusting this restatement.
No rebate is called realized anywhere.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import statistics
import sys
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settlement_bridge as sb  # noqa: E402

SYM = re.compile(r"^GEMI-(BTC15M\d{10})-UP$")
CENT = D("0.01")
ONE = D("1")
TICK = D("0.01")

# ------------------------------------------------------------------ policy
LOT = D("2")                    # contracts per leg. A CONSTANT. No progression.
OPEN_TTE_LO, OPEN_TTE_HI = 120.0, 840.0
MAX_PAIR_COST = D("0.96")       # canonical engine's complement discipline
MAX_FILLS = 8                   # canonical MAX_ACTIONS
MAX_RESIDUAL = D("6")           # canonical directional residual cap, contracts
MIN_SPREAD_TICKS = 2            # below this, arm C's quotes would cross


def maker_fee(C: D, P: D) -> D:
    return (D("0.0175") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_CEILING) * CENT


def taker_fee(C: D, P: D) -> D:
    return (D("0.07") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_CEILING) * CENT


def rebate(C: D, P: D) -> D:
    if P < D("0.20") or P > D("0.80"):
        return D("0")
    return (D("0.021") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_FLOOR) * CENT


def verify_fi_core(path: str) -> None:
    sys.path.insert(0, path)
    import fi_core  # type: ignore
    for c in ("1", "2", "10", "100", "23.61"):
        for p in range(1, 100):
            P, C = D(p) / 100, D(c)
            assert maker_fee(C, P) == fi_core.maker_fee(C, P), (c, p, "maker")
            assert taker_fee(C, P) == fi_core.taker_fee(C, P), (c, p, "taker")
            assert rebate(C, P) == fi_core.rebate(C, P), (c, p, "rebate")
    print("fee/rebate functions verified identical to fi_core over "
          "C in {1,2,10,100,23.61} x P in 0.01..0.99")


# ------------------------------------------------------------------- tape
def _apply(book: Dict[float, float], rows) -> None:
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def load(tape_dir: str) -> Dict[str, Dict[str, Any]]:
    """Per-ticker event timeline: book states and venue prints, time-ordered.

    Book reconstruction is the same snapshot+diff discipline the canonical
    engine uses (`btc15m_shadow_policy.windows_from_tape`): a diff before any
    snapshot is dropped, and a diff whose `u` does not advance is dropped.
    """
    ev: Dict[str, List[dict]] = {}
    expiry: Dict[str, float] = {}
    files = sorted(glob.glob(os.path.join(tape_dir, "*.jsonl"))) + \
        sorted(glob.glob(os.path.join(tape_dir, "*.jsonl.gz")))
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                k = r.get("kind")
                m = SYM.match(r.get("market") or "")
                if not m:
                    continue
                tk = m.group(1)
                if k in ("window", "window_poll") and r.get("expiry"):
                    from datetime import datetime
                    expiry[tk] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).timestamp()
                elif k in ("p_depth", "p_snapshot", "p_trade"):
                    ev.setdefault(tk, []).append(r)

    res = sb.table(tape_dir)
    out: Dict[str, Dict[str, Any]] = {}
    for tk, rows in ev.items():
        E = expiry.get(tk)
        if E is None:
            continue
        bids: Dict[float, float] = {}
        asks: Dict[float, float] = {}
        seeded, last_u = False, None
        timeline: List[dict] = []
        for r in sorted(rows, key=lambda x: x["t_ns"]):
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_trade":
                if seeded:
                    timeline.append({
                        "t": t, "type": "print",
                        "price": D(str(r["price"])).quantize(CENT),
                        "qty": D(str(r["qty"])),
                        "buyer_is_maker": bool(r["buyer_is_maker"]),
                    })
                continue
            if r["kind"] == "p_snapshot":
                nb: Dict[float, float] = {}
                na: Dict[float, float] = {}
                _apply(nb, r.get("bids"))
                _apply(na, r.get("asks"))
                bids, asks = nb, na
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                _apply(bids, r.get("b"))
                _apply(asks, r.get("a"))
                last_u = u
            b = max(bids) if bids else None
            a = min(asks) if asks else None
            timeline.append({
                "t": t, "type": "book",
                "bid": None if b is None else D(str(round(b, 2))),
                "ask": None if a is None else D(str(round(a, 2))),
                "bid_sz": None if b is None else D(str(bids[b])),
                "ask_sz": None if a is None else D(str(asks[a])),
                "ladder_bids": {D(str(round(p, 2))): D(str(s))
                                for p, s in bids.items()},
                "ladder_asks": {D(str(round(p, 2))): D(str(s))
                                for p, s in asks.items()},
            })
        if timeline:
            out[tk] = {"expiry": E, "timeline": timeline,
                       "resolution": res.get(tk, {}).get("resolution_side"),
                       "resolution_provenance":
                           res.get(tk, {}).get("provenance")}
    return out


# ------------------------------------------------------------------- arms
class Leg:
    """One resting order. `queue_ahead` is an UPPER BOUND on our queue position."""

    def __init__(self) -> None:
        self.price: Optional[D] = None
        self.queue_ahead = D("0")
        self.consumed = D("0")       # aggressive qty AT our price since placement
        self.filled = D("0")
        self.placements = 0
        self.repegs = 0

    def place(self, price: D, displayed: D) -> None:
        if self.price == price:
            return
        if self.price is not None:
            self.repegs += 1
        self.price = price
        self.queue_ahead = displayed
        self.consumed = D("0")
        self.filled = D("0")
        self.placements += 1

    def cancel(self) -> None:
        self.price = None
        self.queue_ahead = D("0")
        self.consumed = D("0")
        self.filled = D("0")


ARMS = {
    # name:        (behind_ticks, repeg, inventory_aware)
    "B_AT_TOUCH":   (0, True, False),
    "C_ONE_BEHIND": (1, True, False),
    "D1_HELD":      (0, False, False),
    "D2_INV_AWARE": (0, True, True),
}


def run_arm(w: Dict[str, Any], behind: int, repeg: bool,
            inv_aware: bool) -> Dict[str, Any]:
    E = w["expiry"]
    up = Leg()      # resting BUY of UP
    dn = Leg()      # resting BUY of DOWN == resting SELL of UP
    up_qty = D("0")
    dn_qty = D("0")
    up_cost = D("0")
    dn_cost = D("0")
    fees = D("0")
    rebates = D("0")          # NOT realized
    n_fills = 0
    opt_up = D("0")           # OPTIMISTIC bound, never counted
    opt_dn = D("0")
    fill_log: List[dict] = []
    quoting_ticks = 0
    two_sided_ticks = 0
    last = {"bid": None, "ask": None}

    def residual() -> D:
        return abs(up_qty - dn_qty)

    def book_fill(leg: Leg, side: str, price: D, qty: D, tier: str,
                  t: float) -> None:
        nonlocal up_qty, dn_qty, up_cost, dn_cost, fees, rebates, n_fills
        q = min(qty, LOT - leg.filled)
        if q <= 0:
            return
        leg.filled += q
        # UP-equivalent probability the venue bands fees and rebates on
        p_for_fee = price if side == "UP" else (ONE - price)
        fees += maker_fee(q, p_for_fee)
        rebates += rebate(q, p_for_fee)
        if side == "UP":
            up_qty += q
            up_cost += price * q
        else:
            dn_qty += q
            dn_cost += price * q
        n_fills += 1
        fill_log.append({"t": round(t, 3), "side": side, "price": str(price),
                         "qty": str(q), "tier": tier})
        if leg.filled >= LOT:
            leg.cancel()

    for e in w["timeline"]:
        tte = E - e["t"]
        if tte <= 0:
            break
        if e["type"] == "print":
            px, q, bim = e["price"], e["qty"], e["buyer_is_maker"]
            # aggressive SELL (resting buyer) can fill our resting UP buy
            if bim and up.price is not None:
                if px < up.price:
                    book_fill(up, "UP", up.price, q, "THROUGH", e["t"])
                elif px == up.price:
                    up.consumed += q
                    avail = up.consumed - up.queue_ahead
                    opt_up += q
                    if avail > 0:
                        book_fill(up, "UP", up.price, avail, "DEPLETION", e["t"])
            # aggressive BUY (resting seller) can fill our resting UP sell,
            # which is the DOWN buy at 1 - that UP price
            if (not bim) and dn.price is not None:
                up_sell_px = ONE - dn.price
                if px > up_sell_px:
                    book_fill(dn, "DOWN", dn.price, q, "THROUGH", e["t"])
                elif px == up_sell_px:
                    dn.consumed += q
                    avail = dn.consumed - dn.queue_ahead
                    opt_dn += q
                    if avail > 0:
                        book_fill(dn, "DOWN", dn.price, avail, "DEPLETION",
                                  e["t"])
            continue

        bid, ask = e["bid"], e["ask"]
        last["bid"], last["ask"] = bid, ask
        if bid is not None and ask is not None and ask > bid:
            two_sided_ticks += 1
        in_band = OPEN_TTE_LO <= tte <= OPEN_TTE_HI
        coherent = (bid is not None and ask is not None and ask > bid)
        spread_ticks = int(((ask - bid) / TICK)) if coherent else 0
        blocked = (n_fills >= MAX_FILLS or residual() > MAX_RESIDUAL)
        if not (in_band and coherent and spread_ticks >= MIN_SPREAD_TICKS
                and not blocked):
            if not in_band or blocked:
                up.cancel()
                dn.cancel()
            continue
        quoting_ticks += 1

        up_px = bid - behind * TICK
        dn_px = (ONE - ask) - behind * TICK
        if up_px < CENT or dn_px < CENT:
            continue

        if inv_aware:
            # never quote a complement that would lock a pair above the cap
            if up_qty > dn_qty and up_qty > 0:
                avg_up = up_cost / up_qty
                dn_px = min(dn_px, MAX_PAIR_COST - avg_up)
            if dn_qty > up_qty and dn_qty > 0:
                avg_dn = dn_cost / dn_qty
                up_px = min(up_px, MAX_PAIR_COST - avg_dn)
            if up_px < CENT or dn_px < CENT:
                continue
            up_px = up_px.quantize(CENT, rounding=ROUND_FLOOR)
            dn_px = dn_px.quantize(CENT, rounding=ROUND_FLOOR)

        if up.filled < LOT and (repeg or up.price is None):
            up.place(up_px, e["ladder_bids"].get(up_px, D("0")))
        if dn.filled < LOT and (repeg or dn.price is None):
            dn.place(dn_px, e["ladder_asks"].get(ONE - dn_px, D("0")))

    matched = min(up_qty, dn_qty)
    matched_cost = D("0")
    if matched > 0:
        avg_up = up_cost / up_qty if up_qty else D("0")
        avg_dn = dn_cost / dn_qty if dn_qty else D("0")
        matched_cost = matched * (avg_up + avg_dn)
    matched_pnl = matched - matched_cost        # $1 per matched pair
    res_side = "UP" if up_qty > dn_qty else ("DOWN" if dn_qty > up_qty else None)
    res_qty = residual()
    res_cost = D("0")
    if res_side == "UP" and up_qty:
        res_cost = res_qty * (up_cost / up_qty)
    elif res_side == "DOWN" and dn_qty:
        res_cost = res_qty * (dn_cost / dn_qty)

    out = {
        "placements_up": up.placements, "placements_down": dn.placements,
        "repegs_up": up.repegs, "repegs_down": dn.repegs,
        "quoting_ticks": quoting_ticks, "two_sided_ticks": two_sided_ticks,
        "n_fills": n_fills, "fills": fill_log,
        "UP_qty": str(up_qty), "DOWN_qty": str(dn_qty),
        "matched_pairs": str(matched),
        "matched_pair_pnl": str(matched_pnl),
        "residual_side": res_side, "residual_qty": str(res_qty),
        "residual_cost": str(res_cost),
        "fees": str(fees),
        "rebates_NOT_REALIZED": str(rebates),
        "optimistic_bound_qty_up": str(opt_up),
        "optimistic_bound_qty_down": str(opt_dn),
        "resolution": w["resolution"],
        "resolution_provenance": w["resolution_provenance"],
    }
    if w["resolution"] in ("yes", "no"):
        won = "UP" if w["resolution"] == "yes" else "DOWN"
        res_pnl = (res_qty - res_cost) if res_side == won else (-res_cost)
        if res_qty == 0:
            res_pnl = D("0")
        out["directional_residual_pnl"] = str(res_pnl)
        out["net_settlement_pnl"] = str(matched_pnl + res_pnl - fees)
        out["net_settlement_pnl_with_rebate_if_credited"] = str(
            matched_pnl + res_pnl - fees + rebates)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tape_dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--verify-fi-core", default=None)
    a = ap.parse_args()
    if a.verify_fi_core:
        verify_fi_core(a.verify_fi_core)

    ws = load(a.tape_dir)
    print("=" * 100)
    print("BTC15m RESTING-BID HYPOTHESIS -- SHADOW. NO ORDER IS SENT.")
    print("DOWN is DERIVED (no -DOWN instrument exists): a DOWN bid IS an UP "
          "ask. Arm B/C/D are two-sided quotes on ONE book.")
    print("Fills require venue print evidence. THROUGH and DEPLETION only; "
          "OPTIMISTIC is a bound and is never counted.")
    print("=" * 100)
    resolved = {tk: w for tk, w in ws.items() if w["resolution"] in ("yes", "no")}
    print(f"windows with a reconstructed book: {len(ws)}  "
          f"settlement-resolved: {len(resolved)}")
    print()

    results: Dict[str, Dict[str, Any]] = {}
    for name, (behind, repeg, inv_aware) in ARMS.items():
        per = {}
        for tk, w in sorted(resolved.items()):
            per[tk] = run_arm(w, behind, repeg, inv_aware)
        results[name] = per

    hdr = ("%-14s %6s %6s %7s %7s %8s %9s %8s %9s %9s"
           % ("arm", "wins", "quotT", "places", "fills", "pairs", "matched$",
              "fees", "resid$", "NET$"))
    print(hdr)
    print("-" * len(hdr))
    summary = {}
    for name, per in results.items():
        f = sum(r["n_fills"] for r in per.values())
        pl = sum(r["placements_up"] + r["placements_down"] for r in per.values())
        qt = sum(r["quoting_ticks"] for r in per.values())
        pairs = sum(D(r["matched_pairs"]) for r in per.values())
        mp = sum(D(r["matched_pair_pnl"]) for r in per.values())
        fe = sum(D(r["fees"]) for r in per.values())
        rp = sum(D(r.get("directional_residual_pnl", "0")) for r in per.values())
        net = sum(D(r.get("net_settlement_pnl", "0")) for r in per.values())
        wins = sum(1 for r in per.values() if r["n_fills"] > 0)
        both = sum(1 for r in per.values()
                   if D(r["UP_qty"]) > 0 and D(r["DOWN_qty"]) > 0)
        summary[name] = {
            "windows": len(per), "windows_with_any_fill": wins,
            "windows_with_both_legs": both,
            "placements": pl, "quoting_ticks": qt, "fills": f,
            "completed_pairs": str(pairs),
            "matched_pair_pnl": str(mp), "fees": str(fe),
            "directional_residual_pnl": str(rp),
            "net_settlement_pnl": str(net),
            "net_per_window": str((net / len(per)).quantize(D("0.0001")))
            if per else "0",
            "rebates_NOT_REALIZED": str(
                sum(D(r["rebates_NOT_REALIZED"]) for r in per.values())),
            "optimistic_bound_fill_events": str(
                sum(D(r["optimistic_bound_qty_up"])
                    + D(r["optimistic_bound_qty_down"]) for r in per.values())),
        }
        print("%-14s %6d %6d %7d %7d %8s %9s %8s %9s %9s"
              % (name, wins, qt, pl, f, pairs, mp, fe, rp, net))
    print()
    print("wins = windows with at least one evidenced fill.  "
          "pairs = completed UP/DOWN pairs.  NET = matched + residual - fees, "
          "rebates excluded (never realized).")
    print()
    for name, s in summary.items():
        print(f"{name}: both-leg windows {s['windows_with_both_legs']}/"
              f"{s['windows']}, net/window {s['net_per_window']}, "
              f"rebate-if-credited {s['rebates_NOT_REALIZED']}")

    blob = {
        "label": "BTC15M_RESTING_BID_SHADOW_AUDIT_V1",
        "accounting": ("SHADOW. No order was sent. Nothing is submitted, "
                       "filled, settled or realized. Rebates are a "
                       "counterfactual and are never added to NET."),
        "tape_dir": a.tape_dir,
        "lot": str(LOT), "tte_band": [OPEN_TTE_LO, OPEN_TTE_HI],
        "max_pair_cost": str(MAX_PAIR_COST),
        "arms": {k: {"behind_ticks": v[0], "repeg": v[1],
                     "inventory_aware": v[2]} for k, v in ARMS.items()},
        "summary": summary,
        "per_window": results,
    }
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(blob, fh, indent=2, default=str)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
