"""The CA bundle must not be pinned to one container.

WHAT THIS COST. `order_id_forensics._ctx()` hardcoded this session's
agent-proxy CA bundle. In the deployed image that path does not exist, so the
context raised, rpc()'s bare `except Exception` swallowed it, and every
transaction receipt came back None. Every parent was then recorded with an
EMPTY leg list -- which is indistinguishable from a receipt that genuinely
contains no L5 order. Three prospective windows recorded 361 executions with
chain_receipt_share 0.0 before anyone noticed.
"""
import os
import ssl

from analysis.l5.causal import order_id_forensics as OF
from src.l5_chain_observer import observer as OB


def test_the_context_falls_back_when_the_bundle_is_absent(monkeypatch):
    monkeypatch.setattr(OF.os.path if hasattr(OF, "os") else os.path,
                        "exists", lambda p: False)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    ctx = OF._ctx()
    assert isinstance(ctx, ssl.SSLContext)


def test_the_context_uses_the_bundle_when_it_is_there():
    if not os.path.exists(OF.CA):
        return
    assert isinstance(OF._ctx(), ssl.SSLContext)


def test_it_matches_the_observer_that_already_got_this_right():
    """The observer kept working in-container for exactly this reason. The
    two must not diverge again."""
    import inspect
    obs = inspect.getsource(OB._ca)
    ofs = inspect.getsource(OF._ctx)
    for src in (obs, ofs):
        assert "SSL_CERT_FILE" in src
        assert "create_default_context()" in src, "no unconditional fallback"


def test_no_module_pins_a_bundle_without_a_fallback():
    """A future module that pins it would fail the same silent way."""
    import io
    import pathlib
    bad = []
    for f in pathlib.Path("src").rglob("*.py"):
        t = io.open(f).read()
        if "ca-bundle.crt" in t and "create_default_context()" not in t:
            bad.append(str(f))
    for f in pathlib.Path("analysis").rglob("*.py"):
        t = io.open(f).read()
        if "ca-bundle.crt" in t and "create_default_context()" not in t:
            bad.append(str(f))
    assert not bad, "these pin a CA bundle with no fallback: %s" % bad
