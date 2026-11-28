"""MARKET-AGNOSTIC L5 UNIVERSE: enumerate eligible markets on every venue.

OWNER 5556354536: "stop forcing L5 into whichever markets we happen to be
watching and instead make the system continuously find the sports/venues
where L5's mechanics naturally occur."

THIS IS DISCOVERY, NOT A STRATEGY. There is no sports strategy here and no
sport-specific tuning. This module answers one question -- *what exists, and
what do we know about it before any observation* -- and hands a normalized
record to the SAME frozen habitat scorer and the SAME canonical L5 shadow
that PMUS and Gemini 5m already use.

READ ONLY: GET requests only, no credentials, no order path, no writes.

WHAT IS CAPTURED HERE (the pre-observation half of the owner's field list):
venue, category/sport, event/market identity, start/settlement time and TTE,
tick size, fees/rebates/rewards, and settlement rules + provenance. The
observation half -- BBO, spread, depth, prints/flow, movement, oscillation,
two-sided activity, price-band coverage -- comes from the collectors and is
joined downstream by market_id.

UNKNOWN IS PRESERVED, NEVER IMPUTED. A venue that does not publish its tick
size, its rebate schedule or its settlement agency yields UNKNOWN for that
field. A favourable default here would flow straight into the habitat score's
missing-data penalty and flatter the venue.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

UNKNOWN = "UNKNOWN"

GEMINI_REST = "https://api.gemini.com"
GEMINI_EVENTS = "/v1/prediction-markets/events"
GEMINI_PAGE = 500
GEMINI_MAX_PAGES = 8

# Gemini crypto window tickers, e.g. BTC05M2609141455. Kept so the crypto
# cohort is enumerated by the SAME code path as sports rather than a fork.
GEMINI_CRYPTO_RE = re.compile(r"^(BTC|ETH)(05|15)M(\d{10})$")


# --------------------------------------------------------------- venue facts
#
# Stated facts only. Each carries its own provenance so a downstream reader
# can tell a vendored measurement from an assumption -- and there are no
# assumptions: what is not established is UNKNOWN.
VENUE_FACTS: Dict[str, Dict[str, Any]] = {
    "PMUS": {
        "tick_size": 0.01,
        "tick_source": "vendored: polymarket_l5.transfer_backtest.TICK",
        "maker_fee": "0.00 base; see fee_coefficient per market",
        "taker_fee": UNKNOWN,
        "maker_rebate": "credited and source-accounted per fill; $8.47 over "
                        "670 fills to date (Ledger C)",
        "liquidity_rewards": "PAID rewards observed; accrued rewards EXCLUDED "
                             "until credited",
        "fee_source": "venue-confirmed fills + incentive_audit rows",
        "settlement_authority": "venue position_resolution_activity",
        "settlement_source": "authenticated per-market settlement row",
    },
    "GEMINI": {
        "tick_size": UNKNOWN,          # not published per contract; infer from book
        "tick_source": "NOT PUBLISHED per contract (tickSize null on the "
                       "events feed); must be inferred from observed prices",
        "maker_fee": "vendored ceil-cent maker fee "
                     "(polymarket_l5.transfer_backtest.maker_fee)",
        "taker_fee": UNKNOWN,          # explicitly not vendored
        "maker_rebate": UNKNOWN,
        "liquidity_rewards": UNKNOWN,
        "fee_source": "vendored maker fee only; taker and rebates UNVERIFIED",
        "settlement_authority": "per-event sourceDetails.agency + termsLink",
        "settlement_source": "gemini prediction-markets events feed",
    },
}


def _epoch(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _sport_path(row: Mapping[str, Any]) -> List[str]:
    """Category path, e.g. ['Sports','Soccer','EPL']. The venue's own
    taxonomy is used rather than one of ours, so cohorts are auditable."""
    sub = row.get("subcategory")
    if isinstance(sub, Mapping) and isinstance(sub.get("path"), list):
        return [str(x) for x in sub["path"]]
    cat = row.get("category")
    return [str(cat)] if cat else []


# ------------------------------------------------------------------- Gemini

def gemini_events(get_json, category: Optional[str] = None,
                  max_pages: int = GEMINI_MAX_PAGES) -> tuple[list, dict]:
    """Every ACTIVE Gemini event, paged. Returns (rows, diagnostics).

    Pagination is bounded and a short page ends it; a failure returns what
    was gathered with the error SURFACED, so a partial enumeration degrades
    visibly instead of looking like a small venue."""
    rows: List[dict] = []
    seen: set = set()
    pages, err = 0, None
    for page in range(max_pages):
        params = [("status", "active"), ("limit", str(GEMINI_PAGE)),
                  ("offset", str(page * GEMINI_PAGE))]
        if category:
            params.append(("category", category))
        try:
            data = (get_json(GEMINI_EVENTS, params) or {}).get("data") or []
        except Exception as exc:                      # noqa: BLE001
            err = f"{type(exc).__name__}:{str(exc)[:120]}"
            break
        pages += 1
        new = 0
        for r in data:
            k = str(r.get("id"))
            if k not in seen:
                seen.add(k)
                rows.append(r)
                new += 1
        if len(data) < GEMINI_PAGE or new == 0:
            break
    return rows, {"pages": pages, "error": err,
                  "complete": err is None and pages < max_pages}


def gemini_markets(rows: List[Mapping[str, Any]],
                   now: Optional[float] = None) -> List[dict]:
    """One record per tradeable CONTRACT (not per event).

    A Gemini binary event carries two contracts -- the two sides of the same
    question -- and they are the natural complement pair. Both are emitted,
    linked by `event_id` and `pair_symbols`, so pair economics can be
    measured across the real complement instead of being derived from one
    contract's own ask.
    """
    now = now if now is not None else _now()
    out: List[dict] = []
    for r in rows:
        ticker = str(r.get("ticker") or "")
        cm = GEMINI_CRYPTO_RE.match(ticker.upper())
        sm = r.get("sportsMarket") if isinstance(r.get("sportsMarket"),
                                                 Mapping) else {}
        path = _sport_path(r)
        exp_t = _epoch(r.get("expiryDate"))
        start_t = _epoch(r.get("startTime"))
        contracts = [c for c in (r.get("contracts") or [])
                     if isinstance(c, Mapping)]
        syms = [str(c.get("instrumentSymbol")) for c in contracts
                if c.get("instrumentSymbol")]
        for c in contracts:
            sym = c.get("instrumentSymbol")
            if not sym:
                continue
            state = str(c.get("marketState") or c.get("status") or "").lower()
            c_exp = _epoch(c.get("expiryDate")) or exp_t
            out.append({
                "venue": "GEMINI",
                "market_id": str(sym).upper(),
                "event_id": str(r.get("id")),
                "event_ticker": ticker,
                "event_title": r.get("title"),
                "outcome_label": c.get("label"),
                "category": str(r.get("category") or UNKNOWN),
                "category_path": path,
                "sport": (sm.get("sport") if sm else None)
                         or (path[1] if len(path) > 1 else None) or UNKNOWN,
                "market_type": (sm.get("type") if sm else None)
                               or ("crypto_window" if cm else UNKNOWN),
                "cohort": ("GEMINI_CRYPTO_%sM" % cm.group(2) if cm
                           else "GEMINI_%s" % str(r.get("category") or "OTHER").upper()),
                "asset": cm.group(1) if cm else None,
                "window_minutes": int(cm.group(2)) if cm else None,
                "is_live": bool(r.get("isLive")),
                "state": state,
                "tradeable": state in ("open", "active"),
                "start_t": start_t,
                "expiry_t": c_exp,
                "tte_s": (None if c_exp is None else c_exp - now),
                "duration_s": (None if (c_exp is None or start_t is None)
                               else c_exp - start_t),
                "pair_symbols": [s.upper() for s in syms],
                "pair_size": len(syms),
                "is_binary_pair": len(syms) == 2,
                "volume": r.get("volume"),
                "volume_24h": r.get("volume24h"),
                "tick_size": VENUE_FACTS["GEMINI"]["tick_size"],
                "tick_source": VENUE_FACTS["GEMINI"]["tick_source"],
                "settlement_agency": ((r.get("sourceDetails") or {})
                                      .get("agency") if isinstance(
                                          r.get("sourceDetails"), Mapping)
                                      else None) or UNKNOWN,
                "settlement_terms_url": r.get("termsLink") or UNKNOWN,
                "settlement_source": VENUE_FACTS["GEMINI"]["settlement_source"],
                "maker_fee": VENUE_FACTS["GEMINI"]["maker_fee"],
                "taker_fee": VENUE_FACTS["GEMINI"]["taker_fee"],
                "maker_rebate": VENUE_FACTS["GEMINI"]["maker_rebate"],
                "liquidity_rewards": VENUE_FACTS["GEMINI"]["liquidity_rewards"],
            })
    return out


# --------------------------------------------------------------------- PMUS

def _pmus_sport(ev: Mapping[str, Any]) -> tuple:
    """(sport, league) from the venue's own taxonomy, never guessed.

    `eventState.type` is the sport; the league is the deepest subtag that
    actually carries a `league` object. A tag hierarchy with no league
    yields UNKNOWN rather than the sport repeated as its own league.
    """
    st = ev.get("eventState")
    sport = (str(st.get("type")) if isinstance(st, Mapping) and st.get("type")
             else None)
    league = None
    for tag in (ev.get("tags") or []):
        if not isinstance(tag, Mapping):
            continue
        sp = tag.get("sport")
        if isinstance(sp, Mapping) and sp.get("name") and not sport:
            sport = str(sp["name"]).lower()
        for sub in (tag.get("subtags") or []):
            if isinstance(sub, Mapping) and isinstance(sub.get("league"),
                                                       Mapping):
                nm = sub["league"].get("name")
                # the event's own series slug picks the right league out of a
                # sport tag that lists every league it has
                if nm and str(sub.get("slug") or "").lower() == \
                        str(ev.get("seriesSlug") or "").lower():
                    league = str(nm)
    return (sport or UNKNOWN, league or UNKNOWN)


def _pmus_league_from_sides(m: Mapping[str, Any]) -> Optional[str]:
    """League slug straight off the market's own sides, e.g. "vkl". More
    reliable than walking the event's tag tree, which lists every league a
    sport has rather than this event's."""
    for side in (m.get("marketSides") or []):
        if isinstance(side, Mapping) and isinstance(side.get("team"), Mapping):
            lg = side["team"].get("league")
            if lg:
                return str(lg)
    return None


