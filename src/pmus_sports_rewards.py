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
    return {"qualifies": bool(sb > 0 and sa > 0) if period["maxSpread"]
            else bool(sb > 0 or sa > 0),
            "est_share": round(0.5 * (sb + sa), 6),
            "bid_share": round(sb, 6), "ask_share": round(sa, 6),
            "max_spread_ok": s["max_spread_ok"], "mid": s["mid"],
            "est_usd_per_day_per_market_reading": round(
                period["pool_rate_usd_s"] * 0.5 * (sb + sa) * 86400.0, 4),
            "label": LABEL}


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
    return ladder_side_score(levels, [] if our_px is None else
                             [(our_px, our_qty)], tick, df, target, is_bid,
                             [] if our_px is None else [(our_px, own_resting)])


def ladder_side_score(levels: list, proposed: list, tick: float, df: float,
                      target: float, is_bid: bool, own_resting: list) -> dict:
    """Score a complete proposed side, replacing known visible own levels."""
    if tick <= 0:
        raise ValueError("tick must be > 0")
    lv: dict = {}
    ours_by_level: dict = {}
    for px, q in levels:
        k = int(round(px / tick))
        lv[k] = lv.get(k, 0.0) + float(q)
    for px, q in own_resting:
        k = int(round(px / tick))
        lv[k] = max(0.0, lv.get(k, 0.0) - max(0.0, float(q)))
    for px, q in proposed:
        k = int(round(px / tick))
        q = max(0.0, float(q))
        ours_by_level[k] = ours_by_level.get(k, 0.0) + q
        lv[k] = lv.get(k, 0.0) + q
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
        if ours_by_level.get(k, 0.0) > 0:
            out["inside"] = True
            if out["ticks_from_best"] is None:
                out["ticks_from_best"] = t
            ours += (df ** t) * ours_by_level[k]
    qual = cum >= target - EPS
    out.update(qualifies=qual, our_score=ours, total_score=total,
               share=(ours / total) if (qual and total > 0) else 0.0,
               in_range_size=cum, levels_in_range=used,
               size_adj_px=(notional / cum) if cum > 0 else None)
    return out


def quote_estimate(book_md: Mapping[str, Any], period: Mapping[str, Any],
                   yes_px: Optional[float], no_px: Optional[float],
                   qty: float, tick: float, own_yes: float = 0.0,
                   own_no: float = 0.0, *,
                   yes_qty: Optional[float] = None,
                   no_qty: Optional[float] = None) -> dict:
    """THEORETICAL share / reward rate for our YES bid at ``yes_px`` and our
    NO bid at ``no_px`` (= a YES offer at ``1 - no_px``), ``qty`` each; a
    leg passed as None is not quoted. ``own_*`` = our size already in the
    public book at those prices (removed so it is not double-counted).

    share = 0.5 * (bid_side_share + ask_side_share) (each side is normalized
    to 1.0 per snapshot, so the two sides are equal halves of the pool);
    with maxSpread BOTH sides must qualify and have their size-adjusted
    price within maxSpread of the mid, or neither side scores.
    rate_usd_s = pool_rate_usd_s * share; est_usd_per_day = rate * 86400."""
    yq = qty if yes_qty is None else yes_qty
    nq = qty if no_qty is None else no_qty
    bids, asks = book_sides(book_md or {})
    df, target = float(period["df"]), float(period["targetSize"])
    b = side_score(bids, yes_px, yq if yes_px is not None else 0.0, tick,
                   df, target, True, own_yes)
    a = side_score(asks, None if no_px is None else 1.0 - no_px,
                   nq if no_px is not None else 0.0, tick, df, target,
                   False, own_no)
    return _combine_side_scores(b, a, period, yes_px is not None,
                                no_px is not None)


def ladder_quote_estimate(book_md: Mapping[str, Any], period: Mapping[str, Any],
                          yes_levels: list, no_levels: list, tick: float,
                          own_yes: list, own_no: list) -> dict:
    """Estimate complete replacement ladders. NO prices are contract prices.
    Own levels must be confirmed visible by the caller."""
    bids, asks = book_sides(book_md or {})
    df, target = float(period["df"]), float(period["targetSize"])
    b = ladder_side_score(bids, yes_levels, tick, df, target, True, own_yes)
    a = ladder_side_score(asks, [(1.0-p, q) for p, q in no_levels],
                          tick, df, target, False,
                          [(1.0-p, q) for p, q in own_no])
    return _combine_side_scores(b, a, period, bool(yes_levels), bool(no_levels))


def _combine_side_scores(b: dict, a: dict, period: Mapping[str, Any],
                         has_yes: bool, has_no: bool) -> dict:
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
    rate = float(period.get("pool_rate_usd_s") or 0.0) * share
    legs = [x for x, present in ((b, has_yes), (a, has_no)) if present]
    return {"share": round(share, 9), "bid_share": round(sb, 9),
            "ask_share": round(sa, 9),
            "inside_walk": bool(legs) and all(x["inside"] for x in legs),
            "bid_inside": b["inside"], "ask_inside": a["inside"],
            "bid_qualifies": b["qualifies"], "ask_qualifies": a["qualifies"],
            "bid_ticks_from_best": b["ticks_from_best"],
            "ask_ticks_from_best": a["ticks_from_best"],
            "max_spread_ok": ms_ok, "mid": mid,
            "rate_usd_s": rate, "est_usd_per_hour": round(rate * 3600.0, 6),
            "est_usd_per_day": round(rate * 86400.0, 6), "label": LABEL}


def marginal_ladder_reward(book_md: Mapping[str, Any],
                           period: Mapping[str, Any],
                           yes_levels: list, no_levels: list, tick: float,
                           own_yes: list, own_no: list,
                           side: str, price: float, quantity: float) -> dict:
    """Incremental theoretical reward of adding one order, same snapshot.
    Does not authorize execution or estimate fill losses."""
    if side not in ("YES", "NO") or not (0 < price < 1) or quantity <= 0:
        raise ValueError("invalid candidate")
    before = ladder_quote_estimate(book_md, period, yes_levels, no_levels,
                                   tick, own_yes, own_no)
    y, n = list(yes_levels), list(no_levels)
    (y if side == "YES" else n).append((price, quantity))
    after = ladder_quote_estimate(book_md, period, y, n, tick, own_yes, own_no)
    delta = after["rate_usd_s"] - before["rate_usd_s"]
    return {"before": before, "after": after,
            "incremental_reward_usd_per_hour": delta * 3600,
            "incremental_notional_usd": price * quantity,
            "label": LABEL}
