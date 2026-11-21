"""The halt gate: default NO, and a watchdog that cannot undo its own latch.

Every test here probes a way the gate could wrongly say YES, because that is
the only direction in which a bug costs money.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as H


class FakeStore:
    def __init__(self, obj=None, fail=False):
        self.obj = obj
        self.fail = fail
        self.puts = []

    def get(self, key):
        if self.fail:
            raise OSError("network down")
        return self.obj

    def put(self, key, body):
        if self.fail:
            raise OSError("network down")
        self.puts.append((key, body))
        self.obj = body


def rec(halt, age_s=0.0, **kw):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)
    d = {"halt": halt, "t": t.isoformat(), "who": "watchdog"}
    d.update(kw)
    return json.dumps(d).encode()


# ---------------------------------------------------------------- fail closed

def test_no_object_at_all_is_a_halt():
    assert H.HaltGate(FakeStore(None)).submissions_permitted() is False
    assert H.HaltGate(FakeStore(None)).refuse_reason() == "halt_state_unavailable"


def test_unreachable_store_is_a_halt():
    g = H.HaltGate(FakeStore(fail=True))
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "halt_state_unavailable"


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b"null", b"123",
                                  b'{"halt": "false"}', b'{"halt": 0}',
                                  b'{"nohalt": true}'])
def test_anything_unparseable_or_not_explicitly_false_is_a_halt(body):
    """A permissive reading is never taken: only an explicit boolean False
    can permit, and only then with a fresh timestamp."""
    assert H.HaltGate(FakeStore(body)).submissions_permitted() is False


def test_halt_true_is_a_halt():
    g = H.HaltGate(FakeStore(rec(True)))
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "halt_asserted"


def test_a_stale_no_halt_is_a_halt():
    g = H.HaltGate(FakeStore(rec(False, age_s=5000)), max_age_s=900)
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "halt_record_stale"


def test_a_future_dated_record_is_a_halt():
    g = H.HaltGate(FakeStore(rec(False, age_s=-600)))
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "halt_record_in_the_future"


def test_a_naive_timestamp_is_a_halt():
    body = json.dumps({"halt": False,
                       "t": dt.datetime.now().isoformat()}).encode()
    g = H.HaltGate(FakeStore(body))
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "halt_record_naive_time"


def test_an_unparseable_timestamp_is_a_halt():
    body = json.dumps({"halt": False, "t": "yesterday"}).encode()
    assert H.HaltGate(FakeStore(body)).refuse_reason() == \
        "halt_record_unparseable_time"


def test_the_env_var_kill_switch_still_works_and_wins():
    """L5_LIVE_HALT must keep working even when the store says all clear."""
    g = H.HaltGate(FakeStore(rec(False)), env_halt="1")
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == "env_halt"


def test_the_only_path_to_yes():
    g = H.HaltGate(FakeStore(rec(False, age_s=10)), max_age_s=900)
    assert g.submissions_permitted() is True
    assert g.refuse_reason() is None


# -------------------------------------------------------- assert, never clear

def test_the_asserter_has_no_delete_or_clear_path():
    """The asymmetry is structural: no method on the asserter removes a halt."""
    names = [m for m in dir(H.HaltAsserter) if not m.startswith("_")]
    assert names == ["assert_halt", "heartbeat_no_halt"], names
    # read the CODE, not the prose: an earlier version of this test searched
    # the source text and failed on the word "clear" in a docstring that was
    # explaining that there is no clear.
    import ast, inspect
    tree = ast.parse(inspect.getsource(H.HaltAsserter))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert called & {"put", "get"}, called
    assert not (called & {"delete", "remove", "unlink", "delete_object",
                          "del_object", "clear"}), called


def test_a_latched_halt_cannot_be_overwritten_by_the_watchdog():
    s = FakeStore(None)
    a = H.HaltAsserter(s, who="l5-e1-safety-monitor")
    a.heartbeat_no_halt()
    a.assert_halt(reason="cancel unconfirmed", evidence={"order": "D0VC..."})
    assert H.HaltGate(s).submissions_permitted() is False
    with pytest.raises(PermissionError):
        a.heartbeat_no_halt()          # cannot undo its own latch
    assert H.HaltGate(s).submissions_permitted() is False


def test_assert_halt_requires_a_reason_and_named_evidence():
    a = H.HaltAsserter(FakeStore(None), who="w")
    for kw in ({"reason": "", "evidence": {"a": 1}},
               {"reason": "   ", "evidence": {"a": 1}},
               {"reason": "r", "evidence": {}},
               {"reason": "r", "evidence": None}):
        with pytest.raises(ValueError):
            a.assert_halt(**kw)


def test_the_asserter_requires_a_stated_identity():
    for who in ("", "  ", None):
        with pytest.raises(ValueError):
            H.HaltAsserter(FakeStore(None), who=who)


def test_a_halt_records_who_why_and_evidence():
    s = FakeStore(None)
    a = H.HaltAsserter(s, who="l5-e1-safety-monitor")
    out = a.assert_halt(reason="CANCEL_UNCONFIRMED",
                        evidence={"order_id": "D0VC6VJDMZ7J",
                                  "post_cancel_state": "ORDER_STATE_LIVE"})
    assert out["who"] == "l5-e1-safety-monitor"
    assert out["reason"] == "CANCEL_UNCONFIRMED"
    assert out["evidence"]["order_id"] == "D0VC6VJDMZ7J"
    stored = json.loads(s.obj)
    assert stored == out


def test_a_write_failure_propagates_rather_than_being_swallowed():
    """A watchdog that THINKS it halted and did not is the worst outcome."""
    a = H.HaltAsserter(FakeStore(fail=True), who="w")
    with pytest.raises(OSError):
        a.assert_halt(reason="r", evidence={"a": 1})
