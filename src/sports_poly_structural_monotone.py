"""Strict same-Polymarket structural sports ladder discovery and scoring.

This module is intentionally mutation-free.  It expands the proven monotone
maker->hedge idea beyond the original MLB prop allowlist, but only for market
families whose payout relation is mechanically provable from Polymarket's own
structured market metadata and slug/question corroboration.

Certified shapes in this first pass:
* totals: same parent event + exact sportsMarketType + exact slug base, with
  questions of the form "... more than X";
* threshold props with gte/gt/over semantics: same parent event + exact
  sportsMarketType + exact slug base, with the numeric line independently
  corroborated by market.line and the question text.

For low < high, YES(low) + NO(high) has a minimum payout of $1.  The module
never treats spreads, moneylines, exact scores, or fuzzy text matches as
monotone.  Unknown shapes fail closed.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

import httpx

from src.sports_kp_poly_relay_live_q1 import D, ONE, TARGET, TAKER_FEE_FLOOR, _ask, _bid, _locked
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

BASE = "https://gateway.polymarket.us"
TICK = D("0.01")

_TOTAL_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<line>\d+(?:pt\d+)?)$", re.I)
_GTE_SUFFIX = re.compile(r"^(?P<base>.+)-gte(?P<line>\d+(?:pt\d+)?)$", re.I)
_GT_SUFFIX = re.compile(r"^(?P<base>.+)-gt(?P<line>\d+(?:pt\d+)?)$", re.I)
_NUMERIC_PROP_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<line>\d+(?:pt\d+)?)$", re.I)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace("pt", "."))
    except (InvalidOperation, ValueError):
        return None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("−", "-").split())


def _question_confirms(question: str, line: Decimal, relation: str) -> bool:
    """Require independent natural-language evidence for the threshold relation."""
    text = _norm(question)
    value = str(line.normalize()) if line == line.to_integral() else format(line.normalize(), "f")
    # Decimal('2').normalize() renders 2; keep a second conventional spelling.
    variants = {value, format(line, "f")}
    if line == line.to_integral():
        variants.add(str(int(line)))
    escaped = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    if relation == "MORE_THAN":
        return bool(re.search(rf"\b(?:more\s+than|over|above)\s+(?:{escaped})(?:\b|\?)", text, re.I))
    if relation == "AT_LEAST":
        return bool(
            re.search(rf"\bat\s+least\s+(?:{escaped})(?:\b|\s)", text, re.I)
            or re.search(rf"(?:^|\s)(?:{escaped})\+", text, re.I)
        )
    return False


def structural_identity(market: Mapping[str, Any]) -> tuple[str, str, str, Decimal] | None:
    """Return (family, parent, base_slug, line) for a mechanically safe ladder row."""
    if market.get("active") is False or market.get("closed") is True or market.get("archived") is True:
        return None
    slug = str(market.get("slug") or "").strip()
    parent = str(market.get("_parent_event_key") or market.get("eventSlug") or market.get("event_slug") or "").strip()
    sports_type = str(market.get("sportsMarketType") or "").strip()
    market_type = str(market.get("marketType") or "").strip().lower()
    question = str(market.get("question") or market.get("title") or "").strip()
    line = _decimal(market.get("line"))
    if not slug or not parent or not sports_type or sports_type.upper() == "UNKNOWN" or line is None or not question:
        return None

    match: re.Match[str] | None = None
    relation = ""
    family = ""
    if market_type == "totals":
        match = _TOTAL_SUFFIX.fullmatch(slug)
        relation = "MORE_THAN"
        family = f"TOTAL::{sports_type}"
    elif market_type == "props":
        match = _GTE_SUFFIX.fullmatch(slug)
        if match is not None:
            relation = "AT_LEAST"
            family = f"GTE_PROP::{sports_type}"
        else:
            match = _GT_SUFFIX.fullmatch(slug)
            if match is not None:
                relation = "MORE_THAN"
                family = f"GT_PROP::{sports_type}"
            else:
                # Some structured props use a raw numeric suffix while the
                # question explicitly says Over X (e.g. team aggregate props).
                maybe = _NUMERIC_PROP_SUFFIX.fullmatch(slug)
                if maybe is not None and _question_confirms(question, line, "MORE_THAN"):
                    match = maybe
                    relation = "MORE_THAN"
                    family = f"OVER_PROP::{sports_type}"
    if match is None or not relation:
        return None

    slug_line = _decimal(match.group("line"))
    if slug_line is None or slug_line != line:
        return None
    if not _question_confirms(question, line, relation):
        return None
    return family, parent, match.group("base"), line


async def discover_structural_pairs(
    *, horizon_hours: int = 72, lookback_hours: int = 6, max_pages: int = 40
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]], dict[str, Any]]:
    """Discover current strict low/high ladders from the live sports event feed."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)
    oldest_allowed = now - timedelta(hours=lookback_hours)
    groups: dict[tuple[str, str, str], dict[Decimal, dict[str, Any]]] = defaultdict(dict)
    telemetry: Counter[str] = Counter()

    async with httpx.AsyncClient(timeout=15.0) as http:
        for page in range(max_pages):
            telemetry["event_pages_requested"] += 1
            try:
                response = await http.get(
                    f"{BASE}/v1/events",
                    params={
                        "limit": 100,
                        "offset": page * 100,
                        "active": "true",
                        "closed": "false",
                        "ended": "false",
                        "categories": "sports",
                        "startTimeMax": horizon.isoformat().replace("+00:00", "Z"),
                        "orderBy": "startTime",
                        "orderDirection": "desc",
                    },
                )
                response.raise_for_status()
            except Exception:
                telemetry["event_page_failures"] += 1
                break
            telemetry["event_pages_ok"] += 1
            body = response.json() if response.content else {}
            events = body.get("events") or [] if isinstance(body, Mapping) else []
            stop_after_page = False
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                telemetry["events_seen"] += 1
                raw_start = str(event.get("startTime") or event.get("start_time") or "")
                if raw_start:
                    try:
                        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                        if start < oldest_allowed:
                            stop_after_page = True
                            continue
                    except ValueError:
                        telemetry["event_time_parse_failures"] += 1
                parent = str(event.get("slug") or event.get("id") or event.get("eventSlug") or "").strip()
                if not parent:
                    telemetry["events_without_parent_key"] += 1
                    continue
                for raw in event.get("markets") or []:
                    if not isinstance(raw, Mapping):
                        continue
                    telemetry["markets_seen"] += 1
                    market = dict(raw)
                    market["_parent_event_key"] = parent
                    identity = structural_identity(market)
                    if identity is None:
                        continue
                    telemetry["strict_markets_seen"] += 1
                    family, parent_key, base_slug, threshold = identity
                    key = (family, parent_key, base_slug)
                    prior = groups[key].get(threshold)
                    if prior is not None and str(prior.get("slug") or "") != str(market.get("slug") or ""):
                        groups[key][threshold] = {"_ambiguous": True}
                        telemetry["ambiguous_thresholds"] += 1
                    elif prior is None:
                        groups[key][threshold] = market
            if len(events) < 100 or stop_after_page:
                break

    market_map: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[str, str]] = []
    family_pairs: Counter[str] = Counter()
    family_markets: Counter[str] = Counter()
    for (family, _, _), threshold_map in groups.items():
        clean = [(line, m) for line, m in threshold_map.items() if not m.get("_ambiguous")]
        clean.sort(key=lambda x: x[0])
        if len(clean) < 2:
            continue
        for _, market in clean:
            market_map[str(market["slug"])] = market
            family_markets[family] += 1
        for i in range(len(clean)):
            for j in range(i + 1, len(clean)):
                pairs.append((str(clean[i][1]["slug"]), str(clean[j][1]["slug"])))
                family_pairs[family] += 1

    telemetry.update({
        "ladder_groups": sum(1 for v in groups.values() if len([m for m in v.values() if not m.get("_ambiguous")]) >= 2),
        "strict_ladder_markets": len(market_map),
        "strict_pairs": len(pairs),
    })
    return market_map, pairs, {
        **dict(telemetry),
        "family_markets": dict(family_markets.most_common()),
        "family_pairs": dict(family_pairs.most_common()),
    }


