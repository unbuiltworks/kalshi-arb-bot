"""The claim namespace is per CAMPAIGN, and a restart cannot replenish it.

WHAT WENT WRONG, which is what these tests exist to keep from recurring.
Window 4 of the four-window continuous-inventory campaign of 2026-10-28 was
authorized, funded, armed -- and submitted nothing. Its 40-submission
backstop already read `total_spent: 38`, because 27 claim locks left behind
by two earlier campaigns were counted against it: all three campaigns ran
under the same `L5_EXPERIMENT_ID`, and the durable claim locks are keyed on
exactly that string.

The budget is therefore namespaced by the campaign itself -- the SHA-256 of
the owner-held nonce, which the executor cannot write -- and these tests pin
the four properties that make that safe rather than merely convenient:

  1. two different campaigns do not share a budget (isolation);
  2. the SAME campaign, re-read after a restart, sees its own spend
     (no replenishment -- the property the owner asked for by name);
  3. falling back to the bare id counts EVERY campaign's claims, so an
     absent, unreadable or expired campaign can only ever see more spent;
  4. nothing here creates an authorization: an unnamed experiment stays
     unnamed, and prior claims are never deleted or rewritten.
"""
import datetime as dt
import json
import pathlib

import pytest

from src import l5_live_authorization as AUTH
from src import l5_campaign_approval as CA


NONCE_A = "a" * 64
NONCE_B = "b" * 64


def approval(nonce, *, hours=2.0, approved=True, now=None):
    """A document the real gate accepts, under the real code ceilings."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return json.dumps({
        "approved": approved,
        "nonce": nonce,
        "series": "cpc-btc-updown-15m-",
        "issued_utc": now.isoformat(),
        "expires_utc": (now + dt.timedelta(hours=hours)).isoformat(),
        "max_windows": 4,
        "max_total_exposure_usd": "20",
        "max_daily_loss_usd": "5",
    })


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """A named experiment, an empty ledger directory, no campaign yet."""
    led = tmp_path / "auth_ledger.jsonl"
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(led))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "l5-btc15m-prog24-c1")
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    # the class budgets must be CONFIGURED or `claim_class_ticket` returns
    # None without taking anything -- "unset does not bind".
    for env_name in ("L5_LIVE_AUTH_MAX_OPEN", "L5_LIVE_AUTH_MAX_COMPLETE",
                     "L5_LIVE_AUTH_MAX_REPLACE"):
        monkeypatch.setenv(env_name, "64")
    return {"ledger": str(led), "dir": tmp_path, "mp": monkeypatch}


def arm(env, nonce, **kw):
    env["mp"].setenv("L5_CAMPAIGN_APPROVAL", approval(nonce, **kw))


# ====================================================== 1. the identity itself
def test_without_a_campaign_the_id_is_exactly_what_it_always_was(env):
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1"


def test_an_approved_campaign_narrows_the_id_to_its_own_namespace(env):
    arm(env, NONCE_A)
    got = AUTH.experiment_id()
    assert got.startswith("l5-btc15m-prog24-c1.camp.")
    assert got != "l5-btc15m-prog24-c1"


def test_two_campaigns_get_two_different_namespaces(env):
    arm(env, NONCE_A)
    a = AUTH.experiment_id()
    arm(env, NONCE_B)
    b = AUTH.experiment_id()
    assert a != b


def test_the_nonce_itself_never_appears_in_the_id(env):
    """The id goes into claim payloads on the volume and into logs."""
    arm(env, NONCE_A)
    got = AUTH.experiment_id()
    assert NONCE_A not in got
    assert NONCE_A[:16] not in got


def test_the_suffix_is_the_campaign_sid_and_nothing_invented(env):
    arm(env, NONCE_A)
    want = AUTH.campaign_claim_sid({"nonce_sid": CA.nonce_sid(NONCE_A)})
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1.%s" % want


def test_the_id_survives_the_120_character_claim_truncation(env):
    """Claim keys are sanitised to 120 chars; a truncated suffix would
    silently merge two campaigns' namespaces."""
    env["mp"].setenv("L5_EXPERIMENT_ID", "x" * 60)
    arm(env, NONCE_A)
    got = AUTH.experiment_id()
    assert len(got) <= 120, got
    import re
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", got)[:120]
    assert safe == got, "sanitiser or truncation would alter the id"


# ============================================ 2. a restart cannot replenish
def _spend(led, exp, klass, n):
    """Take `n` tickets the way production does -- through the real claim."""
    for _ in range(n):
        AUTH.claim_class_ticket(led, exp, klass)


