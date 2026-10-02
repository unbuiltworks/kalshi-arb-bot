#!/usr/bin/env python3
"""Phase 2: execution mechanics on the 6 AUTHENTICATED discovery markets.

DISCOVERY ONLY. No order was placed. No touch = fill. No invented queue. No
zero-latency repricing. No capacity claim is made from six markets.

Expressions, all normalized to the SAME terminal payoff vector:
  A  taker favorite        -- cross at the contemporaneous executable price
  B  maker favorite        -- rest to buy the favorite, fill only on evidence
  C  maker opposite-longshot / equivalent sell -- rest to SELL the longshot
  D  WAIT                  -- always available; nonfills are scored as WAIT
"""
from __future__ import annotations
import collections, gzip, json, math, statistics as st, sys
sys.path.insert(0, ".")
from src.pmus_fees import taker_fee, maker_rebate
from decimal import Decimal as D
import datetime as dt

AUTH = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"
ST = "analysis/pmus_late_favorite/states_joined.json"
TICK = 0.01
WHEN = dt.datetime(2026, 9, 30, 13, 0, tzinfo=dt.timezone.utc)   # all 6 markets are this day


def f(x):
    try:
        return float(x)
    except Exception:
        return None


states = [s for s in json.load(open(ST))["states"] if s["source"] == "authenticated_ws"]
trades = collections.defaultdict(list)
for line in gzip.open(AUTH, "rt"):
    d = json.loads(line)
    if d["kind"] == "trade":
        trades[d["slug"]].append((d["t"], f(d["px"]), f(d["qty"]), d["dir"]))
for v in trades.values():
    v.sort()


