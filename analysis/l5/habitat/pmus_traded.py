"""Habitat score for the markets the PROBE ACTUALLY TRADED, from its own
order rows -- so habitat and state-visitation can be measured on the SAME
market instead of on two barely-overlapping sets.

WHY THIS EXISTS. The recorder XCHECK habitat baseline covers markets the
RECORDER seats; the order corpus covers markets the PROBE selects at
concurrency 1. Their intersection is 2 markets of 6 -- far too small to relate
habitat to state visitation. Every order row, however, carries the
`recorder_book` the probe priced against, so the probe's own observations can
be turned into a habitat window for every market it traded.

THE COST, STATED UP FRONT: the probe samples the book once per ORDER, so the
cadence is coarse (single-digit observations per minute against the XCHECK
stream's tens). Rate features are biased DOWN by that and the move-magnitude
features UP; `sampling_limited` is reported per window. These scores are NOT
interchangeable with the XCHECK baseline and are never pooled with it.

No settlement outcome, no future fill and no realized P&L reaches the score:
the inputs are the bid/ask the probe saw BEFORE it acted.
"""
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src import l5_habitat_score as hs                      # noqa: E402
from src import l5_submit_freshness as SF                   # noqa: E402

HERE = os.path.dirname(__file__)
REF = os.path.join(HERE, "l5_reference.json")
DUMPS = ("/root/.claude/projects/-home-user-kalshi-arb-bot/*/tool-results/"
         "mcp-Railway-get-logs-*.txt")
TICK = 0.01
MIN_OBS = 4


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def load_orders():
    seen = {}
    for p in sorted(glob.glob(DUMPS)):
        try:
            doc = json.load(open(p))
        except Exception:                                    # noqa: BLE001
            continue
        for e in doc.get("deploy") or []:
            m = re.search(r"PMUS_L5_CAMPAIGN (\{.*\})", e.get("message") or "",
                          re.S)
            if not m:
                continue
            try:
                j = json.loads(m.group(1))
            except Exception:                                # noqa: BLE001
                continue
            if j.get("kind") != "session_end":
                continue
            for od in j.get("orders") or []:
                oid = od.get("order_id")
                if oid and oid not in seen:
                    seen[oid] = dict(od, _slug=j.get("slug"))
    return list(seen.values())


def observations(orders):
    """One BBO-mid observation per order, from the book the probe priced on."""
    out = collections.defaultdict(list)
    for o in orders:
        rb = o.get("recorder_book")
        if not isinstance(rb, dict):
            continue
        bid, ask = _f(rb.get("yes_bid")), _f(rb.get("yes_ask"))
        t = rb.get("status_utc")
        if bid is None or ask is None or not t:
            continue
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
        except Exception:                                    # noqa: BLE001
            continue
        out[o["_slug"]].append({"t": ts, "price": (bid + ask) / 2.0,
                                "two_sided": bid > 0 and ask > 0,
                                "spread_ticks": round((ask - bid) / TICK, 2),
                                "tte_s": None, "depth_at_touch": None})
    # the same book snapshot is reused across orders; dedupe by timestamp or
    # the series counts one observation many times
    for slug, v in out.items():
        ded, seen_t = [], set()
        for o in sorted(v, key=lambda x: x["t"]):
            if o["t"] in seen_t:
                continue
            seen_t.add(o["t"])
            ded.append(o)
        out[slug] = ded
    return out