def test_restarting_the_same_campaign_sees_its_own_spend(env):
    """THE PROPERTY THE OWNER ASKED FOR BY NAME.

    A restart re-reads the same nonce from the same variable, so it derives
    the same digest and counts the same locks. There is no path by which
    stopping and starting the process hands budget back.
    """
    arm(env, NONCE_A)
    exp = AUTH.experiment_id()
    _spend(env["ledger"], exp, "OPEN", 5)
    before = len(AUTH.class_spent(env["ledger"], exp, "OPEN"))
    assert before == 5

    # the restart: the variable is re-read from scratch, same document.
    arm(env, NONCE_A)
    exp_again = AUTH.experiment_id()
    assert exp_again == exp
    assert len(AUTH.class_spent(env["ledger"], exp_again, "OPEN")) == 5


def test_a_restart_does_not_replenish_the_flat_submission_backstop(env):
    arm(env, NONCE_A)
    exp = AUTH.experiment_id()
    for i in range(4):
        pathlib.Path(env["dir"], "l5_auth_claim_sub1.%s.%d.lock" % (exp, i)
                     ).write_text("x")
    arm(env, NONCE_A)
    assert len(AUTH.submissions_spent(env["ledger"], AUTH.experiment_id())) == 4


def test_a_restart_cannot_reopen_an_exhausted_class_budget(env, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "3")
    arm(env, NONCE_A)
    exp = AUTH.experiment_id()
    _spend(env["ledger"], exp, "OPEN", 3)
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(env["ledger"], exp, "OPEN")
    arm(env, NONCE_A)                                   # the restart
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(env["ledger"], AUTH.experiment_id(), "OPEN")


def test_only_a_new_nonce_opens_a_new_budget(env, monkeypatch):
    """Which is an OWNER action: the nonce lives in a variable the executor
    cannot write, and a new one means a new approval document."""
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "3")
    arm(env, NONCE_A)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 3)
    arm(env, NONCE_B)
    assert AUTH.class_spent(env["ledger"], AUTH.experiment_id(), "OPEN") == []
    assert AUTH.claim_class_ticket(
        env["ledger"], AUTH.experiment_id(), "OPEN") is not None


# ================================ 3. the fallback can only see MORE spent
@pytest.mark.parametrize("why,value", [
    ("absent", None),
    ("empty", ""),
    ("unparseable", "{not json"),
    ("not_an_object", "[1, 2, 3]"),
])
def test_an_unusable_document_falls_back_to_the_bare_id(env, why, value):
    if value is None:
        env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    else:
        env["mp"].setenv("L5_CAMPAIGN_APPROVAL", value)
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1", why


def test_an_unapproved_document_falls_back_to_the_bare_id(env):
    arm(env, NONCE_A, approved=False)
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1"


def test_an_expired_document_falls_back_to_the_bare_id(env):
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=9)
    arm(env, NONCE_A, hours=1.0, now=past)
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1"


def test_the_bare_id_counts_every_campaigns_class_tickets(env):
    """So reverting to it -- however that happens -- is the conservative
    direction. This is what closes the one hole the suffix could open: an
    expiry mid-campaign must not expose a clean budget."""
    arm(env, NONCE_A)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 3)
    arm(env, NONCE_B)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 2)
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    bare = AUTH.experiment_id()
    assert bare == "l5-btc15m-prog24-c1"
    assert len(AUTH.class_spent(env["ledger"], bare, "OPEN")) == 5


def test_the_bare_id_counts_every_campaigns_submission_claims(env):
    arm(env, NONCE_A)
    a = AUTH.experiment_id()
    arm(env, NONCE_B)
    b = AUTH.experiment_id()
    for exp in (a, b):
        pathlib.Path(env["dir"], "l5_auth_claim_sub1.%s.lock" % exp
                     ).write_text("x")
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    assert len(AUTH.submissions_spent(
        env["ledger"], AUTH.experiment_id())) == 2


def test_an_expiry_mid_campaign_cannot_hand_back_an_exhausted_budget(
        env, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "3")
    arm(env, NONCE_A)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 3)
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(env["ledger"], AUTH.experiment_id(), "OPEN")


def test_a_raising_approval_read_falls_back_rather_than_propagating(
        env, monkeypatch):
    """`experiment_id` is called on the money path by a dozen call sites; an
    exception escaping it would be a new failure mode, not a safer one."""
    def boom():
        raise RuntimeError("gate exploded")
    monkeypatch.setattr(CA, "approval_gate", boom)
    arm(env, NONCE_A)
    assert AUTH.experiment_id() == "l5-btc15m-prog24-c1"


# ===================================== 4. it creates no authorization at all
def test_an_unnamed_experiment_stays_unnamed_under_a_campaign(env):
    """The suffix NARROWS a named budget; it must never supply the name, or
    `SUBMISSION_CLAIM_NO_EXPERIMENT` would stop refusing."""
    env["mp"].setenv("L5_EXPERIMENT_ID", "")
    arm(env, NONCE_A)
    assert AUTH.experiment_id() == ""


