"""Contemporaneous executable flow on PMUS, taped per market.

OWNER P0 (#167): "What we really care about is contemporaneous executable
activity: recent trades, two-sided flow, spread, depth, volatility, and how
frequently resting orders on both outcomes actually transact. A $500K-volume
market that's currently dead may be worse than a $50K market doing constant
two-sided trading."

The completion forensics (`src/l5_completion_forensics.py`) could not test
that hypothesis, for a concrete reason: the R5F campaign never recorded a
trade print. Its order rows carry spread, touch extrema and poll counts, and
nothing that says whether the market was transacting while we sat in it. This
collector records the missing quantity.

READ-ONLY BY CONSTRUCTION
-------------------------
The public gateway needs no credentials, so this module holds none, imports
no order transport, and issues GET only. It cannot place, cancel or price an
order, and it is not on the live path.

WHY sharesTraded, AND WHAT IT IS NOT
-------------------------------------
PMUS serves no public trade-print endpoint -- `/trades`, `/history`,
`/ohlc`, `/candles` and `/price-history` all 404. What `/v1/markets/{slug}/bbo`
does serve is `sharesTraded`, a CUMULATIVE per-market share count, alongside
`lastTradePx`, `bestBid`/`bestAsk` and `bidDepth`/`askDepth`.

Cumulative volume is exactly the headline number the owner ruled out. The
measurement here is its FIRST DIFFERENCE between consecutive polls, which is
contemporaneous: shares that traded in the last interval, not since listing.
A market with 500,000 lifetime shares and no delta scores zero.

Three limits are stated rather than glossed:

  * a poll interval sees NET volume, so k prints inside one interval read as
    one observation of their sum. `prints_seen` is a lower bound on trades
    and is named that way, never `trades`.
  * `sharesTraded` does not say which side lifted. Two-sidedness is inferred
    from where `lastTradePx` sits between the recorded bid and ask, which is
    an INFERENCE and is labelled one; it is not the venue telling us the
    aggressor.
  * a market absent from a poll (delisted, resolving, request failed) breaks
    the difference. Gaps are recorded and excluded from rates rather than
    treated as zero flow, because "no data" and "no trading" are the two
    things this study most needs to keep apart.

WHAT THIS IS FOR
----------------
It produces the per-market feature set the flow hypothesis needs, so a
forward high-flow cohort can be ranked and then compared against the R5F
baseline. It does not itself select markets, propose trades, or rank
anything for execution.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

GATEWAY = "https://gateway.polymarket.us"
CA_ENV = "PMUS_CA_BUNDLE"
MARKETS_PATH = "/v1/markets?active=true&closed=false&limit=200"
BBO_PATH = "/v1/markets/%s/bbo"
DEFAULT_POLL_S = 30.0
DEFAULT_MAX_MARKETS = 60
BUDGET_RPS = 3.0
OVERHEAD_RPS = 0.5

READ_ONLY = True
PLACES_ORDERS = False
REQUIRES_CREDENTIALS = False

LABELS = {
    "measured": "first difference of the venue's own cumulative sharesTraded",
    "lower_bound": "prints_seen counts intervals with a positive delta, not trades; "
                   "several prints inside one interval read as one",
    "inference": "two_sided is inferred from lastTradePx against the recorded "
                 "bid/ask, not reported by the venue",
    "gap": "an interval with a missing or unreadable sample is excluded from "
           "rates, never counted as zero flow",
}


# ------------------------------ transport ------------------------------

def _ssl_context() -> ssl.SSLContext:
    ca = os.getenv(CA_ENV, "").strip()
    return ssl.create_default_context(cafile=ca) if ca \
        else ssl.create_default_context()


def http_get_json(path: str, timeout: float = 15.0) -> Any:
    """GET only. There is deliberately no post/put/delete helper in this module."""
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-flow-collector"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------------ parsing ------------------------------

def _money(x: Any) -> Optional[D]:
    """The gateway wraps prices as {"value": "0.4200", "currency": "USD"}."""
    if isinstance(x, Mapping):
        x = x.get("value")
    if x in (None, ""):
        return None
    try:
        return D(str(x))
    except ArithmeticError:
        return None


def _num(x: Any) -> Optional[D]:
    if x in (None, ""):
        return None
    try:
        return D(str(x))
    except ArithmeticError:
        return None


def parse_bbo(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """One flow sample from a /bbo response. Empty strings mean 'never traded'."""
    md = payload.get("marketData") or {}
    return {
        "slug": md.get("marketSlug"),
        "state": md.get("state"),
        "shares_traded": _num(md.get("sharesTraded")),
        "open_interest": _num(md.get("openInterest")),
        "last_trade_px": _money(md.get("lastTradePx")),
        "best_bid": _money(md.get("bestBid")),
        "best_ask": _money(md.get("bestAsk")),
        "bid_depth": _num(md.get("bidDepth")),
        "ask_depth": _num(md.get("askDepth")),
    }


def spread_of(sample: Mapping[str, Any]) -> Optional[D]:
    b, a = sample.get("best_bid"), sample.get("best_ask")
    return (a - b) if (b is not None and a is not None) else None


def two_sided_hint(sample: Mapping[str, Any]) -> Optional[str]:
    """Which side the last print sat on. INFERENCE -- the venue does not say.

    A print at the bid is a seller hitting a resting buyer; at the ask, a
    buyer lifting a resting seller. A market where both appear over a window
    is one where resting orders on BOTH outcomes transact, which is the
    property the owner actually asked about.
    """
    px, b, a = sample.get("last_trade_px"), sample.get("best_bid"), sample.get("best_ask")
    if px is None or b is None or a is None or a < b:
        return None
    if px <= b:
        return "at_bid"
    if px >= a:
        return "at_ask"
    return "inside"


# ------------------------------ flow arithmetic ------------------------------

def delta(prev: Optional[Mapping[str, Any]],
          cur: Mapping[str, Any]) -> Dict[str, Any]:
    """Contemporaneous flow between two samples of one market.

    Returns a `gap` row when the difference cannot be taken. A gap is not
    zero flow: it is an interval we did not observe, and rates must drop it.
    """
    if prev is None:
        return {"gap": True, "why": "first_sample"}
    p, c = prev.get("shares_traded"), cur.get("shares_traded")
    if p is None or c is None:
        return {"gap": True, "why": "shares_traded_unreadable"}
    if c < p:
        # cumulative counters do not decrease; the venue reset or re-keyed it
        return {"gap": True, "why": "counter_went_backwards"}
    dt = float(cur.get("t", 0.0)) - float(prev.get("t", 0.0))
    if dt <= 0:
        return {"gap": True, "why": "non_positive_interval"}
    d = c - p
    return {
        "gap": False,
        "shares": d,
        "interval_s": round(dt, 3),
        "printed": d > 0,
        "shares_per_min": float(d) * 60.0 / dt,
        "side_hint": two_sided_hint(cur),
    }


def summarise(deltas: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-market flow features over a window of consecutive deltas."""
    good = [d for d in deltas if not d.get("gap")]
    gaps = len(deltas) - len(good)
    if not good:
        return {"intervals": 0, "gaps": gaps, "observed_s": 0.0,
                "prints_seen": 0, "print_rate": None, "shares": D(0),
                "shares_per_min": None, "two_sided": False, "sides": {}}
    obs = sum(d["interval_s"] for d in good)
    shares = sum((d["shares"] for d in good), D(0))
    printed = [d for d in good if d["printed"]]
    sides: Dict[str, int] = {}
    for d in printed:
        h = d.get("side_hint")
        if h:
            sides[h] = sides.get(h, 0) + 1
    return {
        "intervals": len(good),
        "gaps": gaps,
        "observed_s": round(obs, 2),
        "prints_seen": len(printed),
        "print_rate": round(len(printed) / len(good), 4),
        "shares": shares,
        "shares_per_min": round(float(shares) * 60.0 / obs, 4) if obs else None,
        "two_sided": ("at_bid" in sides and "at_ask" in sides),
        "sides": sides,
    }


