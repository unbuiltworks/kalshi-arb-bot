"""Every submission limit, checked against the campaign claim namespace.

THE QUESTION THIS ANSWERS. Scoping the claim namespace per campaign gives a
new campaign its authorized budget. The risk is that it also hands back a
bound that was meant to hold ACROSS campaigns -- a global backstop turned
per-campaign is a limit silently removed. So every limit keyed on the
experiment id is exercised here against three consecutive campaigns under
one `L5_EXPERIMENT_ID`, and each is asserted to have exactly one of two
shapes:

  PER-AUTHORIZATION -- isolated per campaign, which is the documented
      intent. `MAX_SUBMISSIONS_ENV` says so in its own comment: "It is the
      owner's number, per authorization." The class budgets, the completion
      reserve and the single-use leg claims are the same authorization's
      numbers.

  GLOBAL -- carried across campaigns, and therefore NOT keyed on the
      experiment id at all. The campaign window ledger is keyed on the
      nonce; the $5 additional-loss baseline is keyed on the nonce; the
      cumulative- and daily-loss ceilings are env thresholds measured
      against account-wide realized figures. None of them reads
      `experiment_id()`, so none of them can be reset by a new namespace.
      That is asserted here too, by source, so a future change that wires
      one of them to the experiment id fails this file.

And in both shapes the same invariant holds: **the bare id counts every
campaign's claims**, so any fallback to it sees more spent, never less.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import pathlib

import pytest

from src import l5_live_authorization as AUTH
from src import l5_campaign_approval as CA
from src import l5_campaign_baseline as CBL

NONCES = ("1" * 64, "2" * 64, "3" * 64)
MKT = "cpc-btc-updown-15m-2026-10-28-0015z"


def approval(nonce):
    now = dt.datetime.now(dt.timezone.utc)
    return json.dumps({
        "approved": True, "nonce": nonce, "series": "cpc-btc-updown-15m-",
        "issued_utc": now.isoformat(),
        "expires_utc": (now + dt.timedelta(hours=2)).isoformat(),
        "max_windows": 4, "max_total_exposure_usd": "20",
        "max_daily_loss_usd": "5"})


@pytest.fixture()
def env(monkeypatch, tmp_path):
    led = tmp_path / "auth_ledger.jsonl"
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(led))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "l5-btc15m-prog24-c1-3bf1daa5")
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "40")
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_COMPLETE", "40")
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_REPLACE", "64")
    return {"led": str(led), "dir": tmp_path, "mp": monkeypatch}


def arm(env, nonce):
    env["mp"].setenv("L5_CAMPAIGN_APPROVAL", approval(nonce))
    return AUTH.experiment_id()


def bare(env):
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    return AUTH.experiment_id()


# ============================== 1. the flat submission backstop (40)
def test_the_flat_backstop_is_isolated_per_campaign(env):
    ids = []
    for n in NONCES:
        e = arm(env, n)
        ids.append(e)
        for i in range(10):
            pathlib.Path(env["dir"],
                         "l5_auth_claim_acc1.%s.m.%d.UP.lock" % (e, i)
                         ).write_text("x")
        assert len(AUTH.submissions_spent(env["led"], e)) == 10
        assert AUTH.submission_budget_refusal(env["led"], e) is None
    assert len(set(ids)) == 3


def test_the_flat_backstop_counts_every_campaign_under_the_bare_id(env):
    for n in NONCES:
        e = arm(env, n)
        for i in range(10):
            pathlib.Path(env["dir"],
                         "l5_auth_claim_acc1.%s.m.%d.UP.lock" % (e, i)
                         ).write_text("x")
    assert len(AUTH.submissions_spent(env["led"], bare(env))) == 30


def test_the_bare_id_still_refuses_once_campaigns_have_spent_it(env):
    for n in NONCES:
        e = arm(env, n)
        for i in range(14):
            pathlib.Path(env["dir"],
                         "l5_auth_claim_acc1.%s.m.%d.UP.lock" % (e, i)
                         ).write_text("x")
    over = AUTH.submission_budget_refusal(env["led"], bare(env))
    assert over is not None
    assert over["reason"] == "SUBMISSION_BUDGET_EXHAUSTED"
    assert over["spent"] == 42 and over["max_submissions"] == 40


# ============================== 2. the three class budgets
@pytest.mark.parametrize("klass,cap", [("OPEN", 40), ("COMPLETE", 40),
                                       ("REPLACE", 64)])
def test_each_class_budget_is_isolated_per_campaign(env, klass, cap):
    for n in NONCES:
        e = arm(env, n)
        for _ in range(3):
            assert AUTH.claim_class_ticket(env["led"], e, klass) is not None
        assert len(AUTH.class_spent(env["led"], e, klass)) == 3
        assert AUTH.class_budget_report(
            env["led"], e)[klass]["remaining"] == cap - 3


@pytest.mark.parametrize("klass", ["OPEN", "COMPLETE", "REPLACE"])
def test_each_class_budget_sums_every_campaign_under_the_bare_id(env, klass):
    for n in NONCES:
        e = arm(env, n)
        for _ in range(3):
            AUTH.claim_class_ticket(env["led"], e, klass)
    rep = AUTH.class_budget_report(env["led"], bare(env))
    assert rep[klass]["spent"] == 9


def test_an_exhausted_class_cannot_be_reopened_by_the_bare_id(env,
                                                              monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "4")
    e = arm(env, NONCES[0])
    for _ in range(4):
        AUTH.claim_class_ticket(env["led"], e, "OPEN")
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(env["led"], e, "OPEN")
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(env["led"], bare(env), "OPEN")


def test_one_class_never_consumes_another_in_any_campaign(env):
    for n in NONCES:
        e = arm(env, n)
        AUTH.claim_class_ticket(env["led"], e, "OPEN")
        rep = AUTH.class_budget_report(env["led"], e)
        assert rep["OPEN"]["spent"] == 1
        assert rep["COMPLETE"]["spent"] == 0
        assert rep["REPLACE"]["spent"] == 0


# ============================== 3. the completion reserve
def test_the_completion_reserve_is_isolated_but_the_bare_id_sees_all(env):
    """The reserve is derived from the two budgets above, so it inherits
    both shapes. This is the gate that silenced window 4."""
    for n in NONCES:
        e = arm(env, n)
        for i in range(13):
            pathlib.Path(env["dir"],
                         "l5_auth_claim_acc1.%s.m.%d.UP.lock" % (e, i)
                         ).write_text("x")
        assert AUTH.completion_capacity_refusal(env["led"], e) is None
    assert AUTH.completion_capacity_refusal(env["led"], bare(env)) is not None


# ============================== 4. the single-use leg and probe claims
def test_the_single_submission_claim_is_per_campaign(env):
    for n in NONCES:
        e = arm(env, n)
        assert AUTH.submission_sid(e) == "sub1.%s" % e
    assert len({AUTH.submission_sid(arm(env, n)) for n in NONCES}) == 3


def test_probe_and_accumulation_claims_are_per_campaign(env):
    sids = set()
    for n in NONCES:
        e = arm(env, n)
        sids.add(AUTH.probe_leg_sid(e, "UP"))
        sids.add(AUTH.accumulation_sid(e, MKT, 1, "DOWN", 2))
    assert len(sids) == 6


def test_a_restart_inside_one_campaign_reuses_the_same_sids(env):
    e1 = arm(env, NONCES[0])
    a1 = AUTH.accumulation_sid(e1, MKT, 1, "DOWN", 2)
    e2 = arm(env, NONCES[0])            # the restart
    assert AUTH.accumulation_sid(e2, MKT, 1, "DOWN", 2) == a1


# ============================== 5. the 120-character lock-file cut
def test_the_longest_sid_fits_the_lock_path_budget(env):
    """`_lock_path` truncates the WHOLE sid at 120, and two claims sharing a
    truncated filename leave the single-use ledger ambiguous. The campaign
    suffix spends 30 of those characters, so this pins the headroom."""
    e = arm(env, NONCES[0])
    longest = AUTH.accumulation_sid(e, MKT, 99, "DOWN", 99)
    assert len(longest) <= 120, len(longest)
    assert AUTH._lock_path(env["led"], longest).name == (
        "l5_auth_claim_%s.lock" % longest), "the sid was truncated"


def test_a_long_owner_label_cannot_push_the_sid_past_the_cut(env):
    env["mp"].setenv("L5_EXPERIMENT_ID", "x" * 200)
    e = arm(env, NONCES[0])
    assert len(e) <= AUTH.MAX_EXPERIMENT_LEN
    longest = AUTH.accumulation_sid(e, MKT, 99, "DOWN", 99)
    assert len(longest) <= 120, len(longest)


def test_a_long_owner_label_keeps_the_digest_whole(env):
    """The label gives way, never the digest: losing digest characters would
    merge two campaigns' budgets, which is the bug this mechanism fixes."""
    env["mp"].setenv("L5_EXPERIMENT_ID", "x" * 200)
    ids = {arm(env, n) for n in NONCES}
    assert len(ids) == 3
    for e in ids:
        assert e.endswith(AUTH.campaign_claim_sid(
            {"nonce_sid": CA.nonce_sid(
                [n for n in NONCES
                 if e.endswith(AUTH.campaign_claim_sid(
                     {"nonce_sid": CA.nonce_sid(n)}))][0])}))


