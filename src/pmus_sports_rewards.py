"""Liquidity-program discovery + qualification for the two-sided sports bot.

THEORETICAL. Nothing here places, moves or cancels an order, holds a
credential, or talks to any host but the public gateway the caller's
``get_json`` reaches. Program rewards are NEVER counted in P&L: a reward is
only real once credited by the venue and source-accounted.

The pure functions below are COPIED (not imported) from
``src/pmus_incentive_recorder.py`` on
``feat/pmus-sports-incentive-recorder-20261113`` so the probe image does not
pull the collector. Scoring per docs.polymarket.us/incentives/liquidity.md:
per side, ``score = DF ** ticks_from_best * size`` for the levels where
cumulative size walked from the best price first reaches Target Size; with
``maxSpread`` BOTH sides must meet Target Size and have their size-adjusted
price within maxSpread of the mid, or neither side scores.

Book orientation: the public book is in YES terms. Our YES bid is a YES-book
bid; our NO bid at ``n`` is economically a YES offer at ``1 - n``.
"""
from __future__ import annotations

import math
import os
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

LABEL = "THEORETICAL_NOT_REALIZED"
SYMBOLS_BATCH = 60
SYMBOLS_CAP_GUARD = 100
MIN_GAP_S = 0.5                     # <= 2 rps on the public gateway
LIVE_HOURS_DEFAULT = 3.0
EPS = 1e-9


# ------------------------------------------------- copied from the recorder

def _num(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value") if v.get("value") is not None else v.get("px")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ts(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_levels(levels: Any) -> list:
    out = []
    for lv in levels or ():
        if not isinstance(lv, Mapping):
            continue
        px = _num(lv.get("px") if "px" in lv else lv.get("price"))
        qty = _num(lv.get("qty") if "qty" in lv else lv.get("size"))
        if px is None or qty is None or qty <= 0 or not (0.0 < px < 1.0):
            continue
        out.append((px, qty))
    return out


def book_sides(book_md: Mapping[str, Any]) -> tuple:
    """(bids, asks) best-first; ``offers`` is authoritative when present."""
    bids = parse_levels(book_md.get("bids"))
    asks = parse_levels(book_md.get("offers") if "offers" in book_md
                        else book_md.get("asks"))
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])
    return bids, asks


def walk_side(levels: list, tick: float, df: float, target: float,
              our_qty: float = 0.0) -> dict:
    if not levels:
        if our_qty <= 0:
            return {"qualifies": False, "reason": "empty_side", "share": 0.0,
                    "others_score": 0.0, "our_score": 0.0,
                    "in_range_size": 0.0, "levels_in_range": 0,
                    "size_adj_px": None, "best": None}
        return {"qualifies": our_qty >= target, "reason": "only_us",
                "share": 1.0 if our_qty >= target else 0.0,
                "others_score": 0.0, "our_score": float(our_qty),
                "in_range_size": float(our_qty), "levels_in_range": 0,
                "size_adj_px": None, "best": None}
    best = levels[0][0]
    cum = float(our_qty)
    others = 0.0
    notional = 0.0
    used = 0
    for i, (px, qty) in enumerate(levels):
        if i > 0 and cum >= target:
            break
        k = int(round(abs(px - best) / tick)) if tick > 0 else 0
        others += (df ** k) * qty
        notional += px * qty
        cum += qty
        used += 1
    notional += best * our_qty
    qualifies = cum >= target - EPS
    our_score = float(our_qty)
    total = our_score + others
    return {"qualifies": qualifies,
            "reason": None if qualifies else "target_not_met",
            "share": (our_score / total) if (qualifies and total > 0) else 0.0,
            "others_score": others, "our_score": our_score,
            "in_range_size": cum, "levels_in_range": used,
            "size_adj_px": (notional / cum) if cum > 0 else None,
            "best": best}


