"""Probe-local candidate feed built from the PUBLIC gateway (2026-11-14).

WHY. The probe's selection reads a status document from
PMUS_SPRING_EVIDENCE_STATUS_URL, served by pmus-ws-evidence-recorder. That
recorder was repurposed to BTC15m and now lists ~0-1 sports markets, so the
probe has nothing to select. pmus-sports-forward-collector polls the sports
slate but serves no HTTP and writes to a volume this service cannot mount.

WHAT. With PMUS_SPRING_CANDIDATES=gateway (default unset = unchanged), the
probe builds the status document itself: live sports events from
/v1/events?live=true&active=true&closed=false&categories=sports, a cheap
in-band screen on the venue-served event quotes, then round-robin
/v1/markets/{slug}/bbo + /book polls and /v1/market/slug/{slug} open-state.
Rows are shaped exactly like the recorder's spring_watch rows, so
rank_candidates / strict_evidence_reason / pair_score are UNCHANGED -- only
the source of rows changes.

PROVENANCE IS NOT LAUNDERED. Public data carries no trade direction; trades
are INFERRED from sharesTraded deltas vs BBO (the same inference the sports
collector and pmus_spring_evidence_transfer.compose_evidence use). The
document therefore declares evidence_provenance="inferred", and the strict
guard's authenticated-provenance gate REFUSES it unless the owner sets
PMUS_SPRING_ACCEPT_INFERRED_PROVENANCE=1 explicitly. Every other gate (band,
two-sided book, q1 depth, venue open state, model OPEN, thin-flow,
two-sided flow, freshness, off-distribution) applies unchanged.

Keyless public GETs only: no credentials, no transport, no order path.
Rate-bounded: a hard minimum interval between requests (<= 2 rps) and a
per-build request budget.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Callable, Mapping, Optional

CANDIDATES_ENV = "PMUS_SPRING_CANDIDATES"
ACCEPT_INFERRED_ENV = "PMUS_SPRING_ACCEPT_INFERRED_PROVENANCE"
RPS_ENV = "PMUS_SPRING_GATEWAY_RPS"
MAX_REQ_ENV = "PMUS_SPRING_GATEWAY_MAX_REQ"
DEFAULT_RPS = 1.5
MAX_RPS = 2.0
DEFAULT_MAX_REQ = 24
EVENTS_REFRESH_S = 60.0
REBUILD_MIN_S = 10.0
HISTORY_S = 3900.0
MAX_OBS_PER_SLUG = 800
MARKET_STATE_TTL_S = 120.0
ROW_MAX_AGE_S = 60.0          # a row older than this is not served
SCREEN_LO, SCREEN_HI = 0.20, 0.80   # cheap pre-screen, wider than the band
PROVENANCE = "inferred"
SOURCE = ("probe-local public gateway feed (keyless GET; trade direction "
          "INFERRED from sharesTraded delta vs BBO, never observed)")

GATEWAY = "https://gateway.polymarket.us"
LIVE_EVENTS_PATH = ("/v1/events?live=true&active=true&closed=false"
                    "&categories=sports")
_STATE: dict = {}


# --- public gateway helpers. Deliberately NOT imported from
# pmus_sports_collector: the probe image must not carry the collector (see
# tests/test_pmus_spring_l5_deploy.py). Same shapes the collector uses.

def http_get_json(path: str, timeout: float = 10.0):
    import json
    import ssl
    import urllib.request
    ca = os.getenv("PMUS_CA_BUNDLE", "").strip()
    ctx = (ssl.create_default_context(cafile=ca) if ca
           else ssl.create_default_context())
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-spring-probe"})
    with urllib.request.urlopen(req, context=ctx,       # noqa: S310
                                timeout=timeout) as r:
        return json.loads(r.read())


def fetch_live_events(get, page_size: int = 500) -> tuple:
    """One page of the venue's own live sports set; only rows the venue
    marks live and not ended are kept."""
    try:
        resp = get(f"{LIVE_EVENTS_PATH}&limit={page_size}&offset=0")
    except Exception as exc:                            # noqa: BLE001
        return [], {"v1_live_error": f"{type(exc).__name__}:{str(exc)[:80]}"}
    evs = resp if isinstance(resp, list) else (
        resp.get("events") if isinstance(resp, Mapping) else None)
    if not isinstance(evs, list):
        return [], {"v1_live_error": "unexpected_response_shape"}
    rows = [e for e in evs if isinstance(e, Mapping)
            and e.get("live") is True and not e.get("ended")]
    return rows, {"v1_live_events": len(rows), "v1_live_error": None}


def live_event_targets(live_events: list) -> list:
    out: list = []
    seen: set = set()
    for ev in live_events or []:
        st = ev.get("eventState") or {}
        state = {"live": True,
                 "score": st.get("score") if isinstance(st, Mapping) else None,
                 "period": st.get("period") if isinstance(st, Mapping) else None}
        league = str(ev.get("slug") or "").split("-", 1)[0] or "sports"
        for m in ev.get("markets") or []:
            if not isinstance(m, Mapping):
                continue
            slug = str(m.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append({"slug": slug, "league": league,
                        "why": "v1_live_event_market", "event_state": state,
                        "market_meta": {k: m.get(k) for k in
                                        ("bestBidQuote", "bestAskQuote")
                                        if m.get(k) is not None}})
    return out


def bbo_book_row(slug: str, meta: Mapping[str, Any], bbo_md: Mapping[str, Any],
                 book_md: Mapping[str, Any], t_iso: str) -> dict:
    """The sports collector's bbo_book row shape (ask depth never
    fabricated; the venue serves sell depth under `offers`)."""
    bids = book_md.get("bids") or []
    asks = (book_md.get("offers") or []) if "offers" in book_md \
        else (book_md.get("asks") or [])

    def val(k):
        v = bbo_md.get(k)
        return v.get("value") if isinstance(v, Mapping) else v
    return {"kind": "bbo_book", "t": t_iso, "slug": slug,
            "league": meta.get("league"), "why": meta.get("why"),
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
            "source": "gateway.polymarket.us"}


def enabled() -> bool:
    return os.getenv(CANDIDATES_ENV, "").strip().lower() == "gateway"


def accept_inferred() -> bool:
    return os.getenv(ACCEPT_INFERRED_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def reset() -> None:
    _STATE.clear()


def _rps() -> float:
    try:
        r = float(os.getenv(RPS_ENV, str(DEFAULT_RPS)))
    except ValueError:
        r = DEFAULT_RPS
    return max(0.1, min(r, MAX_RPS))


def _max_req() -> int:
    try:
        return max(2, int(os.getenv(MAX_REQ_ENV, str(DEFAULT_MAX_REQ))))
    except ValueError:
        return DEFAULT_MAX_REQ


class _Limited:
    """Request budget + minimum spacing between requests."""

    def __init__(self, get: Callable, sleep: Callable, clock: Callable,
                 budget: int):
        self.get, self.sleep, self.clock = get, sleep, clock
        self.left = budget
        self.n = 0
        self.errors: list = []

    def __call__(self, path: str):
        if self.left <= 0:
            raise RuntimeError("gateway_request_budget_spent")
        gap = 1.0 / _rps()
        last = _STATE.get("last_req")
        if last is not None:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self.left -= 1
        self.n += 1
        _STATE["last_req"] = self.clock()
        return self.get(path)


def _qnum(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value", v.get("price", v.get("px")))
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _screen(targets: list) -> list:
    out = []
    for tg in targets:
        bid = _qnum((tg.get("market_meta") or {}).get("bestBidQuote"))
        if bid is None or SCREEN_LO <= bid <= SCREEN_HI:
            out.append(tg)
    return sorted(out, key=lambda x: str(x["slug"]))


def _probe_entry_action(bid: Any, ask: Any) -> Optional[dict]:
    """The canonical L5 first action from flat -- the same computation as
    the enriched recorder's probe_entry_action (frozen v2 + shared planner).
    Absent on any failure; the guard then refuses."""
    try:
        from src import pmus_sports_l5_decision as l5d
        from src import pmus_l5_persistence_v2 as pv2
        b, a = D(str(bid)), D(str(ask))
        d = pv2.v2_dist(pv2.load_v2(),
                        l5d.state_key(float(b), l5d.SPORTS_TTE_DEFAULT_S, 0.0),
                        "open", "q<5")
        if d.get("backed_off"):
            return {"decision": "no_action", "reason": "v2_off_distribution",
                    "state": d.get("state")}
        rels = pv2.relation_mass(d["dist"])
        plan = l5d.plan_action(max(rels, key=rels.get), l5d.Inventory(),
                               {"bid": b, "ask": a})
        return {k: v for k, v in plan.items() if not k.startswith("_")}
    except Exception:                                   # noqa: BLE001
        return None


def _market_state(slug: str, now: float, get) -> Optional[dict]:
    cache = _STATE.setdefault("mstate", {})
    hit = cache.get(slug)
    if hit and now - hit[0] < MARKET_STATE_TTL_S:
        return hit[1]
    try:
        m = (get(f"/v1/market/slug/{slug}") or {}).get("market") or {}
    except Exception:                                   # noqa: BLE001
        return hit[1] if hit else None
    state = {k: m.get(k) for k in
             ("active", "archived", "closed", "status", "endDate")}
    state["minimumTradeQty"] = m.get("minimumTradeQty")
    state["orderPriceMinTickSize"] = m.get("orderPriceMinTickSize")
    cache[slug] = (now, state)
    return state


def build_status(now: Optional[float] = None, get: Optional[Callable] = None,
                 sleep: Callable = time.sleep,
                 clock: Callable = time.monotonic) -> dict:
    """One bounded refresh, then the recorder-shaped status document."""
    from src import pmus_spring_evidence_transfer as xfer
    from src import pmus_spring_shadow_replay as replay
    from src import pmus_spring_l5_session as session
    now = time.time() if now is None else float(now)
    if _STATE.get("doc") is not None and \
            now - float(_STATE.get("doc_at") or 0) < REBUILD_MIN_S:
        return _STATE["doc"]
    get = http_get_json if get is None else get
    lim = _Limited(get, sleep, clock, _max_req())
    diag: dict = {}
    # 1. live sports slate (cached between refreshes)
    if now - float(_STATE.get("events_at") or 0) >= EVENTS_REFRESH_S \
            or "targets" not in _STATE:
        events, ediag = fetch_live_events(lim)
        diag["events"] = ediag
        if not ediag.get("v1_live_error") or events:
            _STATE["targets"] = _screen(live_event_targets(events))
            _STATE["events_at"] = now
    targets = list(_STATE.get("targets") or [])
    by_slug: dict = _STATE.setdefault("by_slug", {})
    live = {str(tg["slug"]) for tg in targets}
    for slug in list(by_slug):
        if slug not in live:
            del by_slug[slug]
    # 2. round-robin BBO + book polls inside the budget
    polled = 0
    if targets:
        cur = int(_STATE.get("cursor") or 0) % len(targets)
        for i in range(len(targets)):
            if lim.left < 3:          # leave one for a market-state read
                break
            tg = targets[(cur + i) % len(targets)]
            slug = str(tg["slug"])
            try:
                bbo = lim(f"/v1/markets/{slug}/bbo")["marketData"]
                book = lim(f"/v1/markets/{slug}/book")["marketData"]
            except Exception as exc:                     # noqa: BLE001
                lim.errors.append(f"{slug}:{type(exc).__name__}")
                continue
            row = bbo_book_row(
                slug, tg, bbo, book,
                datetime.fromtimestamp(now, timezone.utc).isoformat())
            obs = by_slug.setdefault(slug, [])
            obs.append(row)
            cut = now - HISTORY_S
            obs[:] = [o for o in obs[-MAX_OBS_PER_SLUG:]
                      if (xfer.st._ts(o) or 0) >= cut]
            polled += 1
        _STATE["cursor"] = (cur + polled) % len(targets)
    # 3. recorder-shaped spring_watch rows over the observed history
    strict_rows = xfer.strict_rows_from_sports(by_slug, now)
    cats = replay.category_spring_evidence(strict_rows, now)
    watch = []
    for slug, obs in sorted(by_slug.items()):
        if not obs:
            continue
        last = obs[-1]
        ts = xfer.st._ts(last)
        if ts is None or now - ts > ROW_MAX_AGE_S:
            continue
        try:
            bid = D(str(last.get("bid")))
            ask = D(str(last.get("ask")))
        except Exception:                               # noqa: BLE001
            continue
        if not (session.SPRING_BAND_LO <= bid <= session.SPRING_BAND_HI):
            continue
        if last.get("ask_book_missing") or last.get("bid_touch_qty") is None:
            continue
        action = _probe_entry_action(bid, ask)
        ev = xfer.compose_evidence(slug, by_slug, now,
                                   off_distribution=xfer._verdict(action),
                                   strict_rows=strict_rows, cats=cats)
        if ev is None:
            continue
        row = dict(ev)
        row.update({
            "bid": last.get("bid"), "ask": last.get("ask"),
            "bid_qty": last.get("bid_touch_qty"),
            "ask_qty": last.get("ask_touch_qty"),
            "bid_touch_qty": last.get("bid_touch_qty"),
            "both_sides_served": True,
            "last_update_age_s": round(now - ts, 1),
            "collector_gaps": 0,
            "league": last.get("league"),
            "event_state": last.get("event_state"),
        })
        if action is not None:
            row["probe_entry_action"] = action
        if lim.left > 0:
            state = _market_state(slug, now, lim)
        else:
            hit = (_STATE.get("mstate") or {}).get(slug)
            state = hit[1] if hit and now - hit[0] < MARKET_STATE_TTL_S \
                else None
        if state is not None:
            row["market_state"] = state
        watch.append(row)
    doc = {
        "t": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "source": "gateway",
        "evidence_provenance": PROVENANCE,
        "evidence_source": SOURCE,
        "labeling": ("public gateway; trade direction INFERRED; fills and "
                     "economics THEORETICAL"),
        "spring_watch": watch,
        "gateway_diag": {**diag, "targets": len(targets), "polled": polled,
                         "requests": lim.n, "errors": lim.errors[:10],
                         "tracked": len(by_slug), "rows": len(watch),
                         "rps_cap": _rps(), "max_req": _max_req()},
    }
    _STATE["doc"], _STATE["doc_at"] = doc, now
    return doc
