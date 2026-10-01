"""The schema probe must be unable to put more than a cent at risk, and must
refuse rather than improvise. These run with no credentials and submit nothing.
"""
from decimal import Decimal as D

import pytest

from src import pmus_private_schema_probe as P


def test_price_is_at_most_one_cent_and_far_below_the_touch():
    assert P.probe_price(D("0.40")) == D("0.01")
    assert P.probe_price(D("0.95")) == D("0.01")


def test_a_touch_too_low_to_stand_clear_of_is_refused():
    """One tick is the only on-grid price, so the touch must be >= 0.21."""
    for touch in ("0.05", "0.15", "0.20", "0.2099"):
        with pytest.raises(P.ProbeRefuse):
            P.probe_price(D(touch))
    assert P.probe_price(D("0.21")) == D("0.01")      # exactly at the boundary


def test_the_price_is_always_on_the_venue_tick_grid():
    """An earlier rule took min(MAX_PX, touch - MIN_DISTANCE), which could emit
    an OFF-GRID 0.005 at a touch of 0.205. The venue would reject that."""
    for touch in ("0.205", "0.21", "0.37", "0.99"):
        try:
            px = P.probe_price(D(touch))
        except P.ProbeRefuse:
            continue
        assert px % P.PRICE_TICK == 0, f"{px} is off the grid"


def test_no_touch_is_refused_rather_than_guessed():
    with pytest.raises(P.ProbeRefuse):
        P.probe_price(None)


def test_worst_case_exposure_is_one_cent():
    px = P.probe_price(D("0.40"))
    from src import pmus_q1_executor as X
    assert px * D(str(X.QTY)) <= D("0.01")


def test_arming_requires_all_four_keys(monkeypatch):
    sid = "probe-session"
    for env in (P.LIVE_ENV, P.SESSION_ENV, P.ARM_ENV, P.PROBE_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    assert P.armed(sid) is False
    monkeypatch.setenv(P.LIVE_ENV, "YES")
    monkeypatch.setenv(P.SESSION_ENV, sid)
    monkeypatch.setenv(P.ARM_ENV, sid)
    assert P.armed(sid) is False, "the probe-specific key is still missing"
    monkeypatch.setenv(P.PROBE_ARM_ENV, sid)
    assert P.armed(sid) is True
    monkeypatch.setenv(P.PROBE_ARM_ENV, "a-different-session")
    assert P.armed(sid) is False


def test_preflight_refuses_until_both_sockets_are_proven_receiving():
    with pytest.raises(P.ProbeRefuse) as e:
        P.preflight(None, "slug", public_frames=0, private_frames=99)
    assert "PUBLIC_SOCKET_NOT_PROVEN" in str(e.value)
    with pytest.raises(P.ProbeRefuse) as e:
        P.preflight(None, "slug", public_frames=99, private_frames=1)
    assert "PRIVATE_SOCKET_NOT_PROVEN" in str(e.value)


def test_the_order_is_post_only_gtc_through_the_proven_serializer():
    from src import l5_order_body as OB
    body = P.build_body("cpc-btc-updown-15m-2099-01-01-1200z", D("0.01"))
    # post-only is expressed by the venue as participateDontInitiate
    assert body["participateDontInitiate"] is True
    assert body["tif"] == OB.TIF_GTC
    assert body["action"] == "ORDER_ACTION_BUY"
    assert body["outcomeSide"] == "OUTCOME_SIDE_YES"
    # the proven price shape, not a bare number
    assert body["price"]["currency"] == "USD"


def json_dumps(o):
    import json
    return json.dumps(o, default=str)


def test_a_fill_is_labelled_a_probe_failure():
    assert P.classify_outcome(D(0)) == "OBSERVED"
    assert P.classify_outcome(D(1)) == "PROBE_FAILURE_UNEXPECTED_FILL"


def test_frames_are_recorded_verbatim_without_rest_field_mapping():
    led = P.FrameLedger()
    raw = {"subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
           "someUnknownVenueField": 7, "nested": {"a": 1}}
    led.add(raw, source="private")
    rep = led.report()
    assert rep["frames_verbatim"][0]["raw"] == raw, "frame was altered"
    assert "someUnknownVenueField" in \
        rep["observed_frame_keys"]["SUBSCRIPTION_TYPE_ORDER"]
    assert rep["frames_verbatim"][0]["monotonic"] is not None
    assert rep["frames_verbatim"][0]["wall_utc"]
