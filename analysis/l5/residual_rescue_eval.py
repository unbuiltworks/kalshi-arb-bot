#!/usr/bin/env python3
"""Evaluate RESIDUAL RESCUE options on venue-confirmed stranded legs.

For every window whose settlement left unmatched inventory, replay the book
observations recorded AFTER the stranding fill and price the four actions a
policy could take, using only information available at each decision time:

  1. maker completion  -- join the complement touch. DISPLAYED, NOT A FILL.
  2. taker completion  -- cross for the complement. Executable against
                          displayed depth, fee charged.
  3. sell the held leg -- exit as taker, fee charged.
  4. hold to settlement -- the venue's own settled residual_pnl.

Oracle rows pick the best instant over the window and are LOOKAHEAD: they
bound what any policy could have achieved, they are not achievable. The
causal rows use a rule decidable at each tick and are the implementable
comparison.

Top-of-book only: the Oct 24 tapes predate L5_RESIDUAL_BOOK_SAMPLE, so no
depth walk is possible beyond the touch and its displayed size.
"""
import json, glob, re, sys, datetime as dt
from decimal import Decimal as D
sys.path.insert(0, "src")
import pmus_fees as FEES

COMPLETE_CUTOFF_S = 720.0      # TS.complete_cutoff_s()
TICK = D("0.01")

def own_buy(side, bid, ask, mode):
    bid, ask = D(str(bid)), D(str(ask))
    if side == "UP":
        return bid if mode == "MAKER" else ask
    return (D(1) - ask) if mode == "MAKER" else (D(1) - bid)

def own_sell_taker(side, bid, ask):
    bid, ask = D(str(bid)), D(str(ask))
    return bid if side == "UP" else (D(1) - ask)

def wire_for_fee(side, own_px):
    """fee is charged on the venue's price for the contract traded."""
    return own_px if side == "UP" else (D(1) - own_px)

def walk_buy(side, bids, asks, qty):
    """VWAP own cost to BUY `qty` of `side` against displayed depth.

    Buying UP crosses the ASK ladder at wire px. Buying DOWN crosses the
    BID ladder, and its own cost at wire p is 1 - p, so the CHEAPEST own
    cost for DOWN is the HIGHEST wire bid -- the ladder is already in that
    order. Returns (vwap_own, enough_depth).
    """
    need = D(str(qty)); spent = D(0); got = D(0)
    ladder = asks if side == "UP" else bids
    for px, sz in ladder:
        own = D(str(px)) if side == "UP" else (D(1) - D(str(px)))
        take = min(need - got, D(str(sz)))
        if take <= 0:
            break
        spent += own * take; got += take
        if got >= need:
            break
    if got < need or got == 0:
        return None, False
    return spent / got, True


def load(tape):
    fills, books, samples, settled = [], [], [], None
    for ln in open(tape):
        d = json.loads(ln)
        lab = d.get("label")
        try:
            b = json.loads(d["body"], strict=False)
        except Exception:
            continue
        if lab == "L5_LIVE_LIFECYCLE":
            if b.get("terminal") and b.get("state") == "ORDER_STATE_FILLED":
                fills.append((d["ts"], b.get("venue_order_id"),
                              b.get("avg_px"), b.get("cum_qty")))
        elif lab == "L5_LIVE_DECISION":
            bk = b.get("book") or {}
            if bk.get("bid") and bk.get("ask"):
                books.append((d["ts"], bk["bid"], bk["ask"],
                              bk.get("bid_shares"), bk.get("ask_shares")))
        elif lab == "L5_RESIDUAL_BOOK_SAMPLE":
            row = ((b.get("book") or {}).get("row") or {})
            bids, asks = row.get("bids") or [], row.get("asks") or []
            if bids and asks:
                samples.append((d["ts"], bids, asks))
        elif lab == "L5_LIVE_SETTLED":
            settled = b
    return sorted(set(fills)), sorted(set(books)), samples, settled

def t_of(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))

def window_start(day, win):
    return dt.datetime(int(day[:4]), int(day[4:6]), int(day[6:8]),
                       int(win[:2]), int(win[2:4]), tzinfo=dt.timezone.utc)

