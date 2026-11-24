"""A broken halt channel must not leave an unattended controller trading.

Owner 2026-11-24: "verify that losing access to the emergency-halt channel
is detected and prevents new exposure after a bounded timeout. A broken
emergency-stop mechanism must not silently leave an unattended controller
trading."

Before: an unreachable marker was silent and changed nothing. Now, after a
bounded silence, the watchdog withholds its "no halt" attestation and the
executor's gate refuses new exposure by itself. Nothing is latched.
"""
import datetime as dt
import io
import urllib.error
import urllib.request

import pytest

from src import l5_emergency_halt_marker as M
from src import l5_halt_gate as HG
from tests.test_l5_watchdog import NOW, Venue, gate, open_row, wd

FRESH = "2026-10-12T21:59:55.000000000Z"


class Clk:
    t = 1000.0

    def __call__(self):
        return self.t


class Mem:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v


class Src:
    name = "EMERGENCY_HALT_MARKER"

    def __init__(self):
        self.mode = "absent"

    def fetch(self):
        if self.mode == "down":
            raise OSError("unreachable")
        if self.mode == "absent":
            return None
        return self.mode


def one_check(src):
    return M.check(src, object(), Mem(), service="svc", secret="s")


# ------------------------------------------------------------ the answer
def test_no_file_is_an_answer_not_a_failure():
    r = one_check(Src())
    assert r["reason"] == "MARKER_ABSENT" and r["acted"] is False


def test_a_rejected_marker_is_also_an_answer():
    s = Src()
    s.mode = b'{"command": "HALT", "hmac": "nope"}'
    assert one_check(s)["reason"] == "MARKER_SIGNATURE_INVALID"
    h = M.ChannelHealth(120, clock=Clk())
    h.note("MARKER_SIGNATURE_INVALID")
    assert h.answered is True


def test_http_404_reads_as_absent_and_other_errors_raise(monkeypatch):
    src = M.MarkerSource("https://x/y")

    def nf(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {},
                                     io.BytesIO())
    monkeypatch.setattr(urllib.request, "urlopen", nf)
    assert src.fetch() is None
    for code in (401, 403, 500, 502):
        def bad(req, timeout=None, code=code):
            raise urllib.error.HTTPError(req.full_url, code, "x", {},
                                         io.BytesIO())
        monkeypatch.setattr(urllib.request, "urlopen", bad)
        with pytest.raises(urllib.error.HTTPError):
            src.fetch()
        assert M.check(src, object(), Mem(), service="svc",
                       secret="s")["reason"] == "MARKER_UNREACHABLE"


# ------------------------------------------------------------ the timer
def test_off_by_default(monkeypatch):
    monkeypatch.delenv(M.CHANNEL_MAX_DOWN_ENV, raising=False)
    assert M.channel_max_down_s() == 0
    c = Clk()
    h = M.ChannelHealth(0, clock=c)
    c.t += 10 ** 6
    assert h.down() is None


def test_down_only_after_the_bounded_silence_and_up_on_the_next_answer():
    c = Clk()
    h = M.ChannelHealth(120, clock=c)
    h.note("MARKER_ABSENT")
    for _ in range(6):                          # 120 s of failures
        c.t += 20
        h.note("MARKER_UNREACHABLE")
    assert h.down() is None                     # exactly at the limit
    c.t += 20
    h.note("MARKER_UNREACHABLE")
    assert h.down() == M.CHANNEL_DOWN
    c.t += 20
    h.note("MARKER_ABSENT")                     # it answers again
    assert h.down() is None


def test_a_channel_that_never_answered_since_start_is_down_after_the_limit():
    """A wrong URL or a dead token from the first second: not trusted for
    ever just because it never worked."""
    c = Clk()
    h = M.ChannelHealth(120, clock=c)
    c.t += 121
    h.note("MARKER_UNREACHABLE")
    assert h.down() == M.CHANNEL_DOWN and h.answered is False


def test_the_setting_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(M.CHANNEL_MAX_DOWN_ENV, "120")
    assert M.channel_max_down_s() == 120.0
    monkeypatch.setenv(M.CHANNEL_MAX_DOWN_ENV, "junk")
    assert M.channel_max_down_s() == 0.0


# --------------------------------------------- what the watchdog then does
def test_a_withheld_attestation_stops_the_executor_without_latching(
        tmp_path, monkeypatch):
    v = Venue([])
    w, store = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert out["heartbeat"]["halt"] is False
    assert gate(tmp_path).submissions_permitted() is True
    w.attestation_hold = M.CHANNEL_DOWN
    out = w.sweep(now=NOW)
    assert out["heartbeat"] is None
    assert out["attestation_withheld"] == M.CHANNEL_DOWN
    # the last attestation is still there and still fresh: permitted until
    # it goes stale ...
    g = HG.HaltGate(HG.FileStore(str(tmp_path)), max_age_s=120.0)
    assert g.submissions_permitted() is True
    # ... and refused once it is older than the executor's limit
    rec_t = dt.datetime.fromisoformat(
        __import__("json").loads(store.get(HG.HALT_KEY))["t"])
    later = HG.HaltGate(HG.FileStore(str(tmp_path)), max_age_s=120.0)
    real = HG._utc
    monkeypatch.setattr(HG, "_utc",
                        lambda: rec_t + dt.timedelta(seconds=121))
    assert later.submissions_permitted() is False
    st = later.state()
    assert st.get("why") not in ("halt_asserted", "env_halt")   # not a HALT
    monkeypatch.setattr(HG, "_utc", real)
    # nothing was latched: lifting the hold restores trading
    w.attestation_hold = None
    out = w.sweep(now=NOW)
    assert out["heartbeat"]["halt"] is False
    assert gate(tmp_path).submissions_permitted() is True


def test_a_withheld_attestation_never_stops_the_watchdog_cancelling(
        tmp_path):
    """Channel down AND an order the executor has stopped managing: the
    watchdog's own rule still latches and cancels."""
    old = "2026-10-12T21:40:00.000000000Z"
    v = Venue([open_row("A", created=old)],
              reads={"A": {"state": "ORDER_STATE_CANCELED",
                           "leavesQuantity": "0", "cumQuantity": "0"}})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    w.attestation_hold = M.CHANNEL_DOWN
    w.sweep(now=NOW)
    assert v.cancelled == ["A"]
    assert gate(tmp_path).submissions_permitted() is False


def test_a_young_order_is_not_cancelled_because_the_channel_is_down(
        tmp_path):
    v = Venue([open_row("F", created=FRESH)])
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    w.attestation_hold = M.CHANNEL_DOWN
    out = w.sweep(now=NOW)
    assert v.cancelled == [] and out.get("emergency_cancel") is False