async def fetch_books(
    slugs: set[str], *, concurrency: int = 6
) -> tuple[dict[str, TopBook], dict[str, int]]:
    """Fetch exact public top books with bounded concurrency and 429-aware retries."""
    out: dict[str, TopBook] = {}
    stats: Counter[str] = Counter()
    sem = asyncio.Semaphore(max(1, concurrency))
    retry_delays = (0.4, 0.9, 1.8, 3.0)

    async with httpx.AsyncClient(timeout=7.0) as http:
        async def one(slug: str) -> None:
            async with sem:
                stats["requested"] += 1
                for attempt in range(len(retry_delays) + 1):
                    try:
                        response = await http.get(f"{BASE}/v1/markets/{slug}/book")
                    except Exception:
                        stats["transport_failures"] += 1
                        if attempt < len(retry_delays):
                            stats["retries"] += 1
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        return
                    if response.status_code == 429:
                        stats["http_429"] += 1
                        if attempt < len(retry_delays):
                            stats["retries"] += 1
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        return
                    if response.status_code != 200:
                        stats["http_failures"] += 1
                        return
                    try:
                        book = poly_top_book(response.json())
                    except Exception:
                        stats["parse_failures"] += 1
                        return
                    if book is None:
                        stats["empty_books"] += 1
                        return
                    out[slug] = book
                    stats["successful"] += 1
                    return

        await asyncio.gather(*(one(slug) for slug in sorted(slugs)))
    return out, dict(stats)


