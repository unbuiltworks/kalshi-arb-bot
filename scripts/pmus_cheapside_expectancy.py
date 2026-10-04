"""Is the cheap side of a PMUS BTC15m market priced fairly? Read-only.

An EXPECTANCY AND CALIBRATION study, not a classifier and not a controller.
Nothing here selects a cutoff, a price band or a time band.

The opportunity set is built from the TAPE, not from any strategy: every
10 seconds of every eligible window is one observation, so the sample does
not inherit CF_MATCH's action selection. At each instant both economic
outcomes are priced at their executable buy price -- UP at the ask, DOWN at
`1 - bid` -- the cheaper one is identified, and the settlement outcome
answers whether it paid.

The quantity of interest is realized value, not accuracy:

    per share   : 1{side wins} - price - taker fee
    per dollar  : that, divided by price

The statistical hazard is stated up front: settlement is ONE binary draw per
window, so 2,500 samples inside 28 windows carry roughly 28 independent
observations. Every interval here is a WINDOW-level bootstrap, never a
sample-level one.
"""
from __future__ import annotations
import bisect, json, os, random, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402
from src import pmus_fees as FEES                               # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)
ONE = D(1)
GRID_S = 10


def main() -> int:
    by = DCA.load(DCA.TAPE)
    elig = {}
    for slug, w in by.items():
        ok, oc, _ = DCA.eligible(slug, w)
        if ok:
            elig[slug] = (w, oc)
    rows = []
    for slug in sorted(elig):
        w, oc = elig[slug]
        books, trades = w["book"], w["trade"]
        bt = [b["t"] for b in books]
        tt = [t["t"] for t in trades]
        start = DCA.window_start(slug)
        lead = None
        last_flip_t = None
        # leadership history on the 1s grid, for "recently lost leadership"
        # a one-sided book serves a null quote; those instants are skipped
        # rather than imputed, and the count is reported.
        for s in range(0, 900):
            j = bisect.bisect_left(bt, start + s) - 1
            while j >= 0 and (books[j]["bid"] is None
                              or books[j]["ask"] is None):
                j -= 1
            if j < 0:
                continue
            m = (D(books[j]["bid"]) + D(books[j]["ask"])) / 2
            cur = "UP" if m > D("0.5") else ("DOWN" if m < D("0.5") else lead)
            if lead is not None and cur is not None and cur != lead:
                last_flip_t = start + s
            if cur is not None:
                lead = cur
            if s % GRID_S:
                continue
            b = books[j]
            bid, ask = D(b["bid"]), D(b["ask"])
            px = {"UP": ask, "DOWN": ONE - bid}
            cheap = "UP" if px["UP"] <= px["DOWN"] else "DOWN"
            p = px[cheap]
            if not (D("0.01") <= p <= D("0.99")):
                continue
            # recent movement of THIS outcome and recent volatility
            def mid_at(ts):
                k = bisect.bisect_left(bt, ts) - 1
                while k >= 0 and (books[k]["bid"] is None
                                  or books[k]["ask"] is None):
                    k -= 1
                if k < 0:
                    return None
                mm = (D(books[k]["bid"]) + D(books[k]["ask"])) / 2
                return mm if cheap == "UP" else ONE - mm
            m_now = mid_at(start + s)
            m30 = mid_at(start + s - 30)
            path = [mid_at(start + s - k) for k in range(0, 60, 5)]
            path = [x for x in path if x is not None]
            vol = (statistics.pstdev([float(x) for x in path])
                   if len(path) > 2 else 0.0)
            i0 = bisect.bisect_right(tt, start + s - 10)
            i1 = bisect.bisect_right(tt, start + s)
            fq = aq = D(0)
            for tr in trades[i0:i1]:
                q = D(tr["qty"]); dr = tr.get("dir")
                if dr == "buy":
                    fq, aq = ((fq + q, aq) if cheap == "UP" else (fq, aq + q))
                elif dr == "sell":
                    fq, aq = ((fq, aq + q) if cheap == "UP" else (fq + q, aq))
            tot = fq + aq
            fee = FEES.taker_fee(p, ONE)
            rows.append({
                "slug": slug, "s": s, "side": cheap,
                "price": float(p), "fee": float(fee),
                "won": 1 if cheap == oc else 0,
                "is_underdog": 1 if p < D("0.5") else 0,
                "tte_s": 900 - s,
                "move_30s": float(m_now - m30) if m30 is not None else 0.0,
                "vol_60s": vol,
                "flow_imb_10s": float((fq - aq) / tot) if tot > 0 else 0.0,
                "prints_10s": i1 - i0,
                "spread": float(ask - bid),
                "recently_lost_leadership": 1 if (
                    last_flip_t is not None and cheap != lead
                    and (start + s) - last_flip_t <= 60) else 0,
                "s_since_flip": (float((start + s) - last_flip_t)
                                 if last_flip_t is not None else 900.0),
            })
    json.dump(rows, open("/tmp/claude-0/-home-user-kalshi-arb-bot/"
                         "671883cb-e673-5581-ae31-5d97cf169fd1/scratchpad/"
                         "cheapside.json", "w"))
    print(json.dumps({"observations": len(rows),
                      "windows": len({r["slug"] for r in rows}),
                      "cheap_side_won": sum(r["won"] for r in rows)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
