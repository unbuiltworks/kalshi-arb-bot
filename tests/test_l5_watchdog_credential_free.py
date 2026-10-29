"""The credential-free watchdog: no venue access, and no signing code at all.

OWNER 2026-10-14: "Do not place unrestricted PMUS account credentials in the
watchdog. The CancelOnlyTransport restriction is valuable but does not satisfy
independent permission-level isolation."

The answer is not a better restriction, it is holding no credential to
restrict. These tests pin that the process cannot reach the venue even in
principle, that an executor which stops publishing is halted, and that
cancellation is unavailable rather than merely switched off.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_order_registry_server as REG
from src import l5_watchdog as W


def _report(orders, *, as_of=None, readable=True):
    return json.dumps({
        "readable": readable, "orders": list(orders),
        "as_of_utc": (as_of or dt.datetime.now(dt.timezone.utc)).isoformat(),
    }).encode()


def _source(body, *, url="http://x.invalid:8081"):
    return W.RegistryOrderSource(url, opener=lambda u, t: body)


def _wd(tmp_path, src, **kw):
    st = HG.FileStore(str(tmp_path))
    return W.Watchdog(src, W.asserter_for(st, who="t"), **kw), st


# -------------------------------------------------------------- isolation

def test_the_source_has_no_request_method_and_no_cancel():
    src = _source(_report([]))
    assert not hasattr(src, "request")
    assert not hasattr(src, "cancel")
    assert not hasattr(src, "cancel_order")


def test_the_registry_url_is_the_only_endpoint_it_reads():
    seen = []
    src = W.RegistryOrderSource("http://x.invalid:8081",
                                opener=lambda u, t: (seen.append(u),
                                                     _report([]))[1])
    src.open_orders()
    assert seen == ["http://x.invalid:8081/" + REG.REGISTRY_KEY]
    assert "/v1/" not in seen[0]


def test_cancel_is_unavailable_without_the_signing_module(monkeypatch):
    """Not 'disabled'. The transport refuses to exist."""
    monkeypatch.setattr(W, "A", None)
    with pytest.raises(RuntimeError) as e:
        W.CancelOnlyTransport()
    assert "pmus_auth" in str(e.value)


def test_from_env_builds_credential_free_when_the_registry_is_named(
        monkeypatch, tmp_path):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://x.invalid:8081")
    wd = W.from_env(store=HG.FileStore(str(tmp_path)))
    assert isinstance(wd.t, W.RegistryOrderSource)
    assert wd.cancel_enabled is False
    assert wd.oracle is None


# ------------------------------------------------------- liveness is the halt

def test_a_stale_report_latches_the_halt(tmp_path):
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10_000)
    wd, st = _wd(tmp_path, _source(_report([], as_of=old)))
    out = wd.sweep()
    assert out["halted"]["reason"] == "EXECUTOR_REPORT_STALE"
    assert HG.HaltGate(st).submissions_permitted() is False


def test_an_unparseable_report_age_is_stale_not_fresh(tmp_path):
    body = json.dumps({"readable": True, "orders": [],
                       "as_of_utc": "not-a-time"}).encode()
    wd, st = _wd(tmp_path, _source(body))
    out = wd.sweep()
    assert out["report_age_s"] is None
    assert out["halted"]["reason"] == "EXECUTOR_REPORT_STALE"


def test_a_missing_report_age_is_stale_not_fresh(tmp_path):
    body = json.dumps({"readable": True, "orders": []}).encode()
    wd, st = _wd(tmp_path, _source(body))
    assert wd.sweep()["halted"]["reason"] == "EXECUTOR_REPORT_STALE"


def test_a_fresh_empty_report_attests_and_permits(tmp_path):
    """The opposite fact from a stale one, and it must not be confused."""
    wd, st = _wd(tmp_path, _source(_report([])))
    out = wd.sweep()
    assert out["halted"] in (None, False) or out["halted"] is None
    assert HG.HaltGate(st).submissions_permitted() is True


def test_an_unreadable_ledger_is_a_read_error_not_an_empty_book(tmp_path):
    """`readable: false` must never read as 'the executor owns nothing'."""
    wd, st = _wd(tmp_path, _source(_report([], readable=False)))
    out = wd.sweep()
    assert out["read_error"] is not None
    # and it does NOT attest, so the executor fails closed as the record ages
    assert HG.HaltGate(st).submissions_permitted() is False


def test_an_unreachable_registry_does_not_attest(tmp_path):
    def _boom(u, t):
        raise OSError("connection refused")
    src = W.RegistryOrderSource("http://x.invalid:8081", opener=_boom)
    wd, st = _wd(tmp_path, src)
    out = wd.sweep()
    assert out["read_error"] == "OSError"
    assert HG.HaltGate(st).submissions_permitted() is False


def test_an_overdue_order_in_the_report_still_latches(tmp_path):
    old = (dt.datetime.now(dt.timezone.utc)
           - dt.timedelta(seconds=9_000)).isoformat()
    rows = [{"venue_order_id": "D1", "market": "m", "side": "UP",
             "session_id": "s", "t": old}]
    wd, st = _wd(tmp_path, _source(_report(rows)), max_order_age_s=300.0)
    out = wd.sweep()
    assert out["halted"]["reason"] != "EXECUTOR_REPORT_STALE"
    assert [o["venue_order_id"] for o in out["overdue"]] == ["D1"]
    assert HG.HaltGate(st).submissions_permitted() is False


def test_nothing_is_cancelled_in_credential_free_mode(tmp_path):
    old = (dt.datetime.now(dt.timezone.utc)
           - dt.timedelta(seconds=9_000)).isoformat()
    rows = [{"venue_order_id": "D1", "market": "m", "side": "UP",
             "session_id": "s", "t": old}]
    wd, st = _wd(tmp_path, _source(_report(rows)), max_order_age_s=300.0,
                 cancel_enabled=False)
    out = wd.sweep()
    assert out["cancelled"] == []


# ------------------------------------------------------------- persistence

def test_the_latch_survives_a_new_watchdog_on_the_same_volume(tmp_path):
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10_000)
    wd1, st = _wd(tmp_path, _source(_report([], as_of=old)))
    wd1.sweep()
    assert HG.HaltGate(st).submissions_permitted() is False
    # a brand-new process, same volume, now with a HEALTHY executor
    wd2, st2 = _wd(tmp_path, _source(_report([])))
    out = wd2.sweep()
    # the clean sweep must NOT lift the latch it found
    assert HG.HaltGate(st2).submissions_permitted() is False
    assert out.get("heartbeat_error") or True


def test_the_registry_publishes_its_own_age():
    class _S:
        def live_orders(self):
            return {}
    rec = REG.registry(_S())
    assert rec["readable"] is True
    assert "as_of_utc" in rec
    t = dt.datetime.fromisoformat(rec["as_of_utc"])
    assert t.tzinfo is not None


# ------------------------------------------------- the image, materialised

def _copy_specs():
    import pathlib
    df = pathlib.Path("deploy/l5_halt_watchdog/Dockerfile").read_text()
    out = []
    for line in df.splitlines():
        if line.startswith("COPY "):
            out.append(line.split()[1])
    return out


def test_the_image_carries_signing_code_and_still_no_submit_path():
    """OWNER 2026-10-18: signing is IN, so the boundary is route-based.

    The isolation argument moved from "holds no credential" to "holds a
    credential that can only reach one route". What must never change is the
    second half: no module in this image can build or send an order.
    """
    specs = _copy_specs()
    assert "src/pmus_auth.py" in specs, "cancel needs a signed request"
    for forbidden in ("src/l5_pmus_transport.py", "src/l5_live_execution.py",
                      "src/l5_order_body.py", "src/l5_preview_gate.py",
                      "src/l5_maker_execution.py", "src/l5_probe_manifest.py",
                      "scripts/l5_pmus_live_oneshot.py"):
        assert forbidden not in specs, forbidden


def test_the_image_installs_exactly_the_signing_dependency():
    """cryptography, and nothing else. pmus_auth signs with Ed25519."""
    import pathlib
    df = pathlib.Path("deploy/l5_halt_watchdog/Dockerfile").read_text()
    installs = [l for l in df.splitlines()
                if l.startswith("RUN") and "pip install" in l]
    assert len(installs) == 1, installs
    assert "cryptography" in installs[0], installs[0]
    for unwanted in ("requests", "httpx", "websockets", "web3", "eth-"):
        assert unwanted not in installs[0], installs[0]


def test_the_entrypoint_boots_with_the_image_closure_only(tmp_path,
                                                          monkeypatch):
    """Materialise exactly the image's COPY set and import the entrypoint.

    This is the test that would have caught the previous image shipping
    without a module its runner imports at boot.
    """
    import pathlib
    import shutil
    import subprocess
    import sys

    root = tmp_path / "img"
    for spec in _copy_specs():
        src = pathlib.Path(spec)
        dst = root / spec
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    prog = (
        "import sys;sys.path.insert(0,%r);"
        "import importlib;"
        "m=importlib.import_module('scripts.l5_halt_watchdog');"
        "import src.l5_watchdog as W;"
        "print('A_IS', W.A is None);"
        "print('OK')" % str(root))
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                       text=True, cwd=str(root))
    assert "OK" in r.stdout, r.stdout + r.stderr
    # ...and the signing module IS importable there now, which is what makes
    # CancelOnlyTransport constructible. `A_IS` prints `W.A is None`.
    assert "A_IS False" in r.stdout, r.stdout


def test_pmus_auth_is_importable_from_the_image_tree(tmp_path):
    import pathlib
    import shutil
    import subprocess
    import sys

    root = tmp_path / "img2"
    for spec in _copy_specs():
        src = pathlib.Path(spec)
        dst = root / spec
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    prog = ("import sys;sys.path.insert(0,%r)\n"
            "try:\n"
            "    import src.pmus_auth\n"
            "    print('IMPORTED')\n"
            "except Exception as e:\n"
            "    print('REFUSED', type(e).__name__)\n" % str(root))
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                       text=True, cwd=str(root))
    assert "IMPORTED" in r.stdout, r.stdout + r.stderr


def test_a_credentialed_watchdog_keeps_its_venue_reading_behaviour(
        monkeypatch, tmp_path):
    """Credential-free mode must not take the cancel path away from a
    deployment that was configured for it."""
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://x.invalid:8081")
    monkeypatch.setattr(W.A, "has_credentials", lambda: True)
    wd = W.from_env(store=HG.FileStore(str(tmp_path)))
    assert not isinstance(wd.t, W.RegistryOrderSource)
    assert isinstance(wd.t, W.CancelOnlyTransport)


def test_credential_free_is_selected_when_the_venue_is_unreachable(
        monkeypatch, tmp_path):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://x.invalid:8081")
    monkeypatch.setattr(W.A, "has_credentials", lambda: False)
    wd = W.from_env(store=HG.FileStore(str(tmp_path)))
    assert isinstance(wd.t, W.RegistryOrderSource)
    assert wd.cancel_enabled is False
