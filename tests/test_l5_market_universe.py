"""Market-agnostic multi-venue enumeration.

WHAT THESE PIN. The universe feeds the SAME frozen habitat scorer that PMUS
and Gemini 5m already use, so an enumeration bug does not show up as a crash
-- it shows up as a cohort that looks better than it is. The three ways that
can happen are each pinned here: a clock read from the wrong field, a missing
value imputed instead of marked UNKNOWN, and a taxonomy guessed instead of
taken from the venue.
"""
import json

from src import l5_market_universe as U


# ------------------------------------------------------------------ Gemini

def _gem_event(**kw):
    base = {
        "id": "1", "ticker": "NFL-2609150015-DEN-KC-M",
        "title": "Denver vs Kansas City", "category": "Sports",
        "subcategory": {"id": 12, "slug": "sports_pro-football",
                        "name": "Pro Football",
                        "path": ["Sports", "Pro Football"]},
        "sportsMarket": {"sport": "american_football", "type": "moneyline"},
        "sourceDetails": {"agency": "statscore-rest-api"},
        "termsLink": "https://example.invalid/terms.pdf",
        "isLive": True, "startTime": "2026-09-15T00:15:00.000Z",
        "expiryDate": "2026-09-15T18:15:00.000Z",
        "volume": "22315.55", "volume24h": "16301.24",
        "contracts": [
            {"instrumentSymbol": "GEMI-NFL-2609150015-DEN-KC-M-KC",
             "label": "Kansas City", "marketState": "open"},
            {"instrumentSymbol": "GEMI-NFL-2609150015-DEN-KC-M-DEN",
             "label": "Denver", "marketState": "open"}],
    }
    base.update(kw)
    return base


def test_gemini_emits_one_record_per_contract_linked_as_a_pair():
    """The two contracts of a binary event ARE the complement pair. Emitting
    one record per event would make cross-contract pair economics
    unmeasurable."""
    recs = U.gemini_markets([_gem_event()], now=0.0)
    assert len(recs) == 2
    a, b = recs
    assert a["event_id"] == b["event_id"]
    assert a["is_binary_pair"] and a["pair_size"] == 2
    assert set(a["pair_symbols"]) == {"GEMI-NFL-2609150015-DEN-KC-M-KC",
                                      "GEMI-NFL-2609150015-DEN-KC-M-DEN"}
    assert a["market_id"] == a["market_id"].upper()


def test_gemini_takes_the_venues_own_taxonomy_not_ours():
    r = U.gemini_markets([_gem_event()], now=0.0)[0]
    assert r["category_path"] == ["Sports", "Pro Football"]
    assert r["sport"] == "american_football"
    assert r["market_type"] == "moneyline"
    assert r["settlement_agency"] == "statscore-rest-api"
    assert r["settlement_terms_url"].endswith(".pdf")


def test_gemini_crypto_windows_go_through_the_same_path_as_sports():
    """A separate crypto path would let the two cohorts drift apart. They are
    enumerated by one function and separated only by cohort."""
    ev = _gem_event(id="9", ticker="BTC05M2609141455", category="Crypto",
                    subcategory=None, sportsMarket=None,
                    contracts=[{"instrumentSymbol": "GEMI-BTC05M2609141455-UP",
                                "label": "Up", "marketState": "open"}])
    r = U.gemini_markets([ev], now=0.0)[0]
    assert r["asset"] == "BTC" and r["window_minutes"] == 5
    assert r["market_type"] == "crypto_window"
    assert U.cohort_of(r) == "GEMINI_BTC_05M"


def test_gemini_btc_and_eth_five_minute_stay_separate_cohorts():
    for asset, want in (("BTC", "GEMINI_BTC_05M"), ("ETH", "GEMINI_ETH_05M")):
        ev = _gem_event(id=asset, ticker=f"{asset}05M2609141455",
                        category="Crypto", subcategory=None, sportsMarket=None,
                        contracts=[{"instrumentSymbol": f"GEMI-{asset}05M-UP",
                                    "label": "Up", "marketState": "open"}])
        assert U.cohort_of(U.gemini_markets([ev], now=0.0)[0]) == want


def test_gemini_unpublished_fields_are_unknown_not_defaulted():
    """Gemini publishes tickSize as null. A default of 0.01 here would flow
    into the missing-data penalty and flatter the venue."""
    r = U.gemini_markets([_gem_event()], now=0.0)[0]
    assert r["tick_size"] == U.UNKNOWN
    assert r["taker_fee"] == U.UNKNOWN
    assert r["maker_rebate"] == U.UNKNOWN
    assert r["liquidity_rewards"] == U.UNKNOWN


def test_gemini_missing_source_details_yields_unknown_agency():
    r = U.gemini_markets([_gem_event(sourceDetails=None, termsLink=None)],
                         now=0.0)[0]
    assert r["settlement_agency"] == U.UNKNOWN
    assert r["settlement_terms_url"] == U.UNKNOWN


def test_gemini_pagination_surfaces_a_partial_enumeration():
    """A failure mid-page must NOT look like a small venue."""
    calls = {"n": 0}

    def get_json(path, params):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"data": [_gem_event(id=str(i)) for i in range(U.GEMINI_PAGE)]}
        raise RuntimeError("boom")

    rows, diag = U.gemini_events(get_json)
    assert len(rows) == U.GEMINI_PAGE
    assert diag["error"].startswith("RuntimeError")
    assert diag["complete"] is False


