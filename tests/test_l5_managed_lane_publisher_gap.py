"""No shipped publisher attests `manages_own_resting`, so the consumer
variable cannot be wired into something that WORKS.

WHY THIS FILE EXISTS. On 2026-11-18 `l5-pmus-live` (BTC15m) failed
preflight 29 times in one window on `no_unexpected_open_orders` and
placed nothing. The obvious remedy -- "set
L5_WATCHDOG_MANAGED_LANE_URLS at the sports lane" -- is INERT, and
inert in a way that LOOKS configured: `l5_managed_lanes.lane_ids`
requires `manages_own_resting: true`, and the only document any service
publishes (`l5_order_registry_server.registry`) does not emit that key
at all. The lane reads NOT_A_MANAGED_LANE, contributes zero ids, and
the gate refuses exactly as before -- while an operator reading the
variable list believes the exemption is live.

These tests pin BOTH halves of that, so the gap is a failing test the
day somebody closes it rather than a silent non-event:

  1. the registry document, fed verbatim to the consumer, is rejected;
  2. adding the one missing key is what makes it accepted.

(2) is NOT a recommendation. The attestation costs that lane the
watchdog's overdue/age latch -- `l5_live_safety` refuses with
LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH for precisely that reason -- so it
belongs only to a lane that rests orders for minutes BY DESIGN and
whose owner has accepted losing the latch.
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


def test_no_shipped_publisher_emits_manages_own_resting():
    assert "manages_own_resting" not in REG.registry(_Store())


def test_so_the_live_document_is_not_a_managed_lane():
    """THE PROPERTY THAT MATTERS: wiring the variable exempts NOTHING."""
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