def _quote(q: Any) -> Optional[float]:
    """Price from an embedded best-quote object `{"value": "0.6000", ...}`.

    THE FEED CARRIES NO SIZE. Depth at the touch is therefore UNKNOWN from
    discovery alone and must come from the collector's book call -- it is
    NOT defaulted to zero or to anything else, because depth is a scored
    input downstream.
    """
    if not isinstance(q, Mapping):
        return None
    v = q.get("value", q.get("price", q.get("p")))
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def pmus_markets(events: List[Mapping[str, Any]],
                 now: Optional[float] = None) -> List[dict]:
    """One record per tradeable PMUS market, from the venue's authoritative
    live-events set (`/v1/events?live=true&active=true&closed=false`).

    THREE CLOCKS, KEPT APART, because collapsing them corrupts the one
    habitat feature that is scored as a REGIME rather than a capacity:
      game_start_t         market.gameStartTime -- when play begins
      expiry_t             event.endDate -- the event's own settlement horizon
      settlement_deadline_t  market.endDate -- the venue's POSTPONEMENT
                           BACKSTOP, routinely two weeks out. Reading this as
                           the expiry makes a live in-play match look like a
                           14-day market and would flatter tte_p50_s beyond
                           recognition.
    """
    now = now if now is not None else _now()
    out: List[dict] = []
    for ev in events:
        sport, league = _pmus_sport(ev)
        cat = str(ev.get("category") or "sports")
        start_t = _epoch(ev.get("startTime") or ev.get("startDate"))
        ev_end_t = _epoch(ev.get("endDate"))
        state = ev.get("eventState") if isinstance(ev.get("eventState"),
                                                   Mapping) else {}
        for m in (ev.get("markets") or []):
            if not isinstance(m, Mapping):
                continue
            slug = m.get("slug") or m.get("id")
            if not slug:
                continue
            closed = bool(m.get("closed")) or str(
                m.get("status") or "").upper().endswith("RESOLVED")
            game_t = _epoch(m.get("gameStartTime")) or start_t
            deadline_t = _epoch(m.get("endDate"))
            bid, ask = _quote(m.get("bestBidQuote")), _quote(m.get("bestAskQuote"))
            lg = _pmus_league_from_sides(m) or league
            tick = m.get("orderPriceMinTickSize")
            out.append({
                "venue": "PMUS",
                "market_id": str(slug),
                "event_id": str(ev.get("id") or ev.get("slug") or UNKNOWN),
                "event_ticker": ev.get("ticker") or ev.get("slug"),
                "event_title": ev.get("title"),
                "outcome_label": m.get("titleShort") or m.get("title")
                                 or m.get("question"),
                "category": cat,
                "category_path": ["Sports", sport, lg],
                "sport": sport,
                "league": lg,
                "market_type": str(m.get("sportsMarketType")
                                   or m.get("marketType") or UNKNOWN),
                "cohort": "PMUS_%s" % cat.upper(),
                "asset": None, "window_minutes": None,
                "is_live": bool(ev.get("live")),
                "period": state.get("period"), "score": state.get("score"),
                "state": str(m.get("status") or "").lower(),
                "tradeable": not closed and bool(m.get("active", True))
                             and str(m.get("ep3Status") or "").upper() == "OPEN",
                "start_t": game_t,
                "expiry_t": ev_end_t,
                "expiry_source": "event.endDate",
                "settlement_deadline_t": deadline_t,
                "settlement_deadline_source": "market.endDate "
                                              "(postponement backstop)",
                "tte_s": (None if ev_end_t is None else ev_end_t - now),
                # event.endDate is a DAY BOUNDARY (23:59Z), not a settlement
                # instant: every live market on a given day reports the same
                # TTE. tte_p50_s is the one habitat feature scored as a
                # REGIME, so feeding it a known-wrong number would distort the
                # comparison in a way a capacity feature would not. It is
                # marked NOT scoreable and takes the missing-data penalty,
                # which is the standing rule -- preserve UNKNOWN, never impute.
                "tte_quality": "EVENT_DAY_BOUNDARY_UPPER_BOUND",
                "tte_scoreable": False,
                "duration_s": (None if (ev_end_t is None or game_t is None)
                               else ev_end_t - game_t),
                "pair_symbols": [str(slug)],
                "pair_size": 2,          # YES/NO on one slug
                "is_binary_pair": True,
                "volume": m.get("volume"), "volume_24h": m.get("volume24hr"),
                # LIVE BBO, embedded in the venue's own discovery response --
                # a free pre-observation snapshot, not a substitute for the
                # collector's time series.
                "bbo_bid": bid, "bbo_bid_sz": UNKNOWN,
                "bbo_ask": ask, "bbo_ask_sz": UNKNOWN,
                "bbo_spread": (None if bid is None or ask is None
                               else round(ask - bid, 6)),
                "bbo_source": "events feed bestBidQuote/bestAskQuote "
                              "(price only; NO SIZE -- depth needs the book)",
                # PUBLISHED PER MARKET -- better than the vendored constant.
                "tick_size": (float(tick) if tick is not None
                              else VENUE_FACTS["PMUS"]["tick_size"]),
                "tick_source": ("market.orderPriceMinTickSize" if tick is not None
                                else VENUE_FACTS["PMUS"]["tick_source"]),
                "min_trade_qty": m.get("minimumTradeQty"),
                "fee_coefficient": m.get("feeCoefficient"),
                "settlement_agency": str(m.get("description") or "")
                                     .split("Outcome sourced from ")[-1]
                                     .strip().rstrip(".")
                                     if "Outcome sourced from " in str(
                                         m.get("description") or "")
                                     else UNKNOWN,
                "settlement_terms_url": m.get("resolutionSource") or UNKNOWN,
                "settlement_source": VENUE_FACTS["PMUS"]["settlement_source"],
                "settlement_rules": str(m.get("description") or "")[:400]
                                    or UNKNOWN,
                "maker_fee": VENUE_FACTS["PMUS"]["maker_fee"],
                "taker_fee": VENUE_FACTS["PMUS"]["taker_fee"],
                "maker_rebate": VENUE_FACTS["PMUS"]["maker_rebate"],
                "liquidity_rewards": VENUE_FACTS["PMUS"]["liquidity_rewards"],
            })
    return out


