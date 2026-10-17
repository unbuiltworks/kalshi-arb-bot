"""The disarmed cancel must reach the wire, not die inside our own transport.

WHY THESE EXIST AND test_l5_disarmed_cancellation.py WAS NOT ENOUGH. That
file proves the GATE was removed -- cancel no longer raises NotArmed while
disarmed. It cannot prove the request is SENDABLE, because its fixture
injects a permissive `_HTTP` stub AND monkeypatches `_request`, so the real
`pmus_auth.ReadOnlyTransport` that production actually holds while disarmed
is never in the path. Production therefore failed where the suite passed:

    2026-10-15T19:50:03Z  D47R06GG0ZRX  cpc-btc-...-1945z
                          cancel_error PermissionError, left ORDER_STATE_NEW
    2026-10-16T03:39:06Z  D4EFZNW00ZZ0  cpcxrp-xrp-...-0330z
                          cancel_error PermissionError on every sweep while
                          cumQuantity climbed 37.73 -> 47.92

The second settled worthless at -37.9754. These tests use the REAL default
transport -- no injected http -- and stub only the wire.
"""
from __future__ import annotations

import pytest

from src import l5_pmus_transport as T
from src import pmus_auth as Q1


VID = "D4EFZNW00ZZ0"
SLUG = "cpcxrp-xrp-updown-15m-2026-10-16-0330z"


@pytest.fixture
def wire(monkeypatch):
    """Record what _SignedHTTP would have sent; send nothing."""
    sent = []

    def _fake(self, method, path, body=None, base=Q1.API):
        sent.append((str(method).upper(), path))
        return {"id": VID, "state": "ORDER_STATE_CANCELED",
                "leavesQuantity": "0"}

    monkeypatch.setattr(T._SignedHTTP, "request", _fake)
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: True)
    monkeypatch.setattr(T.Q1, "signed_headers",
                        lambda *a, **k: {"X-Test": "1"})
    return sent


def real_disarmed():
    """A REAL transport with the REAL default http, genuinely disarmed."""
    tp = T.PmusTransport(market_slug=SLUG)          # no injected http
    assert tp.armed is False
    assert isinstance(tp.http, Q1.ReadOnlyTransport), (
        "the fixture must hold the GET-only object production holds")
    tp.cancel_owner_oracle = lambda vid: str(vid) == VID
    return tp


def test_the_default_disarmed_http_really_does_refuse_a_post():
    """THE DEFECT, pinned. This is what production hit: the object the
    disarmed transport holds cannot POST at all."""
    tp = T.PmusTransport(market_slug=SLUG)
    with pytest.raises(PermissionError):
        tp.http.request("POST", "/v1/order/%s/cancel" % VID, {})


def test_a_disarmed_cancel_of_our_own_order_reaches_the_wire(wire):
    """THE REPAIR. Ownership is proved, so the one cancel route is sent."""
    tp = real_disarmed()
    out = tp.cancel(VID)
    assert wire == [("POST", "/v1/order/%s/cancel" % VID)]
    assert out.get("state") == "ORDER_STATE_CANCELED"


def test_the_disarmed_cancel_still_refuses_an_order_that_is_not_ours(wire):
    """The repair widens WHAT CAN SEND, never WHAT MAY BE CANCELLED."""
    tp = real_disarmed()
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel("D23N1EHTTZ7D")              # the owner's manual order
    assert "NOT_OUR_ORDER" in str(e.value)
    assert wire == [], "nothing may reach the venue for an order we disown"


def test_the_disarmed_cancel_still_refuses_with_no_oracle(wire):
    """Absent is not permission for the disarmed capability."""
    tp = T.PmusTransport(market_slug=SLUG)
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel(VID)
    assert "OWNERSHIP_UNESTABLISHED" in str(e.value)
    assert wire == []


