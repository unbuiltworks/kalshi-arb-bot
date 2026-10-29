"""Cancelling our own resting order must work while DISARMED.

THE LIVE FAILURE THIS PINS, 2026-10-14, market cpc-btc-updown-15m-...-2230z.
A DOWN order rested at 0.98 (venue id D3N9HH5YTZ73, leaves 0.28). At the
authorization expiry the armed cancel was refused by the VENUE with
HTTPError. The transport then disarmed, and the three orphan sweeps that
followed could not even ask:

    cancel_error: NotArmed   x3,  still_open leaves_qty 0.28

What cleared the order was the venue expiring it at market close, observed by
our re-read -- not a withdrawal of ours. These tests use the REAL
PmusTransport with a REAL disarmed state and a stubbed HTTP layer, never a
stubbed deadman, because a stubbed deadman is exactly what hid this.
"""
from __future__ import annotations

import pytest

from src import l5_pmus_transport as T


VID = "D3N9HH5YTZ73"


class _HTTP(object):
    """Records what reached the wire; raises only when told to."""

    def __init__(self, raise_with=None):
        self.calls = []
        self.raise_with = raise_with

    def request(self, method, path, body=None, *a, **kw):
        self.calls.append((method, path))
        if self.raise_with is not None:
            raise self.raise_with
        return {"id": VID, "state": "ORDER_STATE_CANCELED"}


def disarmed_transport(monkeypatch, *, owned=(VID,), http=None, creds=True):
    """A REAL transport, genuinely disarmed, with ownership installed."""
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: bool(creds))
    tp = T.PmusTransport(market_slug="cpc-btc-updown-15m-2026-10-14-2230z",
                         http=http or _HTTP())
    assert tp.armed is False, "the fixture must be genuinely disarmed"
    tp.cancel_owner_oracle = lambda vid: str(vid) in set(owned)
    monkeypatch.setattr(tp, "_request",
                        lambda m, p, b=None, *a, **k: tp.http.request(m, p, b))
    return tp


# ------------------------------------------------ the regression itself

def test_a_disarmed_transport_can_cancel_our_own_order(monkeypatch):
    """The exact case that failed live: disarmed, our order, must go."""
    tp = disarmed_transport(monkeypatch)
    out = tp.cancel(VID)
    assert tp.armed is False
    assert tp.http.calls == [("POST", "/v1/order/%s/cancel" % VID)]
    assert out.get("state") == "ORDER_STATE_CANCELED"


def test_the_observed_notarmed_failure_no_longer_happens(monkeypatch):
    """Reproduces the live symptom and proves it is gone."""
    tp = disarmed_transport(monkeypatch)
    try:
        tp.cancel(VID)
    except T.NotArmed as exc:                                  # pragma: no cover
        pytest.fail("the live defect is back: cancel raised NotArmed "
                    "on a disarmed transport (%s)" % exc)


def test_submission_is_still_refused_while_disarmed(monkeypatch):
    """The fix must not open the submit path by one inch."""
    tp = disarmed_transport(monkeypatch)
    with pytest.raises(T.NotArmed):
        tp.submit({"side": "UP", "price": "0.5", "qty": "1"})
    assert tp.http.calls == [], "nothing may reach the wire"


def test_cancel_still_requires_arming_nowhere_in_the_class(monkeypatch):
    """_require_armed must guard submit only."""
    import inspect

    def code_only(fn):
        """Comments mention the old guard on purpose; only CALLS matter."""
        return "\n".join(l for l in inspect.getsource(fn).splitlines()
                          if not l.strip().startswith("#"))

    assert "self._require_armed()" not in code_only(T.PmusTransport.cancel)
    assert "self._require_armed()" in code_only(T.PmusTransport.submit)


# ------------------------------------------------------ strict ownership

def test_an_order_we_never_recorded_is_refused(monkeypatch):
    """A manual order the owner placed by hand is never ours to cancel."""
    tp = disarmed_transport(monkeypatch, owned=())
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel("SOMEONE-ELSES-ORDER")
    assert "NOT_OUR_ORDER" in str(e.value)
    assert tp.http.calls == []


