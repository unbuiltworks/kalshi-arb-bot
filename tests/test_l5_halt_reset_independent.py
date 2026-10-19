"""The HALT reset without stopping the production watchdog.

THE OPERATIONAL FAILURE THIS FIXES, 2026-10-19. Obtaining the digest that
gate 2 demands meant pointing the l5-halt-watchdog SERVICE at the one-shot
reset image, because `main()` built its store as
`FileStore(L5_WATCHDOG_STATE_DIR)` and a Railway volume attaches to exactly
one service. The reset image exits by design under restartPolicyType NEVER,
so the watchdog stopped sweeping at 01:00:12Z and stayed down for 5m53s --
the independent HALT authority and the independent cancellation were both
offline, which is precisely the capability the live test exists to validate.
Worse, `redeploy` reuses the previous BUILD, so the first attempt restarted
the WATCHDOG while the service config claimed the reset image, the INSPECT
never ran, and NO DIGEST WAS OBTAINED.

THE SPLIT THESE TESTS PIN:

  * INSPECT may run ANYWHERE that can reach the watchdog's GET-only halt
    server. `l5_halt_server.do_GET` returns the stored bytes VERBATIM, so
    sha256(HTTP body) == sha256(file bytes) == the digest gate 2 requires.
    The watchdog is never touched. Proven here against the REAL server
    handler over a REAL FileStore, not a stub.

  * CLEAR still needs the volume, and that is a Railway constraint, not a
    code one. It is refused BY NAME over HTTP rather than failing later
    inside the audit append, because the ordering guarantee is that the
    audit entry is fsynced BEFORE the latch is cleared.

NOTHING HERE CLEARS THE PRODUCTION HALT. Every write goes to a tmp_path
FileStore or an in-memory fake, and no test places, prices or cancels
anything at the venue.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import l5_halt_reset as R            # noqa: E402
from src import l5_halt_gate as HG                # noqa: E402
from src import l5_halt_server as S               # noqa: E402

from tests.test_l5_halt_reset import (            # noqa: E402
    FakeStore, FakeTransport, fresh_registry, latch, env, PHRASE)


# --------------------------------------------------------------------------
# the real server over a real volume-backed store, with no socket
# --------------------------------------------------------------------------
def served_bytes(store, key=HG.HALT_KEY):
    """Drive the REAL do_GET and return (status, body) with no network.

    The point is the BYTES. A test that re-serialised the record would prove
    nothing about whether the digest survives the HTTP hop.
    """
    captured = {}

    class H(S.make_handler(store)):
        def __init__(self):                        # no socket, no base init
            self.path = "/" + key

        def send_response(self, code):
            captured["status"] = code

        def send_header(self, *a):
            pass

        def end_headers(self):
            pass

        @property
        def wfile(self):
            class W:
                @staticmethod
                def write(b):
                    captured["body"] = b
            return W()

    H().do_GET()
    return captured.get("status"), captured.get("body")


def volume_store(tmp_path):
    return HG.FileStore(str(tmp_path))


def file_latch(store, reason="UNMANAGED_RESTING_ORDER"):
    """Latch a REAL FileStore and return (raw bytes, digest)."""
    rec = {"halt": True, "t": R._utc().isoformat(), "who": "l5-watchdog",
           "reason": reason, "evidence": {"orders": ["X1"]}}
    raw = json.dumps(rec, sort_keys=True).encode()
    store.put(HG.HALT_KEY, raw)
    return raw, hashlib.sha256(raw).hexdigest()


def test_the_server_returns_the_stored_bytes_verbatim(tmp_path):
    """THE WHOLE UNLOCK. If the server re-serialised, a remotely obtained
    digest would never match gate 2 and the swap would be unavoidable."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    status, body = served_bytes(st)
    assert status == 200
    assert body == raw, "the digest gate depends on byte-for-byte identity"
    assert hashlib.sha256(body).hexdigest() == digest


def test_an_http_store_digest_equals_the_file_digest(tmp_path, monkeypatch):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: served_bytes(st)[1])
    got = http.get(HG.HALT_KEY)
    assert got == raw
    assert hashlib.sha256(got).hexdigest() == digest


# --------------------------------------------------------------------------
# the false-permissive read that would have broken gate 3
# --------------------------------------------------------------------------
def test_the_http_store_refuses_every_key_but_the_latch():
    """The audit log is NOT served. A 404 would map to None, and
    `audit_entries` reads None as an EMPTY log -- so the one-time guard
    would pass on a key it cannot see."""
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: pytest.fail("no request"))
    with pytest.raises(HG.HaltReadUnavailable):
        http.get(R.AUDIT_KEY)


def test_the_audit_log_is_unreadable_not_empty_over_http():
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: b"{}")
    with pytest.raises(R.ResetRefused, match="AUDIT_LOG_UNREADABLE"):
        R.audit_entries(http)


def test_the_latch_key_still_reads_normally_through_the_guard():
    """The guard must not break the executor's own read path."""
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: b'{"halt": true}')
    assert http.get(HG.HALT_KEY) == b'{"halt": true}'


