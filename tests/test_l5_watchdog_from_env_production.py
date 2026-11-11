"""`from_env` under the EXACT production variable combination.

Components passing in isolation is not the same as the service being wired
correctly, which is the whole reason this file exists. Each test sets the
full variable set a deployed watchdog would actually see and asserts what
`from_env` builds from it.

TWO MODES, and the difference is load-bearing:

  CREDENTIAL-FREE (deployed today). No signing module, so `_blind` is true
  and the watchdog reads the EXECUTOR'S OWN report through
  RegistryOrderSource. Cancel is unavailable by construction.

  CANCEL-ONLY AUTHENTICATED (owner's Option A). Signing module plus
  credentials, so the watchdog reads the VENUE directly through
  CancelOnlyTransport and can cancel exactly one named order per id it can
  prove it owns.

The mode switch changes WHICH WITNESS the sweep reads, not just whether
cancel is on, and that consequence is pinned explicitly below.
"""
from __future__ import annotations

import pytest

from src import l5_watchdog as W

REGISTRY = "http://l5-pmus-live.railway.internal:8081"


class FakeAuth:
    """Stands in for src/pmus_auth.py being present with credentials."""

    API = "https://api.polymarket.us"

    def __init__(self, creds=True):
        self._creds = creds

    def has_credentials(self):
        return self._creds


def production_env(monkeypatch, tmp_path, *, cancel="YES", registry=REGISTRY,
                   max_order_age="300", report_max_age="45"):
    """The variable set a deployed watchdog sees, nothing omitted."""
    monkeypatch.setenv("L5_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("L5_WATCHDOG_WHO", "l5-watchdog")
    monkeypatch.setenv("L5_WATCHDOG_MAX_ORDER_AGE_S", max_order_age)
    monkeypatch.setenv("L5_WATCHDOG_REPORT_MAX_AGE_S", report_max_age)
    if registry is None:
        monkeypatch.delenv("L5_ORDER_REGISTRY_URL", raising=False)
    else:
        monkeypatch.setenv("L5_ORDER_REGISTRY_URL", registry)
    if cancel is None:
        monkeypatch.delenv("L5_WATCHDOG_CANCEL", raising=False)
    else:
        monkeypatch.setenv("L5_WATCHDOG_CANCEL", cancel)


# ------------------------------------------- credential-free, as deployed now

def test_credential_free_reads_the_executor_report_and_cannot_cancel(
        monkeypatch, tmp_path):
    production_env(monkeypatch, tmp_path)
    monkeypatch.setattr(W, "A", None)            # no signing module in image
    wd = W.from_env()
    assert isinstance(wd.t, W.RegistryOrderSource)
    assert wd.cancel_enabled is False
    assert wd.oracle is None
    assert wd.max_order_age_s == 300.0
    assert wd.report_max_age_s == 45.0, "the report bound must be honoured"


def test_cancel_yes_is_inert_without_the_signing_module(monkeypatch, tmp_path):
    """The deployed flag cannot switch on a capability the image lacks."""
    production_env(monkeypatch, tmp_path, cancel="YES")
    monkeypatch.setattr(W, "A", None)
    assert W.from_env().cancel_enabled is False


def test_a_present_module_without_credentials_is_still_blind(monkeypatch,
                                                             tmp_path):
    production_env(monkeypatch, tmp_path)
    monkeypatch.setattr(W, "A", FakeAuth(creds=False))
    wd = W.from_env()
    assert isinstance(wd.t, W.RegistryOrderSource)
    assert wd.cancel_enabled is False


# --------------------------------------- cancel-only authenticated, Option A

def test_the_production_combination_selects_cancel_only_and_the_oracle(
        monkeypatch, tmp_path):
    """The owner's exact ask: registry set, credentials present, CANCEL=YES."""
    production_env(monkeypatch, tmp_path)
    monkeypatch.setattr(W, "A", FakeAuth())
    wd = W.from_env()
    assert getattr(wd.t, "name", None) == "CANCEL_ONLY"
    assert isinstance(wd.oracle, W.OwnershipOracle)
    assert wd.oracle.url == REGISTRY
    assert wd.cancel_enabled is True
    assert wd.max_order_age_s == 300.0


def test_the_report_bound_is_honoured_in_cancel_only_mode(monkeypatch,
                                                          tmp_path):
    """L5_WATCHDOG_REPORT_MAX_AGE_S must not be silently dropped.

    The credential-free branch passed report_max_age_s and the cancel-only
    branch did not, so a deployment that tuned the bound had it ignored in
    exactly the mode that can move money.
    """
    production_env(monkeypatch, tmp_path, report_max_age="45")
    monkeypatch.setattr(W, "A", FakeAuth())
    assert W.from_env().report_max_age_s == 45.0


def test_the_mode_switch_changes_which_witness_is_read(monkeypatch, tmp_path):
    """Credential-free reads the executor; cancel-only reads the VENUE."""
    production_env(monkeypatch, tmp_path)
    monkeypatch.setattr(W, "A", None)
    blind = W.from_env()
    monkeypatch.setattr(W, "A", FakeAuth())
    seeing = W.from_env()
    assert isinstance(blind.t, W.RegistryOrderSource)
    assert getattr(seeing.t, "name", None) == "CANCEL_ONLY"
    assert not isinstance(seeing.t, W.RegistryOrderSource)


# ------------------------------------------------------- fail-closed switches

def test_cancel_requires_the_explicit_yes(monkeypatch, tmp_path):
    production_env(monkeypatch, tmp_path, cancel=None)
    monkeypatch.setattr(W, "A", FakeAuth())
    assert W.from_env().cancel_enabled is False


@pytest.mark.parametrize("value", ["", "yes ", "Y", "TRUE", "1", "no", "NO"])
def test_only_the_literal_yes_enables_cancel(monkeypatch, tmp_path, value):
    production_env(monkeypatch, tmp_path, cancel=value)
    monkeypatch.setattr(W, "A", FakeAuth())
    enabled = W.from_env().cancel_enabled
    assert enabled is (value.strip().upper() == "YES"), value


def test_losing_the_registry_url_removes_cancel_entirely(monkeypatch,
                                                         tmp_path):
    """No ownership oracle, no cancel -- even with credentials and CANCEL=YES.

    This is the branch that protects the owner's manual orders: without a
    durable claim there is nothing that tells our order from theirs.
    """
    production_env(monkeypatch, tmp_path, registry=None, cancel="YES")
    monkeypatch.setattr(W, "A", FakeAuth())
    wd = W.from_env()
    assert wd.oracle is None
    assert wd.cancel_enabled is False


def test_the_halt_authority_is_independent_in_both_modes(monkeypatch,
                                                         tmp_path):
    """Whichever witness it reads, it latches on its own volume."""
    production_env(monkeypatch, tmp_path)
    for auth in (None, FakeAuth()):
        monkeypatch.setattr(W, "A", auth)
        wd = W.from_env()
        assert wd.asserter is not None
        assert not hasattr(wd.asserter, "clear")
