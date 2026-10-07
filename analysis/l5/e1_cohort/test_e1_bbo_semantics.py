"""THE BBO SEMANTICS, PINNED TO A REAL PMUS RESPONSE.

WHAT THIS CLOSES. E1's window-3 attempt recorded
`price_source: PMUS_BEST_BID, price_sent: 0.1300` while a
`"value": "0.4900"` also sat in the same payload. The first leg's price IS
that parse, so until the semantics were established from venue data no E1
decision could honestly depend on it.

THE FIXTURE IS REAL. `fixtures/pmus_bbo_2026-10-07T2229Z.json` holds the
verbatim `GET /v1/markets/{slug}/bbo` body and AuthBook's own recorded
frame shapes for `cpc-btc-updown-15m-2026-10-07-2215z`, captured read-only
by `scripts/l5_e1_bbo_semantics_probe.py` on deployment dd7583fb. Nothing
in it was altered; nothing was removed (no credential or header was ever
in those bodies).

THE THREE FACTS IT ESTABLISHES, FROM THE VENUE AND NOT FROM FIELD NAMES.

  1. THE PMUS AUTHENTICATED WEBSOCKET CARRIES NO bestBid/bestAsk AT ALL.
     Every book frame's keys are exactly
     ["bids","marketSlug","offers","state","stats","transactTime"], with
     `bestBid: null` and `bestAsk: null`. `bestBid` exists only on the
     REST /bbo route. So over the websocket -- E1's decision feed -- the
     price ALWAYS comes from the LADDER, `bids[0].px.value`. Any belief
     that E1 reads a "best bid field" from its feed is simply false.

  2. FOR THAT PAYLOAD levels[0] IS THE BEST. WS `bid0.px.value` is
     "0.9900" and REST `bestBid.value` is "0.9900" -- the two independent
     routes agree, so the ladder is ordered best-first for bids.

  3. THE SAME PAYLOAD CARRIES NO-DENOMINATED VALUES BESIDE THE YES ONES:
     `shortQuote.value` "0.01" and `lastPriceSample.shortPx` "0.01"
     against `longPx` "0.9900". That is the shape in which a loose parse
     picks up a number from the wrong side of the market and calls it the
     YES bid, so the tests below require the chosen value to come from a
     NAMED path and nowhere else.

AND THE TRANSFORMATIONS, which the proven order lineage fixes rather than
these tests inventing: `l5_order_body.venue_price` encodes that
`price.value` is ALWAYS the long (YES) side's price -- to buy NO at X the
wire value is 1.00 - X -- learned when 24 of 24 NO-side post-only orders
priced in NO terms were rejected live on 2026-09-05/06.

    YES buy maker price  = YES best bid            wire = the same number
    NO  buy maker price  = 1 - YES best ask        wire = YES best ask

Run:
    PYTHONPATH=. python3 -m pytest \
        analysis/l5/e1_cohort/test_e1_bbo_semantics.py -q
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import pmus_book as PBK
from src import l5_order_body as OB
from src import pmus_ws_evidence_recorder as R

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "pmus_bbo_2026-10-07T2229Z.json")

ONE = D("1")

#: the two numbers that motivated the whole investigation
SENT = "0.1300"
OTHER = "0.4900"


@pytest.fixture(scope="module")
def real():
    with open(FIXTURE, encoding="utf8") as fh:
        return json.load(fh)


# =============== 1. THE WS CARRIES NO BEST-FIELD AT ALL ================

def test_the_pmus_websocket_book_frame_has_no_best_bid_or_ask(real):
    """MEASURED. If this ever stops being true the parser's priority
    changes, so it is pinned rather than remembered."""
    shape = real["ws_book_frame_shape"]
    assert shape["keys"] == ["bids", "marketSlug", "offers", "state",
                             "stats", "transactTime"]
    assert shape["bestBid"] is None
    assert shape["bestAsk"] is None
    assert "bestBid" not in shape["keys"]
    assert "bestAsk" not in shape["keys"]


def test_the_rest_bbo_route_DOES_carry_the_best_fields(real):
    md = real["rest_bbo"]["marketData"]
    assert md["bestBid"] == {"value": "0.9900", "currency": "USD"}
    assert md["bestAsk"] is None            # one-sided, by then
    assert md["bidDepth"] == 29 and md["askDepth"] == 0


def test_the_two_routes_agree_on_the_best_bid(real):
    """The whole point: an independent route confirms the ladder's top."""
    ws_top = real["ws_book_frame_shape"]["bid0"]["px"]["value"]
    rest_best = real["rest_bbo"]["marketData"]["bestBid"]["value"]
    assert ws_top == rest_best == "0.9900"