def test_long_labels_still_isolate_budgets_from_each_other(env):
    env["mp"].setenv("L5_EXPERIMENT_ID", "y" * 200)
    for n in NONCES:
        e = arm(env, n)
        AUTH.claim_class_ticket(env["led"], e, "OPEN")
        assert len(AUTH.class_spent(env["led"], e, "OPEN")) == 1


# ============================== 6. what must stay GLOBAL stays global
def test_the_campaign_window_ledger_is_keyed_on_the_nonce_not_the_experiment():
    src = inspect.getsource(CA.WindowLedger)
    assert "nonce_sid" in src
    assert "experiment_id" not in src, \
        "the window budget must not become per-experiment"


def test_the_additional_loss_baseline_is_keyed_on_the_nonce():
    src = inspect.getsource(CBL)
    assert "experiment_id" not in src, \
        "the $5 additional-loss baseline must not read the experiment id"


def test_no_loss_ceiling_reads_the_experiment_id():
    """Cumulative, daily and strategy loss ceilings are env thresholds
    measured against account-wide realized figures. If any of them ever
    starts reading the claim namespace, a new campaign would reset it."""
    from src import l5_live_validation_gates as VG
    src = inspect.getsource(VG)
    assert "experiment_id" not in src


def test_the_approval_gate_itself_does_not_read_the_claim_namespace():
    src = inspect.getsource(CA)
    assert "experiment_id" not in src


# ============================== 7. nothing is ever erased
def test_no_claim_file_is_removed_by_moving_between_campaigns(env):
    before = set()
    for n in NONCES:
        e = arm(env, n)
        AUTH.claim_class_ticket(env["led"], e, "OPEN")
        before |= {p.name for p in env["dir"].glob("l5_auth_claim_*.lock")}
    after = {p.name for p in env["dir"].glob("l5_auth_claim_*.lock")}
    assert before == after and len(after) == 3


def test_a_campaigns_own_claims_are_still_there_after_two_more_campaigns(env):
    e1 = arm(env, NONCES[0])
    AUTH.claim_class_ticket(env["led"], e1, "COMPLETE")
    for n in NONCES[1:]:
        AUTH.claim_class_ticket(env["led"], arm(env, n), "COMPLETE")
    env["mp"].setenv("L5_CAMPAIGN_APPROVAL", approval(NONCES[0]))
    assert len(AUTH.class_spent(env["led"], AUTH.experiment_id(),
                                "COMPLETE")) == 1