def test_ownership_must_be_establishable_at_all(monkeypatch):
    """No oracle installed: absent is not permission."""
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: True)
    tp = T.PmusTransport(market_slug="m", http=_HTTP())
    assert tp.cancel_owner_oracle is None
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel(VID)
    assert "OWNERSHIP_UNESTABLISHED" in str(e.value)
    assert tp.http.calls == []


def test_an_unreadable_oracle_refuses_rather_than_assumes(monkeypatch):
    def boom(vid):
        raise RuntimeError("ledger unreadable")

    tp = disarmed_transport(monkeypatch)
    tp.cancel_owner_oracle = boom
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel(VID)
    assert "OWNERSHIP_UNREADABLE" in str(e.value)
    assert tp.http.calls == []


def test_an_empty_venue_id_is_refused(monkeypatch):
    tp = disarmed_transport(monkeypatch)
    for bad in ("", "   ", None):
        with pytest.raises(T.CancelRefused) as e:
            tp.cancel(bad)
        assert "NO_VENUE_ORDER_ID" in str(e.value)
    assert tp.http.calls == []


def test_no_credentials_refuses_before_the_wire(monkeypatch):
    tp = disarmed_transport(monkeypatch, creds=False)
    with pytest.raises(T.CancelRefused) as e:
        tp.cancel(VID)
    assert "NO_CREDENTIALS" in str(e.value)
    assert tp.http.calls == []


# ------------------------------- a venue refusal is NOT our defect, and is
# ------------------------------- NOT turned into a claim of success

def test_a_venue_refusal_still_propagates(monkeypatch):
    """The ORIGINAL failure was the venue's HTTPError. The fix does not and
    cannot make the venue honour a cancel; it only lets us ask."""
    import urllib.error
    err = urllib.error.HTTPError("u", 400, "Bad Request", None, None)
    tp = disarmed_transport(monkeypatch, http=_HTTP(raise_with=err))
    with pytest.raises(urllib.error.HTTPError):
        tp.cancel(VID)
    assert tp.http.calls == [("POST", "/v1/order/%s/cancel" % VID)]


def test_cancel_refused_is_not_notarmed():
    """Conflating the two is what hid the defect for a whole window."""
    assert not issubclass(T.CancelRefused, T.NotArmed)
    assert not issubclass(T.NotArmed, T.CancelRefused)


# ------------------------------------------------- the ledger's own answer

def test_the_ledger_owns_ids_it_recorded(tmp_path):
    from src import l5_live_execution as LX
    st = LX.InventoryStore(str(tmp_path / "inv.json"))
    assert st.owns_venue_order_id(VID) is False
    st.state.setdefault("live_orders", {})["coid-1"] = {
        "venue_order_id": VID, "market": "m", "side": "DOWN"}
    assert st.owns_venue_order_id(VID) is True
    assert st.owns_venue_order_id("OTHER") is False


def test_the_ledger_still_owns_an_id_that_moved_bucket(tmp_path):
    """live -> pending_terminal -> quarantine: still ours throughout."""
    from src import l5_live_execution as LX
    for bucket in ("pending_terminal", "quarantine", "submitted"):
        st = LX.InventoryStore(str(tmp_path / ("inv-%s.json" % bucket)))
        st.state[bucket] = {"coid-1": {"venue_order_id": VID}}
        assert st.owns_venue_order_id(VID) is True, bucket


def test_blank_and_missing_ids_are_never_owned(tmp_path):
    from src import l5_live_execution as LX
    st = LX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["live_orders"] = {"coid-1": {"venue_order_id": None}}
    assert st.owns_venue_order_id(None) is False
    assert st.owns_venue_order_id("") is False


def test_the_runner_installs_the_oracle():
    """Wired, not merely available."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.roll)
    assert "cancel_owner_oracle" in src
    assert "owns_venue_order_id" in src
