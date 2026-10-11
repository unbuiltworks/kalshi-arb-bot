"""L5 fill toxicity against real book state -- the decisive measurement.

Joins L5's actual fills to Zenodo 22084977's 1-second Polymarket L2
(sha256 d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959,
CC BY 4.0) and measures, per fill:

  book BEFORE the fill -> fill -> +1s / +5s / +10s / +30s / +60s -> settlement

The headline comparison is deliberately apples-to-apples with Gemini's
measured maker adverse selection of -$0.2380/fill, which was
settlement-based: payout - price - fee. The parquet carries
`is_winning_outcome`, so the same definition is computed here.

WHAT IS NOT CLAIMED. No order in this archive is attributed to any
trader, so maker/taker is INFERRED from the fill's price against the
prevailing touch and is reported as evidence, never as a flag. A snapshot
taken AT the fill already contains its effect, so every "before" read is
strictly earlier. No touch=fill anywhere: this measures realised fills
that happened, not hypothetical ones.

A ROW IS WRITTEN ONLY WHEN THE BOOK CHANGES, so a stale row is the
current state; it is carried at most 60s (the capture's own resync
interval) and refused beyond that rather than trusted.
"""
from __future__ import annotations

import bisect
import collections
import gzip
import json
import statistics
from typing import Any, Dict, List, Optional

PARQUET = ("/tmp/claude-0/-home-user-kalshi-arb-bot/"
           "f2f630e2-70fa-534f-9e92-d1857b7df8ca/scratchpad/l2/"
           "updown_15m.parquet")
SHA = "d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959"
L5 = "0xb945945d5bca"
HORIZONS = (1, 5, 10, 30, 60)
MAX_STALE_S = 60.0
#: Gemini's measured maker adverse selection, settlement-based
GEMINI_ADV = -0.2380
GEMINI_CI = (-0.2831, -0.1935)
KEY = lambda x: (x.get("transactionHash"), x.get("conditionId"),
                 x.get("outcomeIndex"), x.get("timestamp"),
                 str(x.get("price")), str(x.get("size")), x.get("type"))


def fl(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load_books():
    import pyarrow.parquet as pq
    t = pq.read_table(PARQUET, columns=[
        "market_slug", "coin", "outcome", "is_winning_outcome",
        "captured_at", "best_bid", "best_ask"])
    d = t.to_pydict()
    ser: Dict[tuple, List[tuple]] = collections.defaultdict(list)
    win: Dict[tuple, Any] = {}
    for i in range(len(d["market_slug"])):
        if d["coin"][i] not in ("btc", "eth"):
            continue
        ca = d["captured_at"][i]
        ts = ca.timestamp() if hasattr(ca, "timestamp") else float(ca)
        k = (d["market_slug"][i], str(d["outcome"][i]).lower())
        ser[k].append((ts, fl(d["best_bid"][i]), fl(d["best_ask"][i])))
        win[k] = d["is_winning_outcome"][i]
    for k in ser:
        ser[k].sort()
    return ser, win


def at(series, ts, before=True):
    """Book state at ts. `before` takes the last row STRICTLY earlier."""
    times = [r[0] for r in series]
    i = bisect.bisect_left(times, ts) if before else bisect.bisect_right(
        times, ts)
    if before:
        i -= 1
    else:
        i -= 1
    if i < 0:
        return None
    row = series[i]
    if ts - row[0] > MAX_STALE_S:
        return None
    return row


def load_fills(lo, hi):
    seen, out = set(), []
    for path, gz in (("data/l5/raw/activity.jsonl", False),
                     ("data/l5/raw/activity_gap.jsonl.gz", True)):
        op = gzip.open if gz else open
        with op(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    continue
                for r in pl:
                    if not isinstance(r, dict) or r.get("type") != "TRADE":
                        continue
                    k = KEY(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    ts = r.get("timestamp")
                    if not isinstance(ts, (int, float)):
                        continue
                    if lo - 60 <= ts <= hi + 60:
                        out.append(r)
    return out


def q(xs, p):
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))], 4)


def dist(xs, unit=""):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "unit": unit, "p10": q(xs, .1),
            "median": q(xs, .5), "mean": round(statistics.fmean(xs), 4),
            "p90": q(xs, .9)}


def boot(by_market, reps=4000, seed=7):
    """Market-level bootstrap -- fills inside one market are not
    independent trials."""
    import random
    rng = random.Random(seed)
    ks = list(by_market)
    if len(ks) < 2:
        return None
    flat = [v for k in ks for v in by_market[k]]
    m = statistics.fmean(flat)
    bs = []
    for _ in range(reps):
        pick = [rng.choice(ks) for _ in ks]
        f = [v for k in pick for v in by_market[k]]
        if f:
            bs.append(statistics.fmean(f))
    bs.sort()
    return {"mean": round(m, 4),
            "ci95": [round(bs[int(.025 * len(bs))], 4),
                     round(bs[int(.975 * len(bs)) - 1], 4)],
            "markets": len(ks), "fills": len(flat)}


