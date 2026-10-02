#!/usr/bin/env python3
"""Two-sided maker shadow on a Gemini BTC15m L2+trades tape.

Offline. No orders. The question is narrow and mechanical:

    can continuous bid+offer quoting, skewed toward flat inventory and
    withdrawn before settlement, net $3-$5 per eligible window?

This is NOT the strategy the 88 captured Gemini fills tested. Those were
88 BUYS and 0 sells -- one-sided long accumulation. Nothing in the old
cohort runs is evidence for or against what is simulated here.

FILL DISCIPLINE -- the part that decides whether any of this is real.

A maker fill is NEVER inferred from the BBO touching or crossing our quote.
That is the stale-maker-fill bug, and it is what made 98.4% of an earlier
engine's fills sit a median 33 cents through the market. Here a resting
quote fills only on defensible trade-through evidence:

  * queue_ahead is the size resting at our price when we join;
  * ONLY TRADES reduce it. A size reduction in the L2 delta is NOT counted,
    because a delta cannot distinguish a cancel ahead of us from a fill
    ahead of us, and assuming "fill" would hand us free queue priority;
  * we fill only once cumulative consuming volume exceeds queue_ahead, and
    then only for the excess;
  * a trade consumes our side only if its aggressor side says so:
    aggressor sell hits the BID, aggressor buy lifts the ASK;
  * a trade whose side cannot be determined fills NOTHING and is counted
    under `unknown_side_trades`.

INVENTORY SKEW. Flat is the target. When inventory is long, the
inventory-REDUCING quote (the offer) is made more aggressive and the
inventory-INCREASING quote (the bid) less aggressive, by a tick count that
scales with |inventory| relative to the clip. Mirrored when short.

WITHDRAWAL. Quotes are pulled at T-WITHDRAW_S before expiry. Any residual is
closed with a TAKER cleanup at the opposite touch, and that cost is charged.
If depth cannot absorb it, the residual is carried and marked UNKNOWN rather
than settled by assumption.

COMPENSATION. The Gemini maker rebate is MODELED, never realized:
0.30 x 0.07 x C x P x (1-P) in the [0.20, 0.80] price band, per the published
program. It is reported on its own line and never folded into trading P&L.
"""
from __future__ import annotations

import json, os, sys, collections, statistics
from decimal import Decimal as D, getcontext
from datetime import datetime, timezone

getcontext().prec = 28
OUT = "analysis/gemini/TWOSIDED_SHADOW.json"
TICK = D("0.01")
QS = (5, 10, 25, 50, 100)
WITHDRAWALS = (90, 60)
MARKOUTS = (1, 5, 15, 30, 60)
REBATE_RATE = D("0.30") * D("0.07")      # published Gemini predictions program
BAND = (D("0.20"), D("0.80"))


def rebate(qty, px):
    if not (BAND[0] <= px <= BAND[1]):
        return D(0)
    return REBATE_RATE * qty * px * (1 - px)


class Book:
    __slots__ = ("bids", "asks")

    def __init__(self):
        self.bids = {}
        self.asks = {}

    def apply(self, changes):
        for c in changes:
            try:
                side, px, sz = c[0], D(str(c[1])), D(str(c[2]))
            except Exception:
                continue
            d = self.bids if side == "buy" else self.asks
            if sz == 0:
                d.pop(px, None)
            else:
                d[px] = sz

    def bb(self):
        return max(self.bids) if self.bids else None

    def ba(self):
        return min(self.asks) if self.asks else None

    def size_at(self, side, px):
        return (self.bids if side == "buy" else self.asks).get(px, D(0))


def load(tape):
    frames = []
    meta = {}
    for line in open(tape):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "symbols":
            meta.update(r.get("meta") or {})
        elif r["kind"] == "frame":
            f = r["f"]
            if f.get("type") == "l2_updates":
                frames.append((r["t"], f))
    return frames, meta


def expiry_of(meta, sym):
    m = meta.get(sym) or {}
    e = m.get("event_expiry") or m.get("expiryDate")
    if not e:
        return None
    try:
        return datetime.fromisoformat(str(e).replace("Z", "+00:00"))
    except Exception:
        return None


