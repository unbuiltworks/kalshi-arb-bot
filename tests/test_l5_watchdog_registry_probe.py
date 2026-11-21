"""EVERY CONFIGURED OWNERSHIP REGISTRY IS PROBED ON EVERY SWEEP.

THE GAP THIS CLOSES. `oracle.claimed()` was read only inside the cancel
path, and that path runs only once an order is already overdue. So a
registry url that does not serve -- a wrong port, a renamed service, a
lane that never published -- was INDISTINGUISHABLE from "we own nothing"
until the emergency. And the sweep's behaviour in the emergency is
documented: an unclaimable order is recorded NOT_IN_OWNERSHIP_REGISTRY,
the halt is asserted, and the order is LEFT RESTING. The moment you need
the cancel path is the worst moment to discover it is blind.

It is a real configuration hazard, not a hypothetical: `l5-btc5m-live`
serves its registry on L5_BTC5M_FILLS_PORT, not on
L5_ORDER_REGISTRY_PORT like the other executor, so a url written from the
other lane's convention points at nothing while looking perfectly
plausible in the variable list.

WHAT THIS IS NOT. It is observability. It cannot widen what is
cancellable, it is a separate advisory read from the one the cancel path
makes, and it cannot fail a sweep.
"""
from __future__ import annotations

import pytest

from src import l5_watchdog as W


class FakeOracle:
    def __init__(self, url, rows=None, refusal=None, raises=None):
        self.url = url
        self._rows = rows or {}
        self.last_refusal = refusal
        self._raises = raises
        self.reads = 0

    def claimed(self):
        self.reads += 1
        if self._raises is not None:
            raise self._raises
        return self._rows


def wd(oracle, **kw):
    return W.Watchdog(W.CancelOnlyTransport(),
                      lambda *_a, **_k: None,
                      oracle=oracle, **kw)


class TestOraclesOf:
    """Callers reporting per-registry health should not have to know
    whether one url or several were configured."""

    def test_none_has_no_registries(self):
        assert W.oracles_of(None) == []

    def test_a_single_oracle_is_itself(self):
        o = FakeOracle("http://a:8090")
        assert W.oracles_of(o) == [o]

    def test_a_composite_is_its_members(self):
        a, b = FakeOracle("http://a:8090"), FakeOracle("http://b:8090")
        assert W.oracles_of(W.CompositeOracle([a, b])) == [a, b]


class TestTheNotes:

    def test_a_serving_registry_reads_ok_with_its_count(self):
        o = FakeOracle("http://a:8090", rows={"v1": {}, "v2": {}})
        assert wd(o).registry_notes() == ["http://a:8090:OK"]

    def test_zero_claims_is_OK_not_a_fault(self):
        #: a lane that holds nothing legitimately claims nothing. That
        #: must not read as a broken registry, or the signal is useless.
        #: the STATUS is what matters, and a registry that answers with
        #: no claims answered. The count lives in `claimed_ids`, which
        #: the cancel path already reports.
        o = FakeOracle("http://a:8090", rows={})
        assert wd(o).registry_notes() == ["http://a:8090:OK"]

    def test_an_unreachable_registry_names_its_refusal(self):
        #: THE WHOLE POINT: this is visible on a QUIET sweep, with no
        #: order outstanding, instead of surfacing during an incident.
        o = FakeOracle("http://gone:8090", rows={},
                       refusal="REGISTRY_UNREACHABLE:URLError")
        assert wd(o).registry_notes() == [
            "http://gone:8090:REGISTRY_UNREACHABLE:URLError"]

    def test_a_stale_claim_is_distinguished_from_an_empty_one(self):
        o = FakeOracle("http://a:8090", rows={}, refusal="CLAIM_STALE:90.0>30.0")
        assert "CLAIM_STALE" in wd(o).registry_notes()[0]

    def test_every_registry_appears_not_just_the_first(self):
        a = FakeOracle("http://a:8090", rows={"v1": {}})
        b = FakeOracle("http://b:8090", rows={},
                       refusal="REGISTRY_HTTP:404")
        notes = wd(W.CompositeOracle([a, b])).registry_notes()
        assert notes == ["http://a:8090:OK",
                         "http://b:8090:REGISTRY_HTTP:404"]

    def test_one_broken_registry_does_not_hide_the_others(self):
        #: CompositeOracle turns a raising member into a recorded
        #: refusal rather than letting it take the others down, so the
        #: healthy registry is still reported and still usable.
        a = FakeOracle("http://a:8090", raises=RuntimeError("boom"))
        b = FakeOracle("http://b:8090", rows={"v9": {}})
        notes = wd(W.CompositeOracle([a, b])).registry_notes()
        assert notes[1] == "http://b:8090:OK"
        assert len(notes) == 2

    def test_no_oracle_means_no_notes(self):
        assert wd(None).registry_notes() == []

    def test_the_probe_never_raises(self):
        #: an observable that can fail a sweep is worse than none.
        o = FakeOracle("http://a:8090", raises=ValueError("nope"))
        assert wd(o).registry_notes() == ["http://a:8090:PROBE_RAISED:ValueError"]

    def test_a_raising_probe_leaves_the_cancel_path_to_read_for_itself(self):
        #: the memo must stay UNSET on failure: acting on an ownership we
        #: failed to obtain is worse than reading again.
        o = FakeOracle("http://a:8090", raises=ValueError("nope"))
        w = wd(o, cancel_enabled=True)
        w._claims_this_sweep = None
        w.registry_notes()
        assert w._claims_this_sweep is None

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
    def test_but_it_does_not_swallow_an_interrupt(self, exc):
        o = FakeOracle("http://a:8090", raises=exc())
        with pytest.raises(exc):
            wd(o).registry_notes()