def evaluate(tape):
    m = re.search(r'_(\d{8})_(\d{4})z\.ndjson$', tape)
    day, win = m.group(1), m.group(2)
    t0 = window_start(day, win)
    fills, books, samples, settled = load(tape)
    if not settled:
        return None
    rq = D(str(settled.get("residual_qty", "0")))
    if rq == 0:
        return None
    rs = settled.get("residual_side")
    rcost = D(str(settled["residual_cost"]))
    first_own = rcost / rq
    comp = "UP" if rs == "DOWN" else "DOWN"
    hold = D(str(settled["residual_pnl"]))
    tfill = t_of(fills[0][0]) if fills else t0

    rows = []
    for ts, bids, asks in samples:
        t = t_of(ts)
        if t <= tfill:
            continue
        el = (t - t0).total_seconds()
        best_bid, best_ask = D(str(bids[0][0])), D(str(asks[0][0]))
        mk = own_buy(comp, best_bid, best_ask, "MAKER")
        tkw, ok = walk_buy(comp, bids, asks, rq)
        mk_net = rq * (D(1) - (first_own + mk))
        if ok:
            tk_fee = FEES.taker_fee(wire_for_fee(comp, tkw), rq)
            tk_net = rq * (D(1) - (first_own + tkw)) - tk_fee
        else:
            tkw, tk_net = D(0), D(-99)
        spw, sok = walk_buy("UP" if rs == "DOWN" else "DOWN", bids, asks, rq)
        sp = own_sell_taker(rs, best_bid, best_ask)
        sl_fee = FEES.taker_fee(wire_for_fee(rs, sp), rq)
        sl_net = rq * (sp - first_own) - sl_fee
        rows.append(dict(ts=ts, el=el, mk=mk, tk=tkw, mk_pair=first_own + mk,
                         tk_pair=first_own + tkw, mk_net=mk_net,
                         tk_net=tk_net, sl_net=sl_net,
                         depth=D(0), depth_ok=ok, src="SAMPLE"))
    for ts, bid, ask, bsh, ash in ([] if samples else books):
        t = t_of(ts)
        if t <= tfill:
            continue
        el = (t - t0).total_seconds()
        mk = own_buy(comp, bid, ask, "MAKER")
        tk = own_buy(comp, bid, ask, "TAKER")
        # taker completion: pay tk per contract, pair redeems exactly 1
        tk_fee = FEES.taker_fee(wire_for_fee(comp, tk), rq)
        tk_net = rq * (D(1) - (first_own + tk)) - tk_fee
        mk_fee = D(0)      # maker pays no fee
        mk_net = rq * (D(1) - (first_own + mk))
        # sell the held leg
        sp = own_sell_taker(rs, bid, ask)
        sl_fee = FEES.taker_fee(wire_for_fee(rs, sp), rq)
        sl_net = rq * (sp - first_own) - sl_fee
        depth = D(str(ash if comp == "UP" else bsh or 0))
        rows.append(dict(ts=ts, el=el, mk=mk, tk=tk, mk_pair=first_own + mk,
                         tk_pair=first_own + tk, mk_net=mk_net, tk_net=tk_net,
                         sl_net=sl_net, depth=depth,
                         depth_ok=(depth >= rq), src="TOUCH"))
    rows.sort(key=lambda x: x["ts"])
    return dict(day=day, win=win, rq=rq, rs=rs, first_own=first_own,
                hold=hold, comp=comp, rows=rows,
                tfill_el=(tfill - t0).total_seconds())

def main():
    tapes = sorted(glob.glob(
        "analysis/l5/residual_exit/preserved/btc15m_*.ndjson"))
    out = []
    for tp in tapes:
        r = evaluate(tp)
        if r:
            out.append(r)
    print("STRANDED LEGS WITH VENUE-CONFIRMED SETTLEMENT\n")
    hdr = (f"{'win':9} {'qty':>5} {'side':5} {'own':>6} {'hold':>8} "
           f"{'ORACLE mk':>10} {'ORACLE tk':>10} {'ORACLE sell':>11} "
           f"{'causal tk':>10}")
    print(hdr); print("-" * len(hdr))
    tot = {k: D(0) for k in ("hold", "omk", "otk", "osl", "ctk")}
    for r in out:
        inside = [x for x in r["rows"] if x["el"] <= COMPLETE_CUTOFF_S]
        if not inside:
            print(f"{r['day'][4:]}/{r['win']}z  NO book obs inside cutoff")
            continue
        omk = max(x["mk_net"] for x in inside)
        otk = max(x["tk_net"] for x in inside if x["depth_ok"])
        osl = max(x["sl_net"] for x in inside)
        # causal rule: take the FIRST taker completion that is net-positive
        ctk = next((x["tk_net"] for x in inside
                    if x["depth_ok"] and x["tk_net"] > 0), None)
        print(f"{r['day'][4:]}/{r['win']}z {str(r['rq']):>5} {r['rs']:5} "
              f"{r['first_own']:>6.3f} {r['hold']:>8.4f} "
              f"{omk:>10.4f} {otk:>10.4f} {osl:>11.4f} "
              f"{(f'{ctk:.4f}' if ctk is not None else 'none'):>10}"
              f"  n={len(inside)} {inside[0]['src']}")
        tot["hold"] += r["hold"]; tot["omk"] += omk
        tot["otk"] += otk; tot["osl"] += osl
        tot["ctk"] += (ctk if ctk is not None else r["hold"])
    print("-" * len(hdr))
    print(f"{'TOTAL':9} {'':>5} {'':5} {'':>6} {tot['hold']:>8.4f} "
          f"{tot['omk']:>10.4f} {tot['otk']:>10.4f} {tot['osl']:>11.4f} "
          f"{tot['ctk']:>10.4f}")
    print("\nORACLE columns are LOOKAHEAD (best instant in hindsight) and")
    print("bound what any policy could reach. 'causal tk' takes the FIRST")
    print("net-positive taker completion, decidable at the tick.")
    print("maker columns assume a fill at the touch that displayed depth")
    print("does NOT guarantee; taker columns are net of the taker fee and")
    print("require displayed depth >= qty. Within complete cutoff T+%ds."
          % COMPLETE_CUTOFF_S)

if __name__ == "__main__":
    main()
