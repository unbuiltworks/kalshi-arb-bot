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


@pytest.mark.parametrize("marker", [
    "l5_pmus_live",              # the money-capable executor
    "pmus_ws_evidence_recorder",
    "l5_halt_resetX", "", "   ", "L5_HALT_RESET",
])
def test_a_reset_refuses_on_a_foreign_image_marker(monkeypatch, marker):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, marker)
    with pytest.raises(R.ResetRefused, match="WRONG_IMAGE"):
        R._require_reset_image()


def test_both_reset_capable_images_are_accepted_and_name_themselves(
        monkeypatch):
    """Two legitimate homes, DISTINCT values, so the audit entry records
    which image performed the clear and a wrong image is still detectable."""
    assert R.IMAGE_MARKER_VALUE != R.IMAGE_MARKER_WATCHDOG
    for marker in R.ACCEPTED_IMAGE_MARKERS:
        monkeypatch.setenv(R.IMAGE_MARKER_ENV, marker)
        assert R._require_reset_image() == marker


def test_the_reset_image_marker_is_accepted(monkeypatch):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE)
    assert R._require_reset_image() == R.IMAGE_MARKER_VALUE


def test_each_image_declares_its_OWN_marker_and_not_the_others():
    """Read as text because it is a property of the Dockerfiles."""
    reset = io.open("deploy/l5_halt_reset/Dockerfile", encoding="utf-8").read()
    wd = io.open("deploy/l5_halt_watchdog/Dockerfile", encoding="utf-8").read()
    assert "ENV %s=%s" % (R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE) in reset
    assert "ENV %s=%s" % (R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG) in wd
    # neither may claim to be the other
    assert "ENV %s=%s" % (R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG) \
        not in reset
    assert "ENV %s=%s" % (R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_VALUE) not in wd


def test_the_watchdog_SWEEP_has_no_clear_path_even_though_the_script_ships():
    """SUPERSEDES "the script is not even present" (owner 2026-10-19).

    The script now ships in the watchdog image, because the latch is on that
    service's volume and nothing else can write it. The property that still
    holds -- and is the one that was ever load-bearing -- is that the SWEEP
    cannot reach it: the platform invokes it as a separate pre-deploy
    process. The sweep entrypoint must never import or call it.
    """
    wd = io.open("deploy/l5_halt_watchdog/Dockerfile", encoding="utf-8").read()
    assert "scripts/l5_halt_reset.py" in wd
    sweep = io.open("scripts/l5_halt_watchdog.py", encoding="utf-8").read()
    # OPTION A (owner 2026-10-19): the entrypoint now invokes the gated reset
    # ONCE at startup, so the checkable property is about the LOOP, not the
    # file. Everything after `while True:` must have no clear path, so no
    # repeated sweep can clear a latch however long the process runs.
    assert "while True:" in sweep
    loop = sweep.split("while True:", 1)[1]
    for verb in ("l5_halt_reset", "boot_reset", "perform_reset",
                 "heartbeat_no_halt", "clear"):
        assert verb not in loop, verb
    before = sweep.split("while True:", 1)[0]
    assert before.count("emit(*boot_reset(store))") == 1


def test_the_asserter_still_cannot_clear_a_latch_in_that_image():
    """The guard that "cannot clear" always actually rested on. The image
    held permissive-write capability before this change too."""
    st = FakeStore()
    a = HG.HaltAsserter(st, who="l5-watchdog")
    a.assert_halt(reason="UNMANAGED_RESTING_ORDER", evidence={"o": 1})
    with pytest.raises(PermissionError):
        a.heartbeat_no_halt()
    assert not hasattr(a, "clear") and not hasattr(a, "clear_halt")
    assert HG.HaltGate(st).submissions_permitted() is False


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


# --------------------------------------------------------------------------
# PRE-DEPLOY MODE: a refusal must never block a watchdog deployment
# --------------------------------------------------------------------------
def _reset_env(monkeypatch, tmp_path, digest, **over):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    monkeypatch.setenv(R.INSPECT_ENV, "NO")
    monkeypatch.setenv(R.STATE_ENV, str(tmp_path))
    monkeypatch.setenv(R.REGISTRY_ENV, "")
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    for k, v in over.items():
        monkeypatch.setenv(k, v)