def quote_shares(bids: list, asks: list, tick: float, df: float,
                 target: float, q: float,
                 max_spread: Optional[float] = None) -> dict:
    b = walk_side(bids, tick, df, target, q)
    a = walk_side(asks, tick, df, target, q)
    mid = ((bids[0][0] + asks[0][0]) / 2.0) if (bids and asks) else None
    ms_ok = None
    if max_spread is not None:
        ms_ok = bool(
            mid is not None and b["qualifies"] and a["qualifies"]
            and b["size_adj_px"] is not None and a["size_adj_px"] is not None
            and abs(b["size_adj_px"] - mid) <= max_spread + EPS
            and abs(a["size_adj_px"] - mid) <= max_spread + EPS)
        if not ms_ok:
            b = {**b, "share": 0.0, "reason": b.get("reason") or "max_spread"}
            a = {**a, "share": 0.0, "reason": a.get("reason") or "max_spread"}
    return {"bid": b, "ask": a, "mid": mid, "max_spread_ok": ms_ok}


def _max_spread(tp: Mapping[str, Any]) -> Optional[float]:
    v = _num(tp.get("maxSpread"))
    return v if (v is not None and v > 0) else None


def period_pacing(tp: Mapping[str, Any], event_start: Optional[datetime],
                  now: float, event_live: bool,
                  live_hours: float = LIVE_HOURS_DEFAULT) -> tuple:
    """(paying_now, period_seconds, basis) for one liquidity time period."""
    per = str(tp.get("period") or "")
    st = _ts(tp.get("start"))
    nowd = datetime.fromtimestamp(now, timezone.utc)
    if str(tp.get("status") or "active") != "active":
        return False, None, "status_not_active"
    if st and st > nowd:
        return False, None, "not_started"
    if per in ("daily", "daily_event"):
        return True, 86400.0, "daily_24h"
    if per == "live":
        live = event_live or (event_start is not None and nowd >= event_start)
        return live, live_hours * 3600.0, "live_assumed_%gh" % live_hours
    if per == "day_of":
        if event_start is None:
            return False, None, "day_of_no_event_start"
        ok = event_start - timedelta(hours=6) <= nowd < event_start
        return ok, 6 * 3600.0, "day_of_6h"
    if per == "early":
        if event_start is None or st is None:
            return False, None, "early_no_bounds"
        end = event_start - timedelta(hours=6)
        return (nowd < end), max(3600.0, (end - st).total_seconds()), \
            "early_start_to_event_minus_6h"
    return False, None, "unknown_period:%s" % per


def fetch_programs(get_json: Callable, slugs: Iterable[str],
                   batch: int = SYMBOLS_BATCH) -> tuple:
    """{slug: program_row} via repeated ``symbols=`` params (the endpoint's
    pagination loops, so it is never paged). Returns (found, diag)."""
    found: dict = {}
    diag = {"requests": 0, "errors": [], "split": 0}
    slugs = sorted({str(s) for s in slugs if s})

    def query(chunk: list) -> None:
        diag["requests"] += 1
        d = get_json("/v1/incentives?" + "&".join(
            "symbols=" + urllib.parse.quote(s, safe="") for s in chunk))
        progs = (d or {}).get("programs") if isinstance(d, Mapping) else None
        progs = progs if isinstance(progs, list) else []
        if len(progs) >= SYMBOLS_CAP_GUARD and len(chunk) > 1:
            diag["split"] += 1
            h = len(chunk) // 2
            query(chunk[:h])
            query(chunk[h:])
            return
        for p in progs:
            if isinstance(p, Mapping) and p.get("marketSlug"):
                found[str(p["marketSlug"])] = dict(p)

    for i in range(0, len(slugs), batch):
        try:
            query(slugs[i:i + batch])
        except Exception as exc:                              # noqa: BLE001
            diag["errors"].append("%s:%s" % (type(exc).__name__,
                                              str(exc)[:80]))
    return found, diag


