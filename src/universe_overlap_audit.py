"""Read-only all-category Polymarket US <-> Kalshi overlap auditor.

Purpose: discover where both exchanges list potentially equivalent contracts before
we build category-specific strict matchers. This process never submits orders and
never labels a fuzzy text match as an executable arbitrage.

It continuously inventories active markets on both venues, maps broad categories,
produces conservative candidate pairs, and logs enough metadata to turn repeated
high-confidence overlaps into explicit settlement-rule matchers later.
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

# Broad discovery mapping only. Exact execution matchers remain category-specific.
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
    # descriptive text often contains unrelated dates/IDs.
    num_overlap = p.numbers & k.numbers
    num_factor = 1.0
    if p.numbers and k.numbers and not num_overlap:
        num_factor = 0.55
    elif num_overlap:
        num_factor = 1.12

    date_overlap = p.dates & k.dates
    date_factor = 1.08 if date_overlap else 1.0

    # Favor distinctive shared language, not generic stop-word similarity.
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


async def fetch_poly(http: httpx.AsyncClient) -> list[PolyRec]:
    out: list[PolyRec] = []
    limit = 500
    for offset in range(0, 20000, limit):
        r = await http.get(
            f"{POLY}/v1/markets",
            params={
                "limit": limit,
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
        if len(rows) < limit:
            break
    return out


async def fetch_kalshi(http: httpx.AsyncClient) -> list[KalshiRec]:
    out: list[KalshiRec] = []
    cursor = ""
    pages = 0
    while pages < 100:
        params: dict[str, Any] = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        r = await http.get(f"{KALSHI}/events", params=params)
        if r.status_code == 429:
            await asyncio.sleep(5)
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
            break
    return out


def find_candidates(poly: list[PolyRec], kalshi: list[KalshiRec], *, threshold: float) -> list[dict[str, Any]]:
    by_category: dict[str, list[KalshiRec]] = defaultdict(list)
    for k in kalshi:
        by_category[k.category].append(k)

    candidates: list[dict[str, Any]] = []
    for p in poly:
        pool: list[KalshiRec] = []
        if p.category in by_category:
            pool.extend(by_category[p.category])
        for mapped in CATEGORY_MAP.get(p.category, set()):
            if mapped != p.category:
                pool.extend(by_category.get(mapped, []))
        if not pool:
            continue

        scored: list[tuple[float, KalshiRec, dict[str, Any]]] = []
        seen: set[str] = set()
        for k in pool:
            if k.ticker in seen or not _compatible(p.category, k.category):
                continue
            seen.add(k.ticker)
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
    return candidates


async def audit_once(http: httpx.AsyncClient, *, threshold: float, top: int) -> None:
    t0 = time.monotonic()
    poly, kalshi = await asyncio.gather(fetch_poly(http), fetch_kalshi(http))
    poly_cats = Counter(p.category for p in poly)
    kal_cats = Counter(k.category for k in kalshi)
    candidates = find_candidates(poly, kalshi, threshold=threshold)

    logger.info("UNIVERSE %s", json.dumps({
        "shadow_only": True,
        "poly_markets": len(poly),
        "kalshi_markets": len(kalshi),
        "poly_categories": dict(poly_cats.most_common()),
        "kalshi_categories": dict(kal_cats.most_common()),
        "candidate_pairs": len(candidates),
        "threshold": threshold,
        "elapsed_s": round(time.monotonic() - t0, 2),
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