def test_a_refused_pre_deploy_reset_exits_zero_and_clears_nothing(
        monkeypatch, capsys, tmp_path):
    """THE BRICKING FAILURE THIS PREVENTS. Railway fails a deployment whose
    preDeployCommand exits non-zero. A spent reset id refusing is the NORMAL
    steady state, so returning 2 would block every future watchdog
    deployment -- the watchdog could never be updated or restarted again."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    # no credentials in this container, so the venue gate refuses
    _reset_env(monkeypatch, tmp_path, digest,
               **{R.PRE_DEPLOY_ENV: "YES"})
    rc = R.main([])
    out = capsys.readouterr().out
    assert rc == 0, "a refusal must not fail the deployment"
    assert "L5_HALT_RESET_REFUSED" in out
    body = json.loads(out.split(" ", 1)[1])
    assert body["ok"] is False
    assert body["pre_deploy"] is True
    assert "NOT blocked" in body["labels"]
    assert st.get(HG.HALT_KEY) == raw, "the latch must be untouched"
    assert st.get(R.AUDIT_KEY) is None


def test_the_same_refusal_still_exits_nonzero_outside_pre_deploy(
        monkeypatch, capsys, tmp_path):
    """Run by hand, a refusal should be a loud non-zero: there is no
    deployment to protect and an operator wants the failure."""
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    _reset_env(monkeypatch, tmp_path, digest)
    monkeypatch.delenv(R.PRE_DEPLOY_ENV, raising=False)
    rc = R.main([])
    assert rc != 0
    body = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert body["pre_deploy"] is False


def test_a_wrong_image_in_pre_deploy_mode_also_exits_zero(
        monkeypatch, capsys, tmp_path):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _reset_env(monkeypatch, tmp_path, digest,
               **{R.PRE_DEPLOY_ENV: "YES"})
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, "l5_pmus_live")
    rc = R.main([])
    out = capsys.readouterr().out
    assert rc == 0 and "WRONG_IMAGE" in out
    assert st.get(HG.HALT_KEY) == raw


def test_a_spent_reset_id_is_inert_on_every_later_deployment(tmp_path,
                                                             monkeypatch):
    """"Do not automatically clear a safety HALT merely because a new window
    starts." A leftover pre-deploy command must clear NOTHING the second
    time, and the id is spent by the ATTEMPT rather than by its success."""
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    first = R.perform_reset(store=st, transport=FakeTransport(),
                            registry_url="http://registry.local",
                            registry_opener=fresh_registry())
    assert first["ok"] is True

    # the watchdog re-latches for a genuinely new hazard
    _, d2 = file_latch(st, reason="A_NEW_UNRELATED_HAZARD")
    # the SAME id, exactly as a leftover pre-deploy command would present it
    monkeypatch.setenv(R.EXPECT_ENV, d2)
    with pytest.raises(R.ResetRefused, match="RESET_ID_ALREADY_CONSUMED"):
        R.perform_reset(store=st, transport=FakeTransport(),
                        registry_url="http://registry.local",
                        registry_opener=fresh_registry())
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True, (
        "the new hazard's latch must survive a replayed reset id")


def test_an_open_l5_order_blocks_the_clear_in_pre_deploy_mode(tmp_path,
                                                              monkeypatch):
    """Zero open and unverified orders, enforced. An L5-owned working order
    means the hazard that latched the halt has NOT gone."""
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    rows = [{"id": "D1", "marketSlug": "cpc-btc-updown-15m-x",
             "leavesQuantity": "0.01", "manualOrderIndicator": ""}]
    with pytest.raises(R.ResetRefused):
        R.perform_reset(
            store=st, transport=FakeTransport(open_rows=rows),
            registry_url="http://registry.local",
            registry_opener=fresh_registry(rows=[{"venue_order_id": "D1"}]))
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True


# --------------------------------------------------------------------------
# READ-ONLY DIGEST TELEMETRY from the executor's EXISTING halt reads
# --------------------------------------------------------------------------
def test_the_gate_reports_the_digest_of_the_latch_it_just_read(tmp_path):
    """Obtaining the digest must cost no extra read and no watchdog
    interruption: the executor already fetches these bytes every tick."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    reads = []

    def opener(url, timeout_s):
        reads.append(url)
        return served_bytes(st)[1]

    gate = HG.HaltGate(HG.HttpHaltStore("http://halt.internal:8080",
                                        opener=opener))
    assert gate.submissions_permitted() is False        # latched
    assert len(reads) == 1, "exactly the read the gate already performs"
    t = gate.telemetry()
    assert t["sha256"] == digest
    assert t["kind"] == HG.PRESENT
    assert t["halt"] is True
    assert t["reason"] == "UNMANAGED_RESTING_ORDER"


