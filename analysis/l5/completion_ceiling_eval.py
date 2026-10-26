#!/usr/bin/env python3
"""Completion-only pair ceiling: 0.96 / 0.97 / 0.98 / 0.99, maker only.

The question is narrow: keep 0.96 for NEW exposure, but allow COMPLETING an
existing unmatched leg up to an all-in pair cost C, provided the completed
quantity has positive guaranteed net settlement P&L after fees.

Three quantities are kept strictly apart:

  GUARANTEED-IF-FILLED  q * (1 - pair).  A matched pair redeems exactly
                        $1.00, and a maker pays no venue fee (confirmed:
                        actual_fees_usd 0 on every window), so this is
                        certain the moment both legs are filled. It says
                        nothing about whether the second leg fills.

  FILL FEASIBILITY      measured, never assumed. Our maker buy of the
                        complement at own price p fills only if the market
                        later comes to us: in own terms the counterparty's
                        taker cost must reach p. AT_TOUCH uses <= p, STRICT
                        uses < p. Per-order queue position is not published,
                        so even AT_TOUCH is an UPPER BOUND on filling, not a
                        fill.

  REALIZED              what the venue actually settled.

Causal throughout. At each observation the policy reprices to the best
maker price the ceiling allows, p_t = min(C - f, maker_join_own_t), and the
fill test uses only LATER observations. Nothing picks a minimum in
hindsight. Completion is allowed for the whole eligible window, so late
opportunities are preserved rather than cut off early.
"""
import json, glob, re, sys, datetime as dt
from decimal import Decimal as D

CUTOFF_S = 720.0        # TS.complete_cutoff_s()
CEILINGS = [D("0.96"), D("0.97"), D("0.98"), D("0.99")]

#: strand first-leg own cost from the MARGINAL fill, not the engine's
#: pro-rata allocation, with the venue-settled residual P&L for comparison.
STRANDS = {
    "20261024_0115z": dict(side="DOWN", qty=D("0.96"), f=D("0.24"), realized=D("-0.2304")),
    "20261024_0145z": dict(side="DOWN", qty=D("1.03"), f=D("0.35"), realized=D("-0.3605")),
    "20261024_0215z": dict(side="UP",   qty=D("1.25"), f=D("0.31"), realized=D("+0.8625")),
    "20261026_2100z": dict(side="DOWN", qty=D("1.77"), f=D("0.26"), realized=D("-0.4602")),
    "20261026_2115z": dict(side="DOWN", qty=D("1.68"), f=D("0.17"), realized=D("-0.2856")),
    "20261026_2130z": dict(side="DOWN", qty=D("2.09"), f=D("0.52"), realized=D("-1.0868")),
}

def own_maker_buy(side, bid, ask):
    return D(str(bid)) if side == "UP" else D(1) - D(str(ask))

def own_taker_buy(side, bid, ask):
    return D(str(ask)) if side == "UP" else D(1) - D(str(bid))

