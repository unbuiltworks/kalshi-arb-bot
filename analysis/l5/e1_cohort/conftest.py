"""Keep the suite fast without weakening the production readiness bound.

The live readiness wait is 20s by design. Pre-existing tests drive a feed
that is never ready and assert an immediate refusal, so with the real bound
each of them would sit for 20s and the suite would take an hour.

So the BOUND is shrunk for tests, and only the bound. The readiness
SEQUENCING -- start, poll, require frames for both exact token ids, verify
freshness, classify the timeout -- is the production code either way, and
the tests that care about sequencing pass their own explicit values rather
than relying on this.

`provider` reads these two module values at CALL time precisely so this
fixture can work without a default argument freezing them at import.
"""
from __future__ import annotations

import pytest

from analysis.l5.e1_cohort import provider as P

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
