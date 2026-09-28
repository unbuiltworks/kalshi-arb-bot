"""The read-only auth module must sign EXACTLY as the executor does, and must
not be able to do anything else.

Two failures motivate these tests. If the signature drifts from
`pmus_q1_executor`'s, authenticated reads start answering 401 in production
and the drift is invisible until the preflight it feeds goes dark. And if this
module can be coaxed into a non-GET, then shipping it to the credential-holding
recorder puts an order path on that service after all, which is the single
thing its image promises it does not have.
"""
from __future__ import annotations

import ast
import base64
import importlib
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from src import pmus_auth as A
from src import pmus_q1_executor as Q1

SEED = base64.b64encode(bytes(range(32))).decode()

ED_MOD = "cryptography.hazmat.primitives.asymmetric.ed25519"


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(A.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(A.SECRET_ENV, SEED)


@pytest.fixture()
def fake_ed(monkeypatch):
    """Sign with a recording stub.

    The real Ed25519 backend is not loadable in every environment, and
    skipping there would silently retire the drift guard on exactly the
    property that breaks production. Substituting the primitive keeps the
    comparison meaningful: both modules must derive the same key seed and
    sign the same bytes, which is what can actually drift. The real backend
    is exercised where the code runs.
    """
    signed = []

    class Key:
        def __init__(self, seed):
            self.seed = seed

        def sign(self, msg):
            signed.append((self.seed, msg))
            return b"SIG" + msg

    mod = types.ModuleType(ED_MOD)
    mod.Ed25519PrivateKey = type(
        "Ed25519PrivateKey", (),
        {"from_private_bytes": staticmethod(Key)})

    # BOTH BINDINGS, OR THE STUB IS INERT (2026-09-28).
    #
    # `pmus_auth.signed_headers` reaches the primitive with
    #
    #     from cryptography.hazmat.primitives.asymmetric import ed25519
    #
    # and `from P import C` prefers the ATTRIBUTE `P.C` over `sys.modules["P.C"]`.
    # Patching sys.modules alone therefore works only while P has no such
    # attribute. Anything that imports the real submodule first sets it, and from
    # then on this fixture silently hands the production code the REAL backend:
    # `mine == theirs` still passes, `signed` stays empty, and the drift guard
    # reports green while measuring nothing.
    #
    # Measured trigger: `from src import main` (-> src.auth ->
    # cryptography.hazmat.primitives.asymmetric.padding) puts the parent in
    # sys.modules AND gives it a real `ed25519` attribute. pytest imports every
    # selected module during collection, so in a large run that happens before the
    # first test executes -- which is why this guard passed alone and failed in the
    # 385-file run, in the four signing parametrisations only.
    #
    # So patch the parent attribute too. monkeypatch restores both, and
    # `raising=False` covers the case where the parent is not imported yet.
    monkeypatch.setitem(sys.modules, ED_MOD, mod)
    parent_name, _, child = ED_MOD.rpartition(".")
    importlib.import_module(parent_name)          # make the binding exist to patch
    monkeypatch.setattr(sys.modules[parent_name], child, mod, raising=False)
    return signed


# ------------------------------------------------------------- equivalence

@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/account"),
    ("GET", "/v1/positions"),
    ("GET", "/v1/markets/cpc-btc-updown-15m-2026-09-23-2115z"),
    ("GET", "/v1/orders?limit=50&cursor=abc"),
])
def test_the_signature_is_byte_identical_to_the_executors(creds, fake_ed,
                                                          method, path):
    ts = 1_800_000_000_000
    mine = A.signed_headers(method, path, ts)
    theirs = Q1.signed_headers(method, path, ts)
    assert mine == theirs
    # and they reached the primitive with the same seed and the same bytes
    assert len(fake_ed) == 2 and fake_ed[0] == fake_ed[1]
    assert fake_ed[0][1] == f"{ts}{method}{A.sign_path(path)}".encode()


def test_the_signed_path_drops_the_query_string_exactly_as_the_executor_does():
    for p in ("/v1/orders?limit=50", "/v1/account", "/a?b=1?c=2"):
        assert A.sign_path(p) == Q1.sign_path(p)


def test_the_credential_env_names_and_hosts_match_the_executor():
    assert (A.KEY_ENV, A.SECRET_ENV) == (Q1.KEY_ENV, Q1.SECRET_ENV)
    assert (A.API, A.GATEWAY) == (Q1.API, Q1.GATEWAY)
    assert A.TICK_FIELD == Q1.TICK_FIELD


def test_credential_presence_agrees_with_the_executor(creds, monkeypatch):
    assert A.has_credentials() is Q1.has_credentials() is True
    monkeypatch.delenv(A.SECRET_ENV)
    assert A.has_credentials() is Q1.has_credentials() is False


# --------------------------------------------------------- no order path

def test_the_transport_refuses_every_mutating_method(creds):
    t = A.ReadOnlyTransport()
    for m in ("POST", "DELETE", "PUT", "PATCH", "post"):
        with pytest.raises(PermissionError):
            t.request(m, "/v1/orders", {"qty": 1})


def test_the_module_exposes_no_order_function():
    # `armed` is deliberately NOT banned: it is a boolean read of three
    # environment names and cannot mutate anything. What must not exist here
    # is any function that can reach the venue's order endpoints.
    banned = ("place_limit_buy", "cancel_exact", "cancel_all_deadman",
              "run_campaign", "wait_for_fill", "persist_intent")
    for name in banned:
        assert not hasattr(A, name), "%s must not exist here" % name


