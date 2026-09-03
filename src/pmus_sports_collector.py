"""Keyless 24/7 Polymarket US sports forward collector (READ-ONLY).

Issue #167 comment 5514571607. Talks ONLY to the public keyless gateway
(https://gateway.polymarket.us) with GET requests: no API keys, no
authenticated API, no socket streams, and no exchange-mutating call of
any kind exists anywhere in this module. Default and only mode: collector.

What it records (append-only JSONL, one file per UTC day):
  * per-market BBO + public top-of-book: bid/ask, bid touch qty, ask
    touch qty WHEN the public book serves the ask side (it is often
    bid-only; we then record null with an explicit limitation flag —
    ask depth is never fabricated), lastTradePx, cumulative
    sharesTraded and openInterest (public taker-volume proxy)
  * periodic /v1/incentives snapshots so liquidity-reward-program state
    is time-aligned with market observations
  * discovery decisions (which markets, why) and collector lifecycle

Discovery is dynamic, never hardcoded slugs:
  * configured league slugs (default "mlb"), live events only, market
    prefixes default "aec-,a5c-" (moneyline, first-5 winner) — add
    "cfb"/"nfl" to PMUS_COLLECTOR_LEAGUES to enable those without any
    code change
  * plus any market carrying an ACTIVE liquidity-program period (this
    is what picks up US Open tennis pools today and CFB pools when
    they run)

Rate safety: the public cap is 20 req/s per IP. Each cycle costs about
2 requests per market (bbo + book) plus discovery overhead; the cycle
planner keeps the request rate under PMUS_RATE_BUDGET_RPS (default 8)
by stretching the poll interval, and hard-caps tracked markets at
PMUS_MAX_MARKETS (default 24).

Persistence: writes under PMUS_COLLECTOR_ROOT (default /state/pmus-sports)
with restart-safe state and an atomic heartbeat. NOTE: durability across
redeploys depends on a volume actually being mounted at that path by the
deployment — this module makes no durability claim on its own.

Run: python3 -m src.pmus_sports_collector          (runs forever)
     python3 -m src.pmus_sports_collector --minutes 10   (bounded)
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

GATEWAY = "https://gateway.polymarket.us"
SOURCE = "gateway.polymarket.us"

ROOT_ENV = "PMUS_COLLECTOR_ROOT"
DEFAULT_ROOT = "/state/pmus-sports"
LEAGUES_ENV = "PMUS_COLLECTOR_LEAGUES"
DEFAULT_LEAGUES = "mlb"
PREFIXES_ENV = "PMUS_MARKET_PREFIXES"
DEFAULT_PREFIXES = "aec-,a5c-"
INCENTIVE_MARKETS_ENV = "PMUS_COLLECT_INCENTIVE_MARKETS"   # default on
POLL_ENV = "PMUS_POLL_SECONDS"
DEFAULT_POLL_S = 5.0
BUDGET_ENV = "PMUS_RATE_BUDGET_RPS"
DEFAULT_BUDGET_RPS = 8.0
MAX_MARKETS_ENV = "PMUS_MAX_MARKETS"
DEFAULT_MAX_MARKETS = 24
DISCOVERY_ENV = "PMUS_DISCOVERY_SECONDS"
DEFAULT_DISCOVERY_S = 60.0
INCENTIVES_SNAP_ENV = "PMUS_INCENTIVES_SNAPSHOT_SECONDS"
DEFAULT_INCENTIVES_SNAP_S = 300.0
STATUS_EVERY_ENV = "PMUS_STATUS_EVERY_SECONDS"
DEFAULT_STATUS_EVERY_S = 60.0     # rolling status line to stdout (0=off)
CA_ENV = "PMUS_CA_BUNDLE"            # optional override; system TLS default
ROLE_ENV = "PMUS_SERVICE_ROLE"       # identity guard vs config collisions
MY_ROLE = "collector"

CALLS_PER_MARKET = 2                 # bbo + book


def assert_service_role() -> None:
    """Permanent guard against the Railway config collision: if this
    process starts inside a service declared (via env) to be a DIFFERENT
    worker — e.g. pmus-ws-evidence-recorder built from the root
    railway.json by mistake — refuse to run instead of silently
    collecting under the wrong service. A crash loop is visible; the
    wrong worker is not."""
    role = os.getenv(ROLE_ENV, "").strip()
    if role and role != MY_ROLE:
        raise SystemExit(
            f"SERVICE_ROLE_MISMATCH: this image runs the '{MY_ROLE}' "
            f"worker but the service declares {ROLE_ENV}={role!r}; "
            "fix the service's Config File Path (see deploy/*/railway.json)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def root_dir() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def _ssl_context() -> ssl.SSLContext:
    ca = os.getenv(CA_ENV, "").strip()
    return ssl.create_default_context(cafile=ca) if ca \
        else ssl.create_default_context()


def http_get_json(path: str, timeout: float = 15.0):
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-collector"})
    with urllib.request.urlopen(req, context=_ssl_context(),
                                timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------- pure planning/discovery -------------------------

def plan_cycle(n_markets: int, poll_s: float, budget_rps: float,
               max_markets: int, overhead_rps: float = 0.5) -> dict:
    """Deterministic rate plan: cap markets, stretch the poll interval so
    (CALLS_PER_MARKET * kept) / effective_poll + overhead <= budget."""
    kept = max(0, min(n_markets, max_markets))
    eff = float(poll_s)
    if kept:
        usable = max(budget_rps - overhead_rps, 0.5)
        min_poll = (CALLS_PER_MARKET * kept) / usable
        eff = max(eff, min_poll)
    return {"kept_markets": kept, "effective_poll_s": round(eff, 3),
            "dropped_markets": max(0, n_markets - kept)}


def active_incentive_slugs(incentives: Mapping[str, Any]) -> list[str]:
    out = []
    for p in incentives.get("programs", []) or []:
        slug = p.get("marketSlug")
        if not slug:
            continue
        if any(tp.get("status") == "active"
               for tp in p.get("timePeriods", []) or []):
            out.append(slug)
    return sorted(set(out))


def _event_state_summary(event: Mapping[str, Any]) -> dict:
    st = event.get("eventState") or {}
    return {"live": bool(event.get("live")),
            "score": st.get("score") if isinstance(st, Mapping) else None,
            "period": st.get("period") if isinstance(st, Mapping) else None}


PRIMARY_MARKET_PREFIXES = ("aec-", "a5c-")   # labeling ONLY, never admission

# Coverage audit (issue #167 comment 5520693117): the events endpoint
# defaults to 10 events/league and embeds at most ~15 markets/event,
# while event.marketCounts.numMarkets reports 47-162+ per event. The
# full open universe is served by /v1/markets?active=true&closed=false
# with limit/offset paging; rows join to events on gameStartTime ==
# event startTime (exact ISO match, verified live).
EVENTS_PAGE_LIMIT = 500
ENUM_PAGE_SIZE = 500
ENUM_MAX_PAGES_DEFAULT = 50


def enumerate_open_markets(get_json, max_pages: int = ENUM_MAX_PAGES_DEFAULT,
                           page_size: int = ENUM_PAGE_SIZE) -> tuple[list, int]:
    """Bounded GET-only enumeration of ALL open+active markets. Returns
    (rows, pages_fetched); stops on the first short page. A mid-way
    failure returns what was gathered — discovery degrades, never dies."""
    rows: list = []
    pages = 0
    for page in range(max_pages):
        try:
            resp = get_json(f"/v1/markets?active=true&closed=false"
                            f"&limit={page_size}&offset={page * page_size}")
        except Exception:
            break
        ms = resp.get("markets") if isinstance(resp, Mapping) else None
        if not isinstance(ms, list):
            break
        pages += 1
        rows.extend(m for m in ms if isinstance(m, Mapping))
        if len(ms) < page_size:
            break
    return rows, pages


def expand_live_event_markets(events_by_league: Mapping[str, list],
                              open_markets: list) -> list[dict]:
    """Join globally-enumerated open markets to LIVE events on the exact
    gameStartTime == event startTime key. Only live events expand; rows
    without a matching live event (futures etc.) are never admitted."""
    start_index: dict[str, tuple[str, dict]] = {}
    for league, events in events_by_league.items():
        for ev in events or []:
            if not isinstance(ev, Mapping) or not ev.get("live"):
                continue
            key = str(ev.get("startTime") or ev.get("startDate") or "")
            if key:
                start_index[key] = (league, _event_state_summary(ev))
    out: list[dict] = []
    for m in open_markets:
        slug = str(m.get("slug") or "")
        hit = start_index.get(str(m.get("gameStartTime") or ""))
        if not slug or hit is None:
            continue
        league, state = hit
        out.append({"slug": slug, "league": league,
                    "why": "expanded_enumeration",
                    "event_state": state,
                    "prop": not slug.startswith(PRIMARY_MARKET_PREFIXES),
                    "market_meta": _market_meta(m)})
    return out


def _market_meta(m: Mapping[str, Any]) -> dict:
    """Tolerant market metadata (kept so the L5 engine stays
    market-agnostic); only known-present keys are copied."""
    meta = {}
    for key in ("name", "title", "type", "marketType", "sportsMarketTypeV2",
                "category", "eventSlug", "event_slug",
                # cheap pre-WS screening fields (issue #167 comment
                # 5521333079): copied only when the venue actually served
                # them — never guessed, never defaulted
                "bestBidQuote", "bestAskQuote", "outcomePrices",
                "volume", "volume24hr", "liquidity", "gameStartTime"):
        if m.get(key) is not None:
            meta[key] = m.get(key)
    return meta


def discover_targets(events_by_league: Mapping[str, list],
                     incentives: Mapping[str, Any],
                     prefixes: Iterable[str],
                     include_incentive_markets: bool = True) -> list[dict]:
    """Pure selection: live league events' markets first (in configured
    league order), then active-liquidity-program markets. An EMPTY
    `prefixes` admits every market on live events — props included; a
    non-empty tuple keeps the legacy prefix filter for callers that want
    it. Prop labeling is diagnostic only and never excludes a market."""
    prefixes = tuple(prefixes)
    targets: list[dict] = []
    seen = set()
    for league, events in events_by_league.items():
        for ev in events or []:
            if not ev.get("live"):
                continue
            state = _event_state_summary(ev)
            for m in ev.get("markets", []) or []:
                slug = m.get("slug", "")
                if not slug or slug in seen:
                    continue
                if prefixes and not slug.startswith(prefixes):
                    continue
                seen.add(slug)
                targets.append({"slug": slug, "league": league,
                                "why": "live_league_market",
                                "event_state": state,
                                "prop": not slug.startswith(
                                    PRIMARY_MARKET_PREFIXES),
                                "market_meta": _market_meta(m)})
    if include_incentive_markets:
        for slug in active_incentive_slugs(incentives):
            if slug not in seen:
                seen.add(slug)
                targets.append({"slug": slug, "league": "incentive_program",
                                "why": "active_liquidity_program",
                                "event_state": None,
                                "prop": not slug.startswith(
                                    PRIMARY_MARKET_PREFIXES),
                                "market_meta": {}})
    return targets


def bbo_book_row(slug: str, meta: Mapping[str, Any],
                 bbo_md: Mapping[str, Any],
                 book_md: Mapping[str, Any]) -> dict:
    """Normalize one observation. Ask depth is NEVER fabricated: when the
    public book omits the ask side we record null plus a limitation flag."""
    bids = book_md.get("bids") or []
    asks = book_md.get("asks") or []
    def val(k):
        v = bbo_md.get(k)
        return v.get("value") if isinstance(v, Mapping) else v
    return {
        "kind": "bbo_book",
        "t": _utcnow().isoformat(),
        "slug": slug,
        "league": meta.get("league"),
        "why": meta.get("why"),
        "event_state": meta.get("event_state"),
        "bid": val("bestBid"),
        "bid_touch_qty": bids[0].get("qty") if bids else None,
        "ask": val("bestAsk"),
        "ask_touch_qty": asks[0].get("qty") if asks else None,
        "ask_book_missing": not asks,
        "last_trade_px": val("lastTradePx"),
        "shares_traded_cum": bbo_md.get("sharesTraded"),
        "open_interest": bbo_md.get("openInterest"),
        "market_state": bbo_md.get("state"),
        "source": SOURCE,
    }


# ------------------------- durable files -------------------------

def daily_raw_path(root: Path, now: Optional[datetime] = None) -> Path:
    now = now or _utcnow()
    return root / "raw" / f"pmus_{now:%Y%m%d}.jsonl"


def append_rows(root: Path, rows: Iterable[Mapping[str, Any]],
                now: Optional[datetime] = None) -> Path:
    path = daily_raw_path(root, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, separators=(",", ":"),
                                  default=str) + "\n")
    return path


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    os.replace(tmp, path)


def load_state(root: Path) -> dict:
    path = root / "state.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"started_utc": _utcnow().isoformat(), "cycles": 0,
            "rows_written": 0, "restarts": 0}


def save_state(root: Path, state: Mapping[str, Any]) -> None:
    _atomic_write(root / "state.json", state)


def heartbeat(root: Path, **fields: Any) -> None:
    _atomic_write(root / "heartbeat.json",
                  {"t": _utcnow().isoformat(), "mode": "READ_ONLY_COLLECTOR",
                   "source": SOURCE, **fields})


# ------------------------- main loop -------------------------

def run(minutes: Optional[float] = None) -> int:
    assert_service_role()
    root = root_dir()
    leagues = [x.strip() for x in
               os.getenv(LEAGUES_ENV, DEFAULT_LEAGUES).split(",") if x.strip()]
    prefixes = [x.strip() for x in
                os.getenv(PREFIXES_ENV, DEFAULT_PREFIXES).split(",") if x.strip()]
    include_inc = os.getenv(INCENTIVE_MARKETS_ENV, "1").strip() not in \
        {"0", "false", "no", "off"}
    poll_s = float(os.getenv(POLL_ENV, str(DEFAULT_POLL_S)))
    budget = float(os.getenv(BUDGET_ENV, str(DEFAULT_BUDGET_RPS)))
    max_markets = int(os.getenv(MAX_MARKETS_ENV, str(DEFAULT_MAX_MARKETS)))
    discovery_s = float(os.getenv(DISCOVERY_ENV, str(DEFAULT_DISCOVERY_S)))
    inc_snap_s = float(os.getenv(INCENTIVES_SNAP_ENV,
                                 str(DEFAULT_INCENTIVES_SNAP_S)))

    state = load_state(root)
    state["restarts"] = int(state.get("restarts", 0)) + 1
    save_state(root, state)
    append_rows(root, [{"kind": "collector_start", "t": _utcnow().isoformat(),
                        "leagues": leagues, "prefixes": prefixes,
                        "include_incentive_markets": include_inc,
                        "poll_s": poll_s, "budget_rps": budget,
                        "max_markets": max_markets, "source": SOURCE,
                        "read_only": True}])

    deadline = time.monotonic() + minutes * 60 if minutes else None
    targets: list[dict] = []
    incentives: dict = {}
    last_discovery = last_inc_snap = 0.0

    while deadline is None or time.monotonic() < deadline:
        now_mono = time.monotonic()
        if now_mono - last_inc_snap >= inc_snap_s or not incentives:
            try:
                incentives = http_get_json("/v1/incentives")
                append_rows(root, [{"kind": "incentives_snapshot",
                                    "t": _utcnow().isoformat(),
                                    "active_market_slugs":
                                        active_incentive_slugs(incentives),
                                    "programs": incentives.get("programs"),
                                    "source": SOURCE}])
            except Exception as exc:
                append_rows(root, [{"kind": "error", "stage": "incentives",
                                    "t": _utcnow().isoformat(),
                                    "err": str(exc)[:120]}])
            last_inc_snap = now_mono
        if now_mono - last_discovery >= discovery_s or not targets:
            events_by_league = {}
            for lg in leagues:
                try:
                    ev = http_get_json(f"/v2/leagues/{lg}/events")
                    events_by_league[lg] = ev if isinstance(ev, list) \
                        else ev.get("events") or []
                except Exception as exc:
                    append_rows(root, [{"kind": "error", "stage": "discovery",
                                        "league": lg,
                                        "t": _utcnow().isoformat(),
                                        "err": str(exc)[:120]}])
            targets = discover_targets(events_by_league, incentives,
                                       prefixes, include_inc)
            plan = plan_cycle(len(targets), poll_s, budget, max_markets)
            targets = targets[:plan["kept_markets"]]
            append_rows(root, [{"kind": "discovery",
                                "t": _utcnow().isoformat(),
                                "targets": [{"slug": t["slug"],
                                             "league": t["league"],
                                             "why": t["why"]}
                                            for t in targets],
                                "plan": plan, "source": SOURCE}])
            last_discovery = now_mono

        plan = plan_cycle(len(targets), poll_s, budget, max_markets)
        rows = []
        for t in targets:
            slug = t["slug"]
            try:
                bbo = http_get_json(f"/v1/markets/{slug}/bbo")["marketData"]
                book = http_get_json(f"/v1/markets/{slug}/book")["marketData"]
                rows.append(bbo_book_row(slug, t, bbo, book))
            except Exception as exc:
                rows.append({"kind": "error", "stage": "poll", "slug": slug,
                             "t": _utcnow().isoformat(),
                             "err": str(exc)[:120]})
        if rows:
            append_rows(root, rows)
        state["cycles"] = int(state.get("cycles", 0)) + 1
        state["rows_written"] = int(state.get("rows_written", 0)) + len(rows)
        save_state(root, state)
        heartbeat(root, markets=len(targets), cycles=state["cycles"],
                  rows_written=state["rows_written"],
                  effective_poll_s=plan["effective_poll_s"])
        status_every = float(os.getenv(STATUS_EVERY_ENV,
                                       str(DEFAULT_STATUS_EVERY_S)))
        if status_every > 0 and \
                now_mono - getattr(run, "_last_status", 0.0) >= status_every:
            try:
                # additive tape-analysis line for Railway logs; failures
                # never affect collection
                from src import pmus_status_report
                pmus_status_report.emit(root)
            except Exception as exc:
                append_rows(root, [{"kind": "error", "stage": "status",
                                    "t": _utcnow().isoformat(),
                                    "err": str(exc)[:120]}])
            run._last_status = now_mono
        time.sleep(plan["effective_poll_s"] if targets else
                   max(poll_s, 20.0))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=None,
                    help="bounded run; default runs forever")
    args = ap.parse_args()
    return run(args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
