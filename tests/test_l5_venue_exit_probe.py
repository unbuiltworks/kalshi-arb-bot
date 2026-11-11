"""The venue exit probe reads, records shape, and can never trade.

It exists because "could we have SOLD the stranded leg?" was answered from a
source grep -- ORDER_ACTION_BUY is the only action in src/, both intents are
BUYs, and VENUE_SIDE_WORDS records that this venue spells "buy NO" as "sell
YES". That is strong evidence and not proof: we have never issued a close
request, so we have never seen one refused. This asks the venue.
"""
from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as R


class _Bare(R.Runner):
    """A Runner without its constructor: the probe must not need one.

    `preflight_status` is set because the probe refuses to read a venue whose
    preflight has not passed -- before that, a read_error would be recorded
    as though the venue had answered.
    """

    def __init__(self):
        self.preflight_status = "PASSED"


class _T:
    def positions(self):
        return [{"marketSlug": "m", "netPositionDecimal": "1.91",
                 "avgPx": {"value": "0.37"}, "qtyAvailable": "1.91"}]

    def available_positions(self):
        return [{"marketSlug": "m", "quantity": "1.91",
                 "sellableQuantity": "1.91"}]


def _records(monkeypatch, transport, runner=None):
    seen = []
    monkeypatch.setattr(R, "emit", lambda kind, doc: seen.append((kind, doc)))
    (runner or _Bare()).maybe_venue_exit_probe(transport)
    return seen


def test_it_records_the_field_names_the_venue_serves(monkeypatch):
    seen = _records(monkeypatch, _T())
    assert len(seen) == 1
    kind, doc = seen[0]
    assert kind == "L5_VENUE_EXIT_CAPABILITY"
    assert doc["positions"]["rows"] == 1
    assert "netPositionDecimal" in doc["positions"]["field_names"]
    assert "sellableQuantity" in doc["available_positions"]["field_names"]


def test_it_surfaces_anything_that_looks_like_an_exit_affordance(monkeypatch):
    """The whole point: if the venue advertises a close/reduce/sellable
    field, it must show up rather than be averaged away."""
    _, doc = _records(monkeypatch, _T())[0]
    assert doc["available_positions"]["exit_affordance_fields"] == [
        "sellableQuantity"]
    assert "qtyAvailable" in doc["positions"]["exit_affordance_fields"]


def test_it_records_no_values_only_names(monkeypatch):
    """Account figures have no business in a capability record."""
    import json
    _, doc = _records(monkeypatch, _T())[0]
    blob = json.dumps(doc)
    for value in ("1.91", "0.37"):
        assert value not in blob, value


def test_a_conclusive_read_stops_it_for_good(monkeypatch):
    """One held position row answers the question; nothing more is needed."""
    r = _Bare()
    seen = _records(monkeypatch, _T(), r)
    assert len(seen) == 1
    assert seen[0][1]["probe"]["conclusive"] is True
    assert _records(monkeypatch, _T(), r) == []


class _Flat:
    """What a DISARMED, flat account actually returns -- which is what the
    first version of this probe read, and why it learned nothing."""

    def positions(self):
        return []

    def available_positions(self):
        return []


def test_a_flat_account_is_recorded_as_INCONCLUSIVE(monkeypatch):
    r = _Bare()
    _, doc = _records(monkeypatch, _Flat(), r)[0]
    assert doc["positions"]["rows"] == 0
    assert doc["probe"]["conclusive"] is False
    assert "NOTHING about whether the venue supports closing" in \
        doc["probe"]["note"]


def test_a_flat_read_leaves_the_probe_armed(monkeypatch):
    """The bug this fixes: one-shot at boot always fired while flat, so the
    capability question could never be answered."""
    r = _Bare()
    assert len(_records(monkeypatch, _Flat(), r)) == 1
    r._venue_exit_probe_t = None          # past the cadence
    assert len(_records(monkeypatch, _Flat(), r)) == 1
    r._venue_exit_probe_t = None
    # ...and when inventory finally exists, it answers and then stops
    seen = _records(monkeypatch, _T(), r)
    assert len(seen) == 1
    assert seen[0][1]["probe"]["conclusive"] is True
    r._venue_exit_probe_t = None
    assert _records(monkeypatch, _Flat(), r) == []