# ============ 2. THE PARSER ON THE REAL FRAME SHAPE ====================

def _frame(slug, bids, offers, extra=None):
    """A frame with the venue's REAL key set."""
    f = {"marketSlug": slug, "state": "MARKET_STATE_OPEN",
         "transactTime": "2026-10-07T22:29:02Z",
         "bids": bids, "offers": offers,
         "stats": {"sharesTraded": "354010.4600"}}
    if extra:
        f.update(extra)
    return f


def _lvl(px, qty="10"):
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


def test_the_parser_reads_the_ladder_top_on_the_real_shape(real):
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, _frame(slug,
                              [_lvl("0.9900", "24597.4900"),
                               _lvl("0.9800")],
                              []), 1.0)
    assert st[slug]["bid"] == "0.9900"
    assert st[slug]["ask"] is None
    assert st[slug]["both_sides_served"] is False


def test_a_one_sided_real_book_yields_no_snapshot(real):
    """What the venue actually served, through E1's feed: no snapshot.
    This is the fail-closed path, on real data."""
    slug = real["provenance"]["slug"]

    class Book:
        def __init__(self):
            self.state = {}
        def start(self):
            pass
        def stop(self):
            pass
        def snapshot(self, s):
            st = self.state.get(s) or {}
            if st.get("bid") is None or st.get("ask") is None:
                return None
            return {"source": "authenticated_ws", "stream_age_s": 0.1,
                    "bid": st["bid"], "ask": st["ask"]}

    b = Book()
    R.apply_events(b.state, _frame(slug, [_lvl("0.9900")], []), 1.0)
    feed = PBK.PmusDecisionFeed(clock=lambda: 1.0, book=b)
    feed.start()
    h = feed.health({"slug": slug})
    assert h["ok"] is False
    assert h["why"] == "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"
    assert real["ws_feed_state"]["health_why"] == h["why"]


# ====== 3. 0.1300 AND 0.4900 CANNOT SILENTLY SELECT THE WRONG FIELD ====

def test_a_nested_object_cannot_replace_the_authoritative_ladder(real):
    """THE DEMONSTRATED DEFECT, with the two observed numbers.

    `stats` is a REAL key in PMUS book frames. Before the fix, a `bids`
    list anywhere inside the frame inherited the slug and became a second
    book event; `apply_events` folds in order, so the LAST one won and the
    authoritative top of book was replaced.
    """
    slug = real["provenance"]["slug"]
    poisoned = _frame(
        slug, [_lvl(SENT, "9"), _lvl("0.1200")], [_lvl("0.1400")],
        extra={"stats": {"sharesTraded": "1",
                         "bids": [_lvl(OTHER, "1")],
                         "offers": [_lvl(OTHER, "1")]}})
    st = {}
    R.apply_events(st, poisoned, 1.0)
    assert st[slug]["bid"] == SENT, "a nested object replaced the best bid"
    assert st[slug]["ask"] == "0.1400"
    assert st[slug]["bid"] != OTHER


@pytest.mark.parametrize("where", [
    "stats", "lastPriceSample", "marketMetadata", "extra",
])
def test_no_nesting_depth_lets_a_stray_level_win(real, where):
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, _frame(
        slug, [_lvl(SENT)], [_lvl("0.1400")],
        extra={where: {"deeper": {"bids": [_lvl(OTHER)],
                                  "offers": [_lvl(OTHER)]}}}), 1.0)
    assert st[slug]["bid"] == SENT, where
    assert st[slug]["ask"] == "0.1400", where


def test_a_no_denominated_value_in_the_payload_is_never_read_as_the_bid(
        real):
    """`shortQuote` and `lastPriceSample.shortPx` are NO-side prices in
    the SAME body. Neither may become the YES bid."""
    md = real["rest_bbo"]["marketData"]
    assert md["shortQuote"]["value"] == "0.01"
    assert md["lastPriceSample"]["shortPx"]["value"] == "0.01"
    # the proven lineage's reader takes bestBid and nothing else
    assert (md.get("bestBid") or {}).get("value") == "0.9900"
    # and the whole payload through the WS parser yields the ladder top,
    # never shortQuote -- these keys carry no bids/offers so they cannot
    # even become a book event
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, _frame(slug, [_lvl("0.9900")], [],
                              extra={"shortQuote": {"value": "0.01"},
                                     "lastPriceSample": {
                                         "longPx": {"value": "0.9900"},
                                         "shortPx": {"value": "0.01"}}}),
                   1.0)
    assert st[slug]["bid"] == "0.9900"