def t_of(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def series(tape, t0):
    """(elapsed_s, bid, ask, depth_at_touch) oldest first, deduplicated."""
    out = []
    for ln in open(tape):
        d = json.loads(ln)
        try:
            b = json.loads(d["body"], strict=False)
        except Exception:
            continue
        lab = d.get("label")
        bid = ask = dep = None
        if lab == "L5_RESIDUAL_BOOK_SAMPLE":
            row = ((b.get("book") or {}).get("row") or {})
            if row.get("bids") and row.get("asks"):
                bid, ask = row["bids"][0][0], row["asks"][0][0]
                dep = {"bid": row["bids"][0][1], "ask": row["asks"][0][1]}
        elif lab == "L5_LIVE_DECISION":
            bk = b.get("book") or {}
            if bk.get("bid") and bk.get("ask"):
                bid, ask = bk["bid"], bk["ask"]
                dep = {"bid": bk.get("bid_shares"), "ask": bk.get("ask_shares")}
        if bid is None:
            continue
        el = (t_of(d["ts"]) - t0).total_seconds()
        out.append((el, D(str(bid)), D(str(ask)), dep))
    seen, uniq = set(), []
    for r in sorted(out):
        k = (r[0], r[1], r[2])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq

def replay(obs, held_side, qty, f, ceiling, mode):
    """First causal maker completion under `ceiling`. No lookahead.

    `held_side` is the leg we already own; we must BUY THE COMPLEMENT, so
    every price below is the complement's, not the held leg's.
    """
    side = "UP" if held_side == "DOWN" else "DOWN"
    allowed = ceiling - f
    if allowed <= 0:
        return None
    for i in range(len(obs) - 1):
        el, bid, ask, dep = obs[i]
        if el > CUTOFF_S:
            break
        touch = own_maker_buy(side, bid, ask)
        p = min(allowed, touch)
        if p <= 0:
            continue
        el2, bid2, ask2, dep2 = obs[i + 1]
        if el2 > CUTOFF_S:
            break
        tk2 = own_taker_buy(side, bid2, ask2)
        hit = (tk2 <= p) if mode == "AT_TOUCH" else (tk2 < p)
        if hit:
            pair = f + p
            return dict(el=el, el_fill=el2, p=p, pair=pair,
                        guaranteed=qty * (D(1) - pair),
                        joined_touch=(p == touch),
                        depth=(dep or {}).get("ask" if side == "DOWN" else "bid"))
    return None

def main():
    pre = "analysis/l5/residual_exit/preserved/btc15m_"
    print("COMPLETION-ONLY CEILING, MAKER ONLY, CAUSAL\n")
    for mode in ("AT_TOUCH", "STRICT"):
        print(f"===== fill model {mode} "
              f"({'market reaches our price' if mode=='AT_TOUCH' else 'market trades THROUGH our price'})"
              f" =====")
        hdr = (f"{'strand':16} {'qty':>5} {'f':>5} {'realized':>9} " +
               " ".join(f"{str(c):>18}" for c in CEILINGS))
        print(hdr); print("-" * len(hdr))
        tot = {c: D(0) for c in CEILINGS}
        nfill = {c: 0 for c in CEILINGS}
        for key, s in STRANDS.items():
            tape = f"{pre}{key}.ndjson"
            if not glob.glob(tape):
                continue
            w = key.split("_")[1]
            t0 = dt.datetime(int(key[:4]), int(key[4:6]), int(key[6:8]),
                             int(w[:2]), int(w[2:4]), tzinfo=dt.timezone.utc)
            obs = series(tape, t0)
            cells = []
            for c in CEILINGS:
                r = replay(obs, s["side"], s["qty"], s["f"], c, mode)
                if r is None:
                    cells.append(f"{'no fill':>18}")
                else:
                    tot[c] += r["guaranteed"]; nfill[c] += 1
                    cells.append(f"{r['pair']:.2f}@{r['el_fill']:.0f}s {r['guaranteed']:+.4f}".rjust(18))
            print(f"{key:16} {s['qty']:>5} {s['f']:>5} {s['realized']:>+9.4f} " + " ".join(cells))
        print("-" * len(hdr))
        print(f"{'completions':16} {'':>5} {'':>5} {'':>9} " +
              " ".join(f"{str(nfill[c])+' of 6':>18}" for c in CEILINGS))
        print(f"{'guaranteed sum':16} {'':>5} {'':>5} {'':>9} " +
              " ".join(f"{tot[c]:>+18.4f}" for c in CEILINGS))
        realized_all = sum(s["realized"] for s in STRANDS.values())
        print(f"\n  realized on these 6 strands (venue): {realized_all:+.4f}")
        for c in CEILINGS:
            unf = realized_all - sum(
                s["realized"] for k, s in STRANDS.items()
                if replay(series(f'{pre}{k}.ndjson', dt.datetime(
                    int(k[:4]), int(k[4:6]), int(k[6:8]),
                    int(k.split('_')[1][:2]), int(k.split('_')[1][2:4]),
                    tzinfo=dt.timezone.utc)), s["side"], s["qty"], s["f"], c, mode))
            print(f"  ceiling {c}: completed {tot[c]:+.4f} guaranteed + "
                  f"{unf:+.4f} left to settle = {tot[c]+unf:+.4f}")
        print()

if __name__ == "__main__":
    main()