def test_no_order_endpoint_appears_in_executable_code():
    """Prose may explain why the order path is absent; code may not contain it.

    Checked on the AST with docstrings removed, so the module can document
    the reason it excludes /v1/orders without the check reading its own
    explanation as a violation.
    """
    import inspect
    tree = ast.parse(inspect.getsource(A))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]
    for s in literals:
        assert "/v1/order" not in s, "order endpoint in executable code: %r" % s
        assert s.upper() not in ("POST", "DELETE", "PUT", "PATCH"), \
            "mutating method literal in a read-only module: %r" % s


def test_armed_is_a_pure_env_read_and_touches_no_network(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("armed() must not open a socket")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.delenv(A.LIVE_ENV, raising=False)
    assert A.armed() is False
    monkeypatch.setenv(A.LIVE_ENV, "YES")
    monkeypatch.setenv(A.SID_ENV, "s1")
    monkeypatch.setenv(A.ARM_ENV, "s1")
    assert A.armed() is True
    monkeypatch.setenv(A.ARM_ENV, "DIFFERENT")
    assert A.armed() is False          # token must match the session id


def test_the_arm_contract_matches_the_executors_exactly():
    # one way to arm this account, not two
    assert (A.LIVE_ENV, A.SID_ENV, A.ARM_ENV) == \
        (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV)


def test_signing_needs_no_network_and_leaks_nothing_into_the_path(creds,
                                                                  fake_ed):
    h = A.signed_headers("GET", "/v1/account", 1)
    assert h["X-PM-Access-Key"] == "key-aaaaaaaaaaaa"
    assert SEED not in h["X-PM-Signature"]
    assert set(h) == {"X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature",
                      "Content-Type"}


# ============================================ ORDER INDEPENDENCE OF THE GUARD
# The signing drift guard must prove its property in EITHER import order. It
# previously could not: in a 385-file run something imported the real Ed25519
# backend during collection, the fixture's sys.modules stub went inert, and the
# guard passed `mine == theirs` while its instrumentation recorded nothing.
#
# Production signing was measured INVARIANT across that pollution -- byte-identical
# headers and mine == theirs before and after, on the real backend -- so nothing in
# `pmus_auth` changed. Only the fixture's isolation was repaired.

POLLUTER_IMPORT = "src.main"        # -> src.auth -> ...asymmetric.padding


def _pollute():
    """Reproduce the measured mutation: parent imported, real child attribute."""
    importlib.import_module(POLLUTER_IMPORT)
    parent = sys.modules["cryptography.hazmat.primitives.asymmetric"]
    assert hasattr(parent, "ed25519")          # the mutation that broke the stub
    return parent


def _assert_guard_holds(creds_unused, fake_ed, method="GET", path="/v1/account"):
    """BOTH substantive properties, in whatever order this is called."""
    ts = 1_800_000_000_000
    mine = A.signed_headers(method, path, ts)
    theirs = Q1.signed_headers(method, path, ts)
    assert mine == theirs, "signers drifted"
    # and the instrumentation was actually exercised by BOTH implementations
    assert len(fake_ed) == 2, (
        "stub recorded %d of 2 signer calls -- the guard is not measuring"
        % len(fake_ed))
    assert fake_ed[0] == fake_ed[1], "same seed and same bytes required"
    return mine


SIGNING = ("tests/test_pmus_auth.py::"
           "test_the_signature_is_byte_identical_to_the_executors")
POLLUTER_TEST = ("tests/test_deploy_fallthrough_guard.py::"
                 "test_src_main_still_runs_when_explicitly_opted_in")


def _pytest(*selectors):
    """Run pytest in a FRESH interpreter, so the order is a real import order.

    In-process ordering cannot express this: the fixture installs the stub before
    the test body runs, so polluting inside the body imports the real backend
    while the stub is in place and cryptography's own internals then resolve
    Ed25519PublicKey off the stub. The orders have to be separate sessions.
    """
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *selectors],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)


def test_the_guard_holds_in_the_canonical_order():
    """Order 1: signing first, in a clean interpreter."""
    out = _pytest(SIGNING)
    assert out.returncode == 0, out.stdout[-3000:]


def test_the_guard_holds_with_the_polluter_first():
    """Order 2: polluter -> signing. The order that used to defeat the guard.

    Before the fixture patched the parent attribute this produced exactly
    `assert (0 == 2)` on len(fake_ed), four times -- green on `mine == theirs`
    while measuring nothing.
    """
    out = _pytest(POLLUTER_TEST, SIGNING)
    assert "FAILED tests/test_pmus_auth.py" not in out.stdout, out.stdout[-3000:]


def test_the_guard_holds_with_signing_on_both_sides_of_the_polluter():
    """Order 3: signing -> polluter -> signing, as one session."""
    out = _pytest(SIGNING, POLLUTER_TEST, SIGNING)
    assert "FAILED tests/test_pmus_auth.py" not in out.stdout, out.stdout[-3000:]


def test_the_polluter_really_does_defeat_a_sys_modules_only_stub(monkeypatch):
    """Negative control. Without this, the two tests above prove nothing.

    Patch ONLY sys.modules -- the old fixture -- after the mutation, and the real
    backend must still be what production reaches, recording nothing.
    """
    _pollute()
    monkeypatch.setenv(A.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(A.SECRET_ENV, SEED)
    seen = []

    class Key:
        def __init__(self, seed):
            self.seed = seed

        def sign(self, msg):
            seen.append((self.seed, msg))
            return b"SIG" + msg

    mod = types.ModuleType(ED_MOD)
    mod.Ed25519PrivateKey = type("Ed25519PrivateKey", (),
                                 {"from_private_bytes": staticmethod(Key)})
    monkeypatch.setitem(sys.modules, ED_MOD, mod)      # the INCOMPLETE patch
    A.signed_headers("GET", "/v1/account", 1_800_000_000_000)
    assert seen == [], (
        "sys.modules-only stub was effective here, so the parent-attribute "
        "patch in fake_ed would be untested -- re-derive the mechanism")