def build_program_map(found: Mapping[str, Mapping[str, Any]],
                      event_of: Optional[Mapping[str, str]] = None) -> dict:
    """{slug: {event, instrumentState, eventStartTime, periods:[...]}} for
    liquidity periods only (same shape as the recorder's)."""
    event_of = event_of or {}
    by_prog: dict = defaultdict(set)
    by_prog_ev: dict = defaultdict(set)
    for slug, row in found.items():
        for tp in row.get("timePeriods") or ():
            if not isinstance(tp, Mapping):
                continue
            if tp.get("programType") not in (None, "liquidityProgram"):
                continue
            key = (tp.get("programId"), tp.get("start"))
            by_prog[key].add(slug)
            by_prog_ev[key + (event_of.get(slug, slug),)].add(slug)
    out = {}
    for slug, row in found.items():
        periods = []
        ev = event_of.get(slug, slug)
        for tp in row.get("timePeriods") or ():
            if not isinstance(tp, Mapping):
                continue
            if tp.get("programType") not in (None, "liquidityProgram"):
                continue
            pool = _num(tp.get("rewardPool"))
            df = _num(tp.get("discountFactor"))
            target = _num(tp.get("targetSize"))
            if pool is None or df is None or target is None:
                continue
            key = (tp.get("programId"), tp.get("start"))
            periods.append({
                "programId": tp.get("programId"), "pool": pool,
                "period": tp.get("period"), "df": df, "target": target,
                "maxSpread": _max_spread(tp),
                "maxSpread_raw": tp.get("maxSpread"),
                "status": tp.get("status"), "start": tp.get("start"),
                "M_event": max(1, len(by_prog_ev[key + (ev,)])),
                "M_program": max(1, len(by_prog[key])),
            })
        if periods:
            out[slug] = {"event": ev,
                         "instrumentState": row.get("instrumentState"),
                         "eventStartTime": row.get("eventStartTime") or None,
                         "periods": periods}
    return out


# ------------------------------------------------- bot-side helpers (new)

class SpacedGet:
    """Minimum spacing between this module's gateway reads (<= 2 rps)."""

    def __init__(self, get: Callable, gap_s: float = MIN_GAP_S,
                 sleep: Callable = time.sleep,
                 clock: Callable = time.monotonic):
        self.get, self.gap_s, self.sleep, self.clock = get, gap_s, sleep, clock
        self.last: Optional[float] = None
        self.n = 0

    def __call__(self, path: str):
        if self.last is not None:
            wait = self.gap_s - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()
        self.n += 1
        return self.get(path)


def paying_period(prog: Mapping[str, Any], now: Optional[float] = None,
                  event_live: bool = True) -> Optional[dict]:
    """The paying liquidity period with the highest pool rate (USD/s), as a
    flat dict, or None. ``event_live`` defaults True: the bot only trades
    markets on the live slate."""
    now = time.time() if now is None else now
    est = _ts(prog.get("eventStartTime"))
    best = None
    for p in prog.get("periods") or ():
        paying, secs, basis = period_pacing(p, est, now, event_live)
        if not paying or not secs:
            continue
        rate = p["pool"] / secs
        if best is None or rate > best["pool_rate_usd_s"]:
            best = {"programId": p["programId"], "pool": p["pool"],
                    "period": p["period"], "df": p["df"],
                    "targetSize": p["target"], "maxSpread": p["maxSpread"],
                    "period_s": secs, "pacing": basis,
                    "pool_rate_usd_s": rate,
                    "M_event": p.get("M_event", 1),
                    "M_program": p.get("M_program", 1)}
    return best


def legs_within_max_spread(yes_px: float, no_px: float, best_bid: float,
                           best_ask: float, max_spread: Optional[float]
                           ) -> bool:
    """Our YES bid and our NO bid (= a YES offer at 1 - no_px) both within
    maxSpread of the observed mid. No maxSpread -> True."""
    if max_spread is None:
        return True
    mid = (best_bid + best_ask) / 2.0
    return (abs(yes_px - mid) <= max_spread + EPS and
            abs((1.0 - no_px) - mid) <= max_spread + EPS)


def estimate(book_md: Mapping[str, Any], period: Mapping[str, Any],
             q: float, tick: float) -> dict:
    """THEORETICAL qualification + share for Q joined at both touches.
    Note: once our orders rest they are already in the public book, so this
    double-counts our size (understates our share slightly)."""
    bids, asks = book_sides(book_md or {})
    s = quote_shares(bids, asks, tick, period["df"], period["targetSize"], q,
                     period["maxSpread"])
    sb, sa = s["bid"]["share"], s["ask"]["share"]
    _scope, _div, factor, fields = pool_reading(period)
    per_market = period["pool_rate_usd_s"] * 0.5 * (sb + sa)
    return {"qualifies": bool(sb > 0 and sa > 0) if period["maxSpread"]
            else bool(sb > 0 or sa > 0),
            "est_share": round(0.5 * (sb + sa), 6),
            "bid_share": round(sb, 6), "ask_share": round(sa, 6),
            "max_spread_ok": s["max_spread_ok"], "mid": s["mid"],
            "est_usd_per_day_per_market_reading": round(
                per_market * 86400.0, 4),
            "est_usd_per_hour": round(per_market * factor * 3600.0, 6),
            "est_usd_per_hour_per_market_reading": round(
                per_market * 3600.0, 6),
            **fields, "label": LABEL}


