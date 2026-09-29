"""The 2215z failure, reproduced, plus the contract that now prevents it.

On 2026-09-28 a live probe spent its single-use authorization on
`cpc-btc-updown-15m-2026-09-28-2215z`, a slug MANUFACTURED FROM THE CLOCK,
and then could not resolve that market's metadata for the whole window. The
venue had created the row at 10:40:01Z -- eleven hours earlier -- so there
was no moment before the window when it was unavailable; the probe simply
first asked at 22:15:54Z, inside a transient gateway 404.

Each test below is one of the things that had to be true for that to happen.

Nothing here touches PROFIT_LOCK_5: theta, variant B, clip, hold,
residual_cap_k, LOCK=$5, floor_guard, complement_bias and the frozen n=25
parity are asserted unchanged at the end.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_pmus_transport as T
from src import pmus_auth as Q1
from src import pmus_book_tape as BT
from src import pmus_market_row as MR

import scripts.l5_pmus_live_oneshot as ONE


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")

SLUG = "cpc-btc-updown-15m-2026-09-28-2215z"
EVENT = "btc-updown-15m-2026-09-28-2215z"
START = dt.datetime(2026, 9, 28, 22, 15, tzinfo=dt.timezone.utc)
NOW = lambda: dt.datetime(2026, 9, 28, 21, 46, tzinfo=dt.timezone.utc)


def market_row(slug=SLUG, **over):
    """The row as the venue actually served it at 22:31:56Z."""
    d = {"slug": slug, "status": "MARKET_STATUS_OPEN", "active": True,
         "closed": False, "archived": False, "outcomes": '["Yes","No"]',
         "orderPriceMinTickSize": "0.01", "minimumTradeQty": "0.01",
         "createdAt": "2026-09-28T10:40:01Z",
         "question": "BTC Up or Down: 15 min", "category": "crypto"}
    d.update(over)
    return d


class Venue:
    """Per-route switchable venue. `rows` is what /v1/market/slug/ serves."""

    def __init__(self, *, row=None, serve_market=True, serve_event=False,
                 api_raises=None):
        self.row = market_row() if row is None else row
        self.serve_market = serve_market
        self.serve_event = serve_event
        self.api_raises = api_raises
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((base, path))
        if base == Q1.API and self.api_raises is not None:
            raise self.api_raises("the api host does not serve this")
        if path == "/v1/account/balances":
            return {"balances": [{"buyingPower": "142.50"}]}
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": []}
        if path.endswith("/book"):
            return {"marketData": {"bid": "0.40", "ask": "0.60",
                                   "bid_shares": "100", "ask_shares": "100"}}
        if path.startswith("/v1/market/slug/"):
            if not self.serve_market:
                raise RuntimeError("404 Market not found")
            return {"market": self.row}
        if path.startswith("/v1/events/slug/"):
            if not self.serve_event:
                raise RuntimeError("404 Event not found")
            return {"event": {"slug": EVENT, "seriesSlug": "btc-updown-15m",
                              "startTime": "2026-09-28T22:15:00Z"}}
        raise RuntimeError("404 %s" % path)


def tx(venue, slug=SLUG):
    # PRIVATE limiter: the shared one is process-wide, and a cool-off left
    # behind by one test file is how test_l5_pmus_transport came to fail 27
    # tests when run after another.
    t = T.PmusTransport(market_slug=slug, http=venue, now_fn=NOW)
    t.limiter = T.RateLimiter(rps=1e6, burst=1e6)
    return t


# ------------------------------------------- selection comes FROM the venue

def windows(*specs):
    return [{"start": s, "slug": g, "event_slug": g.replace("cpc-", ""),
             "event_closed": c, "event_active": True} for s, g, c in specs]


def test_selection_returns_an_exact_venue_provided_slug():
    rows = windows((START, SLUG, True))
    sel = ONE.select_venue_window(now=NOW().timestamp(), windows=rows)
    assert sel["slug"] == SLUG
    assert sel["source"] == "VENUE_SERIES_LISTING"
    assert sel["in_progress"] is False


def test_a_pre_created_future_window_is_selectable_though_closed_is_true():
    """Every pre-created window is `closed: true, active: true`; only the one
    trading right now is `closed: false`. Filtering on it would hide the
    upcoming window from the caller that needs it."""
    rows = windows((START, SLUG, True))
    assert ONE.select_venue_window(now=NOW().timestamp(),
                                   windows=rows)["slug"] == SLUG


def test_an_in_progress_window_with_too_little_left_is_skipped():
    later = START + dt.timedelta(minutes=15)
    rows = windows((START, SLUG, False), (later, "cpc-next", True))
    at = (START + dt.timedelta(minutes=13)).timestamp()      # 2 min left
    assert ONE.select_venue_window(now=at, windows=rows)["slug"] == "cpc-next"


def test_a_finished_window_is_never_selected():
    rows = windows((START, SLUG, True))
    at = (START + dt.timedelta(minutes=30)).timestamp()
    assert ONE.select_venue_window(now=at, windows=rows) == {}


def test_no_venue_confirmed_window_yields_nothing_not_a_computed_slug():
    """The clock is not a fallback. This is the defect that started it."""
    assert ONE.select_venue_window(now=NOW().timestamp(), windows=[]) == {}


def test_the_series_listing_carries_the_closed_flag_rather_than_applying_it():
    def get(path):
        if path.startswith("/v1/series"):
            return {"series": [{"slug": "btc-updown-15m", "id": "375"}]}
        if "offset=0" in path:
            return {"events": [
                {"slug": EVENT, "startTime": "2026-09-28T22:15:00Z",
                 "closed": True, "active": True,
                 "markets": [{"slug": SLUG}]}]}
        return {"events": []}

    rows = BT.series_windows("btc-updown-15m", get=get)
    assert rows == [{"start": START, "slug": SLUG, "event_slug": EVENT,
                     "event_closed": True, "event_active": True}]
    # and the tape's own filter still drops it, unchanged
    assert BT.series_slugs("btc-updown-15m", get=get,
                           now=NOW()) == []


# --------------------------------------- resolve before the window, then pin

def test_metadata_resolves_before_the_window_and_is_pinned():
    v = Venue()
    t = tx(v)
    pin = t.pin_market_metadata()
    assert pin["pinned"] is True
    assert pin["tick_size"] == "0.01" and pin["quantity_increment"] == "0.01"
    assert pin["created_at"] == "2026-09-28T10:40:01Z"
    assert t._market_meta[SLUG]["slug"] == SLUG


def test_a_transient_404_after_the_pin_does_not_kill_the_window(creds, capsys):
    v = Venue()
    t = tx(v)
    t.pin_market_metadata()
    v.serve_market = False                    # the 22:15-22:31 transient
    rep = t.preflight()
    assert rep["preflight"] == "PASSED"
    deg = [c for c in rep["checks"]
           if c["check"] == "market_metadata_refresh"]
    assert deg and deg[0]["blocking"] is False and deg[0]["passed"] is False
    assert "PMUS_PREFLIGHT_DEGRADED" in capsys.readouterr().out
    assert str(t.tick_size) == "0.01"


def test_a_slug_never_successfully_resolved_cannot_trade(creds, capsys):
    v = Venue(serve_market=False)
    t = tx(v)
    with pytest.raises(MR.MarketRowUnavailable):
        t.pin_market_metadata()
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    rep = json.loads(str(e.value))
    assert rep["failing_check"] == "market_readable"
    assert "PMUS_PREFLIGHT_FAIL" in capsys.readouterr().out


def test_the_pin_cannot_bypass_a_nontradable_current_state(creds):
    """Tradability is NEVER pinned. The pin carries tick, minimum quantity
    and identity; whether the market is open is re-read every attempt."""
    v = Venue()
    t = tx(v)
    assert t.pin_market_metadata()["pinned"] is True
    v.row = market_row(status="MARKET_STATUS_CLOSED", closed=True)
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    rep = json.loads(str(e.value))
    failed = [c["check"] for c in rep["checks"] if not c["passed"]]
    assert "market_open_tradable" in failed


def test_the_fresh_executable_book_is_still_mandatory():
    """The pin says nothing about price. require_fresh_book is the binding
    check and refuses a stale or one-sided book on every order."""
    from src import l5_preview_gate as PG
    now = 1_000_000.0
    # a one-sided book is refused
    with pytest.raises(PG.PreviewRefused):
        PG.require_fresh_book(bid=None, ask="0.60", book_t=now - 0.1, now=now)
    # and so is a stale one, however two-sided
    with pytest.raises(PG.PreviewRefused):
        PG.require_fresh_book(bid="0.40", ask="0.60",
                              book_t=now - 600.0, now=now)
    # the fresh two-sided case is what passes
    assert PG.require_fresh_book(bid="0.40", ask="0.60",
                                 book_t=now - 0.1, now=now)["ok"] is True


# ------------------------------------------------ the canonical route itself

def test_the_api_host_is_never_used_for_the_market_row():
    v = Venue(api_raises=RuntimeError)
    assert tx(v).market(SLUG)["slug"] == SLUG
    assert [b for b, _ in v.calls] == [Q1.GATEWAY]


def test_bbo_is_not_an_existence_oracle_and_is_not_consulted():
    """/bbo 404s for every pre-open window and 404ed for 2215z even after
    that window had opened and closed. Existence is the market row."""
    v = Venue()
    tx(v).market(SLUG)
    assert not any("/bbo" in p for _, p in v.calls)


def test_the_event_route_is_not_in_the_execution_chain():
    v = Venue(serve_event=False)
    assert tx(v).market(SLUG)["slug"] == SLUG        # event 404 is irrelevant
    assert not any("/v1/events/" in p for _, p in v.calls)


def test_series_identity_is_satisfied_without_the_event_route(creds):
    v = Venue(serve_event=False)
    rep = tx(v).preflight()
    assert rep["preflight"] == "PASSED"
    got = [c for c in rep["checks"] if c["check"] == "market_series_is_btc15m"]
    assert got and got[0]["passed"] is True


def test_there_is_exactly_one_market_row_implementation():
    """`resolve_market` and `market_micro_spec` both go through
    pmus_market_row now. Two competing implementations is what made the
    2000z failure undiagnosable for three hours."""
    import pathlib
    import re
    docker = pathlib.Path(
        "deploy/l5_pmus_lock5_probe/Dockerfile").read_text()
    packaged = [pathlib.Path(m.group(1)) for m in
                re.finditer(r"^COPY (\S+\.py) ", docker, re.M)]
    assert packaged
    hits = []
    for f in packaged:
        if f.name == "pmus_market_row.py":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"[\"']/v1/market/slug/", line) \
                    and not line.lstrip().startswith("#"):
                hits.append("%s:%d" % (f, n))
    assert hits == [], hits
    # `pmus_micro_executor` is NOT consolidated and is left byte-identical on
    # purpose: it is a PINNED R1 LINEAGE ARTIFACT
    # (tests/test_r1_source_lineage.py fails on any undisclosed delta to it),
    # and pmus_spring_l5_session depends on its exact behaviour. It already
    # makes the same single gateway call this module now holds -- it WAS the
    # proven read -- so there is nothing divergent left to reconcile. The
    # duplication that cost a window was the three-route resolver in the
    # transport, and that is gone.
    micro = pathlib.Path("src/pmus_micro_executor.py").read_text()
    assert 'f"/v1/market/slug/{slug}"' in micro
    assert "base=q1.GATEWAY" in micro
    assert "/v1/events/slug/" not in micro


# ----------------------------------------------------- nothing else moved

def test_the_order_body_serializer_is_untouched():
    from src import l5_order_body as OB
    body = OB.build(slug=SLUG, side="UP", price="0.40", qty="10",
                    post_only=True)
    assert body["price"] == {"value": "0.40", "currency": "USD"}
    assert body["tif"] == OB.TIF_GTC
    assert OB.STATIC_CONTRACT["yes_is"] == "UP"


def test_frozen_q50_parity_is_exactly_the_preregistered_result():
    from src import pmus_native_parity as PAR
    from src import pmus_l5_native_engine as NE
    assert (str(NE.THETA), NE.VARIANT, NE.HOLD_S, str(NE.CLIP),
            str(NE.RESIDUAL_CAP_K), str(NE.PROFIT_LOCK_USD),
            NE.FLOOR_GUARD, NE.COMPLEMENT_BIAS_C) == \
        ("0.96", "B", 60.0, "50", "2", "5", False, None)
    from scripts.audit_economic_complete import load, USABLE
    out = PAR.compare(load(), USABLE)
    assert out["frozen_mean_net"] == out["live_mean_net"]
    assert round(float(out["live_mean_net"]), 4) == 6.1167
    assert [x["window"] for x in out["per_window"]
            if x["delta_net"] != 0] == []
    assert len(out["per_window"]) == 25
