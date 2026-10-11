"""Executable-book ladder at fixed TTE points. DESCRIPTIVE ONLY.

No strategy is fitted, no threshold chosen, no sealed OOS read. This answers
one question: WHAT WAS ACTUALLY BUYABLE late in the window, as opposed to what
the UI percentage suggested.

NO COLLECTION CHANGE WAS NEEDED. Verified on the live tapes: the final 60 s of
every window already carries 59-60 depth20 snapshots plus every @depth diff,
max inter-event gap p50 1.06 s / worst 2.01 s. The T-60/45/30/20/15/10/5/2/1
ladder is therefore an EXTRACTION from the existing stream.

Per window and TTE point, from the reconstructed book:
  best ask UP + ask size          -> what you could actually BUY UP at
  effective DOWN price + size     -> (1 - bid), size = bid size
  spread, mid
  Coinbase price and distance from strike
  eventual settlement value, resolution side

THE DISTINCTION THIS EXISTS TO ENFORCE: the UI/REST percentage is the last
trade price complemented to 100 and can be minutes stale. `up_ask` here is
None whenever no offer existed -- which, in the forensic window, was the case
for the entire final 20 s while the UI displayed "Up 51%".

Coinbase is an EXTERNAL PREDICTIVE FEED, never the settlement index. The Lukka
LS-LRRR index has no public continuous feed, so only its open (strike) and
close (settlement_value) anchors are known and nothing between is interpolated.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TTES = (120, 90, 60, 45, 30, 20, 15, 10, 5, 2, 1)


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def _predicted_side_fields(c, strike, bid, ask, bids, asks, up_won):
    """Side-symmetric executability of the PREDICTED side.

    predicted UP   -> price = best UP ask,        size = UP ask size
    predicted DOWN -> price = 1 - best UP bid,    size = UP bid size

    `predicted_side_edge` is intentionally NOT computed here: it requires an
    empirical conditional settlement probability, which must be estimated on
    DEV and applied out-of-sample. Computing it from the same observations
    being scored would be circular. The field is emitted as None and filled
    by the frozen-model stage.
    """
    if c is None:
        return {"predicted_side": None, "predicted_side_available": None,
                "predicted_side_price": None, "predicted_side_size": None,
                "predicted_side_settled_correct": None,
                "predicted_side_edge": None}
    side = "UP" if (c - strike) > 0 else "DOWN"
    if side == "UP":
        px = ask
        sz = asks[ask] if ask is not None else None
    else:
        px = round(1 - bid, 4) if bid is not None else None
        sz = bids[bid] if bid is not None else None
    correct = (up_won if side == "UP" else (not up_won))
    return {"predicted_side": side,
            "predicted_side_available": px is not None,
            "predicted_side_price": px,
            "predicted_side_size": sz,
            "predicted_side_settled_correct": correct,
            "predicted_side_edge": None}


def ap(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def build(outdir="data/gemini_infoedge"):
    settled, expiry, cb = {}, {}, []
    ev = defaultdict(list)
    for f in sorted(Path(outdir).glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if r.get("resolution_side"):
                    settled[r["ticker"]] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll") and r.get("expiry"):
                expiry[tk] = datetime.fromisoformat(
                    r["expiry"].replace("Z", "+00:00")).replace(
                    tzinfo=timezone.utc).timestamp()
            elif k in ("p_depth", "p_snapshot"):
                ev[tk].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    rows = []
    for tk, s in settled.items():
        E = expiry.get(tk)
        if E is None:
            continue
        try:
            strike = float(s["strike"])
            settle = float(s["settlement_value"])
        except Exception:
            continue
        up_won = s["resolution_side"] == "yes"
        evs = sorted(ev.get(tk, []), key=lambda r: r["t_ns"])
        # reconstruct the book forward, recording state at each TTE point
        bids, asks, seeded, last_u = {}, {}, False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        snapshot_at = {}
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                # snapshots are authoritative full top-20 state
                bids, asks = {}, {}
                ap(bids, r.get("bids"))
                ap(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                ap(bids, r.get("b"))
                ap(asks, r.get("a"))
                last_u = u
            while wi < len(want) and (E - t) <= want[wi]:
                snapshot_at[want[wi]] = (dict(bids), dict(asks), t)
                wi += 1
        for tte in TTES:
            st = snapshot_at.get(tte)
            if st is None:
                rows.append({"ticker": tk, "tte": tte, "observed": False,
                             "up_won": up_won})
                continue
            b, a, t = st
            bid = max(b) if b else None
            ask = min(a) if a else None
            c = cb_at(t)
            rows.append({
                "ticker": tk, "tte": tte, "observed": True, "t": t,
                "up_ask": ask, "up_ask_sz": a[ask] if ask is not None else None,
                "down_price": (round(1 - bid, 4) if bid is not None else None),
                "down_sz": b[bid] if bid is not None else None,
                "bid": bid, "spread": (round(ask - bid, 4)
                                       if (bid is not None and ask is not None) else None),
                "mid": (round((ask + bid) / 2, 4)
                        if (bid is not None and ask is not None) else None),
                "coinbase": c,
                "cb_dist": (round(c - strike, 2) if c is not None else None),
                # ---- SIDE-SYMMETRIC predicted-side executability ----------
                # has_ask is NOT shorthand for "can buy the predicted winner".
                # Determine the predicted side FIRST, then price/size THAT
                # side. Placeholder predictor for DESCRIPTIVE use only:
                # sign(coinbase - strike). It is mechanical, not fitted, and
                # is replaced by the frozen model on the powered cohort.
                **_predicted_side_fields(c, strike, bid, ask, b, a, up_won),
                "strike": strike, "settlement_value": settle,
                "settle_dist": round(settle - strike, 2),
                "resolution_side": s["resolution_side"], "up_won": up_won,
            })
    return rows


def fmt(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def report(rows):
    tickers = {r["ticker"] for r in rows}
    print("=" * 86)
    print("EXECUTABLE-BOOK LADDER AT FIXED TTE  --  DESCRIPTIVE, NO STRATEGY FITTED")
    print("=" * 86)
    print(f"settled windows with book coverage: {len(tickers)}")
    print("Preregistered n=100 DESCRIPTIVE read (PREREG_POWERED_COHORT.md).")
    print("Predictor is the PLACEHOLDER sign(coinbase - strike): mechanical, not")
    print("fitted, not the model. No edge is computed -- P(correct|state) must be")
    print("estimated on DEV and applied out-of-sample, so it stays None here.\n")

    print("--- Q: does an EXECUTABLE UP ask even EXIST at each TTE? ---")
    print(f"  {'TTE':>5s} {'n':>5s} {'has_ask':>8s} {'has_ask%':>9s} "
          f"{'ask_p50':>8s} {'asksz_p50':>10s} {'spread_p50':>11s} {'2sided%':>8s}")
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte and r.get("observed")]
        if not g:
            continue
        ha = [r for r in g if r.get("up_ask") is not None]
        ts = [r for r in g if r.get("spread") is not None]
        ask_p50 = pct([r["up_ask"] for r in ha], .5) if ha else None
        sz_p50 = pct([r["up_ask_sz"] for r in ha], .5) if ha else None
        sp_p50 = pct([r["spread"] for r in ts], .5) if ts else None
        print(f"  {tte:5d} {len(g):5d} {len(ha):8d} {len(ha)/len(g)*100:8.1f}% "
              f"{fmt(ask_p50):>8s} {fmt(sz_p50,1):>10s} {fmt(sp_p50):>11s} "
              f"{len(ts)/len(g)*100:7.1f}%")

    print()
    print("--- Q: DOWN side executability (acquire DOWN = 1 - bid) ---")
    print(f"  {'TTE':>5s} {'has_bid%':>9s} {'down_px_p50':>12s} {'down_sz_p50':>12s}")
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte and r.get("observed")]
        if not g:
            continue
        hb = [r for r in g if r.get("down_price") is not None]
        print(f"  {tte:5d} {len(hb)/len(g)*100:8.1f}% "
              f"{fmt(pct([r['down_price'] for r in hb], .5) if hb else None):>12s} "
              f"{fmt(pct([r['down_sz'] for r in hb], .5) if hb else None, 1):>12s}")

    print()
    print("--- Q: settlement vs Coinbase distance (DESCRIPTIVE, not a signal) ---")
    print(f"  {'TTE':>5s} {'n':>5s} {'cbdist_p50':>11s} {'UPwon%':>8s} "
          f"{'cb>0 n':>7s} {'UPwon%':>8s} {'cb<=0 n':>8s} {'UPwon%':>8s}")
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte and r.get("observed")
             and r.get("cb_dist") is not None]
        if len(g) < 3:
            continue
        pos = [r for r in g if r["cb_dist"] > 0]
        neg = [r for r in g if r["cb_dist"] <= 0]
        wr = lambda s: (sum(1 for r in s if r["up_won"]) / len(s) * 100) if s else None
        print(f"  {tte:5d} {len(g):5d} {fmt(pct([r['cb_dist'] for r in g], .5), 2):>11s} "
              f"{fmt(wr(g),1):>7s}% {len(pos):7d} {fmt(wr(pos),1):>7s}% "
              f"{len(neg):8d} {fmt(wr(neg),1):>7s}%")

    print()
    print("--- THE PREDICTED SIDE, SIDE-SYMMETRICALLY (prereg 9a59a418) ---")
    print("  predicted UP   -> best UP ask    + ask size")
    print("  predicted DOWN -> 1 - best UP bid + bid size")
    print("  Predictor: sign(coinbase - strike). PLACEHOLDER, not the model.")
    print(f"  {'TTE':>5s} {'n':>5s} {'avail':>6s} {'avail%':>7s} "
          f"{'px_p50':>7s} {'px_p10':>7s} {'px_p90':>7s} {'sz_p50':>7s} "
          f"{'correct%':>9s} {'corr|avail%':>12s}")
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte and r.get("observed")
             and r.get("cb_dist") is not None]
        if len(g) < 3:
            continue
        recs = []
        for r in g:
            up = r["cb_dist"] > 0
            px = r["up_ask"] if up else r["down_price"]
            sz = r["up_ask_sz"] if up else r["down_sz"]
            correct = (r["up_won"] is True) if up else (r["up_won"] is False)
            recs.append({"px": px, "sz": sz, "correct": correct})
        av = [x for x in recs if x["px"] is not None]
        wr = lambda s: (sum(1 for x in s if x["correct"]) / len(s) * 100) if s else None
        print(f"  {tte:5d} {len(recs):5d} {len(av):6d} "
              f"{len(av)/len(recs)*100:6.1f}% "
              f"{fmt(pct([x['px'] for x in av], .5) if av else None, 2):>7s} "
              f"{fmt(pct([x['px'] for x in av], .1) if av else None, 2):>7s} "
              f"{fmt(pct([x['px'] for x in av], .9) if av else None, 2):>7s} "
              f"{fmt(pct([x['sz'] for x in av], .5) if av else None, 1):>7s} "
              f"{fmt(wr(recs),1):>8s}% {fmt(wr(av),1):>11s}%")

    print()
    print("--- THE CROSS-TAB THAT MATTERS: executable price CONDITIONAL on distance ---")
    print("  'At T-30, Coinbase +$X above strike, UP ask 0.78 x 25 -> how often")
    print("   does UP settle?'  Buckets are fixed, not fitted.")
    BK = [("cb<=-20", -1e9, -20), ("-20..-5", -20, -5), ("-5..0", -5, 0),
          ("0..+5", 0, 5), ("+5..+20", 5, 20), ("cb>+20", 20, 1e9)]
    for tte in (30, 15, 5):
        g = [r for r in rows if r["tte"] == tte and r.get("observed")
             and r.get("cb_dist") is not None]
        if not g:
            continue
        print(f"\n  TTE = {tte}s   (n={len(g)})")
        print(f"    {'cb_dist':>10s} {'n':>4s} {'UPwon%':>8s} {'has_ask':>8s} "
              f"{'ask_p50':>8s} {'asksz_p50':>10s} {'down_px_p50':>12s}")
        for name, lo, hi in BK:
            b = [r for r in g if lo < r["cb_dist"] <= hi]
            if not b:
                continue
            ha = [r for r in b if r.get("up_ask") is not None]
            hb = [r for r in b if r.get("down_price") is not None]
            wr = sum(1 for r in b if r["up_won"]) / len(b) * 100
            print(f"    {name:>10s} {len(b):4d} {wr:7.1f}% {len(ha):8d} "
                  f"{fmt(pct([r['up_ask'] for r in ha], .5) if ha else None):>8s} "
                  f"{fmt(pct([r['up_ask_sz'] for r in ha], .5) if ha else None,1):>10s} "
                  f"{fmt(pct([r['down_price'] for r in hb], .5) if hb else None):>12s}")
    print()
    print("  READ THIS CAUTIOUSLY. A high UP-win rate when Coinbase is above the")
    print("  strike near expiry is largely MECHANICAL, not an edge -- the market")
    print("  sees the same thing. The edge question is whether a CHEAP UP ask")
    print("  actually EXISTS in the favourable buckets, or whether offers appear")
    print("  only when the favourite is about to lose (selection). That is what")
    print("  the n=100 read must settle, and it is NOT settled here.")

    print()
    print("--- SIDE-SYMMETRIC: can we acquire the PREDICTED side at all? ---")
    print("  (placeholder predictor sign(coinbase-strike); mechanical, NOT fitted)")
    print(f"  {'TTE':>5s} {'n':>5s} {'avail':>6s} {'avail%':>8s} {'px_p50':>8s} "
          f"{'size_p50':>9s} {'correct%':>9s} {'correct%|avail':>15s}")
    for tte in TTES:
        g = [r for r in rows if r.get("observed") and r["tte"] == tte
             and r.get("predicted_side") is not None]
        if not g:
            continue
        av = [r for r in g if r["predicted_side_available"]]
        cr = lambda s2: (sum(1 for r in s2 if r["predicted_side_settled_correct"])
                         / len(s2) * 100) if s2 else None
        print(f"  {tte:5d} {len(g):5d} {len(av):6d} {len(av)/len(g)*100:7.1f}% "
              f"{fmt(pct([r['predicted_side_price'] for r in av], .5) if av else None):>8s} "
              f"{fmt(pct([r['predicted_side_size'] for r in av], .5) if av else None,1):>9s} "
              f"{fmt(cr(g),1):>8s}% {fmt(cr(av),1):>14s}%")
    print("  'correct%|avail' is the one that matters: how often the predicted")
    print("  side won, AMONG the observations where it was actually buyable.")

    print()
    print("--- UI-vs-EXECUTABLE: windows with NO buyable UP at T-5 s ---")
    g5 = [r for r in rows if r["tte"] == 5 and r.get("observed")]
    noask = [r for r in g5 if r.get("up_ask") is None]
    if g5:
        print(f"  {len(noask)}/{len(g5)} windows had NO UP offer at T-5 s "
              f"({len(noask)/len(g5)*100:.0f}%)")
        print("  In each of those, any UI percentage shown was NOT an acquisition")
        print("  price. The forensic window (2610022155) was one of them.")


if __name__ == "__main__":
    rows = build()
    Path("analysis/gemini/info_edge/ie_tte_ladder.json").write_text(
        json.dumps(rows, indent=1))
    report(rows)
