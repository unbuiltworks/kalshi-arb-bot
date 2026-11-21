"""Both gates on live crossing must be visible in the running process.

`TCP.authorize` refuses POLICY_DISABLED without `enabled` and
OWNER_AUTHORIZATION_ABSENT without `owner_authorized`. Only the first was
reported by the runtime verifier, so a maker-only campaign could be verified
against a config that showed one of the two switches and left the other to be
inferred from an env var -- which the verifier's own comment says an operator
should never have to do.
"""
from __future__ import annotations

import os

import pytest

from src import l5_runtime_config_verify as CFGV
from src import l5_taker_completion_policy as TCP

NAMES = ("taker_policy_enabled", "taker_policy_owner_cross_authorized",
         "taker_crossing_reachable")


def observed(report):
    return {r["name"]: r["effective"]
            for r in report.get("observed_not_graded", [])}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for e in (TCP.POLICY_ENV, "L5_TS_COMPLETION_CROSS"):
        monkeypatch.delenv(e, raising=False)


def test_all_three_rows_are_reported():
    seen = observed(CFGV.verify())
    for n in NAMES:
        assert n in seen, n


def test_unset_reads_false_on_all_three():
    seen = observed(CFGV.verify())
    for n in NAMES:
        assert seen[n] is False, n


@pytest.mark.parametrize("policy,cross,reachable", [
    ("YES", "YES", True),
    ("YES", "NO", False),
    ("NO", "YES", False),
    ("YES", "", False),
    ("", "YES", False),
    ("1", "YES", False),      # TCP wants the exact string YES, not truthy
    ("YES", "1", False),
    ("yes", "yes", True),     # case-insensitive, as both accessors are
])
def test_reachable_is_the_conjunction(monkeypatch, policy, cross, reachable):
    monkeypatch.setenv(TCP.POLICY_ENV, policy)
    monkeypatch.setenv("L5_TS_COMPLETION_CROSS", cross)
    seen = observed(CFGV.verify())
    assert seen["taker_crossing_reachable"] is reachable


def test_the_reported_switches_match_what_authorize_actually_does(monkeypatch):
    """The rows must not drift from the gate they describe."""
    monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    monkeypatch.setenv("L5_TS_COMPLETION_CROSS", "NO")
    seen = observed(CFGV.verify())
    assert seen["taker_policy_enabled"] is True
    assert seen["taker_policy_owner_cross_authorized"] is False

    # and the policy itself refuses on exactly that switch
    out = TCP.authorize(
        decision={"ok": True}, enabled=True, owner_authorized=False,
        held_up_qty=0, held_dn_qty=0, held_up_px=None, held_dn_px=None,
        halted=False)
    assert out.get("ok") is not True
    assert out.get("reason") == "OWNER_AUTHORIZATION_ABSENT"


def test_a_maker_only_campaign_can_assert_crossing_is_unreachable(monkeypatch):
    """The one row an owner needs to read before a maker-only authorization."""
    monkeypatch.setenv(TCP.POLICY_ENV, "YES")
    monkeypatch.delenv("L5_TS_COMPLETION_CROSS", raising=False)
    assert observed(CFGV.verify())["taker_crossing_reachable"] is False
