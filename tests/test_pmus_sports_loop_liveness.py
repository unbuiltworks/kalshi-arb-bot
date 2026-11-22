"""Owner 2026-11-22: the independent watchdog must be able to shut the bot's
orders down when the bot itself stalls. It exempts this lane's resting
orders only while the registry document is fresh, and that stamp used to be
the HTTP request time: a wedged trading loop stayed "fresh" for ever."""
import datetime as dt
import json
import threading
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

#: the watchdog's own rule (src/l5_managed_lanes.py on its branch): readable,
#: manages_own_resting, and as_of_utc at most 60 s old
WATCHDOG_MAX_AGE_S = 60.0


def watchdog_exempts(doc, now):
    if doc.get("readable") is not True or \
            doc.get("manages_own_resting") is not True:
        return False
    t = dt.datetime.fromisoformat(str(doc["as_of_utc"]))
    age = now - t.timestamp()
    return -5.0 <= age <= WATCHDOG_MAX_AGE_S


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.setitem(M._LOOP, "beat", None)
    monkeypatch.setitem(M._LOOP, "thread", None)
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def test_fresh_while_the_loop_progresses(eng):
    e, v = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    import time as real
    for idle in (0, 5, 60, M.LOOP_STALL_S - 1):
        M._LOOP["beat"] = real.time() - idle        # last progress `idle` s ago
        doc = M.registry_doc(e.b)
        assert doc["loop_stalled"] is False and len(doc["orders"]) == 2
        assert watchdog_exempts(doc, real.time())


def watchdog_can_cancel(doc, now):
    """The watchdog's ownership rule for its cancel path (OwnershipOracle on
    its branch): a readable document with a fresh stamp names the ids."""
    if doc.get("readable") is not True:
        return set()
    age = now - dt.datetime.fromisoformat(str(doc["as_of_utc"])).timestamp()
    if not (-5.0 <= age <= WATCHDOG_MAX_AGE_S):
        return set()
    return {r["venue_order_id"] for r in doc["orders"]}


def test_a_stalled_loop_loses_the_exemption_but_keeps_its_ownership_claim(eng):
    """The wedged-executor case. The registry thread still answers; it now
    says the loop is NOT managing these orders, while still naming them, so
    the watchdog may both latch on their age and cancel them."""
    e, v = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    import time as real
    last = real.time() - (M.LOOP_STALL_S + 1)
    M._LOOP["beat"] = last
    doc = M.registry_doc(e.b)
    assert doc["loop_stalled"] is True and doc["manages_own_resting"] is False
    assert doc["loop_idle_s"] == pytest.approx(M.LOOP_STALL_S + 1, abs=1)
    assert dt.datetime.fromisoformat(
        doc["loop_last_progress_utc"]).timestamp() == pytest.approx(last,
                                                                    abs=0.01)
    assert not watchdog_exempts(doc, real.time())   # exemption gone
    assert watchdog_can_cancel(doc, real.time()) == {"o1", "o2"}
    M._LOOP["beat"] = real.time() - 7200            # wedged for two hours
    doc = M.registry_doc(e.b)
    assert not watchdog_exempts(doc, real.time())
    assert watchdog_can_cancel(doc, real.time()) == {"o1", "o2"}
    json.dumps(doc, default=str)
    # the loop comes back: exempt again at once
    M.loop_beat()
    assert watchdog_exempts(M.registry_doc(e.b), real.time())


def test_no_other_thread_can_beat_for_the_loop(eng, monkeypatch):
    e, v = eng
    t = [1_000_000.0]
    monkeypatch.setattr(M.time, "time", lambda: t[0])
    M._LOOP["thread"] = threading.get_ident()       # this thread is the loop
    M.loop_beat()
    t[0] += 500
    th = threading.Thread(target=M.loop_beat)       # e.g. the registry thread
    th.start()
    th.join()
    assert M.loop_idle_s() == pytest.approx(500)
    assert M.registry_doc(e.b)["loop_stalled"] is True


def test_before_the_loop_starts_the_document_is_not_called_stalled(eng):
    e, v = eng
    doc = M.registry_doc(e.b)
    assert doc["loop_idle_s"] is None and doc["loop_stalled"] is False


def test_loop_requests_beat_and_other_threads_requests_do_not(eng,
                                                               monkeypatch):
    e, v = eng

    class T:
        calls = 0

        def request(self, *a, **k):
            T.calls += 1
            if a and a[0] == "BOOM":
                raise RuntimeError("x")
            return {}
    v.t = T()
    t = [1_000_000.0]
    monkeypatch.setattr(M.time, "time", lambda: t[0])
    M._LOOP["thread"] = threading.get_ident()
    M.loop_beat()
    e._hook_loop_beat()
    e._hook_loop_beat()                              # idempotent
    t[0] += 90
    v.t.request("GET", "/v1/portfolio/activities")   # the audit, mid-pass
    assert M.loop_idle_s() == 0
    t[0] += 90
    with pytest.raises(RuntimeError):
        v.t.request("BOOM")                          # a failed request is
    assert M.loop_idle_s() == 0                      # still progress
    t[0] += 500
    th = threading.Thread(target=lambda: v.t.request("GET", "/x"))
    th.start()
    th.join()
    assert M.loop_idle_s() == pytest.approx(500)
    assert T.calls == 3


def test_run_registers_its_thread_and_beats_every_pass(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "LOOP_S", 0)
    monkeypatch.setattr(M, "REWARD_CHECK_S", 0)
    monkeypatch.setattr(M, "SHARP_MODE", "off")
    monkeypatch.setattr(e, "report", lambda: None)
    monkeypatch.setattr(e, "orphan_sweep", lambda *a, **k: None)
    v.targets = []
    beats = []
    real = M.loop_beat
    monkeypatch.setattr(M, "loop_beat", lambda: (beats.append(1), real())[1])
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] >= 3:
            e.stop = True
    monkeypatch.setattr(e, "_sleep", sleep)
    e.run()
    assert M._LOOP["thread"] == threading.get_ident()
    assert len(beats) >= 7                           # start + 2 per pass
    assert M.loop_idle_s() is not None


def test_boot_reports_the_stall_threshold():
    assert M.safeguards_cfg()["loop_stall_s"] == M.LOOP_STALL_S == 120.0