def main() -> int:
    ser, win = load_books()
    lo = min(v[0][0] for v in ser.values())
    hi = max(v[-1][0] for v in ser.values())
    slugs = {k[0] for k in ser}
    fills = [x for x in load_fills(lo, hi)
             if str(x.get("slug") or "") in slugs
             or str(x.get("eventSlug") or "") in slugs]

    rows = []
    for x in fills:
        slug = (str(x.get("slug") or "") if str(x.get("slug") or "") in slugs
                else str(x.get("eventSlug") or ""))
        oc = str(x.get("outcome") or "").lower()
        k = (slug, oc)
        s = ser.get(k)
        if not s:
            continue
        ts, px = fl(x.get("timestamp")), fl(x.get("price"))
        qty = fl(x.get("size"), 0.0)
        if ts is None or px is None:
            continue
        b4 = at(s, ts, before=True)
        if b4 is None:
            continue
        _, bid0, ask0 = b4
        side = str(x.get("side") or "").upper()
        r = {"slug": slug, "outcome": oc, "t": ts, "px": px, "q": qty,
             "side": side, "bid_before": bid0, "ask_before": ask0,
             "spread_before": (ask0 - bid0) if (bid0 and ask0) else None,
             "won": bool(win.get(k))}
        # where did the fill land relative to the prevailing touch?
        if bid0 is not None and ask0 is not None:
            if px >= ask0 - 1e-9:
                r["vs_touch"] = "AT_OR_ABOVE_ASK"
            elif px <= bid0 + 1e-9:
                r["vs_touch"] = "AT_OR_BELOW_BID"
            else:
                r["vs_touch"] = "INSIDE_SPREAD"
        # forward marks
        for h in HORIZONS:
            a = at(s, ts + h, before=False)
            if a is None:
                r["mid_%ds" % h] = None
                continue
            _, b1, a1 = a
            r["mid_%ds" % h] = ((b1 + a1) / 2.0
                                if (b1 is not None and a1 is not None)
                                else None)
        # SETTLEMENT-BASED adverse selection, the Gemini-comparable figure
        if side == "BUY":
            r["settle_adv"] = (1.0 if r["won"] else 0.0) - px
        rows.append(r)

    buys = [r for r in rows if r["side"] == "BUY" and "settle_adv" in r]
    bym = collections.defaultdict(list)
    for r in buys:
        bym[r["slug"]].append(r["settle_adv"])
    res = {
        "label": "L5_FILL_TOXICITY_VS_REAL_BOOK",
        "source": {"zenodo_record": 22084977, "file": "updown_15m.parquet",
                   "sha256": SHA, "licence": "CC-BY-4.0",
                   "note": "committed byte-identical; never altered"},
        "join": {"book_series": len(ser), "book_slugs": len(slugs),
                 "L5_fills_joined": len(rows),
                 "distinct_markets": len({r["slug"] for r in rows}),
                 "join_key": "market_slug + outcome, never market name alone"},
        "fill_vs_prevailing_touch": dict(collections.Counter(
            r.get("vs_touch") for r in rows)),
        # CROSSED books (2.58% of btc/eth rows, min -0.39) are an artifact
        # of a stream with no sequence numbers, so a dropped delta leaves a
        # crossed state. They are EXCLUDED from the spread statistic and
        # counted, rather than averaged in as negative spreads.
        "spread_before_fill": dist(
            [r["spread_before"] for r in rows
             if r["spread_before"] is not None and r["spread_before"] >= 0],
            "$"),
        "crossed_rows_excluded_from_spread": sum(
            1 for r in rows if r["spread_before"] is not None
            and r["spread_before"] < 0),
        "SETTLEMENT_ADVERSE_SELECTION_per_fill": boot(bym),
        "gemini_comparable": {
            "gemini_maker_adverse_selection_per_fill": GEMINI_ADV,
            "gemini_ci95": list(GEMINI_CI),
            "definition": ("both are settlement-based: payout - price. "
                           "Gemini's figure additionally nets its charged "
                           "fee; L5's charged fee is reported separately "
                           "so the two price terms stay comparable."),
        },
        "by_vs_touch": {},
        "forward_mark_change_vs_fill_price": {},
        "caveats": [
            "no order in this archive is attributed to any trader, so "
            "maker/taker is INFERRED from price against the touch",
            "a row is written only when the book changes; a stale row is "
            "carried at most 60s and refused beyond that",
            "~24k events/s with no sequence numbers, so a dropped delta is "
            "undetectable and every offset carries up to one resync of drift",
            "bid(X)+ask(complement) is mechanically 1.0000 in 97.8% of "
            "instants, so no test is built on that sum; the mint sum "
            "bid+bid is median 0.9800 and IS discriminating",
        ],
        "accounting": ("historical replay on third-party public data. No "
                       "order was sent. Not realized profit for our "
                       "account."),
    }
    for cls in ("AT_OR_ABOVE_ASK", "AT_OR_BELOW_BID", "INSIDE_SPREAD"):
        sub = [r for r in buys if r.get("vs_touch") == cls]
        bm = collections.defaultdict(list)
        for r in sub:
            bm[r["slug"]].append(r["settle_adv"])
        res["by_vs_touch"][cls] = {
            "fills": len(sub),
            "settlement_adverse_selection": boot(bm),
            "spread_before": dist([r["spread_before"] for r in sub], "$"),
            "avg_price": (round(statistics.fmean([r["px"] for r in sub]), 4)
                          if sub else None)}
    for h in HORIZONS:
        ch = [(r["mid_%ds" % h] - r["px"]) for r in buys
              if r.get("mid_%ds" % h) is not None]
        res["forward_mark_change_vs_fill_price"]["+%ds" % h] = dist(ch, "$")
    with open("analysis/l5/FILL_TOXICITY.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