def simulate(frames, meta, sym, q, withdraw_s):
    """One symbol, one clip size, one withdrawal policy."""
    book = Book()
    exp = expiry_of(meta, sym)
    inv = D(0)
    cash = D(0)
    fills = []                 # (ts, side, px, qty)
    unknown_side = 0
    consuming_vol = {"buy": collections.defaultdict(D),
                     "sell": collections.defaultdict(D)}
    my = {"buy": None, "sell": None}      # (px, queue_ahead, filled_so_far)
    mids = []                             # (ts, mid) for markouts
    withdrawn = False
    traded_vol_total = D(0)

    def quote_prices(bb, ba):
        """Two-sided quotes with inventory skew toward flat."""
        if bb is None or ba is None:
            return None, None
        skew = int(min(3, abs(inv) / max(D(1), D(q))))
        if inv > 0:            # long -> sell is the reducing side
            return bb - skew * TICK, ba - min(skew, 1) * TICK
        if inv < 0:            # short -> buy is the reducing side
            return bb + min(skew, 1) * TICK, ba + skew * TICK
        return bb, ba

    for ts_s, f in frames:
        if f.get("symbol") != sym:
            continue
        ts = datetime.fromisoformat(ts_s)
        book.apply(f.get("changes") or [])
        bb, ba = book.bb(), book.ba()
        if bb is not None and ba is not None:
            mids.append((ts, (bb + ba) / 2))

        if exp and not withdrawn and (exp - ts).total_seconds() <= withdraw_s:
            withdrawn = True
            my = {"buy": None, "sell": None}

        # ---- trades: the ONLY thing that can fill us
        for t in (f.get("trades") or []):
            try:
                tpx = D(str(t["price"]))
                tq = D(str(t.get("quantity") or t.get("amount")))
            except Exception:
                unknown_side += 1
                continue
            traded_vol_total += tq
            side = str(t.get("side") or t.get("makerSide") or "").lower()
            if side == "sell":
                consumed = "buy"       # aggressor sold -> hit the bid
            elif side == "buy":
                consumed = "sell"
            else:
                unknown_side += 1
                continue
            q_state = my.get(consumed)
            if not q_state:
                continue
            mypx, ahead, got = q_state
            # a trade only touches us at our price or through it
            if consumed == "buy" and tpx > mypx:
                continue
            if consumed == "sell" and tpx < mypx:
                continue
            avail = tq
            if ahead > 0:
                eat = min(ahead, avail)
                ahead -= eat
                avail -= eat
            if avail > 0 and got < q:
                take = min(avail, D(q) - got)
                got += take
                # maker buy -> cash out; maker sell -> cash in
                if consumed == "buy":
                    cash -= mypx * take
                    inv += take
                    fills.append((ts, "buy", mypx, take))
                else:
                    cash += mypx * take
                    inv -= take
                    fills.append((ts, "sell", mypx, take))
            my[consumed] = (mypx, ahead, got) if got < q else None

        # ---- (re)post quotes
        if not withdrawn and bb is not None and ba is not None and ba > bb:
            pb, pa = quote_prices(bb, ba)
            for side, px in (("buy", pb), ("sell", pa)):
                cur = my.get(side)
                if px is None:
                    continue
                if cur is None or cur[0] != px:
                    # joining a new price: queue ahead is what rests there now
                    my[side] = (px, book.size_at(side, px), D(0))

    # ---- withdrawal cleanup: taker, charged at the opposite touch
    cleanup_cost = D(0)
    residual = inv
    residual_unknown = False
    if residual != 0:
        bb, ba = book.bb(), book.ba()
        if residual > 0 and ba is not None:
            depth = book.asks.get(ba, D(0))
            qty = min(residual, depth) if depth else D(0)
            if qty < residual:
                residual_unknown = True
            if bb is not None:
                # closing a long means SELLING at the bid. That price already
                # pays the crossing. An earlier revision ALSO subtracted
                # (ba-bb)/2*qty from net, charging the spread twice and
                # overstating the loss by 3.3x on the historical sample.
                # cleanup_cost is now a DIAGNOSTIC only and is never netted.
                cash += bb * qty
                cleanup_cost += (ba - bb) / 2 * qty
                inv -= qty
        elif residual < 0 and bb is not None:
            depth = book.bids.get(bb, D(0))
            qty = min(-residual, depth) if depth else D(0)
            if qty < -residual:
                residual_unknown = True
            if ba is not None:
                # closing a short means BUYING at the ask: same argument.
                cash -= ba * qty
                cleanup_cost += (ba - bb) / 2 * qty
                inv += qty
        else:
            residual_unknown = True

    # ---- per-fill markouts
    mo = {h: [] for h in MARKOUTS}
    for ts, side, px, qty in fills:
        for h in MARKOUTS:
            fut = None
            for mts, m in mids:
                if (mts - ts).total_seconds() >= h:
                    fut = m
                    break
            if fut is None:
                continue
            mo[h].append(float((fut - px) if side == "buy" else (px - fut)))

    buys = sum(qty for _, s, _, qty in fills if s == "buy")
    sells = sum(qty for _, s, _, qty in fills if s == "sell")
    matched = min(buys, sells)
    maker_notional = sum(px * qty for _, _, px, qty in fills)
    modeled_rebate = sum(rebate(qty, px) for _, _, px, qty in fills)
    spread_capture = D(0)
    if matched > 0 and buys and sells:
        avg_b = sum(px * qty for _, s, px, qty in fills if s == "buy") / buys
        avg_s = sum(px * qty for _, s, px, qty in fills if s == "sell") / sells
        spread_capture = (avg_s - avg_b) * matched

    return {
        "symbol": sym, "q": q, "withdraw_s": withdraw_s,
        "fills": len(fills), "buy_qty": str(buys), "sell_qty": str(sells),
        "matched_qty": str(matched),
        "maker_notional": str(maker_notional),
        "spread_capture": str(spread_capture),
        "modeled_rebate_MODELED": str(modeled_rebate),
        "unknown_side_trades": unknown_side,
        "venue_traded_volume": str(traded_vol_total),
        "residual_after_cleanup": str(inv),
        "residual_unknown": residual_unknown,
        "taker_cleanup_cost": str(cleanup_cost),
        # cleanup is already embodied in `cash`, because the close is booked at
        # the opposite touch. It is reported for visibility, never subtracted.
        "taker_cleanup_cost_DIAGNOSTIC_NOT_NETTED": str(cleanup_cost),
        "net_before_compensation": str(cash),
        "net_after_modeled_compensation": str(cash + modeled_rebate),
        "markouts_per_fill": {str(h): (statistics.mean(v) if v else None)
                              for h, v in mo.items()},
        "markout_n": {str(h): len(v) for h, v in mo.items()},
    }