# ------------------------------------------------------------------ cohorts

def cohort_of(rec: Mapping[str, Any]) -> str:
    """The ranking cohort: venue x sport. Crypto windows keep their asset so
    Gemini BTC5m and Gemini ETH5m stay separate cohorts, as the owner's
    comparison requires."""
    if rec.get("asset") and rec.get("window_minutes"):
        return "GEMINI_%s_%02dM" % (rec["asset"], rec["window_minutes"])
    return "%s_%s" % (rec.get("venue"), str(rec.get("sport") or UNKNOWN))


def cohort_league(rec: Mapping[str, Any]) -> str:
    """venue x sport x league, the finest ranking cohort."""
    base = cohort_of(rec)
    lg = rec.get("league")
    return base if not lg or lg == UNKNOWN else "%s/%s" % (base, lg)


def summarize(records: List[Mapping[str, Any]]) -> dict:
    """Enumeration census. Counts only -- no score, no ranking, no judgement."""
    import collections
    tradeable = [r for r in records if r.get("tradeable")]
    by_venue = collections.Counter(r["venue"] for r in tradeable)
    by_cat = collections.Counter(
        "%s/%s" % (r["venue"], r.get("category")) for r in tradeable)
    by_cohort = collections.Counter(cohort_of(r) for r in tradeable)
    unknown_fields = collections.Counter()
    for r in tradeable:
        for k in ("tick_size", "settlement_agency", "taker_fee",
                  "maker_rebate", "liquidity_rewards"):
            if r.get(k) == UNKNOWN:
                unknown_fields[k] += 1
    return {
        "records": len(records), "tradeable": len(tradeable),
        "by_venue": dict(by_venue),
        "by_venue_category": dict(by_cat.most_common()),
        "by_cohort": dict(by_cohort.most_common()),
        # MISSING-DATA IS REPORTED, NOT IMPUTED.
        "unknown_field_counts": dict(unknown_fields),
    }