def test_the_unnamed_experiment_refusal_still_fires_under_a_campaign(env):
    env["mp"].setenv("L5_EXPERIMENT_ID", "   ")
    arm(env, NONCE_A)
    with pytest.raises(AUTH.Refused) as ei:
        AUTH.claim_submission("cpc-btc-updown-15m-x", {"note": "probe"},
                              path=env["ledger"])
    assert "NO_EXPERIMENT" in str(ei.value.reason)


def test_prior_claims_are_never_deleted_or_rewritten(env):
    """Isolation is achieved by what is COUNTED, not by removing evidence.
    The audit trail of every earlier campaign stays on the volume."""
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 2)
    before = sorted(p.name for p in env["dir"].glob("l5_auth_claim_*.lock"))
    arm(env, NONCE_A)
    _spend(env["ledger"], AUTH.experiment_id(), "OPEN", 2)
    after = sorted(p.name for p in env["dir"].glob("l5_auth_claim_*.lock"))
    assert set(before).issubset(set(after))
    assert len(after) == len(before) + 2


def test_a_different_experiment_is_not_mistaken_for_a_campaign_of_this_one(
        env):
    """`exp2` is a different experiment, not `exp` under a campaign. The
    separator dot in the class prefix is what keeps them apart."""
    led = env["ledger"]
    for n in range(3):
        pathlib.Path(env["dir"],
                     "l5_auth_claim_cls1.l5-btc15m-prog24-c1-OTHER.OPEN.%d"
                     ".lock" % n).write_text("x")
    assert AUTH.class_spent(led, "l5-btc15m-prog24-c1", "OPEN") == []


def test_class_tickets_of_one_class_never_count_against_another(env):
    arm(env, NONCE_A)
    exp = AUTH.experiment_id()
    _spend(env["ledger"], exp, "OPEN", 2)
    _spend(env["ledger"], exp, "COMPLETE", 3)
    assert len(AUTH.class_spent(env["ledger"], exp, "OPEN")) == 2
    assert len(AUTH.class_spent(env["ledger"], exp, "COMPLETE")) == 3
    assert len(AUTH.class_spent(env["ledger"], exp, "REPLACE")) == 0


def test_a_campaigns_tickets_count_for_the_right_class_under_the_bare_id(env):
    arm(env, NONCE_A)
    _spend(env["ledger"], AUTH.experiment_id(), "COMPLETE", 2)
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    bare = AUTH.experiment_id()
    assert len(AUTH.class_spent(env["ledger"], bare, "COMPLETE")) == 2
    assert len(AUTH.class_spent(env["ledger"], bare, "OPEN")) == 0


def test_an_unreadable_ledger_directory_reports_nothing_rather_than_raising(
        env):
    arm(env, NONCE_A)
    missing = str(env["dir"] / "nope" / "ledger.jsonl")
    assert AUTH.class_spent(missing, AUTH.experiment_id(), "OPEN") == []


# ================================== 5. the window-4 failure, reproduced
def test_window_4_of_20261028_is_reproduced_under_the_old_shared_id(env,
                                                                    monkeypatch):
    """THE ACTUAL FAILURE. Two earlier campaigns left 38 claims under the
    shared experiment id; the fourth window's 40-submission backstop then
    had 2 left, which the 2-submission completion reserve consumed entirely,
    so no OPEN could be granted. Without the campaign suffix this is exactly
    what still happens -- which is why it is pinned here rather than assumed.
    """
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")
    env["mp"].delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    shared = AUTH.experiment_id()
    for i in range(38):
        pathlib.Path(env["dir"], "l5_auth_claim_acc1.%s.prior.%d.lock"
                     % (shared, i)).write_text("x")
    assert len(AUTH.submissions_spent(env["ledger"], shared)) == 38
    short = AUTH.completion_capacity_refusal(env["ledger"], shared)
    assert short is not None, "the fourth window should be unable to open"


def test_the_same_three_campaigns_no_longer_starve_the_fourth(env,
                                                              monkeypatch):
    """With the suffix, those 38 claims belong to the campaigns that spent
    them. A newly approved fourth campaign starts on its own budget -- the
    one the owner authorized -- without any claim being deleted."""
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")
    arm(env, NONCE_A)
    prior = AUTH.experiment_id()
    for i in range(38):
        pathlib.Path(env["dir"], "l5_auth_claim_acc1.%s.prior.%d.lock"
                     % (prior, i)).write_text("x")
    arm(env, NONCE_B)                        # the next authorized campaign
    fresh = AUTH.experiment_id()
    assert fresh != prior
    assert len(AUTH.submissions_spent(env["ledger"], fresh)) == 0
    assert AUTH.completion_capacity_refusal(env["ledger"], fresh) is None
    # and the evidence of the earlier spend is still on the volume
    assert len(AUTH.submissions_spent(env["ledger"], prior)) == 38