def test_the_disarmed_cancel_still_refuses_without_credentials(monkeypatch,
                                                               wire):
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: False)
    tp = real_disarmed()
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel(VID)
    assert "NO_CREDENTIALS" in str(e.value)
    assert wire == []


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/order"),                       # submit
    ("POST", "/v1/orders"),                      # submit, plural
    ("POST", "/v1/orders/cancel"),               # account-wide
    ("POST", "/v1/order/cancel-all"),            # account-wide
    ("POST", "/v1/order/%s/cancel/extra" % VID),  # path smuggling
    ("POST", "/v1/order/%s/cancel?all=1" % VID),  # query smuggling
    ("DELETE", "/v1/order/%s" % VID),
    ("PUT", "/v1/order/%s/cancel" % VID),
])
def test_the_cancel_only_http_refuses_everything_but_one_route(method, path):
    """The refusal lives in the OBJECT, so this cannot be argued into a
    submit or into an account-wide cancellation."""
    with pytest.raises(PermissionError):
        T._CancelOnlyHTTP().request(method, path, {})


def test_the_cancel_only_http_permits_exactly_the_one_route(wire):
    sent = T._CancelOnlyHTTP().request(
        "POST", "/v1/order/%s/cancel" % VID, {})
    assert sent.get("state") == "ORDER_STATE_CANCELED"
    assert wire == [("POST", "/v1/order/%s/cancel" % VID)]


def test_the_disarmed_cancel_goes_through_the_shared_rate_limiter(wire):
    """Not a second, unmetered door to the venue: the one cancel POST is
    still counted as a write by the process-wide limiter."""
    tp = real_disarmed()
    seen = []
    tp.limiter = type("L", (), {
        "before": lambda _s, write=False: seen.append(("before", write)),
        "on_success": lambda _s: seen.append(("ok", None)),
        "on_429": lambda _s: seen.append(("429", None))})()
    tp.cancel(VID)
    assert ("before", True) in seen and ("ok", None) in seen


def test_an_injected_transport_is_honoured_for_cancel_too(wire):
    """An injected http stays the sender for cancel as well as for reads.

    A review corrected the rationale originally written here, which claimed
    this closed "a hole around ShadowOnlyTransport". It does not: that proxy
    wraps `PmusTransport`, not the `http` object, and is never passed as
    `http=`. What this actually pins is that a test fake, or any wrapper that
    supplied its own http, keeps receiving the cancel -- so the repair cannot
    silently route around an object someone deliberately installed.
    """
    calls = []

    class _Inj:
        def request(self, method, path, body=None, *a, **k):
            calls.append((method, path))
            return {"state": "ORDER_STATE_CANCELED"}

    tp = T.PmusTransport(market_slug=SLUG, http=_Inj())
    tp.cancel_owner_oracle = lambda vid: True
    tp.cancel(VID)
    assert calls == [("POST", "/v1/order/%s/cancel" % VID)]
    assert wire == [], "the injected object sent it, not a fresh signed one"


# ---------------- hardening the review asked for, and its own pins (§65)

def test_the_two_cancel_route_patterns_have_not_drifted():
    """The regex is duplicated in the transport and the watchdog because the
    watchdog image must load without `pmus_auth`. Duplicated security
    constants drift: one gets tightened and the other is forgotten. This
    fails the moment they differ."""
    from src import l5_watchdog as W
    assert T.CANCEL_ROUTE.pattern == W.CANCEL_ROUTE.pattern


def test_the_route_is_anchored_against_a_trailing_newline():
    """`$` also matches before a trailing newline; `\\Z` does not."""
    assert T.CANCEL_ROUTE.match("/v1/order/%s/cancel" % VID)
    assert not T.CANCEL_ROUTE.match("/v1/order/%s/cancel\n" % VID)