def rank(summaries: Mapping[str, Mapping[str, Any]],
         min_intervals: int = 4) -> List[Dict[str, Any]]:
    """Order markets by contemporaneous activity, not lifetime volume.

    Markets observed too briefly to have a rate are returned separately rather
    than ranked at zero, for the same reason a gap is not zero flow.
    """
    ranked, thin = [], []
    for slug, s in summaries.items():
        row = {"slug": slug, **{k: s[k] for k in
                                ("intervals", "prints_seen", "print_rate",
                                 "shares_per_min", "two_sided")}}
        (ranked if s["intervals"] >= min_intervals else thin).append(row)
    ranked.sort(key=lambda r: (r["two_sided"], r["shares_per_min"] or 0.0,
                               r["print_rate"] or 0.0), reverse=True)
    return ranked + [dict(t, under_observed=True) for t in thin]


def deciles(ranked: Sequence[Mapping[str, Any]], key: str = "shares_per_min",
            n: int = 10) -> List[Dict[str, Any]]:
    """Equal-count buckets over an observed feature. Under-observed rows are
    excluded: they have no value on the feature, which is not the same as a
    low one."""
    vals = [r for r in ranked
            if not r.get("under_observed") and r.get(key) is not None]
    vals.sort(key=lambda r: r[key])
    if not vals:
        return []
    out = []
    size = max(1, len(vals) // n)
    for i in range(0, len(vals), size):
        chunk = vals[i:i + size]
        out.append({
            "decile": len(out) + 1,
            "markets": len(chunk),
            "min": chunk[0][key],
            "max": chunk[-1][key],
            "two_sided_share": round(
                sum(1 for c in chunk if c["two_sided"]) / len(chunk), 4),
            "slugs": [c["slug"] for c in chunk],
        })
    return out[:n]


# ------------------------------ collection ------------------------------

def plan_cycle(n_markets: int, poll_s: float, max_markets: int,
               budget_rps: float = BUDGET_RPS,
               overhead_rps: float = OVERHEAD_RPS) -> Dict[str, Any]:
    """Cap the slate and stretch the interval so one bbo call per market per
    cycle stays inside the request budget. We rate-limited ourselves once
    already this campaign; the plan is explicit so it cannot happen quietly."""
    kept = max(0, min(n_markets, max_markets))
    eff = float(poll_s)
    if kept:
        usable = max(budget_rps - overhead_rps, 0.5)
        eff = max(eff, kept / usable)
    return {"kept_markets": kept, "effective_poll_s": round(eff, 3),
            "dropped_markets": max(0, n_markets - kept)}


def _end_epoch(m: Mapping[str, Any]) -> Optional[float]:
    raw = m.get("endDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def discover(limit: int, now: Optional[float] = None,
             getter=None) -> List[str]:
    """The live slate first; otherwise the nearest-resolution open markets.

    A single listing page is NOT the near-term slate. Measured 2026-09-09: one
    `limit=200` page returned 200 rows whose EARLIEST resolution was a month
    out -- the in-play events were buried behind it. That is the same trap
    `pmus_sports_collector` documented (the global listing buried live rows
    beyond its page cap), so this reuses that module's two proven readers
    rather than reimplementing paging here:

      * `fetch_live_events` + `live_event_targets` -- the venue's own
        authoritative live set, admitted on the venue's `live` flag alone
        (a scheduled-but-started event without it is never admitted, so
        futures cannot leak in) and expanded to its embedded markets;
      * `enumerate_open_markets` -- bounded limit/offset paging over the
        whole open universe, which is then ranked by time-to-resolution.

    Both are GET-only and both degrade to what they gathered on failure.
    When the venue has no live events the live set is legitimately empty; the
    fallback then supplies the nearest-resolution markets, and an empty result
    means the venue has no open markets, not that discovery broke.
    """
    from src.pmus_sports_collector import (enumerate_open_markets,
                                           fetch_live_events,
                                           live_event_targets)
    getter = getter or http_get_json
    now = time.time() if now is None else now
    slugs: List[str] = []
    try:
        events, _meta = fetch_live_events(getter)
    except Exception:
        events = []
    for t in live_event_targets(events):
        if t["slug"] not in slugs:
            slugs.append(t["slug"])
    if len(slugs) >= limit:
        return slugs[:limit]
    rows = []
    try:
        openm, _pages = enumerate_open_markets(getter)
    except Exception:
        openm = []
    for m in openm:
        slug, end = m.get("slug"), _end_epoch(m)
        if slug and slug not in slugs and end is not None and end > now:
            rows.append((end, slug))
    rows.sort()
    slugs += [slug for _, slug in rows]
    return slugs[:limit]


def poll_once(slugs: Sequence[str], now: float,
              getter=http_get_json) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for slug in slugs:
        try:
            s = parse_bbo(getter(BBO_PATH % slug))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                TimeoutError, OSError) as e:
            out[slug] = {"slug": slug, "t": now, "unreadable": type(e).__name__}
            continue
        s["t"] = now
        out[slug] = s
    return out