def test_the_http_store_still_has_no_write_path():
    http = HG.HttpHaltStore("http://halt.internal:8080")
    with pytest.raises(PermissionError):
        http.put(HG.HALT_KEY, b"{}")


# --------------------------------------------------------------------------
# inspect over HTTP: the digest without touching the watchdog
# --------------------------------------------------------------------------
def test_inspect_over_http_yields_the_digest_and_flags_the_audit_gap(tmp_path):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: served_bytes(st)[1])
    out = R.inspect(store=http, transport=FakeTransport(),
                    registry_url="http://registry.local",
                    registry_opener=fresh_registry())
    assert out["sha256"] == digest
    assert out["set_this_as"][R.EXPECT_ENV] == digest
    assert out["set_this_as"][R.REASON_ENV] == "UNMANAGED_RESTING_ORDER"
    # the gap is REPORTED, never silently read as "no prior resets"
    assert "AUDIT_LOG_UNREADABLE" in out["prior_resets_error"]
    assert "prior_resets" not in out


def test_inspect_over_http_wrote_nothing_to_the_volume(tmp_path):
    st = volume_store(tmp_path)
    raw, _ = file_latch(st)
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: served_bytes(st)[1])
    R.inspect(store=http, transport=FakeTransport(),
              registry_url="http://registry.local",
              registry_opener=fresh_registry())
    assert st.get(HG.HALT_KEY) == raw, "the latch must be untouched"
    assert st.get(R.AUDIT_KEY) is None, "no audit entry for a dry run"


# --------------------------------------------------------------------------
# the clear cannot happen over HTTP, and says so by name
# --------------------------------------------------------------------------
def test_a_clear_over_http_is_refused_by_name():
    http = HG.HttpHaltStore("http://halt.internal:8080")
    with pytest.raises(R.ResetRefused,
                       match="RESET_REQUIRES_VOLUME_BACKED_STORE"):
        R._require_clearable_store(http)


def test_a_store_that_cannot_put_is_refused():
    class ReadOnly:
        def get(self, key):
            return None

    with pytest.raises(R.ResetRefused,
                       match="RESET_REQUIRES_VOLUME_BACKED_STORE"):
        R._require_clearable_store(ReadOnly())


def test_a_volume_store_is_accepted_as_clearable(tmp_path):
    R._require_clearable_store(volume_store(tmp_path))      # must not raise


# --------------------------------------------------------------------------
# the image must name itself (the redeploy-reuses-the-build failure)
# --------------------------------------------------------------------------
def test_a_reset_refuses_when_the_image_marker_is_absent(monkeypatch):
    monkeypatch.delenv(R.IMAGE_MARKER_ENV, raising=False)
    with pytest.raises(R.ResetRefused, match="WRONG_IMAGE"):
        R._require_reset_image()


def test_a_reset_refuses_on_a_foreign_image_marker(monkeypatch):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, "l5_halt_watchdog")
    with pytest.raises(R.ResetRefused, match="WRONG_IMAGE"):
        R._require_reset_image()


def test_the_reset_image_marker_is_accepted(monkeypatch):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE)
    assert R._require_reset_image() == R.IMAGE_MARKER_VALUE


def test_the_marker_is_baked_into_the_reset_image_and_no_other():
    """Read as text because it is a property of the Dockerfiles."""
    reset = io.open("deploy/l5_halt_reset/Dockerfile", encoding="utf-8").read()
    assert "ENV %s=%s" % (R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE) in reset
    wd = io.open("deploy/l5_halt_watchdog/Dockerfile", encoding="utf-8").read()
    assert R.IMAGE_MARKER_ENV not in wd, (
        "the watchdog image must NOT be able to claim to be the reset image")


def test_the_watchdog_image_still_cannot_run_the_reset_at_all():
    """Defence in depth behind the marker: the script is not even present."""
    wd = io.open("deploy/l5_halt_watchdog/Dockerfile", encoding="utf-8").read()
    assert "scripts/l5_halt_reset.py" not in wd


# --------------------------------------------------------------------------
# build_store: which medium, and the fail-closed refusal
# --------------------------------------------------------------------------
def test_build_store_picks_http_only_for_inspect(monkeypatch):
    monkeypatch.setenv(R.VIA_HTTP_ENV, "YES")
    monkeypatch.setenv(R.HALT_URL_ENV, "http://halt.internal:8080")
    store, medium = R.build_store(inspecting=True)
    assert isinstance(store, HG.HttpHaltStore) and medium == "http_read_only"
    # the CLEAR path never gets the remote store, whatever the env says
    store, medium = R.build_store(inspecting=False)
    assert isinstance(store, HG.FileStore) and medium == "volume"


def test_build_store_refuses_http_inspect_without_a_url(monkeypatch):
    monkeypatch.setenv(R.VIA_HTTP_ENV, "YES")
    monkeypatch.delenv(R.HALT_URL_ENV, raising=False)
    with pytest.raises(R.ResetRefused, match="NO_HALT_URL"):
        R.build_store(inspecting=True)