def test_the_real_marketData_wrapper_still_parses(real):
    """The fix must not break the shape the venue DOES send."""
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, {"marketData": _frame(slug, [_lvl("0.4000")],
                                             [_lvl("0.4200")])}, 1.0)
    assert st[slug]["bid"] == "0.4000"
    assert st[slug]["ask"] == "0.4200"


def test_a_trade_nested_in_a_market_object_is_still_collected(real):
    """The fix suppresses nested BOOKS only. A trade legitimately appears
    inside a market object and must survive."""
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, _frame(
        slug, [_lvl("0.4000")], [_lvl("0.4200")],
        extra={"trades": [{"price": {"value": "0.4200"}, "qty": "3",
                           "side": "BUY"}]}), 1.0)
    assert len(st[slug]["trades"]) == 1
    assert st[slug]["trades"][0]["px"] == "0.4200"
    assert st[slug]["bid"] == "0.4000"


def test_the_ladder_order_is_what_makes_levels_0_the_best(real):
    """If a ladder ever arrives ascending, `levels[0]` is the WORST bid.
    The real capture is descending; this pins that the parser's answer
    tracks level 0 so a future ordering change is VISIBLE as a failure
    here rather than as a bad price in production."""
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, _frame(slug,
                              [_lvl("0.9900"), _lvl("0.9800"),
                               _lvl("0.9700")], []), 1.0)
    assert st[slug]["bid"] == "0.9900"
    # and the real capture IS best-first
    assert real["ws_book_frame_shape"]["bid0"]["px"]["value"] == \
        real["rest_bbo"]["marketData"]["bestBid"]["value"]


# ============ 4. THE TWO TRANSFORMATIONS, AGAINST VENUE DATA ===========

def test_yes_buy_maker_price_is_the_yes_best_bid_and_the_wire_matches():
    yes_bid, yes_ask = D("0.40"), D("0.42")
    body = OB.build(slug="cpc-btc-updown-15m-2026-10-07-2215z", side="UP",
                    price=yes_bid, qty=D("0.13"), post_only=True)
    assert body["price"]["value"] == "0.40"
    assert body["outcomeSide"] == OB.outcome_side("UP")
    assert body["participateDontInitiate"] is True
    # a YES maker buy at the bid does not cross the ask
    assert yes_bid < yes_ask


def test_no_buy_maker_price_is_one_minus_the_yes_ask_and_the_wire_inverts():
    yes_bid, yes_ask = D("0.40"), D("0.42")
    comp = PBK.complement_of(str(yes_bid), str(yes_ask))
    no_bid = comp["bid"]
    assert no_bid == ONE - yes_ask == D("0.58")
    assert comp["ask"] == ONE - yes_bid == D("0.60")
    body = OB.build(slug="cpc-btc-updown-15m-2026-10-07-2215z",
                    side="DOWN", price=no_bid, qty=D("0.13"),
                    post_only=True)
    # THE WIRE VALUE IS THE LONG SIDE'S PRICE: 1 - 0.58 = 0.42 = YES ask
    assert body["price"]["value"] == "0.42"
    assert D(body["price"]["value"]) == yes_ask
    assert body["outcomeSide"] == OB.outcome_side("DOWN")
    assert OB.venue_price(OB.outcome_side("DOWN"), no_bid) == yes_ask


def test_the_complement_arithmetic_holds_on_the_real_payloads_numbers(
        real):
    """The capture is one-sided, so the complement of the ASK cannot be
    formed -- which is itself the correct answer, and is why E1 got no
    snapshot. What CAN be checked on the real numbers is the venue's own
    long/short pair in the same body."""
    md = real["rest_bbo"]["marketData"]
    long_px = D(md["lastPriceSample"]["longPx"]["value"])
    short_px = D(md["lastPriceSample"]["shortPx"]["value"])
    # the venue's own long/short sample is a complement pair
    assert long_px + short_px == ONE
    assert D(md["shortQuote"]["value"]) == ONE - long_px
    # and bestAsk being null is why no complement bid exists here
    assert md["bestAsk"] is None
    assert PBK.complement_of(md["bestBid"]["value"], None)["bid"] is None


