"""Keep the suite fast without weakening the production readiness bound.

The live readiness wait is 20s by design. Tests drive a feed that is never
ready and assert an immediate refusal, so with the real bound each of them
would sit for 20s and the suite would take an hour.

So the BOUND is shrunk for tests, and only the bound. The readiness
SEQUENCING -- start the PMUS book, poll until THIS slug's book is
connected, two-sided and fresh, classify the timeout as a feed failure
rather than a market refusal -- is the production code either way.

`pmus_provider` reads these two module values at CALL time precisely so
this fixture can work without a default argument freezing them at import.
"""
from __future__ import annotations

import pytest

from analysis.l5.e1_cohort import pmus_provider as P

#: the real production values, asserted below so this file cannot quietly
#: become the reason a shipped build waits the wrong amount of time.
PRODUCTION_TIMEOUT_S = 20.0
PRODUCTION_POLL_S = 0.25

TEST_TIMEOUT_S = 0.05
TEST_POLL_S = 0.005


def test_production_defaults_are_what_we_think_they_are():
    """If someone changes the shipped bound, this says so."""
    assert P.WS_READY_TIMEOUT_S == PRODUCTION_TIMEOUT_S
    assert P.WS_READY_POLL_S == PRODUCTION_POLL_S


@pytest.fixture(autouse=True)
def _fast_ws_readiness(monkeypatch):
    """Shrink ONLY the bound, for ONLY the duration of each test."""
    monkeypatch.setattr(P, "WS_READY_TIMEOUT_S", TEST_TIMEOUT_S)
    monkeypatch.setattr(P, "WS_READY_POLL_S", TEST_POLL_S)
    yield


#: The production credential contract, satisfied with a key that belongs to
#: nobody. Production HAS credentials; their absence is the exceptional case
#: and belongs in the tests that are about it, not in every other test's
#: environment. So the default here is "present and usable", and
#: `test_e1_local_fault_taxonomy.py` removes them explicitly.
#:
#: This fixture exists because the 2026-10-07 incident was invisible to a
#: green suite: the armed tests used a fake transport that never signs, so
#: nothing exercised the signing contract at all.
@pytest.fixture(autouse=True)
def _production_credential_contract(monkeypatch):
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from src import pmus_auth as A
    k = ed25519.Ed25519PrivateKey.generate()
    raw = k.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    monkeypatch.setenv(A.KEY_ENV, "e1-suite-key-id")
    monkeypatch.setenv(A.SECRET_ENV, base64.b64encode(raw).decode())
    monkeypatch.delenv("PMUS_API_SECRET", raising=False)
    yield
