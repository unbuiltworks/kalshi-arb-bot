"""Read-only all-category Polymarket US <-> Kalshi overlap auditor.

Purpose: discover where both exchanges list potentially equivalent contracts before
we build category-specific strict matchers. This process never submits orders and
never labels a fuzzy text match as an executable arbitrage.

It inventories active markets on both venues, maps broad categories, uses a
rare-token inverted index to avoid an O(P*K) all-pairs scan, produces conservative
candidate pairs, and logs enough metadata to turn repeated high-confidence overlaps
into explicit settlement-rule matchers later.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import logging
import re
import time
from typing import Any

import httpx

POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
logger = logging.getLogger("kalshi_arb.universe_overlap_audit")

STOP = {
    "a","an","and","are","at","be","before","by","did","do","does","for","from",
    "how","if","in","is","it","of","on","or","the","this","to","will","with","yes",
    "no","market","markets","than","more","less","over","under","above","below",
}
TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?", re.I)
NUM_RE = re.compile(r"(?<![a-z])\d+(?:\.\d+)?%?", re.I)
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")

# Discovery mapping only. A candidate is never executable until a category-specific
# matcher verifies exact outcome semantics and settlement rules.
CATEGORY_MAP = {
    "sports": {"sports"},
    "politics": {"politics", "elections"},
    "crypto": {"crypto", "cryptocurrency"},
    "culture": {"culture", "entertainment"},
    "finance": {"finance", "financials", "companies"},
    "macro": {"economics", "macro", "economic"},
    "climate": {"climate", "weather"},
    "geopolitics": {"politics", "geopolitics", "world"},
    "technology": {"technology", "tech", "science"},
    "mentions": {"mentions", "politics", "entertainment"},
}

# Guardrails for public-API inventory work. These are intentionally much larger
# than the current observed universes but finite so a pagination bug cannot run away.
POLY_PAGE_SIZE = 500
POLY_MAX_RECORDS = 50000
KALSHI_PAGE_SIZE = 200
KALSHI_MAX_PAGES = 150
KALSHI_PAGE_PAUSE_S = 0.08
INDEX_MAX_POSTING = 2500
MAX_SCORE_POOL = 400


@dataclass(frozen=True)
class PolyRec:
    slug: str
    category: str
    question: str
    description: str
    title: str
    subtitle: str
    end_date: str
    text: str
    tokens: frozenset[str]
    numbers: frozenset[str]
    dates: frozenset[str]
    volume: float
    liquidity: float


@dataclass(frozen=True)
class KalshiRec:
    ticker: str
    event_ticker: str
    series_ticker: str
    category: str
    event_title: str
    event_subtitle: str
    market_title: str
    yes_subtitle: str
    no_subtitle: str
    rules: str
    close_time: str
    text: str
    tokens: frozenset[str]
    numbers: frozenset[str]
    dates: frozenset[str]
    volume: float


def _as_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _words(text: str) -> frozenset[str]:
    return frozenset(
        t.lower() for t in TOKEN_RE.findall(text)
        if len(t) > 1 and t.lower() not in STOP
    )


def _nums(text: str) -> frozenset[str]:
    return frozenset(x.lower() for x in NUM_RE.findall(text))


def _dates(text: str) -> frozenset[str]:
    return frozenset(DATE_RE.findall(text))


def _poly_category(raw: Any) -> str:
    return str(raw or "unknown").strip().lower()


def _kalshi_category(raw: Any) -> str:
    return str(raw or "unknown").strip().lower()


def _compatible(poly_cat: str, kal_cat: str) -> bool:
    if poly_cat == kal_cat:
        return True
    allowed = CATEGORY_MAP.get(poly_cat)
    return kal_cat in allowed if allowed else False


def _compatible_categories(poly_cat: str) -> set[str]:
    return {poly_cat, *CATEGORY_MAP.get(poly_cat, set())}


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score(p: PolyRec, k: KalshiRec) -> tuple[float, dict[str, Any]]:
    shared = p.tokens & k.tokens
    if len(shared) < 2:
        return 0.0, {}

    jac = _jaccard(p.tokens, k.tokens)
    seq = SequenceMatcher(None, p.question.lower(), k.text.lower()).ratio()

    # Numbers are useful discriminators for thresholds/years. If both sides have
    # numbers and share none, sharply reduce confidence rather than rejecting:
    # descriptive text can contain unrelated dates/IDs.
    num_overlap = p.numbers & k.numbers
    num_factor = 1.0
    if p.numbers and k.numbers and not num_overlap:
        num_factor = 0.55
    elif num_overlap:
        num_factor = 1.12

    date_overlap = p.dates & k.dates
    date_factor = 1.08 if date_overlap else 1.0

    shared_factor = min(1.0, len(shared) / 7.0)
    raw = (0.58 * jac + 0.27 * seq + 0.15 * shared_factor) * num_factor * date_factor
    score = min(1.0, raw)
    return score, {
        "jaccard": round(jac, 4),
        "sequence": round(seq, 4),
        "shared": sorted(shared)[:18],
        "number_overlap": sorted(num_overlap),
        "date_overlap": sorted(date_overlap),
    }


async def fetch_poly(http: httpx.AsyncClient) -> tuple[list[PolyRec], bool]:
    out: list[PolyRec] = []
    truncated = False
    for offset in range(0, POLY_MAX_RECORDS, POLY_PAGE_SIZE):
        r = await http.get(
            f"{POLY}/v1/markets",
            params={
                "limit": POLY_PAGE_SIZE,
                "offset": offset,
                "active": "true",
                "closed": "false",
                "archived": "false",
                "orderBy": "volumeNum",
                "orderDirection": "desc",
            },
        )
        r.raise_for_status()
        rows = r.json().get("markets", [])
        for m in rows:
            subject = m.get("subject") or {}
            tags = m.get("tags") or []
            extras = " ".join(
                [str(subject.get("name") or ""), str(subject.get("displayName") or "")]
                + [str(t.get("label") or t.get("slug") or "") for t in tags if isinstance(t, dict)]
            )
            question = str(m.get("question") or "")
            title = str(m.get("title") or "")
            subtitle = str(m.get("subtitle") or "")
            description = str(m.get("description") or "")
            text = " ".join((question, title, subtitle, description, extras))
            out.append(PolyRec(
                slug=str(m.get("slug") or ""),
                category=_poly_category(m.get("category")),
                question=question,
                description=description,
                title=title,
                subtitle=subtitle,
                end_date=str(m.get("endDate") or ""),
                text=text,
                tokens=_words(text),
                numbers=_nums(text),
                dates=_dates(text),
                volume=_as_float(m.get("volumeNum", m.get("volume"))),
                liquidity=_as_float(m.get("liquidityNum", m.get("liquidity"))),
            ))
        if len(rows) < POLY_PAGE_SIZE:
            return out, False
    truncated = True
    logger.warning("Polymarket inventory hit safety cap of %d records", POLY_MAX_RECORDS)
    return out, truncated


async def fetch_kalshi(http: httpx.AsyncClient) -> tuple[list[KalshiRec], bool]:
    out: list[KalshiRec] = []
    cursor = ""
    pages = 0
    truncated = False
    while pages < KALSHI_MAX_PAGES:
        params: dict[str, Any] = {
            "limit": KALSHI_PAGE_SIZE,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        r = await http.get(f"{KALSHI}/events", params=params)
        if r.status_code == 429:
            retry_after = r.headers.get("retry-after")
            try:
                wait_s = max(1.0, float(retry_after or 0))
            except ValueError:
                wait_s = 5.0
            logger.warning("Kalshi inventory rate-limited; retrying cursor in %.1fs", wait_s)
            await asyncio.sleep(wait_s)
            continue
        r.raise_for_status()
        body = r.json()
        for e in body.get("events", []):
            category = _kalshi_category(e.get("category"))
            event_title = str(e.get("title") or "")
            event_subtitle = str(e.get("sub_title") or "")
            event_ticker = str(e.get("event_ticker") or "")
            series_ticker = str(e.get("series_ticker") or "")
            for m in e.get("markets") or []:
                market_title = str(m.get("title") or "")
                yes_sub = str(m.get("yes_sub_title") or "")
                no_sub = str(m.get("no_sub_title") or "")
                rules = str(m.get("rules_primary") or "")
                product = json.dumps(e.get("product_metadata") or {}, sort_keys=True)
                text = " ".join((event_title, event_subtitle, market_title, yes_sub, no_sub, rules, product))
                out.append(KalshiRec(
                    ticker=str(m.get("ticker") or ""),
                    event_ticker=event_ticker,
                    series_ticker=series_ticker,
                    category=category,
                    event_title=event_title,
                    event_subtitle=event_subtitle,
                    market_title=market_title,
                    yes_subtitle=yes_sub,
                    no_subtitle=no_sub,
                    rules=rules,
                    close_time=str(m.get("close_time") or m.get("expiration_time") or ""),
                    text=text,
                    tokens=_words(text),
                    numbers=_nums(text),
                    dates=_dates(text),
                    volume=_as_float(m.get("volume_fp", m.get("volume"))),
                ))
        cursor = str(body.get("cursor") or "")
        pages += 1
        if not cursor:
            return out, False
        await asyncio.sleep(KALSHI_PAGE_PAUSE_S)
    truncated = bool(cursor)
    if truncated:
        logger.warning("Kalshi inventory hit safety cap of %d pages", KALSHI_MAX_PAGES)
    return out, truncated


def _build_index(kalshi: list[KalshiRec]) -> tuple[
    dict[str, dict[str, list[KalshiRec]]],
    dict[str, Counter[str]],
]:
    """Build per-category token postings and document frequencies."""
    postings: dict[str, dict[str, list[KalshiRec]]] = defaultdict(lambda: defaultdict(list))
    dfs: dict[str, Counter[str]] = defaultdict(Counter)
    for k in kalshi:
        for token in k.tokens:
            postings[k.category][token].append(k)
            dfs[k.category][token] += 1
    return postings, dfs


def _candidate_pool(
    p: PolyRec,
    postings: dict[str, dict[str, list[KalshiRec]]],
    dfs: dict[str, Counter[str]],
) -> tuple[list[KalshiRec], int]:
    """Return likely Kalshi records using rare shared tokens, not all-pairs scan."""
    compatible = _compatible_categories(p.category)
    # Rank tokens by their total document frequency across compatible categories.
    ranked: list[tuple[int, str]] = []
    for token in p.tokens:
        df = sum(dfs.get(cat, Counter()).get(token, 0) for cat in compatible)
        if 0 < df <= INDEX_MAX_POSTING:
            ranked.append((df, token))
    ranked.sort()
    # Up to 12 rare tokens usually gives a very small candidate pool while retaining
    # multiple independent identity signals (entity + threshold/date/etc.).
    chosen = ranked[:12]
    if not chosen:
        return [], 0

    shared_counts: Counter[str] = Counter()
    by_ticker: dict[str, KalshiRec] = {}
    for _, token in chosen:
        for cat in compatible:
            for k in postings.get(cat, {}).get(token, []):
                if not _compatible(p.category, k.category):
                    continue
                by_ticker[k.ticker] = k
                shared_counts[k.ticker] += 1

    # _score itself requires >=2 shared tokens. Prioritize records that already
    # share multiple rare tokens; cap expensive SequenceMatcher calls.
    ordered = sorted(
        by_ticker.values(),
        key=lambda k: (shared_counts[k.ticker], k.volume),
        reverse=True,
    )
    return ordered[:MAX_SCORE_POOL], len(by_ticker)


def find_candidates(
    poly: list[PolyRec],
    kalshi: list[KalshiRec],
    *,
    threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    postings, dfs = _build_index(kalshi)
    candidates: list[dict[str, Any]] = []
    score_calls = 0
    indexed_pool_total = 0
    poly_with_pool = 0

    for p in poly:
        pool, raw_pool_size = _candidate_pool(p, postings, dfs)
        indexed_pool_total += raw_pool_size
        if not pool:
            continue
        poly_with_pool += 1

        scored: list[tuple[float, KalshiRec, dict[str, Any]]] = []
        for k in pool:
            score_calls += 1
            score, why = _score(p, k)
            if score >= threshold:
                scored.append((score, k, why))
        scored.sort(key=lambda x: x[0], reverse=True)
        for score, k, why in scored[:3]:
            candidates.append({
                "score": round(score, 4),
                "poly_category": p.category,
                "kalshi_category": k.category,
                "poly_slug": p.slug,
                "poly_question": p.question,
                "poly_end": p.end_date,
                "poly_volume": round(p.volume, 2),
                "poly_liquidity": round(p.liquidity, 2),
                "kalshi_ticker": k.ticker,
                "kalshi_series": k.series_ticker,
                "kalshi_event": k.event_title,
                "kalshi_yes": k.yes_subtitle,
                "kalshi_close": k.close_time,
                "kalshi_volume": round(k.volume, 2),
                "why": why,
                "status": "CANDIDATE_ONLY_NOT_EXECUTABLE",
            })
    candidates.sort(key=lambda x: (x["score"], x["poly_volume"] + x["kalshi_volume"]), reverse=True)
    return candidates, {
        "poly_with_index_pool": poly_with_pool,
        "raw_index_pool_total": indexed_pool_total,
        "expensive_score_calls": score_calls,
    }


async def audit_once(http: httpx.AsyncClient, *, threshold: float, top: int) -> None:
    t0 = time.monotonic()
    (poly, poly_truncated), (kalshi, kalshi_truncated) = await asyncio.gather(
        fetch_poly(http), fetch_kalshi(http)
    )
    fetch_elapsed = time.monotonic() - t0
    poly_cats = Counter(p.category for p in poly)
    kal_cats = Counter(k.category for k in kalshi)
    candidates, match_stats = find_candidates(poly, kalshi, threshold=threshold)

    logger.info("UNIVERSE %s", json.dumps({
        "shadow_only": True,
        "poly_markets": len(poly),
        "kalshi_markets": len(kalshi),
        "poly_truncated": poly_truncated,
        "kalshi_truncated": kalshi_truncated,
        "poly_categories": dict(poly_cats.most_common()),
        "kalshi_categories": dict(kal_cats.most_common()),
        "candidate_pairs": len(candidates),
        "threshold": threshold,
        "fetch_elapsed_s": round(fetch_elapsed, 2),
        "elapsed_s": round(time.monotonic() - t0, 2),
        **match_stats,
    }, separators=(",", ":")))

    category_hits = Counter(c["poly_category"] for c in candidates)
    logger.info("OVERLAP_COUNTS %s", json.dumps(dict(category_hits.most_common()), separators=(",", ":")))
    for row in candidates[:top]:
        logger.info("OVERLAP_CANDIDATE %s", json.dumps(row, separators=(",", ":")))


async def main_async(args: argparse.Namespace) -> None:
    logger.info("START universe_overlap_audit shadow_only=true interval_s=%s threshold=%s", args.interval, args.threshold)
    async with httpx.AsyncClient(timeout=20.0, limits=httpx.Limits(max_connections=20)) as http:
        while True:
            try:
                await audit_once(http, threshold=args.threshold, top=args.top)
            except Exception:
                logger.exception("Universe audit cycle failed")
            if args.once:
                return
            await asyncio.sleep(args.interval)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only Polymarket US/Kalshi all-category overlap audit")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--threshold", type=float, default=0.44)
    p.add_argument("--top", type=int, default=80)
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
