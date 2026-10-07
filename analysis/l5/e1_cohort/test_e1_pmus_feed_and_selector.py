"""The PMUS decision feed and selector: identity, freshness, no CTF."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import pmus_book as PB
from analysis.l5.e1_cohort import pmus_selector as S

SLUG = "cpc-btc-updown-15m-2026-10-07-2100z"
WS = 1791406800


def market(**over):
    m = {"slug": SLUG, "active": True, "closed": False,
         "status": "MARKET_STATUS_OPEN", "orderPriceMinTickSize": 0.01,
         "minimumTradeQty": 0.01, "outcomes": '["Yes","No"]'}
    m.update(over)
    return m


def resolver(m=None):
    return lambda slug, http=None: (m if m is not None else market())


class FakeAuthBook:
    """Stands in for AuthBook with the SAME contract: snapshot() returns
    None unless connected and two-sided."""

    def __init__(self, snap=None):
        self._snap = snap
        self.started = False

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.started = False

    def snapshot(self, slug):
        if self._snap is None:
            return None
        return dict(self._snap, _slug=slug)


def snap(**over):
    s = {"bid": "0.40", "ask": "0.42", "bid_shares": "120",
         "ask_shares": "95", "stream_age_s": 0.4,
         "last_update_t": 1791406900.0, "book_updates": 31,
         "market_state": "MARKET_STATUS_OPEN",
         "source": "authenticated_ws"}
    s.update(over)
    return s


def feed(snapshot=None, *, start=True, clock=lambda: 1791406900.0):
    f = PB.PmusDecisionFeed(clock=clock, book=FakeAuthBook(snapshot))
    if start:
        f.start()
    return f


# ========================== SELECTOR: PMUS IDENTITY =====================

def test_the_pin_is_pmus_and_carries_no_token_ids():
    pin = S.select_window(WS, resolver=resolver())
    assert pin["slug"] == SLUG
    assert pin["series"] == "cpc-btc-updown-15m"
    assert pin["venue"] == "polymarket_us"
    assert pin["outcome_up"] == "OUTCOME_SIDE_YES"
    assert pin["outcome_down"] == "OUTCOME_SIDE_NO"
    assert pin["token_ids_in_pin"] is False
    assert "token_up" not in pin and "token_down" not in pin
    assert pin["selection_provenance"]["gamma_used"] is False
    assert pin["selection_provenance"]["clob_ws_used"] is False
    assert pin["window_start"] == WS and pin["window_end"] == WS + 900


def test_a_slug_the_venue_does_not_confirm_is_refused():
    other = market(slug="cpc-btc-updown-15m-2026-01-01-0000z")
    with pytest.raises(S.WindowRefused, match="identity fault"):
        S.select_window(WS, resolver=resolver(other))


def test_a_resolver_failure_refuses_rather_than_assuming():
    def boom(slug, http=None):
        raise LookupError("not served")
    with pytest.raises(S.WindowRefused, match="would not confirm"):
        S.select_window(WS, resolver=boom)


@pytest.mark.parametrize("over", [{"closed": True}, {"active": False}])
def test_a_closed_or_inactive_market_is_refused(over):
    with pytest.raises(S.WindowRefused):
        S.select_window(WS, resolver=resolver(market(**over)))


def test_an_off_grid_window_is_refused():
    with pytest.raises(S.WindowRefused, match="off the 900s venue grid"):
        S.select_window(WS + 7, resolver=resolver())


def test_is_selected_slug_is_exact():
    pin = S.select_window(WS, resolver=resolver())
    assert S.is_selected_slug(SLUG, pin) is True
    assert S.is_selected_slug(SLUG + "x", pin) is False
    assert S.is_selected_slug("btc-updown-15m-1791406800", pin) is False
    assert S.is_selected_slug(SLUG, None) is False


# ===================== FEED: ONE BOOK, TWO OUTCOME SIDES ================

def test_the_complement_is_the_venues_own_arithmetic():
    """NO bid = 1 - YES ask, NO ask = 1 - YES bid, exactly as
    pmus_q1_executor.would_cross states it."""
    c = PB.complement_of("0.40", "0.42")
    assert c["bid"] == D("0.58")
    assert c["ask"] == D("0.60")


@pytest.mark.parametrize("b,a", [(None, "0.42"), ("0.40", None),
                                 ("NaN", "0.42"), ("0.40", "Infinity"),
                                 ("", "0.42"), ("abc", "zzz")])
def test_an_unusable_quote_yields_none_not_a_guess(b, a):
    c = PB.complement_of(b, a)
    assert c["bid"] is None or c["ask"] is None


def test_the_book_names_the_slug_it_came_from():
    pin = S.select_window(WS, resolver=resolver())
    bk = feed(snap()).book(pin)
    assert bk["market"] == SLUG
    assert bk["first_leg"]["slug"] == SLUG
    assert bk["complement"]["slug"] == SLUG
    assert bk["first_leg"]["outcome_side"] == "OUTCOME_SIDE_YES"
    assert bk["complement"]["outcome_side"] == "OUTCOME_SIDE_NO"
    assert bk["first_leg"]["best_bid"] == "0.40"
    assert bk["complement"]["best_bid"] == "0.58"
    assert bk["first_leg"]["size_at_our_price"] == "120"
    assert bk["COMPLEMENT_IS_ARITHMETIC_NOT_A_SECOND_FEED"] is True


def test_the_feed_refuses_a_slug_that_is_not_the_pin():
    f = feed(snap())
    with pytest.raises(ValueError, match="not a PMUS BTC15m slug"):
        f.book({"slug": "btc-updown-15m-1791406800"})


# =========================== FEED: HEALTH ===============================

def test_a_healthy_book_is_ok_and_names_the_authority():
    pin = S.select_window(WS, resolver=resolver())
    h = feed(snap()).health(pin)
    assert h["ok"] is True
    assert h["timing_authority"] == PB.TIMING_PMUS_AUTHENTICATED_WS
    assert h["rest_is_sufficient_timing_authority"] is False
    assert h["ctf_token_ids_used"] is False
    assert h["market"] == SLUG
    assert h["proven_for_the_selected_market"] is True


def test_an_unstarted_feed_is_not_healthy():
    pin = S.select_window(WS, resolver=resolver())
    h = feed(snap(), start=False).health(pin)
    assert h["ok"] is False and h["why"] == "FEED_NOT_STARTED"


def test_no_connected_two_sided_book_is_not_healthy():
    """AuthBook returns None unless connected AND two-sided, so this is
    the empty/one-sided/disconnected case in one."""
    pin = S.select_window(WS, resolver=resolver())
    h = feed(None).health(pin)
    assert h["ok"] is False
    assert h["why"] == "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"


def test_a_stale_book_is_not_healthy():
    pin = S.select_window(WS, resolver=resolver())
    h = feed(snap(stream_age_s=PB.MAX_BOOK_AGE_S + 0.001)).health(pin)
    assert h["ok"] is False
    assert h["why"].startswith("BOOK_STALE_")


@pytest.mark.parametrize("age", [None, "soon", float("nan")])
def test_an_unusable_stream_age_is_not_healthy(age):
    pin = S.select_window(WS, resolver=resolver())
    h = feed(snap(stream_age_s=age)).health(pin)
    assert h["ok"] is False, h


def test_a_non_websocket_source_can_never_be_healthy():
    """THE NO-REST-FALLBACK PROOF. If a REST snapshot ever reached this
    wrapper it must be refused, not silently used as timing authority."""
    pin = S.select_window(WS, resolver=resolver())
    for src in ("rest", "/v1/markets/bbo", "cache", None):
        h = feed(snap(source=src)).health(pin)
        assert h["ok"] is False, src
        assert "SOURCE_IS_NOT_THE_AUTHENTICATED_WS" in h["why"], src


def test_health_never_raises_on_a_broken_book():
    class Broken:
        started = True

        def start(self):
            return self

        def snapshot(self, slug):
            raise RuntimeError("socket exploded")

    f = PB.PmusDecisionFeed(book=Broken())
    f.start()
    h = f.health({"slug": SLUG})
    assert h["ok"] is False
    assert h["why"].startswith("FEED_ERROR:")


def test_the_module_reuses_authbook_rather_than_reimplementing_it():
    """It must USE the proven book, not grow one. Scanned over CODE only --
    the docstrings legitimately discuss websockets at length, and judging
    a module by its prose is how the duplicate-serializer guard in this
    package learned to strip docstrings first."""
    import ast
    src = open("analysis/l5/e1_cohort/pmus_book.py").read()
    assert "from src import l5_pmus_auth_book as AB" in src
    assert "AB.AuthBook(" in src

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code = ast.unparse(tree)
    for forbidden in ("ws://", "wss://", "socket.socket", "websockets",
                      "create_connection"):
        assert forbidden not in code, forbidden