def score_structural_pairs(
    feed: Mapping[str, Mapping[str, Any]],
    books: Mapping[str, TopBook],
    pairs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Return >=3c-after-fee maker candidates for proven low/high relations."""
    rows: list[dict[str, Any]] = []
    for low_slug, high_slug in pairs:
        low, high = feed.get(low_slug), feed.get(high_slug)
        lb, hb = books.get(low_slug), books.get(high_slug)
        if low is None or high is None or lb is None or hb is None:
            continue
        li, hi = structural_identity(low), structural_identity(high)
        if li is None or hi is None:
            continue
        lf, lp, lbase, lx = li
        hf, hp, hbase, hx = hi
        if (lf, lp, lbase) != (hf, hp, hbase) or lx >= hx:
            continue

        for direction, maker_slug, maker_side, maker_bid, maker_ask, hedge_slug, hedge_side, hedge_px, hedge_depth in (
            ("MAKER_LOW_HEDGE_HIGH", low_slug, "YES", _bid(lb, "YES"), _ask(lb, "YES")[0], high_slug, "NO", *_ask(hb, "NO")),
            ("MAKER_HIGH_HEDGE_LOW", high_slug, "NO", _bid(hb, "NO"), _ask(hb, "NO")[0], low_slug, "YES", *_ask(lb, "YES")),
        ):
            locked_at_bid = _locked(maker_bid, hedge_px)
            if maker_bid <= 0 or maker_bid >= ONE or hedge_depth < ONE or locked_at_bid < TARGET:
                continue
            max_for_floor = ONE - hedge_px - TAKER_FEE_FLOOR - TARGET
            improved = min(maker_ask - TICK, max_for_floor)
            executable_maker_quote = max(maker_bid, improved)
            quote_locked = _locked(executable_maker_quote, hedge_px)
            if executable_maker_quote <= 0 or executable_maker_quote >= maker_ask or quote_locked < TARGET:
                continue
            rows.append({
                "direction": direction,
                "family": lf,
                "low_slug": low_slug,
                "high_slug": high_slug,
                "low_threshold": lx,
                "high_threshold": hx,
                "maker_slug": maker_slug,
                "maker_side": maker_side,
                "maker_price": maker_bid,
                "maker_ask": maker_ask,
                "executable_maker_quote": executable_maker_quote,
                "maker_gap_to_ask": maker_ask - executable_maker_quote,
                "improves_bid": executable_maker_quote > maker_bid,
                "hedge_slug": hedge_slug,
                "hedge_side": hedge_side,
                "hedge_ask": hedge_px,
                "hedge_depth": hedge_depth,
                "locked_profit": locked_at_bid,
                "quote_locked_profit": quote_locked,
            })

    rows.sort(key=lambda r: (
        not r["improves_bid"],
        r["maker_gap_to_ask"],
        -r["hedge_depth"],
        -r["quote_locked_profit"],
    ))
    return rows


def tradeable_structural_exact(
    feed_low: Mapping[str, Any],
    feed_high: Mapping[str, Any],
    low_resp: Mapping[str, Any],
    high_resp: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Final authenticated semantic gate for a structural same-venue pair."""
    li, hi = structural_identity(feed_low), structural_identity(feed_high)
    if li is None or hi is None:
        raise PermissionError("structural identity failed")
    lf, lp, lb, lx = li
    hf, hp, hb, hx = hi
    if (lf, lp, lb) != (hf, hp, hb) or lx >= hx:
        raise PermissionError("structural monotone relation failed")

    low = low_resp.get("market") if isinstance(low_resp.get("market"), Mapping) else low_resp
    high = high_resp.get("market") if isinstance(high_resp.get("market"), Mapping) else high_resp
    if str(low.get("slug") or "") != str(feed_low.get("slug") or "") or str(high.get("slug") or "") != str(feed_high.get("slug") or ""):
        raise PermissionError("authenticated exact slug mismatch")
    for market in (low, high):
        if market.get("active") is False or market.get("closed") is True or market.get("archived") is True:
            raise PermissionError("authenticated exact market not tradeable")
    # Feed discovery already requires the same concrete parent event.  When the
    # authenticated shape exposes an event slug too, require exact agreement.
    le, he = str(low.get("eventSlug") or low.get("event_slug") or ""), str(high.get("eventSlug") or high.get("event_slug") or "")
    if le and he and le != he:
        raise PermissionError("authenticated exact parent mismatch")
    return low, high