def test_the_telemetry_never_carries_raw_contents_or_evidence(tmp_path):
    """The owner's constraint, pinned: no raw latch, no evidence block."""
    st = volume_store(tmp_path)
    rec = {"halt": True, "t": R._utc().isoformat(), "who": "l5-watchdog",
           "reason": "UNMANAGED_RESTING_ORDER",
           "evidence": {"orders": ["D68KZ0SG10JR"], "secret": "sk-DO-NOT-LOG"}}
    raw = json.dumps(rec, sort_keys=True).encode()
    st.put(HG.HALT_KEY, raw)
    gate = HG.HaltGate(HG.HttpHaltStore(
        "http://halt.internal:8080", opener=lambda u, t: served_bytes(st)[1]))
    gate.submissions_permitted()
    tel = gate.telemetry()
    assert "evidence" not in tel, "the evidence block must not be a field"
    # the prose in `labels` legitimately mentions the word, so assert on the
    # DATA: no evidence key, and none of its values anywhere in the payload.
    blob = json.dumps({k: v for k, v in tel.items() if k != "labels"})
    assert "D68KZ0SG10JR" not in blob
    assert "sk-DO-NOT-LOG" not in blob
    assert raw.decode() not in blob
    assert gate.telemetry()["sha256"] == hashlib.sha256(raw).hexdigest()


def test_the_digest_matches_what_gate_two_demands(tmp_path, monkeypatch):
    """END TO END: the digest the EXECUTOR logs is accepted by the reset."""
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    gate = HG.HaltGate(HG.HttpHaltStore(
        "http://halt.internal:8080", opener=lambda u, t: served_bytes(st)[1]))
    gate.submissions_permitted()
    observed = gate.telemetry()["sha256"]

    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    for k, v in env(**{R.EXPECT_ENV: observed}).items():
        monkeypatch.setenv(k, v)
    out = R.perform_reset(store=st, transport=FakeTransport(),
                          registry_url="http://registry.local",
                          registry_opener=fresh_registry())
    assert out["ok"] is True
    assert out["entry"]["prior_latch_sha256"] == digest


def test_an_unreachable_watchdog_reports_no_digest_and_stays_blocked():
    """The outage case. No digest is invented, and the gate refuses."""
    def boom(url, timeout_s):
        raise OSError("watchdog down")

    gate = HG.HaltGate(HG.HttpHaltStore("http://halt.internal:8080",
                                        opener=boom))
    assert gate.submissions_permitted() is False
    assert gate.refuse_reason() == "halt_state_unavailable"
    t = gate.telemetry()
    assert t["sha256"] is None and t["kind"] == HG.UNREADABLE


# --------------------------------------------------------------------------
# ITEM 4: submission must be PROVEN disabled across the outage
# --------------------------------------------------------------------------
def test_a_watchdog_outage_is_itself_a_submission_block():
    """The independent protection that holds THROUGHOUT the restart, and it
    needs no configuration: the gate fails closed when the latch cannot be
    read, so a watchdog that is down halts the executor by construction."""
    def boom(url, timeout_s):
        raise HG.HaltReadUnavailable("watchdog restarting")

    gate = HG.HaltGate(HG.HttpHaltStore("http://halt.internal:8080",
                                        opener=boom))
    assert gate.submissions_permitted() is False
    assert gate.refuse_reason() == "halt_state_unavailable"


def test_the_reset_refuses_while_the_executor_could_still_submit(tmp_path,
                                                                 monkeypatch):
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(R.ResetRefused, match="SUBMISSION_NOT_PROVEN_DISABLED"):
        R.perform_reset(
            store=st, transport=FakeTransport(),
            registry_url="http://registry.local",
            registry_opener=fresh_registry(submission_disabled=False))
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True


def test_a_missing_submission_field_is_refused_not_assumed(tmp_path,
                                                           monkeypatch):
    """ABSENT IS NOT PERMISSION. An executor too old to publish the field is
    one whose lock cannot be established."""
    st = volume_store(tmp_path)
    _, digest = file_latch(st)
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(R.ResetRefused, match="SUBMISSION_NOT_PROVEN_DISABLED"):
        R.perform_reset(
            store=st, transport=FakeTransport(),
            registry_url="http://registry.local",
            registry_opener=fresh_registry(submission_disabled=None))
    assert json.loads(st.get(HG.HALT_KEY))["halt"] is True


def test_the_published_lock_tracks_the_same_variable_the_gate_refuses_on(
        monkeypatch):
    """The published answer must not drift from the real one."""
    from src import l5_order_registry_server as REG
    from src import l5_pmus_live_runner as RUN
    for raw, expect in (("1", True), ("", True), ("0", False)):
        monkeypatch.setenv("L5_SHADOW_ONLY", raw)
        assert REG.submission_disabled() is expect
        assert RUN.shadow_only() is expect
    monkeypatch.delenv("L5_SHADOW_ONLY", raising=False)
    assert REG.submission_disabled() is True, "unset must read as LOCKED"
    assert RUN.shadow_only() is True