def visitation(orders):
    """State-visitation metrics per market, from the order stream itself."""
    by = collections.defaultdict(lambda: {"orders": [], "sides": [],
                                          "prices": []})
    for o in sorted(orders, key=lambda x: str(x.get("_slug"))):
        d = by[o["_slug"]]
        d["orders"].append(o)
        if o.get("side"):
            d["sides"].append(o["side"])
        p = _f(o.get("px"))
        if p is not None:
            d["prices"].append(p)
    out = {}
    for slug, d in by.items():
        os_ = d["orders"]
        rels = collections.Counter(SF.relation_of(o) for o in os_)
        fills = [o for o in os_ if (_f(o.get("filled_qty")) or 0) > 0]
        comp = [o for o in os_ if SF.relation_of(o) == "complete"]
        comp_f = [o for o in comp if (_f(o.get("filled_qty")) or 0) > 0]
        sw = sum(1 for a, b in zip(d["sides"], d["sides"][1:]) if a != b)
        out[slug] = {
            "orders": len(os_), "fills": len(fills),
            "open": rels["open"], "extend": rels["extend"],
            "complete": rels["complete"],
            "complete_fills": len(comp_f),
            "complete_conversion": (round(len(comp_f) / len(comp), 4)
                                    if comp else None),
            "side_switches": sw,
            "switch_rate": (round(sw / (len(d["sides"]) - 1), 4)
                            if len(d["sides"]) > 1 else None),
            "distinct_prices": len(set(d["prices"])),
            "price_span": (round(max(d["prices"]) - min(d["prices"]), 4)
                           if d["prices"] else None),
        }
    return out


def main():
    ref = json.load(open(REF))
    orders = load_orders()
    obs = observations(orders)
    vis = visitation(orders)
    rows = []
    for slug, v in obs.items():
        if len(v) < MIN_OBS:
            continue
        feat = hs.window_features(v)
        if not feat.get("usable") or feat["span_s"] <= 0:
            continue
        cand = {k: x for k, x in feat.items() if not k.startswith("_")}
        cand["observations_per_min"] = round(
            feat["observations"] / (feat["span_s"] / 60.0), 4)
        s = hs.score(cand, ref)
        rows.append({"market": slug, "score": s["habitat_score_on_observed"],
                     "coverage": s["coverage"],
                     "disc": s["coverage_discounted_score"],
                     "obs": feat["observations"], "span_s": feat["span_s"],
                     "obs_per_min": cand["observations_per_min"],
                     "sampling_limited": s.get("sampling_limited_features"),
                     **(vis.get(slug) or {})})
    rows.sort(key=lambda r: -(r["score"] or 0))
    print(f"orders {len(orders)}   markets with a usable window "
          f"{len(rows)} of {len(obs)}\n")
    hdr = (f"{'market':42}{'score':>7}{'obs':>5}{'o/min':>7}{'ord':>4}"
           f"{'cmpl':>5}{'cfill':>6}{'conv':>7}{'switch':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        conv = r.get("complete_conversion")
        print(f"{r['market'][:42]:42s}{(r['score'] if r['score'] is not None else 0):>7.4f}"
              f"{r['obs']:>5}{r['obs_per_min']:>7.1f}{r.get('orders',0):>4}"
              f"{r.get('complete',0):>5}{r.get('complete_fills',0):>6}"
              f"{('%.0f%%' % (conv*100)) if conv is not None else 'n/a':>7}"
              f"{(('%.2f' % r['switch_rate']) if r.get('switch_rate') is not None else 'n/a'):>7}")
    scored = [r for r in rows if r["score"] is not None
              and r.get("complete_conversion") is not None
              and r.get("complete", 0) >= 3]
    print(f"\nmarkets with a score AND >=3 COMPLETE attempts: {len(scored)}")
    if len(scored) >= 3:
        import statistics
        xs = [r["score"] for r in scored]
        ys = [r["complete_conversion"] for r in scored]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        dx = sum((a - mx) ** 2 for a in xs) ** .5
        dy = sum((b - my) ** 2 for b in ys) ** .5
        r = num / (dx * dy) if dx and dy else None
        print(f"  Pearson r(habitat, COMPLETE conversion) = "
              f"{('%.4f' % r) if r is not None else 'undefined'}  n={len(scored)}")
        print("  NOT causal, NOT a gate, and n is small. Reported as an "
              "association only.")
    else:
        print("  INSUFFICIENT for a correlation. Reported as UNKNOWN.")
    json.dump(rows, open(os.path.join(HERE, "pmus_traded_habitat.json"), "w"),
              indent=1, default=str)


if __name__ == "__main__":
    main()