def wstart(slug):
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})z$", slug)
    return dt.datetime.fromisoformat(
        f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00+00:00").timestamp()


def resting_fill(slug, t0, t_end, side, price, queue_ahead):
    """side 'bid' (we buy UP at `price`) or 'offer' (we sell UP at `price`).
    Returns (filled, mode, seconds_to_fill). Two defensible modes only:
      STRICT_THROUGH  a taker trade printed strictly through our price
      QUEUE_CONSUMED  same-price taker volume exceeded the depth displayed ahead
                      of us when we joined
    A same-price print that does NOT exceed the displayed queue is NOT a fill."""
    vol_at = 0.0
    for t, px, qty, dr in trades.get(slug, ()):
        if t <= t0:
            continue
        if t > t_end:
            break
        if px is None or qty is None:
            continue
        if side == "bid":
            if dr == "sell" and px < price - 1e-9:
                return True, "STRICT_THROUGH", t - t0
            if dr == "sell" and abs(px - price) < 1e-9:
                vol_at += qty
                if queue_ahead is not None and vol_at > queue_ahead:
                    return True, "QUEUE_CONSUMED", t - t0
        else:
            if dr == "buy" and px > price + 1e-9:
                return True, "STRICT_THROUGH", t - t0
            if dr == "buy" and abs(px - price) < 1e-9:
                vol_at += qty
                if queue_ahead is not None and vol_at > queue_ahead:
                    return True, "QUEUE_CONSUMED", t - t0
    return False, "NO_FILL", None


def ev_buy_favorite(price, won, *, maker):
    """1 share. Payoff normalized: +(1-price) if favorite settles, -price if not.
    Fees kept strictly separate from directional P&L."""
    gross = (1.0 - price) if won else (-price)
    if maker:
        return gross, float(maker_rebate(D(str(round(price, 2))), D(1), when=WHEN)), 0.0
    return gross, 0.0, float(taker_fee(D(str(round(price, 2))), D(1), when=WHEN))


print("PHASE 2 -- DISCOVERY ONLY, 6 authenticated markets, 2026-09-30 12:15z-13:30z\n")

# ---------- C's equivalence, shown before any economics ----------
print("=== EXPRESSION C: payoff-vector and queue equivalence, shown explicitly ===")
print("  Buy FAVORITE 1 share at f : payoff = +(1-f) if fav settles, -f if not")
print("  Sell LONGSHOT 1 share at q: receive q now, owe $1 iff longshot settles")
print("                            : payoff = +q if fav settles, (q-1) if not")
print("  Setting q = 1-f makes the two payoff vectors IDENTICAL:")
print("     fav settles : (1-f)   vs  q   = 1-f      -> equal")
print("     fav loses   : (-f)    vs  q-1 = -f       -> equal")
print("  So C is the same economic position as A/B, NOT an arbitrage against them.")
print()
print("  Fee identity: the venue fee is theta*C*p*(1-p), and p(1-p) is SYMMETRIC about 0.5,")
print("  so pricing the same position at f or at q=1-f incurs the SAME fee and the SAME rebate:")
for fv in (0.99, 0.95, 0.90):
    a = float(taker_fee(D(str(fv)), D(1), when=WHEN)); b = float(taker_fee(D(str(round(1-fv,2))), D(1), when=WHEN))
    ra = float(maker_rebate(D(str(fv)), D(1), when=WHEN)); rb = float(maker_rebate(D(str(round(1-fv,2))), D(1), when=WHEN))
    print(f"    f={fv:.2f} -> taker {a*100:.4f}c, rebate {ra*100:.4f}c   |   "
          f"q={1-fv:.2f} -> taker {b*100:.4f}c, rebate {rb*100:.4f}c   {'MATCH' if abs(a-b)<1e-12 and abs(ra-rb)<1e-12 else 'DIFFER'}")
print()
print("  Queue identity on this venue's book representation: the tape serves ONE book per")
print("  market, quoted in UP terms (single bid/ask/offer-ladder per slug). Resting an OFFER")
print("  on the longshot at q is therefore the SAME order, at the SAME price level, in the")
print("  SAME queue, as resting a BID to buy the favorite at 1-q. C is not a distinct")
print("  expression on this book; it is B written the other way round.")
print("  WHAT WOULD CHANGE THIS: evidence that PMUS maintains SEPARATE per-side books with")
print("  independent queues (as a mint-and-match CLOB can). The single bid/ask/ladder in this")
print("  tape does not support that, so it is recorded as unresolved-but-unsupported, not proven.")
print()

rows = []
for s in states:
    slug, cp = s["slug"], s["checkpoint_s_remaining"]
    T = wstart(slug); mark = T + 900 - cp; t_end = T + 900
    won = s["favorite_won"]; fav_up = s["fav_side"] == "UP"
    bid, ask = s["book_bid_up"], s["book_ask_up"]
    r = {"slug": slug, "cp": cp, "won": won, "fav": s["fav_side"], "z": s["z"],
         "abs_z": s["abs_z"], "fav_ask": s["fav_ask"], "opp_ask": s["opp_ask"],
         "bid": bid, "ask": ask, "bid_qty": s["bid_qty"], "ask_qty": s["ask_qty"]}

    # ---- A: taker favorite
    if s["fav_ask"] is not None:
        g, reb, fee = ev_buy_favorite(s["fav_ask"], won, maker=False)
        depth = (s["ask_qty"] if fav_up else s["bid_qty"])
        r["A"] = {"price": s["fav_ask"], "gross": g, "fee": fee, "net": g - fee,
                  "depth_shares": depth}
    else:
        r["A"] = None   # favorite unbuyable: A unavailable, WAIT is forced

    # ---- B: maker favorite, JOIN the side that buys the favorite
    if fav_up and bid is not None:
        side, px, qa = "bid", bid, s["bid_qty"]
    elif (not fav_up) and ask is not None:
        side, px, qa = "offer", ask, s["ask_qty"]
    else:
        side = None
    if side:
        fl, mode, tf = resting_fill(slug, mark, t_end, side, px, qa)
        eff = px if fav_up else 1.0 - px          # price PAID for the favorite
        g, reb, fee = ev_buy_favorite(eff, won, maker=True)
        r["B"] = {"side": side, "rest_px_up": px, "fav_price_paid": eff,
                  "queue_ahead": qa, "filled": fl, "mode": mode, "t_fill": tf,
                  "gross": g, "rebate": reb}
    else:
        r["B"] = None
    rows.append(r)

print("=== PER-STATE EXECUTION RECORD (all 30 authenticated states) ===")
print(f"{'window':>6} {'T-':>4} {'fav':>5} {'z':>6} {'won':>3} {'A_px':>6} {'A_net':>7} "
      f"{'depth':>8} {'B_rest':>7} {'B_paid':>7} {'qahead':>8} {'B_fill':>6} {'mode':>15} {'t_fill':>7}")
for r in rows:
    A, B = r["A"], r["B"]
    print(f"{r['slug'][30:35]:>6} {r['cp']:4d} {r['fav']:>5} {r['z']:+6.2f} {r['won']:3d} "
          f"{(('%.2f'%A['price']) if A else 'NONE'):>6} {(('%+.4f'%A['net']) if A else '      -'):>7} "
          f"{(('%.0f'%A['depth_shares']) if A and A['depth_shares'] is not None else '-'):>8} "
          f"{(('%.2f'%B['rest_px_up']) if B else '-'):>7} {(('%.2f'%B['fav_price_paid']) if B else '-'):>7} "
          f"{(('%.0f'%B['queue_ahead']) if B and B['queue_ahead'] is not None else '-'):>8} "
          f"{(('YES' if B['filled'] else 'no') if B else '-'):>6} {(B['mode'] if B else '-'):>15} "
          f"{(('%.1fs'%B['t_fill']) if B and B['t_fill'] is not None else '-'):>7}")

# ---------- aggregate ----------
print("\n=== A: TAKER FAVORITE ===")
Aa = [r for r in rows if r["A"]]
nav = [r for r in rows if not r["A"]]
print(f"  opportunities (states)            {len(rows)}")
print(f"  A AVAILABLE                       {len(Aa)}")
print(f"  A UNAVAILABLE (favorite unbuyable){len(nav)}  -> forced WAIT")
if Aa:
    print(f"  mean favorite price paid          {st.mean(r['A']['price'] for r in Aa):.4f}")
    print(f"  gross directional P&L / share     {st.mean(r['A']['gross'] for r in Aa):+.5f}")
    print(f"  taker fees / share                {st.mean(r['A']['fee'] for r in Aa):.5f}")
    print(f"  NET / share (filled states)       {st.mean(r['A']['net'] for r in Aa):+.5f}")
    print(f"  UNCONDITIONAL net / opportunity   {sum(r['A']['net'] for r in Aa)/len(rows):+.5f}  "
          f"(nonavailable counted as WAIT = 0)")
    d = [r['A']['depth_shares'] for r in Aa if r['A']['depth_shares'] is not None]
    if d: print(f"  displayed depth at the touch      p50 {st.median(d):.0f} shares, min {min(d):.0f}, max {max(d):.0f}")

print("\n=== B: MAKER FAVORITE (= C, same order) ===")
Bb = [r for r in rows if r["B"]]
fil = [r for r in Bb if r["B"]["filled"]]
print(f"  placements (eligible opportunities) {len(Bb)}")
print(f"  defensible fills                    {len(fil)}")
print(f"  nonfills                            {len(Bb)-len(fil)}")
print(f"  fill rate                           {len(fil)/len(Bb):.1%}" if Bb else "")
print(f"  fill modes                          {dict(collections.Counter(r['B']['mode'] for r in Bb))}")
if fil:
    print(f"  time to fill                        p50 {st.median(r['B']['t_fill'] for r in fil):.1f}s, "
          f"min {min(r['B']['t_fill'] for r in fil):.1f}s, max {max(r['B']['t_fill'] for r in fil):.1f}s")

print("\n=== THE ADVERSE-SELECTION MEASUREMENT ===")
if Bb:
    p_all = st.mean(r["won"] for r in Bb)
    p_fill = st.mean(r["won"] for r in fil) if fil else None
    print(f"  P(correct | all eligible opportunities) = {p_all:.4f}   (n={len(Bb)} states)")
    print(f"  P(correct | defensible fill)            = "
          f"{('%.4f'%p_fill) if p_fill is not None else 'n/a'}   (n={len(fil)} fills)")
    if p_fill is not None:
        print(f"  ADVERSE_SELECTION_DELTA                 = {p_fill-p_all:+.4f}")
    ev_all = st.mean(r["B"]["gross"] for r in Bb)
    ev_fill = st.mean(r["B"]["gross"] for r in fil) if fil else None
    ev_unc = sum(r["B"]["gross"] for r in fil) / len(Bb) if Bb else 0.0
    print(f"  EV/share over ALL eligible (hypothetical always-fill) = {ev_all:+.5f}")
    print(f"  EV/share CONDITIONAL on defensible fill               = "
          f"{('%+.5f'%ev_fill) if ev_fill is not None else 'n/a'}")
    if ev_fill is not None:
        print(f"  EV ADVERSE-SELECTION DELTA                            = {ev_fill-ev_all:+.5f}")
    print(f"  UNCONDITIONAL EV / opportunity (nonfill = WAIT = 0)   = {ev_unc:+.5f}")
    if fil:
        print(f"  maker rebate on fills only (kept separate)            = "
              f"{sum(r['B']['rebate'] for r in fil):+.5f} total, "
              f"{st.mean(r['B']['rebate'] for r in fil):+.5f}/share")
print("\n  NOTE: these are 6 markets in one 90-minute stretch. No $3/$5 claim, no capacity")
print("  claim, and no extrapolation is made from them.")
