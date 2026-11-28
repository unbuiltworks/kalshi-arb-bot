"""The registry's claim is what the watchdog cancels on, so it must not
claim the owner's orders.

WHY THIS FILE EXISTS, and why it was missing. The ownership boundary added
to `owns_venue_order_id` and `deadman` closed the LANE's own cancel path.
It did not touch `registry()`, which is the claim the INDEPENDENT watchdog
acts on -- and that loop published every `live_orders` row carrying a
venue id, adopted ones included. `reconcile_from_venue` writes the venue's
open orders into `live_orders`, so the owner's hand-placed order was being
announced to the watchdog as ours to cancel.

SEVERITY, STATED HONESTLY. The watchdog additionally requires the venue's
own row to report AUTOMATIC and exempts MANUAL, so a hand-placed order
survived that second check and this was defence in depth rather than the
only guard. But a claim here is what makes an order cancellable AT ALL,
and an in-series FOREIGN order the venue reports as AUTOMATIC had nothing
else between it and a cancel.

IT WOULD HAVE BEEN ACTIVATED BY A CONFIG FIX. The watchdog could not
reach this lane at all -- it was pointed at port 8090 while the lane
serves 8081 -- so correcting the port without this change would have
switched the gap on.

NOTHING BECOMES INVISIBLE. Adopted rows are still reported, under
`adopted_not_ours`, a name that invites no action. Exposure accounting
reads positions and never this document.
"""
from __future__ import annotations

import pytest

from src import l5_order_registry_server as R


class Store:
    def __init__(self, rows):
        self._rows = rows

    def live_orders(self):
        return dict(self._rows)


class Broken:
    def live_orders(self):
        raise RuntimeError("volume gone")


def ours(vid="V_OURS", market="m", side="UP"):
    return {"status": "NEW", "market": market, "side": side,
            "venue_order_id": vid, "adopted": False,
            "session_id": "s1", "t": "2026-11-18T23:00Z"}


def adopted(vid="DT6MANUAL", market="m", side="DOWN",
            first_seen="2026-11-18T22:22Z"):
    return {"status": "NEW", "market": market, "side": side,
            "venue_order_id": vid, "adopted": True,
            "adopted_first_seen_utc": first_seen}


def claimed(doc):
    return [o["venue_order_id"] for o in doc["orders"]]


def disclaimed(doc):
    return [o["venue_order_id"] for o in doc["adopted_not_ours"]]


# ------------------------------------------------- what is claimed

def test_an_adopted_order_is_not_claimed():
    """THE DEFECT, as one assertion."""
    doc = R.registry(Store({"DT6MANUAL": adopted()}))
    assert claimed(doc) == []


def test_our_own_order_is_still_claimed():
    """The registry must keep doing its job: the watchdog has to be able
    to cancel OUR orders, which is the whole reason it exists."""
    doc = R.registry(Store({"l5f-a": ours()}))
    assert claimed(doc) == ["V_OURS"]


def test_a_mixed_book_claims_only_ours():
    doc = R.registry(Store({"l5f-a": ours(), "DT6MANUAL": adopted()}))
    assert claimed(doc) == ["V_OURS"]
    assert disclaimed(doc) == ["DT6MANUAL"]


def test_a_row_without_the_flag_is_claimed_as_before():
    """Unchanged for every pre-existing row, which carries no `adopted`."""
    row = ours()
    row.pop("adopted")
    doc = R.registry(Store({"l5f-a": row}))
    assert claimed(doc) == ["V_OURS"]


@pytest.mark.parametrize("flag", [True, 1, "yes", "true"])
def test_any_truthy_provenance_disclaims(flag):
    doc = R.registry(Store({"x": dict(adopted(), adopted=flag)}))
    assert claimed(doc) == []


@pytest.mark.parametrize("flag", [False, 0, "", None])
def test_a_falsy_flag_is_not_a_disclaimer(flag):
    """Fail in the direction that keeps OUR orders cancellable: an
    unreadable or absent flag means claimed, not abandoned."""
    doc = R.registry(Store({"x": dict(ours(), adopted=flag)}))
    assert claimed(doc) == ["V_OURS"]


# ------------------------------------------------- nothing is hidden

def test_the_disclaimed_list_is_always_present():
    """Absent would read as 'there are none'; it must read as 'checked'."""
    assert "adopted_not_ours" in R.registry(Store({}))
    assert R.registry(Store({}))["adopted_not_ours"] == []


def test_the_disclaimed_row_carries_enough_to_investigate():
    doc = R.registry(Store({"DT6MANUAL": adopted(market="mkt-9")}))
    row = doc["adopted_not_ours"][0]
    assert row["venue_order_id"] == "DT6MANUAL"
    assert row["market"] == "mkt-9"
    assert row["side"] == "DOWN"
    assert row["adopted_first_seen_utc"] == "2026-11-18T22:22Z"


def test_an_order_with_no_venue_id_is_in_neither_list():
    """It cannot be named, so it can be neither claimed nor disclaimed --
    the separate gap already reported, unchanged here."""
    doc = R.registry(Store({"x": dict(adopted(), venue_order_id="")}))
    assert claimed(doc) == [] and disclaimed(doc) == []


# --------------------------------- the rest of the document is unchanged

def test_the_safety_fields_survive():
    doc = R.registry(Store({"l5f-a": ours()}))
    for key in ("readable", "submission_disabled", "as_of_utc", "labels"):
        assert key in doc, key


def test_an_unreadable_ledger_still_publishes_nothing_not_an_empty_claim():
    doc = R.registry(Broken())
    assert doc["readable"] is False
    assert doc["orders"] == []


def test_the_watchdog_reads_the_path_this_serves():
    """The connectivity failure was a PORT, not a path: the watchdog
    requests `<base>/l5/live_orders.json` and this is what serves it."""
    from src import l5_watchdog as W
    import inspect
    src = inspect.getsource(W)
    assert 'REG.REGISTRY_KEY' in src
    assert R.REGISTRY_KEY == "l5/live_orders.json"


# ------------------------- a requested port that did not listen is loud

def test_a_port_that_did_not_bind_is_alarmed_not_silent():
    """`serve_from_env` swallows its own exceptions and returns None, and
    the runner's `except` only catches an IMPORT failure -- so a failed
    bind emitted NOTHING and the diagnosis needed log archaeology."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN)
    i = src.index("order_registry_not_listening")
    block = src[max(0, i - 900):i + 400]
    assert "if self.registry_server is None:" in block
    assert "port_requested" in block
    #: and an UNSET port stays silent, because that is the off switch
    assert "if _want:" in block


def test_serve_from_env_is_still_off_when_no_port_is_set(monkeypatch):
    monkeypatch.delenv(R.PORT_ENV, raising=False)
    assert R.serve_from_env(Store({})) is None
