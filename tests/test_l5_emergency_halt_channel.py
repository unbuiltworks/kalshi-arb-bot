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


def _urlopen(table, seen=None):
    """A fake urlopen: table maps url -> 200 bytes or an HTTP error code."""
    class R:
        status = 200

        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def f(req, timeout=None):
        if seen is not None:
            seen.append((req.full_url,
                         {k.lower(): v for k, v in req.header_items()}))
        v = table[req.full_url]
        if isinstance(v, int):
            raise urllib.error.HTTPError(req.full_url, v, "x", {},
                                         io.BytesIO())
        return R(v)
    return f


MK, PR = "https://h/ops/EMERGENCY_HALT.json", "https://h/ops/README.md"


def test_404_with_a_readable_probe_means_nothing_is_published(monkeypatch):
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: 404, PR: b"readme"}, seen))
    src = M.MarkerSource(MK, probe_url=PR,
                         header={"Authorization": "Bearer tok"})
    assert src.fetch() is None
    assert [u for u, _h in seen] == [MK, PR]
    assert seen[1][1]["authorization"] == "Bearer tok"   # same credential
    assert M.check(src, object(), Mem(), service="svc",
                   secret="s")["reason"] == "MARKER_ABSENT"


@pytest.mark.parametrize("probe", [404, 401, 403, 500])
def test_404_with_an_unreadable_probe_is_an_access_failure(monkeypatch,
                                                           probe):
    """An expired or revoked token on a private repository answers 404 for
    BOTH files. That is not "no marker": it is a dead channel."""
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: 404, PR: probe}))
    src = M.MarkerSource(MK, probe_url=PR)
    with pytest.raises(Exception):
        src.fetch()
    assert M.check(src, object(), Mem(), service="svc",
                   secret="s")["reason"] == "MARKER_UNREACHABLE"


def test_404_without_a_probe_is_never_taken_as_absent(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen({MK: 404}))
    src = M.MarkerSource(MK)
    with pytest.raises(RuntimeError):
        src.fetch()
    assert M.check(src, object(), Mem(), service="svc",
                   secret="s")["reason"] == "MARKER_UNREACHABLE"


@pytest.mark.parametrize("code", [401, 403, 500, 502])
def test_other_errors_on_the_marker_are_failures_whatever_the_probe_says(
        monkeypatch, code):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: code, PR: b"readme"}))
    src = M.MarkerSource(MK, probe_url=PR)
    with pytest.raises(urllib.error.HTTPError):
        src.fetch()


def test_a_present_marker_does_not_need_the_probe(monkeypatch):
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: b"{}", PR: 500}, seen))
    assert M.MarkerSource(MK, probe_url=PR).fetch() == b"{}"
    assert [u for u, _h in seen] == [MK]


def test_an_expired_token_takes_the_channel_down(monkeypatch):
    """End to end on the timer: the token dies, both files answer 404, and
    after the bounded silence the channel reads DOWN."""
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: 404, PR: b"readme"}))
    src = M.MarkerSource(MK, probe_url=PR)
    c = Clk()
    h = M.ChannelHealth(120, clock=c)
    h.note(M.check(src, object(), Mem(), service="svc",
                   secret="s")["reason"])
    assert h.down() is None
    monkeypatch.setattr(urllib.request, "urlopen",
                        _urlopen({MK: 404, PR: 404}))      # token dead
    for _ in range(7):
        c.t += 20
        h.note(M.check(src, object(), Mem(), service="svc",
                       secret="s")["reason"])
    assert h.down() == M.CHANNEL_DOWN


def test_the_probe_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(M.MARKER_URL_ENV, MK)
    monkeypatch.setenv(M.MARKER_SECRET_ENV, "s")
    monkeypatch.setenv(M.MARKER_PROBE_URL_ENV, PR)
    assert M.from_env(Mem(), object())["source"].probe_url == PR
    monkeypatch.delenv(M.MARKER_PROBE_URL_ENV)
    assert M.from_env(Mem(), object())["source"].probe_url is None


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