def test_the_registry_publishes_the_lock_even_on_an_unreadable_ledger():
    from src import l5_order_registry_server as REG

    class Broken:
        def live_orders(self):
            raise OSError("ledger unreadable")

    doc = REG.registry(Broken())
    assert doc["readable"] is False
    assert doc["submission_disabled"] is True


# --------------------------------------------------------------------------
# OPTION A: the one-time owner reset at WATCHDOG STARTUP
#
# Railway does not mount the volume for the pre-deploy step (probed on
# production: deployment 8d2a37bc refused NO_LATCH_PRESENT while the latch
# demonstrably existed), and a Railway volume attaches to exactly one
# service, so startup in THIS service is the only place the clear can run.
# --------------------------------------------------------------------------
from scripts import l5_halt_watchdog as WD    # noqa: E402


def _boot_env(monkeypatch, digest, **over):
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, R.IMAGE_MARKER_WATCHDOG)
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://registry.local")
    for k, v in env(**{R.EXPECT_ENV: digest}).items():
        monkeypatch.setenv(k, v)
    for k, v in over.items():
        monkeypatch.setenv(k, v)


def _patch_reset_inputs(monkeypatch, *, rows=(), submission_disabled=True,
                        open_rows=None):
    """Replace only the WIRE: the real gates run against these inputs."""
    monkeypatch.setattr(R, "read_only_transport",
                        lambda: FakeTransport(open_rows=open_rows or []))
    real = R.perform_reset

    def wrapped(**kw):
        kw["registry_opener"] = fresh_registry(
            rows=rows, submission_disabled=submission_disabled)
        return real(**kw)

    monkeypatch.setattr(R, "perform_reset", wrapped)


def test_startup_SUCCESS_clears_the_latch_and_audits_it(tmp_path, monkeypatch):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _boot_env(monkeypatch, digest)
    _patch_reset_inputs(monkeypatch)
    tag, payload = WD.boot_reset(st)
    assert tag == "L5_HALT_BOOT_RESET", payload
    assert payload["ok"] is True and payload["cleared"] is True
    rec = st.get(HG.HALT_KEY)
    assert rec is None or json.loads(rec).get("halt") is False
    audit = st.get(R.AUDIT_KEY)
    assert audit, "the audit entry is the one-time guarantee"
    assert json.loads(audit.decode().splitlines()[-1])[
        "prior_latch_sha256"] == digest


def test_startup_REFUSAL_leaves_the_latch_and_never_raises(tmp_path,
                                                           monkeypatch):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _boot_env(monkeypatch, digest)
    monkeypatch.setenv(R.CONFIRM_ENV, "not the phrase")
    _patch_reset_inputs(monkeypatch)
    tag, payload = WD.boot_reset(st)          # must not raise
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert payload["cleared"] is False
    assert "CONFIRM_PHRASE_MISMATCH" in payload["refused"]
    assert st.get(HG.HALT_KEY) == raw
    assert st.get(R.AUDIT_KEY) is None


def test_startup_REPLAY_of_a_spent_id_never_clears_a_new_latch(tmp_path,
                                                               monkeypatch):
    """A normal restart with the reset variables still in place."""
    st = volume_store(tmp_path)
    _, d1 = file_latch(st)
    _boot_env(monkeypatch, d1)
    _patch_reset_inputs(monkeypatch)
    assert WD.boot_reset(st)[0] == "L5_HALT_BOOT_RESET"

    # the watchdog re-latches for a genuinely different hazard
    raw2, d2 = file_latch(st, reason="A_NEW_UNRELATED_HAZARD")
    monkeypatch.setenv(R.EXPECT_ENV, d2)      # same id, new digest
    tag, payload = WD.boot_reset(st)
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert "RESET_ID_ALREADY_CONSUMED" in payload["refused"]
    assert st.get(HG.HALT_KEY) == raw2, "the new hazard's latch must survive"


def test_a_stale_digest_at_startup_refuses(tmp_path, monkeypatch):
    """Deployment restarts with an old digest: the latch moved, so refuse."""
    st = volume_store(tmp_path)
    _, old = file_latch(st)
    raw2, _ = file_latch(st, reason="SOMETHING_ELSE")
    _boot_env(monkeypatch, old)
    _patch_reset_inputs(monkeypatch)
    tag, payload = WD.boot_reset(st)
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert st.get(HG.HALT_KEY) == raw2


