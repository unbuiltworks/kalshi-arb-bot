"""`l5_order_registry_server.registry()` does not attest
`manages_own_resting`, so a lane served by THAT function alone is not a
managed lane.

CORRECTION, 2026-11-18. This file first claimed the stronger thing: that
NO shipped publisher emits the key, that the sports lane serves no
document, and that wiring `L5_WATCHDOG_MANAGED_LANE_URLS` would be inert.
**That was wrong about production.** The live watchdog sweep reads

    managed_lanes: ["http://pmus-spring-l5-probe.railway.internal:8090:OK:13"]
    managed_lane_exempt: ["DSZEBSVG13YY", "DSZJRM4G144P", ...]

`OK:n` is only reachable when the document carries
`manages_own_resting: true`, `L5_WATCHDOG_MANAGED_LANE_URLS` IS set on
`l5-halt-watchdog`, and 5-16 live sports order ids are being exempted
from the 300 s age latch right now. The error was mine: I searched one
branch for the emitter, found none, and generalised to the deployment.
The emitter is NOT located in `ac18a20bf` either, so the mechanism is
confirmed working in production and unexplained in source -- an open
item, not a settled fact.

WHAT THESE TESTS STILL ESTABLISH, which is narrower and true: the
document `registry()` builds lacks the one key the consumer requires, so
anyone pointing the variable at a plain `registry()` endpoint gets an
exemption that silently does nothing. That is a real trap and worth
pinning. It is NOT a statement about every publisher on the account.

Adding the key is not a recommendation: the attestation costs that lane
the watchdog's overdue/age latch -- `l5_live_safety` refuses with
LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH for exactly that reason.
"""
from __future__ import annotations

import datetime as dt
import json

import src.l5_managed_lanes as ML
import src.l5_order_registry_server as REG


class _Store:
    """The narrowest thing `registry()` reads: one live, ACKed order."""

    def live_orders(self):
        return {"coid-1": {"venue_order_id": "sports-1",
                           "market": "aec-cs2-x", "side": "yes",
                           "session_id": "s", "t": "2026-11-18T13:00:00Z"}}


def _lane(doc):
    """Run the REAL consumer against `doc`, with no socket."""
    return ML.lane_ids(["http://lane.internal:8081"],
                       opener=lambda url, timeout: json.dumps(doc).encode())


def test_the_registry_document_names_the_order_and_is_fresh():
    """So the rejection below is about the MISSING KEY, nothing else."""
    doc = REG.registry(_Store())
    assert doc["readable"] is True
    assert [r["venue_order_id"] for r in doc["orders"]] == ["sports-1"]
    age = (dt.datetime.now(dt.timezone.utc)
           - ML._parse_t(doc["as_of_utc"])).total_seconds()
    assert -ML.MAX_SKEW_S < age <= ML.MAX_AGE_S


def test_this_publisher_does_not_emit_manages_own_resting():
    assert "manages_own_resting" not in REG.registry(_Store())


def test_so_a_document_from_this_publisher_is_not_a_managed_lane():
    """Pointing the variable at a plain `registry()` endpoint exempts
    nothing -- silently. Scoped to this publisher, NOT to production."""
    ids, notes = _lane(REG.registry(_Store()))
    assert ids == set()
    assert notes == ["http://lane.internal:8081:NOT_A_MANAGED_LANE"]


def test_the_one_missing_key_is_the_whole_difference():
    """Adding it -- and only it -- flips the verdict, which is why the
    gap is a one-key publisher change and not a config change."""
    ids, notes = _lane(dict(REG.registry(_Store()),
                            manages_own_resting=True))
    assert ids == {"sports-1"}
    assert notes == ["http://lane.internal:8081:OK:1"]
