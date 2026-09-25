"""Move stale local state with the venue's answer, not with an editor.

After Probe 04 the inventory file still said one order was live at $0.72
outstanding. The venue said CQ5NPBADMYC3 was ORDER_STATE_FILLED at
13:01:46.847706680Z, the account held nothing, and the market had expired at
settlementPx 0.0000.

Deleting that by hand would defeat the one rule the design has: only an
authoritative answer moves state. So recovery runs the SAME production
lifecycle and reconciliation code an armed session runs, over a transport
that cannot mutate.

The acceptance test here is Probe 04's actual transition, end to end.
"""
from __future__ import annotations

import ast
import json
import pathlib
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as P
from src import l5_live_execution as EX
from src import pmus_auth as Q1
from tests.test_l5_order_lifecycle import PROBE_04_ORDER, MARKET

RUNNER = pathlib.Path("scripts/l5_pmus_live_oneshot.py")

#: /v1/portfolio/positions and /v1/orders/open as they answered at 13:40Z,
#: after the position had settled away
VENUE_FLAT = {"positions": {}, "nextCursor": "", "eof": True,
              "availablePositions": []}
VENUE_OPEN = {"orders": []}
VENUE_BBO = {"marketData": {"marketSlug": MARKET,
                            "settlementPx": {"value": "0.0000"},
                            "state": "MARKET_STATE_EXPIRED"}}


class GetOnly:
    """pmus_auth.ReadOnlyTransport's contract, with canned answers."""

    def __init__(self):
        self.paths = []

    def request(self, method, path, body=None, base=None):
        if str(method).upper() != "GET":
            raise PermissionError(
                "%s %s refused: this transport is read-only" % (method, path))
        self.paths.append(path)
        if path == "/v1/order/CQ5NPBADMYC3":
            return {"order": dict(PROBE_04_ORDER)}
        if path == "/v1/portfolio/positions":
            return dict(VENUE_FLAT)
        if path == "/v1/orders/open":
            return dict(VENUE_OPEN)
        if path.endswith("/bbo"):
            return dict(VENUE_BBO)
        raise AssertionError("unexpected path %s" % path)


def stale_probe_04_state(tmp_path):
    """The inventory file exactly as Probe 04 left it."""
    s = EX.InventoryStore(str(tmp_path / "inv.json"))
    s.record_submit("l5f-probe04", {"client_order_id": "l5f-probe04",
                                    "market": MARKET, "side": "UP",
                                    "price": D("0.32"), "qty": D("2.25")})
    s.attach_venue_id("l5f-probe04", "CQ5NPBADMYC3")
    s.mark_uncertain("reconciliation failed: TypeError")
    assert s.live_order_count() == 1
    assert s.outstanding_by_market()[MARKET] == D("0.72")
    assert s.orders_uncertain()
    return s


def test_recovery_reconciles_probe_04_from_the_venue(tmp_path):
    """THE acceptance test: the actual Probe 04 transition."""
    stale_probe_04_state(tmp_path)
    http = GetOnly()
    rep = P.recover(http=http, market=MARKET,
                    state_path=str(tmp_path / "inv.json"),
                    journal_path=str(tmp_path / "j.jsonl"))

    # it could not have mutated anything
    assert rep["post_capable"] is False
    assert rep["armed"] is False
    assert all(p.startswith("/v1/") for p in http.paths)

    # before: stale
    assert D(rep["before"]["outstanding_usd"][MARKET]) == D("0.72")
    assert rep["before"]["orders_uncertain"]

    # the fill is booked exactly once, at the ACTUAL average price
    prog = rep["order_progress"][0]
    assert prog["delta"] == "2.25"
    assert prog["venue_state"] == "ORDER_STATE_FILLED"
    assert prog["avg_px"] == "0.2900"
    assert prog["commission"] == "0.0300"
    assert prog["released"] is True

    # after: the reservation is gone and the slot is free
    assert rep["after"]["live_orders"] == {}
    assert D(rep["after"]["outstanding_usd"].get(MARKET, "0")) == 0
    assert rep["after"]["orders_uncertain"] is None

    # the venue had the last word
    assert rep["reconciliation"]["reconciled"] is True
    assert rep["reconciliation"]["open_orders_at_venue"] == 0
    assert rep["reconciliation"]["uncertainty_cleared"] is True
    assert rep["market_state"] == {"state": "MARKET_STATE_EXPIRED",
                                   "settlement_px": "0.0000"}