def run(cycles: int, poll_s: float, max_markets: int, out_path: Optional[str],
        sleep=time.sleep, getter=http_get_json, clock=time.time) -> Dict[str, Any]:
    slugs = discover(max_markets, getter=getter)
    plan = plan_cycle(len(slugs), poll_s, max_markets)
    prev: Dict[str, Dict[str, Any]] = {}
    dels: Dict[str, List[Dict[str, Any]]] = {s: [] for s in slugs}
    fh = open(out_path, "a", encoding="utf-8") if out_path else None
    try:
        for c in range(cycles):
            now = clock()
            cur = poll_once(slugs, now, getter=getter)
            for slug, s in cur.items():
                if s.get("unreadable"):
                    dels.setdefault(slug, []).append(
                        {"gap": True, "why": s["unreadable"]})
                    continue
                dels.setdefault(slug, []).append(delta(prev.get(slug), s))
                prev[slug] = s
            if fh:
                fh.write(json.dumps({"t": now, "cycle": c,
                                     "samples": cur}, default=str) + "\n")
                fh.flush()
            summ = {s: summarise(v) for s, v in dels.items()}
            top = rank(summ)[:5]
            print(json.dumps({"cycle": c, "markets": len(slugs),
                              "top_by_flow": top}, default=str), flush=True)
            if c + 1 < cycles:
                sleep(plan["effective_poll_s"])
    finally:
        if fh:
            fh.close()
    summ = {s: summarise(v) for s, v in dels.items()}
    ranked = rank(summ)
    return {"plan": plan, "markets": len(slugs), "per_market": summ,
            "ranked": ranked, "deciles": deciles(ranked),
            "read_only": READ_ONLY, "places_orders": PLACES_ORDERS,
            "labels": LABELS}


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    ap.add_argument("--max-markets", type=int, default=DEFAULT_MAX_MARKETS)
    ap.add_argument("--tape", default=None, help="jsonl path for raw samples")
    ap.add_argument("--out", default=None, help="json path for the summary")
    a = ap.parse_args(argv)
    rep = run(a.cycles, a.poll_s, a.max_markets, a.tape)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({"markets": rep["markets"], "plan": rep["plan"],
                      "deciles": len(rep["deciles"])}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