# ------------------------------------------------- shared pools (2026-11-25)
# OWNER 2026-11-25: "Correct the reward model to account for shared program
# pools. We cannot make capital allocation decisions using potentially
# duplicated reward estimates."
#
# WHAT THE VENUE SAYS. polymarket.us/rewards: "Reward pool -- The total
# amount paid out for a time period, shared across the program's markets --
# never summed per market" and "each program's reward pool is split
# pro-rata by score". docs .../incentives/liquidity.md: a Time Period is
# "a window during the event lifecycle (e.g., pre-event, event day,
# mid-event) with its own reward pool".
#
# WHAT THE MODEL DID. quote_estimate multiplied our share of ONE market's
# score by the whole pool rate, as if every market had a pool of its own.
# For a ladder type (34 spread lines a game on one programme) that counts
# the same dollars once per line.
#
# WHAT THE DATA SUPPORTS (GET /v1/incentives, 9,111 markets, 83 programmes,
# 2026-11-25). A programme row carries: programId (a name such as
# nba_regular_spreads_live_20261118: competition, market type, period,
# date), programType, period (early / day_of / live / daily_event), start,
# an optional end, rewardPool, discountFactor, targetSize, an optional
# maxSpread, status. The market row adds marketSlug, eventStartTime,
# category, subcategory, instrumentProduct. For every programme the pool
# and the parameters are the SAME on every market and every event (0 of 83
# programmes showed two pool values); start/end are the programme's own
# dates (days long, often open-ended), not a game's. There is NO list or
# count of a programme's markets, NO event identifier and NO per-event
# pool in the response; membership is only learnt by asking, slug by slug.
# So the data cannot tell a pool shared by the programme's markets WITHIN
# ONE GAME from a pool shared across ALL the programme's games. The docs'
# "event lifecycle" wording leans to per game; nothing confirms it.
#
# WHAT THIS DOES. our reward = pool rate * our score / (total score across
# every market sharing the pool). The total across markets is not visible
# from one book, so the pool is split EVENLY across the markets that share
# it -- an approximation: the venue splits by score, so a busy line takes
# more than an empty one -- and the sharing reading is a setting:
#   PMUS_REWARD_POOL_SCOPE=market     each market its own pool: the figure
#                                     as it always was (default: nothing
#                                     changes until the owner approves)
#                         =event      the programme's markets in the SAME
#                                     GAME share it: divide by how many of
#                                     them have a paying period now
#                         =programme  ALL the programme's markets paying
#                                     now share it: divide by that count
# An unrecognised value is read as programme (the smallest figure), never
# as market. The count comes from the programme rows the bot holds: it is
# COMPLETE for a game only after every market of that product in the game
# has been looked up (the census the controller runs for games whose full
# market list it has); otherwise it is a LOWER BOUND, so the figure is an
# UPPER bound, and `pool_divisor_complete` says which. For the programme
# reading it is always a lower bound (the venue publishes no member list).
# PMUS_REWARD_POOL_REQUIRE_CENSUS=1 turns an estimate whose game has no
# complete count into zero instead of an upper bound (default 0).
# Every estimate carries pool_scope, pool_divisor, the divided figure and
# est_usd_per_hour_per_market_reading (the undivided one). THEORETICAL.
POOL_SCOPES = ("market", "event", "programme")
POOL_READING = {
    "market": "each market read as having the whole pool to itself (may "
              "count one pool once per market)",
    "event": "pool split EVENLY across the programme's markets paying in "
             "the same game (the venue splits by score, not evenly)",
    "programme": "pool split EVENLY across all the programme's markets "
                 "paying now (the venue splits by score, not evenly)",
}


