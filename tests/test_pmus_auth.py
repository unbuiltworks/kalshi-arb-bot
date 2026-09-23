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
import sys
import types

import pytest

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
    monkeypatch.setitem(sys.modules, ED_MOD, mod)
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
