"""P1 OPPORTUNITY FUNNEL -- read-only scan of the broad Polymarket universe.

READ-ONLY BY CONSTRUCTION. Public gamma endpoints only, no credentials, no
order path, nothing that can reach the live executor. A test forbids every
order and deploy verb from this file.

VENUE CAVEAT, STATED UP FRONT. Gamma is polymarket.COM. Our executor trades
polymarket US (PMUS). Category structure transfers; a specific market slug
does not. Nothing here is a claim that a .com market is executable by us.

WHAT IT MEASURES AND WHAT IT CANNOT. Gamma exposes, per market: spread,
liquidity, 24h volume, tick size, minimum order size, and the liquidity-reward
parameters rewardsMaxSpread / rewardsMinSize. Those support the structural
half of the owner's metric list.

THE TWO DATE TRAPS, both of which produced wrong numbers in the first run and
are the reason this file carries so much comment:

  (a) `startDate` is the LISTING date -- it equals the event's deploy
      timestamp -- not the start of the trading window. `endDate - startDate`
      therefore measures how long a market has been listed, not how long
      capital is locked. It scored basketball at a 345-DAY median "duration"
      and put 0.0 in every short-duration bucket. Listing age is not horizon;
      this file no longer computes it.

  (c) NOT a date trap but the same class of error: `order=volume24hr` alone
      SYSTEMATICALLY HIDES the short-duration lane. A 5-minute market lives
      five minutes, so its 24h volume is tiny and it never enters the top
      1,200 events -- the first run saw ZERO `*-updown-5m-*` markets and
      reported crypto as a ~5-day-median-horizon category, which is the exact
      opposite of the lane we actually trade and the lane in the owner's
      screenshot. The scan therefore walks TWO orderings and unions them:
      volume24hr descending (the depth side) and endDate ascending FLOORED AT
      NOW via `end_date_min` (the short-horizon side), deduped by event id.
      The floor is not optional: unfloored, endDate-ascending returns the
      OLDEST-ending markets, which are long-dead 5-minute markets still
      flagged closed=false (`eth-updown-5m-1766161800`, from December, with
      acceptingOrders=None and a 0/1 book). 81 of 91 updown markets in that
      walk were stale. Floored, the same walk returns the live lane --
      `eth-updown-5m` at 0.55/0.56 with 2.9 minutes to resolution.
      Ranking on a single volume-ordered walk is not a safe sample.

  (b) `endDate` is a SCHEDULED end, not an actual one. In the top-100 events,
      5,162 of 8,085 open markets are already past their endDate and still
      accepting orders -- an EPL match with endDate 15:30Z keeps trading while
      the match is played. So a past endDate does NOT mean "resolving now",
      and bucketing it as short duration would have inverted the ranking
      toward exactly the sports whose horizon we cannot see. Those markets are
      counted separately as `share_past_scheduled_end` and are EXCLUDED from
      the short-horizon share rather than silently folded into it.

It does NOT expose: first-leg maker fill frequency, complement maker fill
frequency, time-to-complement, adverse movement after first fill, residual
frequency or residual loss severity. Those are EXECUTION metrics and need
either a forward collector on this venue or reconstruction from L5's own
public trade history (P2). Every such field is reported as `needs_execution_data`
rather than guessed, because a category ranked on structure alone is exactly
the error the owner warned against -- "do not rank categories merely by number
of markets".

THE RANKING METRIC IS NOT MARKET COUNT. It is a structural proxy for
EXPECTED REALIZED ECONOMICS = matched-pair profit + directional residual P&L
+ CONFIRMED incentives - fees, and the pieces of it we can see today are:
spread (how cheaply a pair can be assembled), depth/liquidity (whether the
size is executable), duration (capital turnover) and the reward parameters
(the only incentive term the venue states up front). The residual term -- the
one that actually decided both of our live losses -- is NOT measurable here
and the report says so on every row.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "curl/8", "Accept": "application/json"}

# Categories the owner named, plus the fallbacks. Order matters: the first
# matching tag wins, so specific sports beat the generic "Sports".
CATEGORY_TAGS = [
    ("crypto", ("Crypto", "Bitcoin", "Ethereum", "Solana", "crypto prices")),
    ("tennis", ("Tennis", "ATP", "WTA")),
    ("soccer", ("Soccer", "EPL", "Champions League", "La Liga", "Serie A", "MLS")),
    ("football", ("NFL", "CFB", "Football")),
    ("baseball", ("MLB", "Baseball")),
    ("basketball", ("NBA", "CBB", "Basketball", "WNBA")),
    ("hockey", ("NHL", "Hockey")),
    ("esports", ("Esports", "CS2", "League of Legends", "Dota", "Valorant")),
    ("economics", ("Economics", "Fed", "Inflation", "CPI", "Interest Rates")),
    ("politics", ("Politics", "Elections", "Geopolitics", "US Politics")),
    ("weather", ("Weather", "Climate", "Hurricane", "Temperature")),
    ("business", ("Business", "Finance", "Earnings", "Companies", "Tech")),
    ("culture", ("Culture", "Pop Culture", "Awards", "Entertainment", "Music")),
    ("sports_other", ("Sports", "Golf", "MMA", "Boxing", "F1", "Cricket", "Olympics")),
]

# Short-duration buckets, because turnover is the whole capital-efficiency
# argument and a 5-minute market is a different business from a 6-month one.
DURATION_BUCKETS = ((5, "<=5m"), (15, "<=15m"), (60, "<=1h"), (60 * 6, "<=6h"),
                    (60 * 24, "<=1d"), (60 * 24 * 7, "<=1w"),
                    (60 * 24 * 30, "<=1mo"), (10 ** 9, ">1mo"))

NEEDS_EXEC = "needs_execution_data"


def _get(url: str, timeout: float = 25.0, retries: int = 3) -> Any:
    last: Optional[Exception] = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as exc:                       # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"gamma_unreachable:{type(last).__name__}") from last


# Two walks, because neither ordering alone is a representative sample of the
# venue -- see trap (c). Gamma 422s past roughly offset 2000, so each walk is
# depth-capped and the union is what the report is computed on.
def walks(now: datetime) -> tuple[tuple[str, str, dict], ...]:
    """(order, ascending, extra-params). The second walk MUST carry the
    end_date_min floor -- see trap (c)."""
    floor = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (("volume24hr", "false", {}),
            ("endDate", "true", {"end_date_min": floor}))


def _walk(order: str, ascending: str, extra: dict, pages: int, limit: int,
          log=lambda *a: None) -> list[dict]:
    out: list[dict] = []
    for p in range(pages):
        q = urllib.parse.urlencode({"closed": "false", "limit": limit,
                                    "offset": p * limit, "order": order,
                                    "ascending": ascending, **extra})
        try:
            rows = _get(f"{GAMMA}/events?{q}")
        except RuntimeError as exc:            # gamma 422s past its offset cap
            log(f"  {order}: stopped at page {p + 1} ({exc})")
            break
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < limit:
            break
    log(f"  order={order} asc={ascending}{' +floor' if extra else ''}: "
        f"{len(out)} events")
    return out


def fetch_events(pages: int = 30, limit: int = 100,
                 log=lambda *a: None) -> list[dict]:
    """Events, because only EVENTS carry the category tags. Markets hang off
    them, so one walk gets both the taxonomy and the executable detail.

    Unions the two orderings and dedupes by event id, so the short-horizon
    lane is present even though it carries almost no 24h volume."""
    seen: dict[str, dict] = {}
    for order, asc, extra in walks(datetime.now(timezone.utc)):
        for ev in _walk(order, asc, extra, pages, limit, log):
            key = str(ev.get("id") or ev.get("slug") or id(ev))
            seen.setdefault(key, ev)
    log(f"  union: {len(seen)} distinct events")
    return list(seen.values())


def categorize(event: dict) -> str:
    labels = {str(t.get("label") or "") for t in (event.get("tags") or [])
              if isinstance(t, dict)}
    lower = {l.lower() for l in labels}
    for name, wanted in CATEGORY_TAGS:
        if any(w.lower() in lower for w in wanted):
            return name
    return "other"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:                                  # noqa: BLE001
        return None


def _iso(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:                                  # noqa: BLE001
        return None


def horizon_minutes(m: dict, now: datetime) -> tuple[Optional[float], str]:
    """Minutes from NOW to the scheduled end -- the only thing resembling a
    capital-lockup horizon that gamma actually states.

    Returns (minutes, state). State is `pre_end` when the scheduled end is
    still ahead (the minutes are meaningful), `past_scheduled_end` when the
    market is open past it (minutes are NOT meaningful -- see trap (b)), and
    `unknown` when no end date is published."""
    e = _iso(m.get("endDate"))
    if not e:
        return None, "unknown"
    mins = (e - now).total_seconds() / 60.0
    if mins <= 0:
        return None, "past_scheduled_end"
    return mins, "pre_end"


def horizon_bucket(mins: Optional[float], state: str) -> str:
    if state != "pre_end" or mins is None:
        return state
    for cap, name in DURATION_BUCKETS:
        if mins <= cap:
            return name
    return ">1mo"


def market_rows(events: Iterable[dict], now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for ev in events:
        cat = categorize(ev)
        for m in (ev.get("markets") or []):
            if not isinstance(m, dict) or m.get("closed") or not m.get("active"):
                continue
            mins, hstate = horizon_minutes(m, now)
            rows.append({
                "slug": m.get("slug"), "category": cat,
                "event": ev.get("slug"),
                "horizon_min": mins, "horizon_state": hstate,
                "horizon_bucket": horizon_bucket(mins, hstate),
                "spread": _f(m.get("spread")),
                "liquidity": _f(m.get("liquidityNum")),
                "volume24hr": _f(m.get("volume24hr")),
                "volume_total": _f(m.get("volumeNum")),
                "best_bid": _f(m.get("bestBid")), "best_ask": _f(m.get("bestAsk")),
                "last_price": _f(m.get("lastTradePrice")),
                "tick": _f(m.get("orderPriceMinTickSize")),
                "min_size": _f(m.get("orderMinSize")),
                "rewards_max_spread": _f(m.get("rewardsMaxSpread")),
                "rewards_min_size": _f(m.get("rewardsMinSize")),
                "accepting_orders": bool(m.get("acceptingOrders")),
                "order_book": bool(m.get("enableOrderBook")),
                "neg_risk": bool(m.get("negRisk")),
            })
    return rows


def _med(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 6) if xs else None


def _share(xs: list, pred) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(1 for x in xs if pred(x)) / len(xs), 4) if xs else None


def aggregate(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    out: dict[str, Any] = {}
    for cat, rs in by.items():
        # LIVENESS. `acceptingOrders && enableOrderBook` is NOT sufficient:
        # 3,579 of football's 8,486 such markets quote only ONE side. A market
        # with no bid (or no ask) cannot host a two-legged pair at all, so
        # including them is not conservative -- it poisons the spread median
        # (football 0.88 -> 0.35, tennis 0.61 -> 0.11 once excluded). Pair
        # assembly needs both sides, so both sides is the bar. The one-sided
        # count is kept as its own funnel metric rather than discarded.
        listed = [r for r in rs if r["accepting_orders"] and r["order_book"]]
        tradeable = [r for r in listed if r["best_bid"] and r["best_ask"]]
        pre_end = [r for r in tradeable if r["horizon_state"] == "pre_end"]
        spreads = [r["spread"] for r in tradeable]
        out[cat] = {
            "markets": len(rs),
            "listed_with_book": len(listed),
            "tradeable_now": len(tradeable),
            "share_one_sided": _share(
                [bool(r["best_bid"]) == bool(r["best_ask"]) for r in listed],
                lambda both: not both),
            "median_spread": _med(spreads),
            "share_spread_le_0_02": _share(spreads, lambda s: s <= 0.02),
            "share_spread_le_0_01": _share(spreads, lambda s: s <= 0.01),
            "median_liquidity_usd": _med([r["liquidity"] for r in tradeable]),
            "median_volume24hr_usd": _med([r["volume24hr"] for r in tradeable]),
            # HORIZON. Only `pre_end` markets have a meaningful number of
            # minutes left; see trap (b). The medians and the short share are
            # computed over those alone, and `horizon_known` publishes the
            # denominator so a share over a handful of rows is visible as such.
            "horizon_known": len(pre_end),
            "share_past_scheduled_end": _share(
                [r["horizon_state"] for r in tradeable],
                lambda st: st == "past_scheduled_end"),
            "median_horizon_min": _med([r["horizon_min"] for r in pre_end]),
            "horizon_mix": dict(sorted(
                ((k, sum(1 for r in tradeable if r["horizon_bucket"] == k))
                 for k in {r["horizon_bucket"] for r in tradeable}),
                key=lambda kv: -kv[1])),
            "share_short_le_1h": _share(
                [r["horizon_min"] for r in pre_end], lambda d: d <= 60),
            "share_short_le_6h": _share(
                [r["horizon_min"] for r in pre_end], lambda d: d <= 360),
            "median_tick": _med([r["tick"] for r in tradeable]),
            "median_min_size": _med([r["min_size"] for r in tradeable]),
            "rewards_programme_share": _share(
                [r["rewards_max_spread"] for r in tradeable], lambda v: v and v > 0),
            "median_rewards_max_spread": _med(
                [r["rewards_max_spread"] for r in tradeable if r["rewards_max_spread"]]),
            # every execution metric the owner asked for that gamma cannot serve
            "first_leg_maker_fill_freq": NEEDS_EXEC,
            "complement_maker_fill_freq": NEEDS_EXEC,
            "time_to_complement_s": NEEDS_EXEC,
            "effective_pair_cost": NEEDS_EXEC,
            "share_completing_le_1_00": NEEDS_EXEC,
            "share_completing_le_0_99": NEEDS_EXEC,
            "adverse_move_after_first_fill": NEEDS_EXEC,
            "residual_frequency": NEEDS_EXEC,
            "residual_loss_severity": NEEDS_EXEC,
            "two_sided_print_frequency": NEEDS_EXEC,
            "trades_per_minute": NEEDS_EXEC,
            "l5_actual_activity": "see P2 (L5 public activity), not gamma",
        }
    return out


def short_horizon_breakout(rows: list[dict], cap_min: float = 60.0) -> dict:
    """The owner named short-duration cohorts as their own funnel rows
    ("BTC 5m/15m, ETH 5m/15m, other liquid short-duration crypto ... other
    liquid short-duration"), and they behave differently enough from their
    parent category to deserve them: category-wide, crypto's depth median is
    dragged down by the very 5-minute markets that make the lane interesting.

    Restricted to markets that are live, two-sided AND still before their
    scheduled end, so every row here is something that could actually be
    quoted on both legs right now."""
    live = [r for r in rows
            if r["accepting_orders"] and r["order_book"]
            and r["best_bid"] and r["best_ask"]
            and r["horizon_state"] == "pre_end" and r["horizon_min"] <= cap_min]
    by: dict[str, list[dict]] = defaultdict(list)
    for r in live:
        by[r["category"]].append(r)
    out: dict[str, Any] = {"cap_minutes": cap_min, "markets": len(live),
                           "categories": {}}
    for cat, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        series = Counter(re.sub(r"\d{6,}", "#", str(r["slug"])) for r in rs)
        out["categories"][cat] = {
            "markets": len(rs),
            "median_spread": _med([r["spread"] for r in rs]),
            "median_liquidity_usd": _med([r["liquidity"] for r in rs]),
            "median_horizon_min": _med([r["horizon_min"] for r in rs]),
            "top_series": dict(series.most_common(8)),
            "first_leg_maker_fill_freq": NEEDS_EXEC,
            "complement_maker_fill_freq": NEEDS_EXEC,
            "time_to_complement_s": NEEDS_EXEC,
            "residual_frequency": NEEDS_EXEC,
            "residual_loss_severity": NEEDS_EXEC,
        }
    return out


def structural_score(a: dict) -> Optional[float]:
    """A STRUCTURAL proxy only, and deliberately not called an expectancy.

    Tight spread is what makes a sub-par pair assemblable; depth is whether
    our size is executable at all; short duration is capital turnover. It
    carries NO residual term, and the residual is what decided both live
    losses -- so this ranks where to LOOK, never what to trade."""
    s, liq = a.get("median_spread"), a.get("median_liquidity_usd")
    n = a.get("tradeable_now") or 0
    if s is None or liq is None or n < 5:
        return None
    # The turnover term is only admissible when we actually saw horizons. If
    # the category is dominated by markets trading past their scheduled end
    # (in-play sports), a 0.0 here would be a measurement gap masquerading as
    # "slow", so the term is dropped and the weight renormalised instead.
    known = a.get("horizon_known") or 0
    short = a.get("share_short_le_1h")
    tight = max(0.0, 1.0 - min(s, 0.10) / 0.10)
    depth = min(1.0, (liq or 0.0) / 5000.0)
    if short is None or known < 5:
        return round((0.45 * tight + 0.35 * depth) / 0.80, 4)
    return round(0.45 * tight + 0.35 * depth + 0.20 * short, 4)


def _print_report(doc: dict) -> None:
    """Extracted from main() so the column formatting is testable: the first
    run printed raw floats into fixed-width fields and three columns ran
    together into one 24-digit blob (`0.00156073.53408496959.621006` was one
    cell's worth of three)."""

    def col(v: Any, w: int, nd: int = 2) -> str:
        """None-safe fixed-width cell: every numeric cell is rounded to the
        field BEFORE it is padded, which is what the first run skipped."""
        if v is None:
            return "-".rjust(w)
        if isinstance(v, float):
            v = f"{v:,.{nd}f}" if abs(v) >= 1000 else f"{v:.{nd}f}"
        return str(v)[:w - 1].rjust(w)

    hdr = (f"{'category':<14}{'mkts':>7}{'2sided':>7}{'1side':>7}"
           f"{'spread':>8}{'liq$':>10}{'horiz_m':>10}{'known':>7}"
           f"{'<=1h':>7}{'<=6h':>7}{'past_end':>9}{'rwd':>6}{'score':>7}")
    print(hdr)
    print("-" * len(hdr))
    for cat, a in doc["categories"].items():
        print(f"{cat:<14}{col(a['markets'], 7)}{col(a['tradeable_now'], 7)}"
              f"{col(a['share_one_sided'], 7, 2)}"
              f"{col(a['median_spread'], 8, 4)}"
              f"{col(a['median_liquidity_usd'], 10, 0)}"
              f"{col(a['median_horizon_min'], 10, 0)}"
              f"{col(a['horizon_known'], 7)}"
              f"{col(a['share_short_le_1h'], 7, 3)}"
              f"{col(a['share_short_le_6h'], 7, 3)}"
              f"{col(a['share_past_scheduled_end'], 9, 3)}"
              f"{col(a['rewards_programme_share'], 6, 2)}"
              f"{col(a['structural_score'], 7, 3)}")

    sh = doc["short_horizon"]
    print(f"\nSHORT-HORIZON BREAKOUT -- live, TWO-SIDED, <= "
          f"{sh['cap_minutes']:.0f} min to scheduled end "
          f"({sh['markets']} markets).")
    print("The category ranking above INVERTS here: crypto's depth median is")
    print("dragged down venue-wide by the very 5-minute markets that make the")
    print("lane tradeable, so the venue-wide row understates the short lane.")
    sh_hdr = (f"{'category':<14}{'mkts':>7}{'spread':>9}{'liq$':>10}"
              f"{'horiz_m':>9}  top series")
    print(sh_hdr)
    print("-" * len(sh_hdr))
    for cat, a in sh["categories"].items():
        top = ", ".join(list(a["top_series"])[:3]) or "-"
        print(f"{cat:<14}{col(a['markets'], 7)}"
              f"{col(a['median_spread'], 9, 4)}"
              f"{col(a['median_liquidity_usd'], 10, 0)}"
              f"{col(a['median_horizon_min'], 9, 1)}  {top[:58]}")

    print("\nhoriz_m/<=1h/<=6h are over the `known` (pre-scheduled-end) rows ONLY.")
    print("past_end = share still accepting orders past endDate: horizon NOT observable.")
    print("score ranks WHERE TO LOOK. It carries no residual term. Execution")
    print(f"metrics are '{NEEDS_EXEC}' on every row -- see P2/P3.")


def main(argv: list[str]) -> int:
    pages = int(argv[1]) if len(argv) > 1 else 30
    out_path = Path(argv[2]) if len(argv) > 2 else Path(
        "analysis/l5/breadth/universe_scan.json")
    print(f"scanning gamma events, up to {pages} pages ...")
    events = fetch_events(pages=pages, log=print)
    rows = market_rows(events)
    agg = aggregate(rows)
    short = short_horizon_breakout(rows)
    for cat, a in agg.items():
        a["structural_score"] = structural_score(a)
    doc = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": f"{GAMMA}/events?closed=false (public, keyless, read-only)",
        "events_scanned": len(events),
        "open_markets": len(rows),
        "labels": {
            "structural_score": "RANKS WHERE TO LOOK, NOT WHAT TO TRADE. "
                                "0.45*tight_spread + 0.35*depth + 0.20*short_duration. "
                                "Carries NO residual term, and the residual decided "
                                "both live losses.",
            "needs_execution_data": "gamma cannot serve this; it requires a forward "
                                    "collector or L5 public-trade reconstruction (P2/P3).",
        },
        "short_horizon": short,
        "categories": dict(sorted(
            agg.items(),
            key=lambda kv: (kv[1].get("structural_score") is None,
                            -(kv[1].get("structural_score") or 0)))),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}  ({len(rows)} open markets, {len(agg)} categories)")
    _print_report(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