def test_the_cadence_stops_it_becoming_a_poller(monkeypatch):
    r = _Bare()
    assert len(_records(monkeypatch, _Flat(), r)) == 1
    # immediately again, inside the cadence: no read
    assert _records(monkeypatch, _Flat(), r) == []


def test_the_attempt_cap_bounds_a_permanently_flat_account(monkeypatch):
    r = _Bare()
    for _ in range(R.VENUE_EXIT_PROBE_MAX + 5):
        r._venue_exit_probe_t = None
        _records(monkeypatch, _Flat(), r)
    assert r._venue_exit_probes == R.VENUE_EXIT_PROBE_MAX
    r._venue_exit_probe_t = None
    assert _records(monkeypatch, _Flat(), r) == []


def test_a_venue_failure_is_recorded_and_never_raised(monkeypatch):
    class Broken:
        def positions(self):
            raise RuntimeError("venue down")

        def available_positions(self):
            raise ValueError("bad shape")

    _, doc = _records(monkeypatch, Broken())[0]
    assert doc["positions"]["read_error"] == "RuntimeError"
    assert doc["available_positions"]["read_error"] == "ValueError"


def test_an_interrupt_is_not_swallowed(monkeypatch):
    class Stopping:
        def positions(self):
            raise KeyboardInterrupt

        def available_positions(self):
            return []

    monkeypatch.setattr(R, "emit", lambda kind, doc: None)
    with pytest.raises(KeyboardInterrupt):
        _Bare().maybe_venue_exit_probe(Stopping())


def test_it_calls_only_read_methods(monkeypatch):
    """A probe that could construct a mutation is not worth having."""
    called = []

    class Recording(_T):
        def __getattribute__(self, name):
            if not name.startswith("_"):
                called.append(name)
            return object.__getattribute__(self, name)

    _records(monkeypatch, Recording())
    assert set(called) <= {"positions", "available_positions"}, called
    for forbidden in ("submit", "cancel", "request", "place"):
        assert forbidden not in called


def test_the_recorded_vocabulary_matches_the_source(monkeypatch):
    """If src/ ever gains a sell action, this record must stop claiming
    otherwise -- so the claim is pinned against the source here."""
    import pathlib
    src = pathlib.Path("src")
    actions = set()
    for f in src.rglob("*.py"):
        for tok in ("ORDER_ACTION_BUY", "ORDER_ACTION_SELL"):
            if tok in f.read_text(errors="ignore"):
                actions.add(tok)
    assert actions == {"ORDER_ACTION_BUY"}, actions
    _, doc = _records(monkeypatch, _T())[0]
    assert doc["vocabulary_from_source"]["order_actions_in_src"] == [
        "ORDER_ACTION_BUY"]


def test_it_refuses_to_read_a_venue_whose_preflight_has_not_passed(
        monkeypatch):
    """A read_error from an unreadable account is not the venue answering."""
    r = _Bare()
    r.preflight_status = "FAILED"
    assert _records(monkeypatch, _T(), r) == []
    r.preflight_status = "PASSED"
    assert len(_records(monkeypatch, _T(), r)) == 1


def test_a_missing_transport_is_not_a_venue_answer(monkeypatch):
    assert _records(monkeypatch, None) == []


def test_it_is_driven_from_the_tick_path_not_from_inside_preflight():
    """`maybe_preflight` keeps its single job -- preflight and its logging --
    and its test double borrows exactly that one method. Hanging the probe
    inside it broke that isolation, so the probe is called beside it."""
    import inspect
    assert "self.maybe_venue_exit_probe" not in inspect.getsource(
        R.Runner.maybe_preflight)
    src = inspect.getsource(R.Runner)
    assert src.count(
        "self.maybe_preflight(now)\n        "
        "self.maybe_venue_exit_probe(self.transport)") == 2