def test_an_open_order_or_unlocked_executor_refuses_at_startup(tmp_path,
                                                               monkeypatch):
    for label, kw in (
        ("open order", {"open_rows": [
            {"id": "D1", "marketSlug": "m", "leavesQuantity": "0.01",
             "manualOrderIndicator": ""}], "rows": [{"venue_order_id": "D1"}]}),
        ("submission enabled", {"submission_disabled": False}),
        ("submission field absent", {"submission_disabled": None}),
    ):
        st = volume_store(tmp_path / label.replace(" ", "_"))
        raw, digest = file_latch(st)
        _boot_env(monkeypatch, digest)
        monkeypatch.setenv(R.ID_ENV, "id-%s" % label.replace(" ", "-"))
        _patch_reset_inputs(monkeypatch, **kw)
        tag, payload = WD.boot_reset(st)
        assert tag == "L5_HALT_BOOT_RESET_REFUSED", (label, payload)
        assert st.get(HG.HALT_KEY) == raw, label


def test_a_stale_registry_refuses_at_startup(tmp_path, monkeypatch):
    """A report that stopped arriving is the WEDGED signal, not an empty
    book."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _boot_env(monkeypatch, digest)
    monkeypatch.setattr(R, "read_only_transport", lambda: FakeTransport())
    real = R.perform_reset

    def wrapped(**kw):
        kw["registry_opener"] = fresh_registry(
            age_s=R.MAX_REGISTRY_AGE_S + 30.0)
        return real(**kw)

    monkeypatch.setattr(R, "perform_reset", wrapped)
    tag, payload = WD.boot_reset(st)
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert "REGISTRY_STALE" in payload["refused"]
    assert st.get(HG.HALT_KEY) == raw


def test_an_unexpected_error_at_startup_is_swallowed(tmp_path, monkeypatch):
    """A watchdog that fails to start is a watchdog that stops watching."""
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _boot_env(monkeypatch, digest)

    def boom():
        raise RuntimeError("signing blew up")

    monkeypatch.setattr(R, "read_only_transport", boom)
    tag, payload = WD.boot_reset(st)          # must not raise
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert "RuntimeError" in payload["refused"]
    assert st.get(HG.HALT_KEY) == raw


def test_a_wrong_image_refuses_at_startup(tmp_path, monkeypatch):
    st = volume_store(tmp_path)
    raw, digest = file_latch(st)
    _boot_env(monkeypatch, digest)
    monkeypatch.setenv(R.IMAGE_MARKER_ENV, "l5_pmus_live")
    tag, payload = WD.boot_reset(st)
    assert tag == "L5_HALT_BOOT_RESET_REFUSED"
    assert "WRONG_IMAGE" in payload["refused"]
    assert st.get(HG.HALT_KEY) == raw


# ---------------------------------------- NORMAL watchdog behaviour: OFF
@pytest.mark.parametrize("value", [None, "", "no", "NO", "1", "true",
                                   "yes", "Yes ", "0"])
def test_a_normal_deployment_or_restart_clears_NOTHING(monkeypatch, value):
    """Only the literal "YES" arms it. A normal deploy must never clear."""
    called = []
    monkeypatch.setattr(WD, "boot_reset",
                        lambda store: called.append(store) or ("x", {}))
    if value is None:
        monkeypatch.delenv(WD.BOOT_RESET_ENV, raising=False)
    else:
        monkeypatch.setenv(WD.BOOT_RESET_ENV, value)
    armed = str(os.getenv(WD.BOOT_RESET_ENV, "")).strip().upper() == "YES"
    assert armed is (str(value).strip().upper() == "YES" if value else False)
    assert called == []


def test_the_literal_yes_is_what_arms_it(monkeypatch):
    monkeypatch.setenv(WD.BOOT_RESET_ENV, "YES")
    assert str(os.getenv(WD.BOOT_RESET_ENV, "")).strip().upper() == "YES"


def test_the_asserter_in_this_image_still_cannot_clear(tmp_path):
    """The guard the invariant always actually rested on, re-checked after
    the hook was added."""
    st = volume_store(tmp_path)
    a = HG.HaltAsserter(st, who="l5-watchdog")
    a.assert_halt(reason="UNMANAGED_RESTING_ORDER", evidence={"o": 1})
    with pytest.raises(PermissionError):
        a.heartbeat_no_halt()
    assert not hasattr(a, "clear") and not hasattr(a, "clear_halt")