def test_build_store_defaults_to_the_volume(monkeypatch):
    monkeypatch.delenv(R.VIA_HTTP_ENV, raising=False)
    monkeypatch.setenv(R.STATE_ENV, "/data")
    store, medium = R.build_store(inspecting=True)
    assert isinstance(store, HG.FileStore) and medium == "volume"


# --------------------------------------------------------------------------
# main(): the reset path refuses before any gate when the image is wrong
# --------------------------------------------------------------------------
def test_main_refuses_the_wrong_image_before_anything_else(monkeypatch,
                                                           capsys, tmp_path):
    """Even with EVERY owner gate satisfied, a wrong image clears nothing."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    monkeypatch.delenv(R.IMAGE_MARKER_ENV, raising=False)
    monkeypatch.setenv(R.INSPECT_ENV, "NO")
    monkeypatch.setenv(R.STATE_ENV, str(tmp_path))
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    rc = R.main([])
    out = capsys.readouterr().out
    assert rc != 0
    assert "WRONG_IMAGE" in out
    assert st.get(HG.HALT_KEY) == raw, "the latch must be untouched"
    assert st.get(R.AUDIT_KEY) is None, "no audit entry may be written"


def test_main_inspect_over_http_reports_the_medium(monkeypatch, capsys,
                                                   tmp_path):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    monkeypatch.setenv(R.INSPECT_ENV, "YES")
    monkeypatch.setenv(R.VIA_HTTP_ENV, "YES")
    monkeypatch.setenv(R.HALT_URL_ENV, "http://halt.internal:8080")
    monkeypatch.setenv(R.REGISTRY_ENV, "")
    monkeypatch.setattr(
        HG.HttpHaltStore, "get",
        lambda self, key: (served_bytes(st)[1] if key == HG.HALT_KEY
                           else (_ for _ in ()).throw(
                               HG.HaltReadUnavailable("not served"))))
    rc = R.main([])
    out = capsys.readouterr().out
    assert rc == 0, out
    body = json.loads(out.split(" ", 1)[1])
    assert body["sha256"] == digest
    assert body["medium"] == "http_read_only"
    assert st.get(R.AUDIT_KEY) is None


# --------------------------------------------------------------------------
# THE WHOLE PROCEDURE, end to end, on a throwaway volume
# --------------------------------------------------------------------------
def test_the_full_procedure_clears_a_TEMP_latch_and_audits_it(tmp_path,
                                                              monkeypatch):
    """Proves the clear works -- on tmp_path, never on production.

    This is the rehearsal the owner asked for: inspect to get the digest,
    then clear with that digest, with the audit entry present afterwards.
    """
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)

    # 1. INSPECT over HTTP, exactly as the real procedure will
    http = HG.HttpHaltStore("http://halt.internal:8080",
                            opener=lambda url, t: served_bytes(st)[1])
    seen = R.inspect(store=http, transport=FakeTransport(),
                     registry_url="http://registry.local",
                     registry_opener=fresh_registry())
    assert seen["sha256"] == digest
    assert seen["preconditions"]["venue"]["ok"] is True
    assert seen["preconditions"]["registry"]["ok"] is True
    assert seen["would_clear"] is True
    assert st.get(HG.HALT_KEY) == raw, "inspect wrote nothing"

    # 2. CLEAR on the volume, with the digest the inspect produced
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE)
    R._require_reset_image()
    R._require_clearable_store(st)
    for k, v in env(**{R.EXPECT_ENV: seen["sha256"]}).items():
        monkeypatch.setenv(k, v)
    out = R.perform_reset(store=st, transport=FakeTransport(),
                          registry_url="http://registry.local",
                          registry_opener=fresh_registry())
    assert out["ok"] is True
    assert out["entry"]["reset_id"] == "reset-1"
    assert out["entry"]["prior_latch_sha256"] == digest

    # 3. the latch is gone and the attempt is on the record
    rec = st.get(HG.HALT_KEY)
    assert rec is None or json.loads(rec).get("halt") is False
    audit = st.get(R.AUDIT_KEY)
    assert audit, "the audit entry is the one-time guarantee"
    rows = [json.loads(l) for l in audit.decode().splitlines() if l.strip()]
    assert rows[-1]["reset_id"] == "reset-1"
    assert rows[-1]["prior_latch_sha256"] == digest


def test_a_stale_digest_refuses_after_the_latch_moved(tmp_path, monkeypatch):
    """Gate 2's real purpose: the watchdog re-latched for a NEW reason
    between the review and the reset, so the digest moved."""
    st = volume_store(tmp_path)
    _, old_digest = file_latch(st, reason="UNMANAGED_RESTING_ORDER")
    file_latch(st, reason="A_COMPLETELY_DIFFERENT_HAZARD")   # re-latched
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE)
    for k, v in env(**{R.EXPECT_ENV: old_digest}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(R.ResetRefused):
        R.perform_reset(store=st, transport=FakeTransport(),
                        registry_url="http://registry.local",
                        registry_opener=fresh_registry())
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True