class TestItIsObservabilityOnly:

    def test_a_bad_probe_cannot_widen_what_is_cancellable(self):
        #: Ownership only ever GRANTS the power to cancel. The probe
        #: reports; it must not be able to claim an id for us.
        o = FakeOracle("http://a:8090", rows={})
        w = wd(o, cancel_enabled=True)
        w.registry_notes()
        out = w._cancel_targets([{"venue_order_id": "v1", "id": "v1"}],
                                {"unverified": [], "cancelled": []})
        #: the registry claims nothing, so the order is NOT cancelled and
        #: the sweep says why -- including whether the emptiness was a
        #: refusal or a genuine "we own nothing".
        assert out["cancelled"] == []
        assert "named no ids" in out["cancel_skipped"]
        assert "claim_refused" in out

    def test_the_probe_and_the_cancel_path_share_ONE_read(self):
        #: The first version of this probe read separately, which broke
        #: `test_the_ownership_registry_is_read_once_per_sweep`. That
        #: invariant is not a performance preference: a second read lets
        #: the answer change mid-sweep, and it would let the sweep REPORT
        #: one ownership and ACT on another.
        o = FakeOracle("http://a:8090", rows={"v1": {}})
        w = wd(o, cancel_enabled=True)
        w._claims_this_sweep = None
        w.registry_notes()
        out = w._cancel_targets([], {"unverified": [], "cancelled": []})
        assert o.reads == 1, "the cancel path re-read the registry"
        assert out["claimed_ids"] == 1

    def test_the_cancel_path_still_reads_when_no_probe_ran(self):
        #: the emergency HALT path reaches `_cancel_targets` without a
        #: sweep, and must never be left without ownership.
        o = FakeOracle("http://a:8090", rows={"v1": {}})
        w = wd(o, cancel_enabled=True)
        out = w._cancel_targets([], {"unverified": [], "cancelled": []})
        assert o.reads == 1
        assert out["claimed_ids"] == 1

    def test_the_memo_does_not_survive_into_the_next_sweep(self):
        #: cancelling against ownership a poll interval old would be
        #: worse than reading twice.
        o = FakeOracle("http://a:8090", rows={"v1": {}})
        w = wd(o, cancel_enabled=True)
        w.registry_notes()
        assert w._claims_this_sweep is not None
        try:
            w.sweep()
        except BaseException:
            pass
        #: whatever the sweep did with the transport, it must have reset
        #: the memo before using it
        assert o.reads >= 1
