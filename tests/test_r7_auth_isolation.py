"""R7 pre-flight verification: a spent authorization cannot trade again.

R7 runs on a SEPARATE ledger (`nativeq10_r7_auth.jsonl`) from R6. These
tests pin the properties that separation is supposed to buy, so that "R7 is
isolated" is measured rather than asserted in a commit message.
"""
from __future__ import annotations

import json

import pytest

from src import l5_live_authorization as AUTH
from src import pmus_auth as Q1


R7_SID = "l5probe-20260929-btc15m-r7"
MKT = "cpc-btc-updown-15m-2026-09-29-2000z"


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, R7_SID)
    monkeypatch.setenv(Q1.ARM_ENV, R7_SID)
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)


def test_one_claim_then_never_again(armed, tmp_path):
    led = str(tmp_path / "r7_auth.jsonl")
    AUTH.Authorization(led).claim(MKT)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.Authorization(led).claim(MKT)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_a_crash_after_claiming_still_spends_it(armed, tmp_path):
    """The ledger write happens BEFORE any order exists, so a process that
    dies immediately after claiming fails to run rather than running twice.
    """
    led = str(tmp_path / "r7_auth.jsonl")
    AUTH.Authorization(led).claim(MKT)          # then "crash": never consumed
    with pytest.raises(AUTH.Refused):
        AUTH.Authorization(led).claim(MKT)      # a restart re-reads the file


def test_the_next_window_cannot_reuse_it(armed, tmp_path):
    """A 15-minute market rolls over four times an hour; a standing env arm
    must not become a licence for every future window."""
    led = str(tmp_path / "r7_auth.jsonl")
    AUTH.Authorization(led).claim(MKT)
    with pytest.raises(AUTH.Refused):
        AUTH.Authorization(led).claim("cpc-btc-updown-15m-2026-09-29-2015z")


def test_r6s_spent_authorization_does_not_reach_r7s_ledger(armed, tmp_path):
    """The separation itself. R6's ledger is spent; R7's is clean."""
    r6 = tmp_path / "nativeq10_r6_auth.jsonl"
    r6.write_text(json.dumps({
        "event": "CLAIMED", "session_id": R7_SID,
        "market": "cpc-btc-updown-15m-2026-09-29-1815z"}) + "\n")
    assert AUTH.spent(R7_SID, str(r6)) is not None, "fixture is wrong"

    r7 = str(tmp_path / "nativeq10_r7_auth.jsonl")
    assert AUTH.spent(R7_SID, r7) is None
    claim = AUTH.Authorization(r7).claim(MKT)
    assert claim["market"] == MKT


def test_no_owner_token_means_no_claim_and_no_order_path(tmp_path,
                                                         monkeypatch):
    """The state R7 is parked in right now."""
    for v in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.setenv(v, "")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.Authorization(str(tmp_path / "r7_auth.jsonl")).claim(MKT)
    assert e.value.reason == "OWNER_AUTHORIZATION_ABSENT"
    assert not (tmp_path / "r7_auth.jsonl").exists(), (
        "a refused claim wrote to the ledger")


def test_a_mismatched_arm_token_is_not_armed(tmp_path, monkeypatch):
    """ARM must equal SESSION_ID. A typo is a refusal, not a trade."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, R7_SID)
    monkeypatch.setenv(Q1.ARM_ENV, R7_SID + "x")
    assert Q1.armed() is False
    with pytest.raises(AUTH.Refused):
        AUTH.Authorization(str(tmp_path / "r7_auth.jsonl")).claim(MKT)


def test_an_unreadable_ledger_line_is_not_an_empty_ledger(armed, tmp_path):
    """A line we cannot parse might be the very claim that would refuse us."""
    led = tmp_path / "r7_auth.jsonl"
    led.write_text("{not json at all " + R7_SID + "\n")
    assert AUTH.spent(R7_SID, str(led)) is not None, (
        "an unparseable ledger was treated as empty")
