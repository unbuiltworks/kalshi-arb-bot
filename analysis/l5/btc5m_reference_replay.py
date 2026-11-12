#!/usr/bin/env python3
"""BTC 5-minute controller replayed with an INDEPENDENT probability: the
venue's own settlement index against the market's strike.

Research only: reads public data, places nothing, holds no credential.
Every number printed is a REPLAY number with HYPOTHETICAL fills. None of
it is realized profit, and the sample is one evening.

WHY. `btc5m_controller_replay.py` passes no probability, so the controller
falls back to the book mid -- which cannot disagree with the price it would
pay. The integration review asked for the spot-versus-strike test. The
series these markets settle on is public (`l5_btc_reference`): the venue
publishes the 60-second-average BRTI once a second, and the strike is the
market's own `priceToBeat`. So the probability used here is the one a live
runner would have, from `l5_btc_reference.probability_up`, unchanged.

NO LOOK-AHEAD. At a decision made `t` seconds into a window the estimate
sees only index buckets that had ENDED at least LAG_S before that instant.
The strike is the recorded `to_beat`. The outcome is used only to settle.

WHAT IS MEASURED
  1. calibration of that probability on these windows, at fixed times;
  2. the controller with that probability, on the same step and the same
     two fill rules as the book-mid replay, across the same allowance grid.

    python3 analysis/l5/btc5m_reference_replay.py QUOTES.jsonl INDEX.json
    python3 analysis/l5/btc5m_reference_replay.py --fetch-index FROM_EPOCH OUT.json
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import json
import os
import sys
import urllib.request
from decimal import Decimal as D

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import l5_btc5m_controller as C          # noqa: E402
from src import l5_btc_reference as REF           # noqa: E402

LABEL = "REPLAY, hypothetical fills, not realized"
WINDOW_S = 300.0
#: a bucket is usable only this long after it ended (publication + poll)
LAG_S = 1.0
DECIDE_EVERY_S = 5.0
COVER_FROM_S, COVER_TO_S = 30.0, 240.0
ADVERSE_GRID = (D("0.06"), D("0.03"), D("0.0"))
MIN_EDGE = D("0.01")
PREFIXES = ("BTC5M_QUOTE ", "BTC5M_SETTLED ")


def fetch_index(frm: int, out: str) -> int:
    url = ("%s/v1/asset-prices/history?assets=crypto:btc"
           "&bucket=ASSET_PRICE_BUCKET_1S&from=%d" % (REF.GATEWAY, int(frm)))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:           # noqa: S310
        doc = json.loads(r.read().decode("utf-8"))
    rows = [[int(c["timestamp"]), c["close"]]
            for c in doc["series"][0]["candles"]]
    with open(out, "w") as fh:
        json.dump({"source": url, "label": "public venue index, 1s buckets, "
                   "each the 60-second average", "rows": rows}, fh)
    return len(rows)


def slug_start(slug: str) -> float:
    d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz")
    return d.replace(tzinfo=dt.timezone.utc).timestamp()


def load_quotes(path):
    quotes, winners, strikes = collections.defaultdict(list), {}, {}
    for line in open(path, encoding="utf-8", errors="replace"):
        for p in PREFIXES:
            if p in line:
                line = line.split(p, 1)[1]
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        slug = rec.get("slug")
        if not slug:
            continue
        if "settled" in rec:
            try:
                s = json.loads(rec["settled"]) if isinstance(
                    rec["settled"], str) else rec["settled"]
                winners[slug] = C.UP if str(s[0]) == "1" else C.DOWN
            except (ValueError, TypeError, IndexError):
                pass
            continue
        if rec.get("closed"):
            continue
        try:
            t = float(rec["t"])
        except (KeyError, TypeError, ValueError):
            continue
        def dec(x):
            try:
                return None if x is None else D(str(x))
            except Exception:                                  # noqa: BLE001
                return None
        quotes[slug].append({"t": t, "bid": dec(rec.get("bid")),
                             "ask": dec(rec.get("ask"))})
        if rec.get("to_beat") is not None:
            strikes[slug] = float(rec["to_beat"])
    out = []
    for slug in sorted(quotes):
        s = sorted(quotes[slug], key=lambda x: x["t"])
        if slug in winners and slug in strikes \
                and s[0]["t"] <= COVER_FROM_S and s[-1]["t"] >= COVER_TO_S:
            out.append((slug, s, winners[slug], strikes[slug]))
    return out


class Index:
    def __init__(self, rows):
        self.ts = [int(r[0]) for r in rows]
        self.val = [float(r[1]) for r in rows]

    def upto(self, now: float):
        """Buckets that had ended LAG_S before `now`, last KEEP_S of them."""
        hi = bisect.bisect_right(self.ts, now - 1.0 - LAG_S)
        lo = bisect.bisect_left(self.ts, now - REF.KEEP_S)
        return list(zip(self.ts[lo:hi], self.val[lo:hi]))

    def at(self, bucket: int):
        i = bisect.bisect_left(self.ts, bucket)
        return self.val[i] if i < len(self.ts) and self.ts[i] == bucket else None


def estimate(index, ws, strike, t):
    now = ws + t
    return REF.probability_up(series=index.upto(now), strike=strike,
                              window_end=ws + WINDOW_S, now=now)


def simulate(samples, winner, params, fill_rule, p_of):
    inv = {C.UP: {"qty": D("0"), "cost": D("0")},
           C.DOWN: {"qty": D("0"), "cost": D("0")}}
    resting, n_id, last_t = [], 0, None
    st = {"actions": collections.Counter(), "holds": collections.Counter(),
          "placed": 0, "fills": 0, "fill_qty": D("0"), "fill_cost": D("0"),
          "no_estimate": 0, "decisions": 0}
    for s in samples:
        t, bid, ask = s["t"], s["bid"], s["ask"]
        ask_of = {C.UP: ask, C.DOWN: (D("1") - bid) if bid is not None else None}
        still = []
        for o in resting:
            a = ask_of[o["side"]]
            hit = a is not None and (a < o["price"] if fill_rule == "through"
                                     else a <= o["price"])
            if hit:
                inv[o["side"]]["qty"] += o["qty"]
                inv[o["side"]]["cost"] += o["price"] * o["qty"]
                st["fills"] += 1
                st["fill_qty"] += o["qty"]
                st["fill_cost"] += o["price"] * o["qty"]
            else:
                still.append(o)
        resting = still
        if last_t is not None and t - last_t < DECIDE_EVERY_S:
            continue
        last_t = t
        est = p_of(t)
        st["decisions"] += 1
        p_up = est.get("p_up") if est.get("ok") else None
        if p_up is None:
            st["no_estimate"] += 1
        d = C.decide(
            book={"bid": bid, "ask": ask, "age_s": 0.0}, t_in_window_s=t,
            inventory={k: dict(v) for k, v in inv.items()},
            resting=[{"id": o["id"], "side": o["side"], "price": o["price"],
                      "qty": o["qty"], "age_s": max(0.0, t - o["placed_t"])}
                     for o in resting],
            params=params, p_up=p_up,
            p_source=(REF.VENUE_INDEX if p_up is not None else None))
        st["actions"][d["action"]] += 1
        if d["action"] == C.HOLD_OR_SKIP:
            st["holds"][d["why"]] += 1
        for o in d["orders"]:
            if o["op"] == "CANCEL":
                resting = [r for r in resting if r["id"] != o["id"]]
            elif o["op"] == "PLACE" and o.get("post_only") \
                    and o.get("intent", "BUY") == "BUY":
                n_id += 1
                resting.append({"id": "o%d" % n_id, "side": o["side"],
                                "price": o["price"], "qty": o["qty"],
                                "placed_t": t})
                st["placed"] += 1
    cost = inv[C.UP]["cost"] + inv[C.DOWN]["cost"]
    st["pnl"] = inv[winner]["qty"] - cost
    st["unmatched"] = abs(inv[C.UP]["qty"] - inv[C.DOWN]["qty"])
    return st


def counts(c):
    return "  ".join("%s=%d" % kv for kv in sorted(
        c.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def main(argv):
    if len(argv) == 3 and argv[0] == "--fetch-index":
        print("index rows:", fetch_index(int(argv[1]), argv[2]))
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    os.environ[C.CONTROLLER_ENV] = "ON"      # replay only: nothing can place
    windows = load_quotes(argv[0])
    index = Index(json.load(open(argv[1]))["rows"])
    print("%s. windows used: %d. Probability: l5_btc_reference."
          "probability_up on the venue's public index, buckets ended >= %gs "
          "before each decision." % (LABEL, len(windows), LAG_S))

    # 0. the recorded strike is the index at the window start
    off = []
    for slug, _s, _w, strike in windows:
        v = index.at(int(slug_start(slug)) - 1)
        if v is not None:
            off.append(abs(v - strike))
    if off:
        print("strike check: recorded to_beat vs index bucket ending at the "
              "window start, %d windows: max |diff| $%.2f"
              % (len(off), max(off)))

    # 1. calibration
    print("--- calibration of the probability (no fills involved) ---")
    for t in (30.0, 120.0, 210.0, 270.0):
        rows = []
        for slug, _s, winner, strike in windows:
            e = estimate(index, slug_start(slug), strike, t)
            if e.get("ok"):
                rows.append((float(e["p_up"]), 1.0 if winner == C.UP else 0.0))
        if not rows:
            print("  t=%3.0fs: no estimates" % t)
            continue
        brier = sum((p - y) ** 2 for p, y in rows) / len(rows)
        fav = [(max(p, 1 - p), (y if p >= 0.5 else 1 - y)) for p, y in rows]
        conf = [f for f in fav if f[0] >= 0.65]
        print("  t=%3.0fs: n=%d  Brier %.3f (0.250 = coin)  mean stated "
              "P(favorite) %.3f  favorite won %d/%d (%.0f%%)  | stated >= "
              "0.65: n=%d mean %.3f won %s"
              % (t, len(rows), brier, sum(f[0] for f in fav) / len(fav),
                 sum(f[1] for f in fav), len(fav),
                 100.0 * sum(f[1] for f in fav) / len(fav), len(conf),
                 (sum(f[0] for f in conf) / len(conf)) if conf else float("nan"),
                 ("%d/%d" % (sum(f[1] for f in conf), len(conf))) if conf
                 else "-"))

    # 2. the controller with that probability
    for adverse in ADVERSE_GRID:
        for rule in ("through", "touch"):
            params = C.Params(adverse_selection_per_share=adverse,
                              min_edge_per_share=MIN_EDGE)
            runs = []
            for slug, samples, winner, strike in windows:
                ws = slug_start(slug)
                runs.append(simulate(
                    samples, winner, params, rule,
                    lambda t, ws=ws, strike=strike: estimate(
                        index, ws, strike, t)))
            actions, holds = collections.Counter(), collections.Counter()
            for r in runs:
                actions.update(r["actions"])
                holds.update(r["holds"])
            pnls = [r["pnl"] for r in runs]
            n = len(pnls)
            total = sum(pnls, D("0"))
            mean = float(total) / n if n else 0.0
            sd = (sum((float(p) - mean) ** 2 for p in pnls) / (n - 1)) ** 0.5 \
                if n > 1 else 0.0
            half = 1.96 * sd / (n ** 0.5) if n else 0.0
            print("--- adverse=%s fill=%s [%s] ---" % (adverse, rule, LABEL))
            print("  decisions %d (no estimate %d)  %s"
                  % (sum(r["decisions"] for r in runs),
                     sum(r["no_estimate"] for r in runs), counts(actions)))
            print("  hold reasons: %s" % counts(holds))
            print("  placed %d  hypothetical fills %d (qty %s, cost $%s)  "
                  "windows with a fill %d  unmatched at settlement %s"
                  % (sum(r["placed"] for r in runs),
                     sum(r["fills"] for r in runs),
                     sum((r["fill_qty"] for r in runs), D("0")),
                     sum((r["fill_cost"] for r in runs), D("0")).quantize(
                         D("0.0001")),
                     sum(1 for r in runs if r["fills"]),
                     sum((r["unmatched"] for r in runs), D("0"))))
            print("  replay P&L (not realized): total $%s  mean $%.4f/window "
                  "(95%% CI %.4f..%.4f)  ahead %d/%d"
                  % (total.quantize(D("0.0001")), mean, mean - half,
                     mean + half, sum(1 for p in pnls if p > 0), n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