def pool_scope_from(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    if not v:
        return "market"
    v = {"program": "programme", "game": "event"}.get(v, v)
    return v if v in POOL_SCOPES else "programme"


POOL_SCOPE = pool_scope_from(os.getenv("PMUS_REWARD_POOL_SCOPE"))
POOL_REQUIRE_CENSUS = os.getenv("PMUS_REWARD_POOL_REQUIRE_CENSUS",
                                "0").strip() == "1"
#: games whose markets of one product are looked up per programme read, to
#: complete a count (public GET /v1/incentives, 60 symbols a request)
POOL_CENSUS_PER_ROUND = int(os.getenv("PMUS_REWARD_POOL_CENSUS_PER_ROUND",
                                      "1"))


def pool_counts(records: Mapping[str, Mapping[str, Any]], now: float,
                live_of: Callable[[str], bool],
                event_of: Callable[[str], str]) -> dict:
    """{"programme": {programId: n}, "event": {(programId, event): n}}:
    how many of the held programme rows (build_program_map shape) have a
    paying period of each programme right now."""
    prog: dict = defaultdict(int)
    ev: dict = defaultdict(int)
    for slug, row in records.items():
        if not row:
            continue
        est = _ts(row.get("eventStartTime"))
        live = bool(live_of(slug))
        seen = set()
        for p in row.get("periods") or ():
            pid = p.get("programId")
            if pid in seen:
                continue
            paying, secs, _basis = period_pacing(p, est, now, live)
            if paying and secs:
                seen.add(pid)
                prog[pid] += 1
                ev[(pid, event_of(slug))] += 1
    return {"programme": dict(prog), "event": dict(ev)}


def pool_divisor(counts: Mapping[str, Any], slug: str, program_id: Any,
                 scope: str, event_of: Callable[[str], str]) -> int:
    """How many markets share the pool under `scope` (never below 1: the
    market itself)."""
    if scope == "event":
        n = (counts.get("event") or {}).get((program_id, event_of(slug)), 0)
    elif scope == "programme":
        n = (counts.get("programme") or {}).get(program_id, 0)
    else:
        n = 1
    return max(1, int(n))


def scoped_period(period: Mapping[str, Any], scope: str,
                  divisor: Optional[int], complete: bool) -> dict:
    """The paying-period dict with the sharing reading attached; every
    estimate made from it is divided. divisor None = not known: zero."""
    return dict(period, pool_scope=scope, pool_divisor=divisor,
                pool_divisor_complete=bool(complete))


def pool_reading(period: Mapping[str, Any]) -> tuple:
    """(scope, divisor | None, factor, fields) for one period dict. A
    period with nothing attached is the per-market reading, divisor 1."""
    scope = str(period.get("pool_scope") or "market")
    if "pool_divisor" not in period:
        div: Optional[float] = 1
    else:
        try:
            div = int(period["pool_divisor"])
            if div < 1:
                div = None
        except (TypeError, ValueError):
            div = None
    factor = 0.0 if div is None else 1.0 / div
    fields = {"pool_scope": scope, "pool_divisor": div,
              "pool_divisor_complete": (scope == "market") or bool(
                  period.get("pool_divisor_complete")),
              "pool_reading": POOL_READING.get(scope, scope) if div
              else "number of markets sharing the pool not known: no "
                   "estimate"}
    return scope, div, factor, fields


def pool_rate(period: Mapping[str, Any]) -> float:
    """Pool rate (USD/s) per market under the period's sharing reading: the
    headline rate when nothing is attached."""
    return float(period.get("pool_rate_usd_s") or 0.0) * pool_reading(
        period)[2]


def pool_scope_fit(credited_rows: Iterable[Mapping[str, Any]],
                   measured: Mapping[str, Mapping[str, Any]],
                   min_payout_usd: float = 1.0) -> dict:
    """Which sharing reading fits what the venue actually paid. NOT wired
    to anything: a tool for the day credited rows exist.

    credited_rows: the venue's reward rows for ONE day that it marks as
      paid -- {"marketSlug", "reward" or "amount"} (the shape
      /v1/incentives/earnings returned to src/pmus_incentive_audit; those
      rows carry no programId, so the market slug is the join key). The
      caller filters by status: a pending or skipped row is not a payment.
    measured: {slug: {"share_seconds": sum over the day's seconds of our
      modelled share of THAT market's score (0..1 each second),
      "pool_rate_usd_s", "event_divisor", "programme_divisor"}} from the
      bot's own record.

    For each reading, predicted_m = pool_rate * share_seconds / divisor
    (divisor 1 / event / programme). Returns per reading the predicted and
    credited totals over the markets present in both, their ratio, and the
    mean absolute log error per market; `best` is the reading with the
    smallest error (that figure plus the fraction of compared markets the
    reading said were payable and the venue did not pay), or None with a
    reason when it cannot be judged.

    LIMITS, all of which bias the answer: the even split (the venue splits
    by score); our share is modelled from sampled public books; a market
    measured but not credited is counted against a reading only when that
    reading predicted at least the minimum payout; whether the minimum
    applies per market, per programme or per day is not known."""
    paid: dict = defaultdict(float)
    for r in credited_rows or ():
        if not isinstance(r, Mapping) or not r.get("marketSlug"):
            continue
        amt = _num(r.get("reward"))
        if amt is None:
            amt = _num(r.get("amount"))
        if amt is None:
            continue
        paid[str(r["marketSlug"])] += amt
    div_key = {"market": None, "event": "event_divisor",
               "programme": "programme_divisor"}
    out: dict = {"markets_measured": len(measured or {}),
                 "markets_credited": len(paid), "readings": {},
                 "label": LABEL}
    for scope, key in div_key.items():
        pred_tot = paid_tot = err = 0.0
        n = missing = 0
        for slug, m in (measured or {}).items():
            ss, rate = _num(m.get("share_seconds")), _num(
                m.get("pool_rate_usd_s"))
            d = 1.0 if key is None else _num(m.get(key))
            if ss is None or rate is None or not d or d < 1 or ss <= 0 \
                    or rate <= 0:
                continue
            pred = rate * ss / d
            got = paid.get(slug)
            if got is None or got <= 0:
                if pred >= min_payout_usd:
                    missing += 1
                continue
            n += 1
            pred_tot += pred
            paid_tot += got
            err += abs(math.log(pred / got))
        out["readings"][scope] = {
            "markets_compared": n, "predicted_usd": round(pred_tot, 6),
            "credited_usd": round(paid_tot, 6),
            "credited_over_predicted": round(paid_tot / pred_tot, 6)
            if pred_tot > 0 else None,
            "mean_abs_log_error": round(err / n, 6) if n else None,
            "predicted_payable_but_not_credited": missing}
    judged = {s: r["mean_abs_log_error"] + r[
        "predicted_payable_but_not_credited"] / max(1, r["markets_compared"])
        for s, r in out["readings"].items()
        if r["mean_abs_log_error"] is not None}
    if not judged:
        out.update(best=None, why="no market both measured and credited")
    else:
        lo = min(judged.values())
        best = [s for s in POOL_SCOPES if s in judged
                and judged[s] <= lo + 1e-9]
        if len(best) > 1:
            out.update(best=None, why="readings not distinguishable on "
                       "this data (equal divisors): %s" % ",".join(best))
        else:
            out.update(best=best[0], why="smallest error")
    return out


# ------------------------------------------------- reward-first (2026-11-15)
# Venue rule (docs.polymarket.us/incentives/liquidity.md): every second a
# random snapshot; per side, walk from the best price outward, LEVEL BY
# LEVEL, until cumulative raw size reaches targetSize; every order in the
# walked levels scores discountFactor ** ticks_from_best * size, beyond = 0;
# each side normalized to 1.0 per snapshot; payout proportional to score
# share. Rewards < $1.00 are not paid. THEORETICAL estimates only.

def side_score(levels: list, our_px: Optional[float], our_qty: float,
               tick: float, df: float, target: float, is_bid: bool,
               own_resting: float = 0.0) -> dict:
    """Our score share on ONE side with ``our_qty`` resting at ``our_px``.

    ``levels`` best-first [(px, qty)] as the public book shows them.
    ``own_resting`` is the size of our order already in that public book at
    ``our_px``; it is removed first so our order is counted exactly once."""
    if tick <= 0:
        raise ValueError("tick must be > 0")
    lv: dict = {}
    for px, q in levels:
        k = int(round(px / tick))
        lv[k] = lv.get(k, 0.0) + float(q)
    ok = None if our_px is None else int(round(our_px / tick))
    if ok is not None and own_resting > 0:
        lv[ok] = max(0.0, lv.get(ok, 0.0) - float(own_resting))
    if ok is not None and our_qty > 0:
        lv[ok] = lv.get(ok, 0.0) + float(our_qty)
    keys = sorted((k for k, q in lv.items() if q > EPS), reverse=is_bid)
    out = {"inside": False, "qualifies": False, "share": 0.0,
           "our_score": 0.0, "total_score": 0.0, "ticks_from_best": None,
           "in_range_size": 0.0, "levels_in_range": 0, "size_adj_px": None,
           "best": (keys[0] * tick) if keys else None}
    if not keys:
        return out
    best = keys[0]
    cum = total = ours = notional = 0.0
    used = 0
    for i, k in enumerate(keys):
        if i > 0 and cum >= target - EPS:
            break
        t = abs(k - best)
        q = lv[k]
        total += (df ** t) * q
        notional += k * tick * q
        cum += q
        used += 1
        if ok is not None and k == ok and our_qty > 0:
            out["inside"] = True
            out["ticks_from_best"] = t
            ours = (df ** t) * our_qty
    qual = cum >= target - EPS
    out.update(qualifies=qual, our_score=ours, total_score=total,
               share=(ours / total) if (qual and total > 0) else 0.0,
               in_range_size=cum, levels_in_range=used,
               size_adj_px=(notional / cum) if cum > 0 else None)
    return out


def quote_estimate(book_md: Mapping[str, Any], period: Mapping[str, Any],
                   yes_px: Optional[float], no_px: Optional[float],
                   qty: float, tick: float, own_yes: float = 0.0,
                   own_no: float = 0.0) -> dict:
    """THEORETICAL share / reward rate for our YES bid at ``yes_px`` and our
    NO bid at ``no_px`` (= a YES offer at ``1 - no_px``), ``qty`` each; a
    leg passed as None is not quoted. ``own_*`` = our size already in the
    public book at those prices (removed so it is not double-counted).

    share = 0.5 * (bid_side_share + ask_side_share) (each side is normalized
    to 1.0 per snapshot, so the two sides are equal halves of the pool);
    with maxSpread BOTH sides must qualify and have their size-adjusted
    price within maxSpread of the mid, or neither side scores.
    rate_usd_s = pool_rate_usd_s * share / pool_divisor (the number of
    markets sharing the pool under the period's `pool_scope`; a period with
    nothing attached is the per-market reading, divisor 1, as before);
    est_usd_per_hour / est_usd_per_day are that divided rate, and
    est_usd_per_hour_per_market_reading is the undivided one."""
    bids, asks = book_sides(book_md or {})
    df, target = float(period["df"]), float(period["targetSize"])
    b = side_score(bids, yes_px, qty if yes_px is not None else 0.0, tick,
                   df, target, True, own_yes)
    a = side_score(asks, None if no_px is None else 1.0 - no_px,
                   qty if no_px is not None else 0.0, tick, df, target,
                   False, own_no)
    mid = ((b["best"] + a["best"]) / 2.0) if (b["best"] is not None and
                                              a["best"] is not None) else None
    ms = period.get("maxSpread")
    ms_ok = None
    if ms is not None:
        ms_ok = bool(mid is not None and b["qualifies"] and a["qualifies"]
                     and b["size_adj_px"] is not None
                     and a["size_adj_px"] is not None
                     and abs(b["size_adj_px"] - mid) <= ms + EPS
                     and abs(a["size_adj_px"] - mid) <= ms + EPS)
    sb = b["share"] if ms_ok in (None, True) else 0.0
    sa = a["share"] if ms_ok in (None, True) else 0.0
    share = 0.5 * (sb + sa)
    _scope, _div, factor, fields = pool_reading(period)
    rate_market = float(period.get("pool_rate_usd_s") or 0.0) * share
    rate = rate_market * factor
    legs = [x for x, px in ((b, yes_px), (a, no_px)) if px is not None]
    return {"share": round(share, 9), "bid_share": round(sb, 9),
            "ask_share": round(sa, 9),
            "inside_walk": bool(legs) and all(x["inside"] for x in legs),
            "bid_inside": b["inside"], "ask_inside": a["inside"],
            "bid_qualifies": b["qualifies"], "ask_qualifies": a["qualifies"],
            "bid_ticks_from_best": b["ticks_from_best"],
            "ask_ticks_from_best": a["ticks_from_best"],
            "max_spread_ok": ms_ok, "mid": mid,
            "rate_usd_s": rate, "est_usd_per_hour": round(rate * 3600.0, 6),
            "est_usd_per_day": round(rate * 86400.0, 6),
            "est_usd_per_hour_per_market_reading": round(
                rate_market * 3600.0, 6),
            **fields, "label": LABEL}
