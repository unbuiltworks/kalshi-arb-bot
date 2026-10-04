"""The WS pricing boundary: no silent fallback, no stale quote, no order path."""
from __future__ import annotations

import time
from decimal import Decimal as D

import pytest

from src import l5_order_body as OB
from src import pmus_q1_executor as Q1
from src import pmus_ws_bbo_adapter as AD


def _state(slug="s", bid="0.44", ask="0.45", age=0.0, both=True, upd=7):
    return {slug: {"bid": bid, "ask": ask, "both_sides_served": both,
                   "book_updates": upd, "last_t": time.time() - age}}


# ---------------------------------------------------------------- normalize

def test_normalize_returns_the_gateway_shape_plus_provenance():
    out = AD.normalize(_state(), "s", time.time())
    assert out["bid"] == "0.44" and out["ask"] == "0.45"
    assert out["source"] == "authenticated_ws"
    assert out["age_s"] >= 0


@pytest.mark.parametrize("kw,frag", [
    ({"bid": None}, "ONE_SIDED"),
    ({"ask": None}, "ONE_SIDED"),
    ({"both": False}, "NEVER_SERVED"),
    ({"age": 99.0}, "STALE_WS_BOOK"),
])
def test_normalize_refuses_unusable_state(kw, frag):
    with pytest.raises(AD.StaleWsState) as e:
        AD.normalize(_state(**kw), "s", time.time())
    assert frag in str(e.value)


def test_normalize_refuses_an_unknown_slug():
    with pytest.raises(AD.StaleWsState):
        AD.normalize(_state(), "other", time.time())


def test_staleness_boundary_is_enforced_exactly():
    st = _state(age=1.9)
    assert AD.normalize(st, "s", time.time(), max_age=2.0)["bid"] == "0.44"
    with pytest.raises(AD.StaleWsState):
        AD.normalize(_state(age=2.5), "s", time.time(), max_age=2.0)


# ------------------------------------------- economic vs wire representation

def test_prices_stay_in_long_side_terms_and_wire_mapping_is_unchanged():
    """The adapter must not invert anything. UP is quoted directly; DOWN is
    `1 - the UP quote`, and the WIRE value for a NO order is `1 - X`."""
    q = AD.normalize(_state(bid="0.30", ask="0.31"), "s", time.time())
    up_ask, up_bid = D(q["ask"]), D(q["bid"])
    down_ask = D(1) - up_bid           # buying DOWN lifts the UP bid side
    assert down_ask == D("0.70")
    # the proven serializer still owns the wire mapping
    assert OB.venue_price("OUTCOME_SIDE_NO", down_ask) == D("0.30")
    assert OB.venue_price("OUTCOME_SIDE_YES", up_ask) == D("0.31")
    # and the executor's own crossing test reads the adapter's dict as-is
    assert Q1.would_cross("OUTCOME_SIDE_YES", up_ask, q) is True
    assert Q1.would_cross("OUTCOME_SIDE_YES", up_ask - D("0.01"), q) is False
    assert Q1.would_cross("OUTCOME_SIDE_NO", down_ask, q) is True


# -------------------------------------------------- NO SILENT FALLBACK, EVER

class _Boom:
    """Any HTTP read from an armed path is a test failure."""

    def get(self, *a, **k):
        raise AssertionError("the gateway was read on an armed path")


def test_armed_run_without_a_feed_refuses_instead_of_reading_the_gateway(
        monkeypatch):
    monkeypatch.setattr(Q1, "WS_FEED", None)
    monkeypatch.setattr(Q1, "armed", lambda: True)
    monkeypatch.setattr(Q1, "_gateway_bbo",
                        lambda *a, **k: pytest.fail("gateway was called"))
    with pytest.raises(Q1.Halt) as e:
        Q1.fresh_bbo(_Boom(), "slug")
    assert "WS_PRICING_REQUIRED" in str(e.value)


def test_stale_feed_halts_and_does_not_fall_back(monkeypatch):
    class F:
        def bbo(self, slug):
            raise AD.StaleWsState("STALE_WS_BOOK: x")
    monkeypatch.setattr(Q1, "WS_FEED", F())
    monkeypatch.setattr(Q1, "armed", lambda: True)
    monkeypatch.setattr(Q1, "_gateway_bbo",
                        lambda *a, **k: pytest.fail("gateway was called"))
    with pytest.raises(Q1.Halt) as e:
        Q1.fresh_bbo(_Boom(), "slug")
    assert "WS_BBO_UNAVAILABLE" in str(e.value)


def test_attached_feed_is_used_even_when_unarmed(monkeypatch):
    class F:
        def bbo(self, slug):
            return {"bid": "0.10", "ask": "0.11", "source": "authenticated_ws"}
    monkeypatch.setattr(Q1, "WS_FEED", F())
    monkeypatch.setattr(Q1, "armed", lambda: False)
    monkeypatch.setattr(Q1, "_gateway_bbo",
                        lambda *a, **k: pytest.fail("gateway was called"))
    assert Q1.fresh_bbo(_Boom(), "slug")["source"] == "authenticated_ws"


def test_unarmed_diagnostic_may_still_read_the_gateway(monkeypatch):
    monkeypatch.setattr(Q1, "WS_FEED", None)
    monkeypatch.setattr(Q1, "armed", lambda: False)
    monkeypatch.setattr(Q1, "_gateway_bbo",
                        lambda *a, **k: {"bid": "1", "ask": "2",
                                         "source": "public_gateway"})
    assert Q1.fresh_bbo(object(), "slug")["source"] == "public_gateway"


# ------------------------------------------------------- structural guards

def test_only_the_gateway_reader_mentions_the_gateway():
    """A regression guard against re-introducing a silent HTTP path: the
    decision boundary must not name GATEWAY at all."""
    assert "GATEWAY" not in Q1.fresh_bbo.__code__.co_names
    assert "GATEWAY" in Q1._gateway_bbo.__code__.co_names
    assert "armed" in Q1.fresh_bbo.__code__.co_names


def test_the_adapter_has_no_order_path():
    src = open("src/pmus_ws_bbo_adapter.py").read()
    for forbidden in ("ORDER_ACTION_BUY", "/v1/orders", "place_limit",
                      "submit", "cancel"):
        assert forbidden not in src, forbidden
    # and every outbound frame goes through the recorder's allowlist
    assert "guard_outbound" in src


def test_adapter_reuses_the_recorder_rather_than_reimplementing_it():
    import inspect
    src = inspect.getsource(AD)
    assert "from src.pmus_ws_evidence_recorder import" in src
    for name in ("apply_events", "auth_headers", "subscribe_frames",
                 "guard_outbound"):
        assert name in src