def test_the_complement_is_uncrossed_whenever_the_source_book_is():
    for b, a in (("0.01", "0.02"), ("0.40", "0.42"), ("0.60", "0.61"),
                 ("0.98", "0.99")):
        c = PBK.complement_of(b, a)
        assert D(b) < D(a)
        assert c["bid"] < c["ask"]
        assert c["bid"] == ONE - D(a)
        assert c["ask"] == ONE - D(b)


def test_a_crossed_or_absent_quote_yields_no_complement():
    assert PBK.complement_of(None, "0.42")["ask"] is None
    assert PBK.complement_of("0.40", None)["bid"] is None
    for bad in ("NaN", "Infinity", "abc", ""):
        c = PBK.complement_of(bad, "0.42")
        assert c["ask"] is None


# ========= 5. THE LADDER ORDER, FROM THE REAL /book RESPONSE ===========

def test_the_real_bid_ladder_is_strictly_descending(real):
    """THE ORDERING QUESTION, ANSWERED BY THE VENUE.

    `_level()` takes `levels[0]` and calls it the best. Since the PMUS
    websocket carries no best-field, E1's entire first-leg price rests on
    that. The captured `GET /book` body has 29 bid levels and they are
    strictly descending, so levels[0] IS the best -- and it equals both
    other readings.
    """
    bids = real["rest_book"]["marketData"]["bids"]
    vals = [D(b["px"]["value"]) for b in bids]
    assert len(vals) == 29
    assert all(a > b for a, b in zip(vals, vals[1:])), vals
    assert vals[0] == max(vals) == D("0.9900")
    # three independent readings, one number
    assert str(vals[0]) == "0.9900"
    assert real["rest_bbo"]["marketData"]["bestBid"]["value"] == "0.9900"
    assert real["ws_book_frame_shape"]["bid0"]["px"]["value"] == "0.9900"
    f = real["ladder_facts"]
    assert f["bids_are_strictly_descending"] is True
    assert f["bids_level_0_equals_rest_bestBid"] is True
    assert f["bids_level_0_equals_ws_bid0"] is True


def test_the_offers_ladder_is_empty_in_the_capture(real):
    """One-sided, which is why no complement price exists and why E1
    correctly got no snapshot."""
    assert real["rest_book"]["marketData"]["offers"] == []
    assert real["rest_bbo"]["marketData"]["askDepth"] == 0
    assert real["rest_bbo"]["marketData"]["askShares"] == "0"


def test_the_parser_on_the_verbatim_real_book_body(real):
    """Feed the ACTUAL captured /book body through the ACTUAL parser."""
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, real["rest_book"], 1.0)
    assert st[slug]["bid"] == "0.9900"
    assert st[slug]["ask"] is None
    assert st[slug]["both_sides_served"] is False
    # and `stats` -- a real nested object carrying a NO-denominated
    # shortPx -- contributed nothing to the price
    assert st[slug]["bid"] != "0.01"


def test_a_mid_ladder_level_cannot_become_the_top(real):
    """The captured ladder contains 0.5000, 0.4000 and 0.2500. A parse
    that reached any of them would report a price the top of book never
    showed."""
    slug = real["provenance"]["slug"]
    st = {}
    R.apply_events(st, real["rest_book"], 1.0)
    for deeper in real["ladder_facts"][
            "levels_that_could_be_mistaken_for_the_top"]:
        assert st[slug]["bid"] != deeper, deeper
    assert st[slug]["bid"] == real["ladder_facts"]["bids_level_0_value"]


def test_stats_carries_a_no_denominated_price_in_the_real_body(real):
    """Recorded because it is the wrong-side value sitting closest to the
    right one."""
    stats = real["rest_book"]["marketData"]["stats"]
    assert stats["lastPriceSample"]["shortPx"]["value"] == "0.01"
    assert stats["lastPriceSample"]["longPx"]["value"] == "0.9900"
    assert (D(stats["lastPriceSample"]["longPx"]["value"])
            + D(stats["lastPriceSample"]["shortPx"]["value"])) == ONE