def test_the_object_refuses_a_foreign_base_and_a_non_empty_body(wire):
    """`super()` forwarded both unchecked: an arbitrary body on the real
    cancel route, and an unauthenticated POST to any host."""
    h = T._CancelOnlyHTTP()
    with pytest.raises(PermissionError):
        h.request("POST", "/v1/order/%s/cancel" % VID, {},
                  "https://evil.example")
    with pytest.raises(PermissionError):
        h.request("POST", "/v1/order/%s/cancel" % VID, {"cancelAll": True})
    assert wire == []


def test_the_object_itself_refuses_an_unowned_id(wire):
    """THE REVIEW'S MAIN POINT. Ownership must be a property of the OBJECT,
    not of one caller, because the object is reachable by attribute."""
    h = T._CancelOnlyHTTP(owns=lambda: (lambda oid: oid == VID))
    assert h.request("POST", "/v1/order/%s/cancel" % VID, {})
    with pytest.raises(PermissionError):
        h.request("POST", "/v1/order/D23N1EHTTZ7D/cancel", {})
    assert wire == [("POST", "/v1/order/%s/cancel" % VID)]


def test_an_unreadable_oracle_is_not_permission_inside_the_object(wire):
    def _boom():
        raise RuntimeError("registry down")

    h = T._CancelOnlyHTTP(owns=_boom)
    with pytest.raises(PermissionError):
        h.request("POST", "/v1/order/%s/cancel" % VID, {})
    assert wire == []


def test_the_cached_cancel_object_is_not_exposed_on_the_shadow_proxy(wire):
    """It used to be reachable as `transport._cancel_http` straight through
    `ShadowOnlyTransport.__getattr__`, which delegates everything but
    `submit`. Name-mangled now, and the object checks for itself anyway."""
    from src import l5_pmus_live_runner as R
    tp = real_disarmed()
    tp.cancel(VID)                                  # force it to be built
    proxy = R.ShadowOnlyTransport(tp)
    assert getattr(proxy, "_cancel_http", None) is None
    assert not hasattr(proxy, "_cancel_http")


def test_the_request_override_accepts_only_the_transports_own_object(wire):
    """An unconstrained `http=` would let any caller pass a general
    _SignedHTTP and submit while disarmed and shadow-locked."""
    tp = real_disarmed()
    with pytest.raises(PermissionError):
        tp._request("POST", "/v1/order", {"x": 1}, T.Q1.API,
                    http=T._SignedHTTP())
    assert wire == []


def test_the_armed_cancel_is_route_validated_too(wire):
    """The first draft sent the ARMED cancel through a general _SignedHTTP,
    making the owner-token path the LOOSER of the two. Both go through the
    restricted object now."""
    tp = real_disarmed()
    tp.armed = True
    tp.cancel(VID)
    assert wire == [("POST", "/v1/order/%s/cancel" % VID)]
    sender = getattr(tp, "_PmusTransport__cancel_http")
    assert isinstance(sender, T._CancelOnlyHTTP)


def test_the_cancel_object_is_built_only_by_an_authorised_cancel(wire):
    """A POST-capable object must not come into existence until a cancel has
    actually passed ownership. A refused cancel leaves the graph as it was."""
    tp = real_disarmed()
    assert getattr(tp, "_PmusTransport__cancel_http") is None
    with pytest.raises(T.CancelRefused):
        tp.cancel("D23N1EHTTZ7D")                   # not ours
    assert getattr(tp, "_PmusTransport__cancel_http") is None, (
        "a refused cancel must not construct a POST-capable object")
    tp.cancel(VID)
    assert isinstance(getattr(tp, "_PmusTransport__cancel_http"),
                      T._CancelOnlyHTTP)


def test_a_disarm_after_arm_can_still_cancel(wire):
    """disarm() replaces self.http; the cancel path must survive it, because
    disarmed is exactly the state the orphan sweeps run in."""
    tp = real_disarmed()
    tp.armed = True
    tp.cancel(VID)
    tp.armed = False
    tp.http = T.Q1.ReadOnlyTransport()
    tp.cancel(VID)
    assert wire == [("POST", "/v1/order/%s/cancel" % VID)] * 2