def test_gemini_pagination_dedupes_and_stops_on_a_short_page():
    def get_json(path, params):
        off = int(dict(params)["offset"])
        if off == 0:
            return {"data": [_gem_event(id=str(i)) for i in range(U.GEMINI_PAGE)]}
        return {"data": [_gem_event(id=str(i)) for i in range(5)]}   # overlap

    rows, diag = U.gemini_events(get_json)
    assert len(rows) == U.GEMINI_PAGE          # 5 were duplicates
    assert diag["error"] is None and diag["complete"] is True


# -------------------------------------------------------------------- PMUS

def _pmus_event(**kw):
    base = {
        "id": "105764", "ticker": "vkl-int-vps-2026-09-14",
        "title": "FC Inter Turku vs. Vaasan Palloseura", "category": "sports",
        "startTime": "2026-09-14T15:00:00Z", "endDate": "2026-09-14T23:59:00Z",
        "live": True, "seriesSlug": "vkl",
        "eventState": {"type": "soccer", "period": "4'", "score": "0-0"},
        "markets": [{
            "id": "718746", "slug": "atc-vkl-int-vps-2026-09-14-int",
            "question": "Will FC Inter Turku win?",
            "titleShort": "INT (Reg. Time)",
            "endDate": "2026-09-28T15:00:00Z",          # two-week BACKSTOP
            "gameStartTime": "2026-09-14T15:00:00Z",
            "active": True, "closed": False, "ep3Status": "OPEN",
            "status": "MARKET_STATUS_OPEN",
            "sportsMarketType": "soccer_team_full_time_winner",
            "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01,
            "feeCoefficient": 0.06,
            "description": "This market will settle to the winner ... "
                           "Outcome sourced from SPL.",
            "bestBidQuote": {"value": "0.6000", "currency": "USD"},
            "bestAskQuote": {"value": "0.6100", "currency": "USD"},
            "marketSides": [{"description": "Yes", "price": "0.6000",
                             "team": {"name": "FC Inter Turku",
                                      "league": "vkl"}}],
        }],
    }
    base.update(kw)
    return base


def test_pmus_never_reads_the_postponement_backstop_as_the_expiry():
    """market.endDate is two weeks out by design. Reading it as the expiry
    makes a live in-play match look like a 14-day market, which would
    flatter tte_p50_s -- the one habitat feature scored as a REGIME."""
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["expiry_source"] == "event.endDate"
    # event end 23:59Z vs market end 2026-09-28: 327 hours apart
    assert r["settlement_deadline_t"] - r["expiry_t"] > 300 * 3600
    assert r["tte_s"] == r["expiry_t"]                  # now=0


def test_pmus_tte_is_marked_not_scoreable_rather_than_quietly_used():
    """Every live market on a day shares one 23:59Z boundary, so the number
    is an upper bound, not a settlement instant."""
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["tte_scoreable"] is False
    assert r["tte_quality"] == "EVENT_DAY_BOUNDARY_UPPER_BOUND"


def test_pmus_reads_the_published_tick_and_fee_not_a_vendored_constant():
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["tick_size"] == 0.01
    assert r["tick_source"] == "market.orderPriceMinTickSize"
    assert r["fee_coefficient"] == 0.06
    assert r["min_trade_qty"] == 0.01


def test_pmus_quote_value_key_is_read_and_size_stays_unknown():
    """The feed carries `{"value": ...}` and NO size. Depth is a scored
    input, so it must be UNKNOWN here rather than zero."""
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["bbo_bid"] == 0.60 and r["bbo_ask"] == 0.61
    assert r["bbo_spread"] == 0.01
    assert r["bbo_bid_sz"] == U.UNKNOWN and r["bbo_ask_sz"] == U.UNKNOWN


def test_pmus_sport_and_league_come_from_the_venue_not_a_guess():
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["sport"] == "soccer"
    assert r["league"] == "vkl"
    assert U.cohort_league(r) == "PMUS_soccer/vkl"
    assert r["period"] == "4'" and r["score"] == "0-0"


def test_pmus_settlement_agency_is_extracted_and_absent_means_unknown():
    r = U.pmus_markets([_pmus_event()], now=0.0)[0]
    assert r["settlement_agency"] == "SPL"
    ev = _pmus_event()
    ev["markets"][0]["description"] = "no agency sentence here"
    assert U.pmus_markets([ev], now=0.0)[0]["settlement_agency"] == U.UNKNOWN


def test_pmus_non_open_market_is_not_tradeable():
    ev = _pmus_event()
    ev["markets"][0]["ep3Status"] = "HALTED"
    assert U.pmus_markets([ev], now=0.0)[0]["tradeable"] is False


# ------------------------------------------------------------------ census

def test_summarize_reports_missing_data_rather_than_hiding_it():
    recs = U.gemini_markets([_gem_event()], now=0.0) + \
           U.pmus_markets([_pmus_event()], now=0.0)
    s = U.summarize(recs)
    assert s["by_venue"] == {"GEMINI": 2, "PMUS": 1}
    # Gemini's two contracts have no tick size; PMUS's does
    assert s["unknown_field_counts"]["tick_size"] == 2
    assert "GEMINI_american_football" in s["by_cohort"]
    assert "PMUS_soccer" in s["by_cohort"]


def test_universe_is_read_only_no_order_path():
    import inspect
    src = inspect.getsource(U)
    for forbidden in ("place_order", "new_order", "cancel_order",
                      "submit_order", "private_key", "api_secret"):
        assert forbidden not in src, forbidden