def main(tape):
    frames, meta = load(tape)
    syms = sorted({f.get("symbol") for _, f in frames if f.get("symbol")})
    print(f"tape={tape}  frames={len(frames)}  symbols={len(syms)}")
    rows = []
    for sym in syms:
        for q in QS:
            for w in WITHDRAWALS:
                rows.append(simulate(frames, meta, sym, q, w))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"tape": tape, "rows": rows,
               "fill_rule": "trade-through only; L2 size reductions never "
                            "reduce queue; unknown-side trades fill nothing",
               "rebate": "MODELED 0.30*0.07*C*P*(1-P) in [0.20,0.80]"},
              open(OUT, "w"), indent=2)
    print(f"\n{'symbol':30s} {'q':>4s} {'wd':>3s} {'fills':>6s} {'matched':>8s} "
          f"{'net_pre':>9s} {'rebate':>8s} {'net_post':>9s}")
    for r in rows:
        if r["fills"] == 0:
            continue
        print(f"{r['symbol'][-30:]:30s} {r['q']:>4d} {r['withdraw_s']:>3d} "
              f"{r['fills']:>6d} {D(r['matched_qty']):>8} "
              f"{D(r['net_before_compensation']):>+9.4f} "
              f"{D(r['modeled_rebate_MODELED']):>8.4f} "
              f"{D(r['net_after_modeled_compensation']):>+9.4f}")
    active = [r for r in rows if r["fills"] > 0]
    print(f"\nrows with >=1 defensible fill: {len(active)}/{len(rows)}")
    print(f"total venue traded volume on tape: "
          f"{sum(D(r['venue_traded_volume']) for r in rows if r['q']==5 and r['withdraw_s']==90)}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