def test_running_recovery_twice_changes_nothing_the_second_time(tmp_path):
    stale_probe_04_state(tmp_path)
    args = dict(market=MARKET, state_path=str(tmp_path / "inv.json"),
                journal_path=str(tmp_path / "j.jsonl"))
    first = P.recover(http=GetOnly(), **args)
    second = P.recover(http=GetOnly(), **args)

    assert first["order_progress"][0]["delta"] == "2.25"
    # the second run has nothing to reconcile and says so rather than
    # performing a no-op that looks exactly like a successful reconciliation.
    # A recovery flag left set from an earlier run has twice hijacked a
    # window that was meant to observe, and that is how it stayed invisible.
    assert second["skipped"] == "NOTHING_TO_RECONCILE"
    assert second["live_orders"] == {}
    assert second["orders_uncertain"] is None


def test_recovery_refuses_to_release_when_the_venue_cannot_be_read(tmp_path):
    """Fail closed: an unreadable order leaves the exposure exactly alone."""
    stale_probe_04_state(tmp_path)

    class Broken(GetOnly):
        def request(self, method, path, body=None, base=None):
            if str(method).upper() != "GET":
                raise PermissionError("read-only")
            if path.startswith("/v1/order/"):
                raise RuntimeError("venue unreachable")
            return super().request(method, path, body, base)

    rep = P.recover(http=Broken(), market=MARKET,
                    state_path=str(tmp_path / "inv.json"),
                    journal_path=str(tmp_path / "j.jsonl"))
    assert rep["order_progress"][0]["error"] == "RuntimeError"
    # reconciliation still runs and is authoritative about open orders, but
    # the fill was never booked from a row we could not read
    assert rep["after"]["positions"].get("%s|UP" % MARKET) in (None, "0")


def test_a_mutating_call_through_the_recovery_transport_raises():
    with pytest.raises(PermissionError):
        Q1.ReadOnlyTransport().request("POST", "/v1/orders", {}, Q1.API)


def test_recovery_never_arms_and_never_submits():
    """Structural, over the function's own source."""
    src = ast.get_source_segment(RUNNER.read_text(),
                                 [n for n in ast.walk(
                                     ast.parse(RUNNER.read_text()))
                                  if isinstance(n, ast.FunctionDef)
                                  and n.name == "recover"][0])
    for forbidden in (".arm(", ".submit(", ".cancel(", "auth.claim",
                      "record_submit", "_SignedHTTP"):
        assert forbidden not in src, forbidden


def test_recovery_mode_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv(P.RECOVER_ENV, raising=False)
    assert P.recover_only_enabled() is False
    monkeypatch.setenv(P.RECOVER_ENV, "YES")
    assert P.recover_only_enabled() is True
    monkeypatch.setenv(P.RECOVER_ENV, "")
    assert P.recover_only_enabled() is False


def test_the_recovery_record_is_journaled_and_serialisable(tmp_path):
    stale_probe_04_state(tmp_path)
    j = tmp_path / "j.jsonl"
    rep = P.recover(http=GetOnly(), market=MARKET,
                    state_path=str(tmp_path / "inv.json"),
                    journal_path=str(j))
    recs = [json.loads(l) for l in j.read_text().splitlines() if l.strip()]
    kinds = [r["kind"] for r in recs]
    assert "recovery" in kinds, "the change is recorded where it happened"
    line = json.dumps(rep, default=str)
    assert json.loads(line)["LABEL"] == "LIVE_RECOVERY"
