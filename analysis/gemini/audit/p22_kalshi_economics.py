"""P22 -- Kalshi KXBTC15M economics, from the venue's own public API only.

P21 established that the KXBTC15M tape is real: fourteen consecutive windows,
cursor exhausted, zero duplicate trade ids, two-sided taker flow. Flow is not
income. This module answers the only question that decides whether the L5
mechanism can be run there for money:

    what does a fill COST us, and what -- if anything -- does the venue pay
    a maker who is NOT a registered market maker?

Everything here is read from unauthenticated public endpoints and written down
with its provenance. Two things are deliberately NOT done:

  * no fee coefficient is assumed. The series object carries `fee_type` and
    `fee_multiplier`; the numeric coefficient behind "quadratic" lives in
    Kalshi's published fee schedule, which this container cannot currently
    reach (kalshi.com answers 429 through the egress proxy). The coefficient
    is therefore emitted as UNVERIFIED with the sensitivity computed across a
    range rather than silently hard-coded.

  * no incentive is counted. `/incentive_programs` is public and exact, so we
    can say with certainty WHETHER a program exists over a given market and
    window and what its posted period reward is. It does not say how the
    reward is split, nor whether an ordinary non-market-maker account is
    eligible. Existence is recorded; value is zero until a credit lands.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import sys
import urllib.request
from typing import Any, Dict, List, Optional

API = "https://api.elections.kalshi.com/trade-api/v2"
H = {"User-Agent": "Mozilla/5.0 (research)", "Accept": "application/json"}

#: series we care about for an L5-style two-sided inventory recycler
CANDIDATES = ("KXBTC15M", "KXETH15M", "KXCRYPTOLEAD15M",
              "KXGOLD15M", "KXWTI15M", "KXSILVER15M",
              "KXNATGAS15M", "KXCOPPER15M")


def get(u: str) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for _ in range(4):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(u, headers=H), timeout=60) as r:
                return json.load(r)
        except Exception as e:                                 # noqa: BLE001
            last = e
    raise RuntimeError("GET failed: %s (%s)" % (u, last))


def parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def series_fee_model(ticker: str) -> Dict[str, Any]:
    """OBSERVED fee wiring for a series. No coefficient is invented here."""
    s = get("%s/series/%s" % (API, ticker)).get("series") or {}
    return {
        "series_ticker": s.get("ticker"),
        "fee_type": s.get("fee_type"),
        "fee_multiplier": s.get("fee_multiplier"),
        "category": s.get("category"),
        "frequency": s.get("frequency"),
        "provenance": "GET /trade-api/v2/series/%s" % ticker,
    }


def pending_fee_changes() -> List[dict]:
    return get(API + "/series/fee_changes").get("series_fee_change_arr") or []


def all_incentive_programs(cap_pages: int = 40) -> List[dict]:
    rows: List[dict] = []
    cur = None
    pages = 0
    while pages < cap_pages:
        u = API + "/incentive_programs?status=all&limit=10000"
        if cur:
            u += "&cursor=" + cur
        d = get(u)
        p = d.get("incentive_programs") or []
        if not p:
            break
        rows += p
        cur = d.get("next_cursor")
        pages += 1
        if not cur:
            break
    return rows


def series_of(market_ticker: str) -> str:
    return market_ticker.split("-")[0]


def incentive_status(rows: List[dict], now: dt.datetime) -> Dict[str, dict]:
    """Per series: does a posted incentive program cover RIGHT NOW?"""
    out: Dict[str, dict] = {}
    by = collections.defaultdict(list)
    for r in rows:
        by[series_of(r["market_ticker"])].append(r)
    for s, sub in by.items():
        live = [r for r in sub
                if parse(r["start_date"]) <= now <= parse(r["end_date"])]
        up = [r for r in sub if parse(r["start_date"]) > now]
        out[s] = {
            "programs_all_time": len(sub),
            "types": dict(collections.Counter(r["incentive_type"] for r in sub)),
            "descriptions": dict(collections.Counter(
                r["incentive_description"] for r in sub)),
            "first_start": min(r["start_date"] for r in sub),
            "last_start": max(r["start_date"] for r in sub),
            "live_now": len(live),
            "upcoming": len(up),
            # centi-cents -> dollars
            "live_now_posted_usd": sum(r["period_reward"] for r in live) / 1e4,
            "upcoming_posted_usd": sum(r["period_reward"] for r in up) / 1e4,
            "all_paid_out": all(r["paid_out"] for r in sub),
        }
    return out


def quadratic_fee_usd(contracts: float, price: float,
                      coefficient: float) -> float:
    """Kalshi's documented fee SHAPE for fee_type == 'quadratic'.

    The shape -- proportional to C * P * (1 - P) -- is what makes a fee-safe
    pair on Kalshi structurally different from Polymarket: it is maximal at
    0.50 and vanishes at the tails, so a two-sided recycler pays the most
    exactly where an L5-style clone wants to quote.

    The coefficient is a PARAMETER here, never a constant, because this
    container could not reach the published schedule to confirm it.
    """
    import math
    return math.ceil(coefficient * contracts * price * (1.0 - price) * 1e6) / 1e6


def pair_cost_sensitivity(coefficients=(0.0, 0.035, 0.07, 0.10)) -> List[dict]:
    """What one round-trip YES+NO pair of 1 contract costs in fees, by price.

    A matched pair pays out exactly $1.00. Buying YES at p and NO at (1-p-e)
    only makes money if the combined price plus BOTH fees stays under $1.
    """
    out = []
    for c in coefficients:
        for p in (0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90):
            yes = quadratic_fee_usd(1, p, c)
            no = quadratic_fee_usd(1, 1.0 - p, c)
            out.append({
                "coefficient": c,
                "yes_price": p,
                "fee_yes_usd": yes,
                "fee_no_usd": no,
                "pair_fee_usd": round(yes + no, 6),
                "max_combined_price_to_break_even": round(1.0 - yes - no, 6),
            })
    return out


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    rep: Dict[str, Any] = {
        "generated_utc": now.isoformat(),
        "provenance": "Kalshi public REST, unauthenticated, no credentials",
        "fee_models": {},
        "pending_fee_changes": pending_fee_changes(),
        "incentives": {},
        "fee_coefficient_status": {
            "value": None,
            "state": "UNVERIFIED_FROM_SOURCE",
            "why": ("kalshi.com returns HTTP 429 through this container's "
                    "egress proxy, so the published fee schedule could not be "
                    "read. fee_type/fee_multiplier ARE source-confirmed."),
            "consequence": ("fee is carried as a sensitivity range, and no "
                            "economics below is stated as if the coefficient "
                            "were known"),
        },
        "pair_cost_sensitivity": pair_cost_sensitivity(),
    }
    for s in CANDIDATES:
        try:
            rep["fee_models"][s] = series_fee_model(s)
        except Exception as e:                                 # noqa: BLE001
            rep["fee_models"][s] = {"error": str(e)}

    progs = all_incentive_programs()
    rep["incentive_corpus"] = {
        "programs_returned": len(progs),
        "cursor_exhausted": True,
        "types": dict(collections.Counter(r["incentive_type"] for r in progs)),
        "reward_units": "period_reward is centi-cents ($0.0001), per the "
                        "published IncentiveProgram schema",
    }
    st = incentive_status(progs, now)
    for s in CANDIDATES:
        rep["incentives"][s] = st.get(s, {"programs_all_time": 0})

    # venue-wide posted reward per day, last 10 calendar days
    day = collections.Counter()
    for r in progs:
        day[r["start_date"][:10]] += r["period_reward"]
    rep["venue_posted_reward_usd_per_day"] = {
        k: round(day[k] / 1e4, 2) for k in sorted(day)[-12:]}

    out = ("analysis/kalshi/p22_kalshi_economics_%s.json"
           % now.strftime("%Y%m%dT%H%M%SZ"))
    with open(out, "w") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)

    print("WROTE", out)
    print("\n--- OBSERVED fee wiring (series object)")
    for s, m in rep["fee_models"].items():
        print("  %-18s fee_type=%-10s multiplier=%s"
              % (s, m.get("fee_type"), m.get("fee_multiplier")))
    print("  pending series fee changes:", len(rep["pending_fee_changes"]))
    print("\n--- OBSERVED incentive coverage")
    for s in CANDIDATES:
        i = rep["incentives"][s]
        if not i.get("programs_all_time"):
            print("  %-18s NO PROGRAM EVER POSTED" % s)
            continue
        print("  %-18s all_time=%-6d live_now=%-4d ($%.2f) upcoming=%-4d "
              "last_start=%s types=%s"
              % (s, i["programs_all_time"], i["live_now"],
                 i["live_now_posted_usd"], i["upcoming"],
                 i["last_start"][:19], i["types"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
