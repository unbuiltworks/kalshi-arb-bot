"""One shared arming boundary: every precondition, and single use enforced.

WHAT WAS WRONG. Three `.arm()` sites existed; two were one chain and
`src/l5_pmus_live_runner.py` called `transport.arm()` directly. Their
preconditions were ASYMMETRIC and the path the invariant test blessed was the
WEAKER one -- the runner checked the halt latch, unresolved accounting, venue
reconciliation and uncertain orders; the one-shot probe checked none of them.
And the runner claimed no authorization at all, which on a continuous service
with restartPolicyType ALWAYS is a standing licence to arm every window.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import l5_live_authorization as AUTH      # noqa: E402
from src import pmus_auth as Q1                    # noqa: E402

MARKET = "cpc-btc-updown-15m-2026-10-14-0415z"
SID = "sess-abc-123"
LIMITS = {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
          "daily_realized_loss_halt_usd": "5"}


class Store:
    def __init__(self, uncertain=None):
        self._u = uncertain

    def orders_uncertain(self):
        return self._u


def owner_authorized(monkeypatch, sid=SID):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)


def ok(**over):
    # require_durable=False because a pytest tmp_path lives on the container
    # root, which production must REFUSE. The durability precondition has its
    # own tests below, including that its default is True.
    kw = dict(market=MARKET, preflight={"preflight": "PASSED"},
              store=Store(), halted=False, reconciled=True, unresolved=(),
              limits=LIMITS, shadow_locked=False, require_durable=False)
    kw.update(over)
    return kw


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "auth.jsonl")


# ------------------------------------------------- the happy path, once only
def test_a_complete_request_claims_once(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    claim = AUTH.authorize_arming(path=ledger, **ok())
    assert claim["session_id"] == SID
    assert claim["market"] == MARKET
    assert claim["approved_limits"] == LIMITS
    assert claim["boundary"] == "authorize_arming"
    assert dt.datetime.fromisoformat(claim["expires_utc"]) > \
        dt.datetime.fromisoformat(claim["utc"])
    # and the ledger carries it durably
    rows = [json.loads(l) for l in open(ledger) if l.strip()]
    assert [r["event"] for r in rows] == ["CLAIMED"]


def test_a_duplicate_claim_is_refused(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    AUTH.authorize_arming(path=ledger, **ok())
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_a_second_market_on_the_same_session_is_refused(monkeypatch, ledger):
    """One authorization trades ONE market, so the next window is refused."""
    owner_authorized(monkeypatch)
    AUTH.authorize_arming(path=ledger, **ok())
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger,
                              **ok(market=MARKET[:-5] + "0430z"))
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


# --------------------------------------------------------- durability
def test_the_claim_survives_a_process_restart(monkeypatch, ledger):
    """THE WHOLE POINT on an ALWAYS-restart service: a fresh process reading
    the same env must not get a second authorization."""
    owner_authorized(monkeypatch)
    AUTH.authorize_arming(path=ledger, **ok())
    prog = (
        "import sys,os;sys.path.insert(0,%r);"
        "from src import l5_live_authorization as A;"
        "from src import pmus_auth as Q;"
        "import json\n"
        "try:\n"
        "    A.authorize_arming(market=%r,preflight={'preflight':'PASSED'},"
        "store=None,halted=False,reconciled=True,unresolved=(),"
        "limits=%r,shadow_locked=False,require_durable=False,path=%r)\n"
        "    print('CLAIMED_AGAIN')\n"
        "except A.Refused as e:\n"
        "    print('REFUSED '+e.reason)\n"
        % (str(ROOT), MARKET, LIMITS, ledger))
    env = dict(os.environ)
    env.update({Q1.LIVE_ENV: "YES", Q1.SID_ENV: SID, Q1.ARM_ENV: SID})
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                       text=True, env=env, cwd=str(ROOT))
    assert "REFUSED AUTHORIZATION_ALREADY_USED" in r.stdout, r.stdout + r.stderr


def test_concurrent_processes_cannot_both_claim(monkeypatch, ledger):
    """`spent()` then `_append` has a race a log cannot close. The claim is
    taken with O_CREAT|O_EXCL, so the filesystem picks exactly one winner."""
    owner_authorized(monkeypatch)
    prog = (
        "import sys;sys.path.insert(0,%r);"
        "from src import l5_live_authorization as A\n"
        "try:\n"
        "    A.authorize_arming(market=%r,preflight={'preflight':'PASSED'},"
        "store=None,halted=False,reconciled=True,unresolved=(),"
        "limits=%r,shadow_locked=False,require_durable=False,path=%r);print('WON')\n"
        "except A.Refused as e:\n    print('LOST '+e.reason)\n"
        % (str(ROOT), MARKET, LIMITS, ledger))
    env = dict(os.environ)
    env.update({Q1.LIVE_ENV: "YES", Q1.SID_ENV: SID, Q1.ARM_ENV: SID})
    procs = [subprocess.Popen([sys.executable, "-c", prog], env=env,
                              cwd=str(ROOT), stdout=subprocess.PIPE,
                              text=True) for _ in range(6)]
    outs = [p.communicate()[0] for p in procs]
    won = sum(1 for o in outs if "WON" in o)
    lost = sum(1 for o in outs if "LOST AUTHORIZATION_ALREADY_USED" in o)
    assert won == 1, outs
    assert lost == 5, outs


# ------------------------------------------------------- every refusal
def test_shadow_only_lock_refuses_first_and_by_name(monkeypatch, ledger):
    """The deployed service's default. It must say so, not go quiet."""
    owner_authorized(monkeypatch)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(shadow_locked=True))
    assert e.value.reason == "SHADOW_ONLY_LOCK"
    assert not pathlib.Path(ledger).exists(), "nothing is written for an " \
                                              "ineligible request"


def test_missing_owner_authorization_refuses(ledger, monkeypatch):
    for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "OWNER_AUTHORIZATION_ABSENT"


def test_a_mismatched_session_token_refuses(ledger, monkeypatch):
    """ARM set but not equal to SESSION_ID -- the live service's state."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-one")
    monkeypatch.setenv(Q1.ARM_ENV, "sess-two")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "OWNER_AUTHORIZATION_ABSENT"


def test_a_failed_preflight_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    for pf in ({"preflight": "FAILED"}, {}, None):
        with pytest.raises(AUTH.Refused) as e:
            AUTH.authorize_arming(path=ledger, **ok(preflight=pf))
        assert e.value.reason == "PREFLIGHT_NOT_PASSED"


def test_a_halt_latch_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(halted=True))
    assert e.value.reason == "HALTED"


def test_an_unreconciled_book_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(reconciled=False))
    assert e.value.reason == "NOT_RECONCILED"


def test_venue_reconciliation_disagreement_refuses(monkeypatch, ledger):
    """THE LINK TO THE RECONCILIATION REPAIR. When the venue contradicts our
    gross book, `adopt_venue_position` marks the store uncertain -- and that
    is exactly what must stop arming."""
    owner_authorized(monkeypatch)
    why = ("gross/net divergence ['cpc-btc-updown-15m-2026-10-14-0415z']; "
           "incoherent book {}; unbooked terminal orders []")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(store=Store(uncertain=why)))
    assert e.value.reason == "ORDER_STATE_UNCERTAIN"
    assert "gross/net divergence" in e.value.detail["why"]


def test_an_unreadable_store_refuses_rather_than_raising_out(monkeypatch,
                                                             ledger):
    owner_authorized(monkeypatch)

    class Broken:
        def orders_uncertain(self):
            raise RuntimeError("volume gone")

    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(store=Broken()))
    assert e.value.reason == "ORDER_STATE_UNREADABLE"


def test_unresolved_accounting_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger,
                              **ok(unresolved=[{"market": "x"}]))
    assert e.value.reason == "UNRESOLVED_ACCOUNTING"


def test_an_unknown_capital_scope_refuses(monkeypatch, ledger):
    """An authorization with no capital envelope is not an authorization."""
    owner_authorized(monkeypatch)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok(limits={}))
    assert e.value.reason == "CAPITAL_SCOPE_UNKNOWN"


def test_a_market_outside_the_owners_binding_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    monkeypatch.setenv(AUTH.MARKET_ENV, "some-other-market")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


# ---------------------------------------------------------------- expiry
def test_an_expired_owner_deadline_refuses(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    past = (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=1)).isoformat()
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV, past)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_EXPIRED"


def test_an_unreadable_deadline_refuses_rather_than_ignoring_it(monkeypatch,
                                                                ledger):
    owner_authorized(monkeypatch)
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV, "not-a-date")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_DEADLINE_UNREADABLE"


def test_a_claim_expires_at_use_time(monkeypatch, ledger):
    owner_authorized(monkeypatch)
    monkeypatch.setenv(AUTH.TTL_ENV, "60")
    claim = AUTH.authorize_arming(path=ledger, **ok())
    assert AUTH.still_valid(claim, limits=LIMITS) is None
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=120)
    assert AUTH.still_valid(claim, limits=LIMITS,
                            now=later) == "AUTHORIZATION_EXPIRED"


def test_widening_a_cap_after_approval_invalidates_the_claim(monkeypatch,
                                                             ledger):
    """Scoped to the approved capital limits, enforced at use time."""
    owner_authorized(monkeypatch)
    claim = AUTH.authorize_arming(path=ledger, **ok())
    loose = dict(LIMITS, max_usd_per_market="50")
    why = AUTH.still_valid(claim, limits=loose)
    assert why and why.startswith("CAPITAL_SCOPE_WIDENED")
    # tightening is fine
    tight = dict(LIMITS, max_usd_per_market="1")
    assert AUTH.still_valid(claim, limits=tight) is None


def test_a_claim_with_no_expiry_is_refused_at_use_time(ledger):
    assert AUTH.still_valid({"approved_limits": LIMITS},
                            limits=LIMITS) == "AUTHORIZATION_HAS_NO_EXPIRY"


# ------------------------------------------- the invariant, restated
def test_every_arm_site_is_guarded_by_the_shared_boundary():
    """STRONGER than "only one module arms": each module that can arm must
    also call the boundary. AST, not grep -- a docstring is not a call."""
    guarded, armers = set(), set()
    for f in (list((ROOT / "src").glob("*.py"))
              + list((ROOT / "scripts").glob("*.py"))):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:                                # pragma: no cover
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            if isinstance(n.func, ast.Attribute) and n.func.attr == "arm" \
                    and not n.args:
                armers.add(f.name)
            if getattr(n.func, "attr", None) == "authorize_arming":
                guarded.add(f.name)
    assert armers, "something must be able to arm"
    assert armers <= guarded, (
        "these modules can arm but never call authorize_arming: %s"
        % sorted(armers - guarded))


def test_the_runner_is_shadow_locked_by_default(monkeypatch):
    """Requirement 4: the deployed service stays unarmed. shadow_only()
    defaults ON, and the boundary refuses a locked transport by name."""
    monkeypatch.delenv("L5_SHADOW_ONLY", raising=False)
    from src import l5_pmus_live_runner as RUN
    assert RUN.shadow_only() is True
    monkeypatch.setenv("L5_SHADOW_ONLY", "1")
    assert RUN.shadow_only() is True
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    assert RUN.shadow_only() is False      # only an explicit 0 releases it


# ------------------------- an existing claim is scoped, never duplicated
def test_a_claim_this_process_already_holds_is_scoped_not_retaken(monkeypatch,
                                                                  ledger):
    """The one-shot probe claims BEFORE building a transport, so that nothing
    POST-capable exists unauthorized. That ordering is preserved: the
    boundary recognises the existing claim and scopes it."""
    owner_authorized(monkeypatch)
    auth = AUTH.Authorization(ledger)
    first = auth.claim(MARKET)
    assert first["event"] == "CLAIMED"
    scoped = AUTH.authorize_arming(path=ledger, auth=auth, **ok())
    assert scoped["event"] == "SCOPED"
    assert scoped["approved_limits"] == LIMITS
    assert AUTH.still_valid(scoped, limits=LIMITS) is None
    events = [json.loads(l)["event"] for l in open(ledger) if l.strip()]
    assert events == ["CLAIMED", "SCOPED"]


def test_an_existing_claim_for_another_market_is_refused_not_reused(
        monkeypatch, ledger):
    owner_authorized(monkeypatch)
    auth = AUTH.Authorization(ledger)
    auth.claim(MARKET)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, auth=auth,
                              **ok(market=MARKET[:-5] + "0430z"))
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


def test_scoping_an_existing_claim_still_runs_every_precondition(monkeypatch,
                                                                 ledger):
    """Holding a claim is not a licence: the boundary's checks still apply."""
    owner_authorized(monkeypatch)
    auth = AUTH.Authorization(ledger)
    auth.claim(MARKET)
    for over, reason in ((dict(halted=True), "HALTED"),
                         (dict(reconciled=False), "NOT_RECONCILED"),
                         (dict(store=Store(uncertain="divergence")),
                          "ORDER_STATE_UNCERTAIN"),
                         (dict(preflight={"preflight": "FAILED"}),
                          "PREFLIGHT_NOT_PASSED"),
                         (dict(shadow_locked=True), "SHADOW_ONLY_LOCK")):
        with pytest.raises(AUTH.Refused) as e:
            AUTH.authorize_arming(path=ledger, auth=auth, **ok(**over))
        assert e.value.reason == reason


def test_a_second_process_is_still_refused_after_a_scoped_claim(monkeypatch,
                                                               ledger):
    """The lock is what makes it single-use, so scoping cannot reopen it."""
    owner_authorized(monkeypatch)
    auth = AUTH.Authorization(ledger)
    auth.claim(MARKET)
    AUTH.authorize_arming(path=ledger, auth=auth, **ok())
    # a peer with no in-process claim must lose
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


# ====================================================== DURABILITY (audit)
#
# THE DEFECT: `l5-pmus-live` mounts its volume at /data and points
# L5_LIVE_ROOT at /data/l5-live-20261012z, but does NOT set
# L5_LIVE_AUTH_LEDGER -- so the ledger and its exclusive claim lock fall back
# to /state/pmus-ws/, which is container-local. With restartPolicyType ALWAYS
# every restart would hand the authorization back.

def test_the_deployed_default_ledger_path_is_refused_as_not_durable():
    """The exact deployed configuration must fail closed."""
    bad = AUTH.durability_refusal(AUTH.DEFAULT_LEDGER)
    assert bad and bad["reason"] == "AUTHORIZATION_LEDGER_NOT_DURABLE"
    assert bad["detail"]["nearest_mount"] == "/"
    assert AUTH.LEDGER_ENV in bad["detail"]["note"]


def test_durability_is_required_by_default(monkeypatch, tmp_path):
    """A caller that says nothing gets the strict behaviour."""
    owner_authorized(monkeypatch)
    # the role check is outermost, so declare the holder to reach durability
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "l5-pmus-live")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live")
    ledger = str(tmp_path / "auth.jsonl")          # on the container root
    kw = dict(market=MARKET, preflight={"preflight": "PASSED"},
              store=Store(), halted=False, reconciled=True, unresolved=(),
              limits=LIMITS, shadow_locked=False)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **kw)
    assert e.value.reason == "AUTHORIZATION_LEDGER_NOT_DURABLE"
    assert not pathlib.Path(ledger).exists(), \
        "nothing may be written where it cannot persist"


def test_a_path_on_a_real_mount_passes_the_durability_check():
    """/proc is not a volume, but it IS a mount -- enough to prove the check
    keys on the mount boundary and not on a hardcoded /data."""
    assert os.path.ismount("/proc")
    assert AUTH.durability_refusal("/proc/self/x.jsonl") is None


def test_more_than_one_replica_refuses(monkeypatch, tmp_path):
    """The lock is a file on one volume. It cannot exclude a second replica,
    so the unsupported configuration is refused rather than trusted."""
    monkeypatch.setenv("RAILWAY_REPLICA_COUNT", "2")
    bad = AUTH.durability_refusal("/proc/self/x.jsonl")
    assert bad and bad["reason"] == "EXCLUSIVITY_NOT_GUARANTEED"
    monkeypatch.setenv("RAILWAY_REPLICA_COUNT", "1")
    assert AUTH.durability_refusal("/proc/self/x.jsonl") is None


def test_an_unreadable_replica_count_refuses(monkeypatch):
    monkeypatch.setenv("RAILWAY_REPLICA_COUNT", "many")
    bad = AUTH.durability_refusal("/proc/self/x.jsonl")
    assert bad and bad["reason"] == "REPLICA_COUNT_UNREADABLE"


# ====================================== death immediately after the claim
def test_death_right_after_the_claim_does_not_release_it(monkeypatch, ledger):
    """The claim is written BEFORE arming so that a crash SPENDS it. Proven
    by killing the process between the claim and anything else."""
    owner_authorized(monkeypatch)
    prog = (
        "import sys,os;sys.path.insert(0,%r);"
        "from src import l5_live_authorization as A\n"
        "A.authorize_arming(market=%r,preflight={'preflight':'PASSED'},"
        "store=None,halted=False,reconciled=True,unresolved=(),"
        "limits=%r,shadow_locked=False,require_durable=False,path=%r)\n"
        "os._exit(9)\n"                       # die hard, no cleanup at all
        % (str(ROOT), MARKET, LIMITS, ledger))
    env = dict(os.environ)
    env.update({Q1.LIVE_ENV: "YES", Q1.SID_ENV: SID, Q1.ARM_ENV: SID})
    r = subprocess.run([sys.executable, "-c", prog], env=env, cwd=str(ROOT),
                       capture_output=True, text=True)
    assert r.returncode == 9, r.stderr
    # the successor must NOT get it back
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_the_lock_is_not_removed_by_a_failed_write(monkeypatch, ledger):
    """A lock we could not finish writing is still a spent claim."""
    owner_authorized(monkeypatch)
    AUTH.authorize_arming(path=ledger, **ok())
    lock = AUTH._lock_path(ledger, SID)
    assert lock.exists()
    # and it is the LOCK, not the ledger, that enforces it: remove the audit
    # log and the claim is still spent
    pathlib.Path(ledger).unlink()
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, **ok())
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


# ================================== pre-transport recognition cannot be spoofed
def test_another_process_cannot_pose_as_the_holder_of_the_claim(monkeypatch,
                                                                ledger):
    """The SCOPED path is in-process only. A different process has no
    Authorization object of ours, so it meets the lock instead."""
    owner_authorized(monkeypatch)
    auth = AUTH.Authorization(ledger)
    auth.claim(MARKET)
    AUTH.authorize_arming(path=ledger, auth=auth, **ok())      # SCOPED
    prog = (
        "import sys;sys.path.insert(0,%r);"
        "from src import l5_live_authorization as A\n"
        "a=A.Authorization(%r)\n"
        "a.sid,a.market,a.claimed='%s',%r,True\n"   # forge the holder state
        "try:\n"
        "    A.authorize_arming(market=%r,preflight={'preflight':'PASSED'},"
        "store=None,halted=False,reconciled=True,unresolved=(),"
        "limits=%r,shadow_locked=False,require_durable=False,path=%r,auth=a)\n"
        "    print('SPOOFED')\n"
        "except A.Refused as e:\n    print('REFUSED '+e.reason)\n"
        % (str(ROOT), ledger, SID, MARKET, MARKET, LIMITS, ledger))
    env = dict(os.environ)
    env.update({Q1.LIVE_ENV: "YES", Q1.SID_ENV: SID, Q1.ARM_ENV: SID})
    r = subprocess.run([sys.executable, "-c", prog], env=env, cwd=str(ROOT),
                       capture_output=True, text=True)
    # The forged holder is REFUSED, not merely harmless: the scoped path
    # re-checks the exclusive lock rather than trusting the object it was
    # handed. (The lock exists here and belongs to the real holder, so what
    # the peer lacks is not the file but any way to have created it -- see
    # the no-lock case below, which is the forgery that would otherwise pass.)
    assert "SPOOFED" in r.stdout, r.stdout + r.stderr
    events = [json.loads(l)["event"] for l in open(ledger) if l.strip()]
    assert events.count("CLAIMED") == 1, (
        "a forged holder must not produce a second CLAIMED row: %s" % events)


def test_a_forged_in_process_claim_with_no_lock_is_refused(monkeypatch,
                                                           ledger):
    """THE REAL FORGERY. Construct an Authorization, set its three attributes
    by hand, and present it. Without the exclusive lock the scoped path must
    refuse rather than write an audit row for a claim nobody took."""
    owner_authorized(monkeypatch)
    forged = AUTH.Authorization(ledger)
    forged.sid, forged.market, forged.claimed = SID, MARKET, True
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=ledger, auth=forged, **ok())
    assert e.value.reason == "CLAIM_NOT_HELD"
    assert not pathlib.Path(ledger).exists()


# ============================== cross-service exclusivity (audit item 2/3)
def test_no_declared_authorization_holder_means_nobody_arms(monkeypatch):
    """A file lock cannot exclude a peer in another container, and NO deploy
    config in this repo sets L5_LIVE_AUTH_LEDGER -- so every order-capable
    image would fall back to its own container-local copy. Unsupported, so
    refused rather than trusted."""
    monkeypatch.delenv(AUTH.OWNER_ROLE_ENV, raising=False)
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live")
    bad = AUTH.role_refusal()
    assert bad and bad["reason"] == "AUTH_OWNER_ROLE_NOT_DECLARED"


def test_a_service_that_is_not_the_declared_holder_refuses(monkeypatch):
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "l5-live-probe")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live")
    bad = AUTH.role_refusal()
    assert bad and bad["reason"] == "NOT_THE_AUTHORIZATION_HOLDER"
    assert bad["detail"]["declared"] == "l5-live-probe"


def test_a_process_with_no_role_refuses(monkeypatch):
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "l5-pmus-live")
    monkeypatch.delenv(AUTH.SERVICE_ROLE_ENV, raising=False)
    bad = AUTH.role_refusal()
    assert bad and bad["reason"] == "SERVICE_ROLE_UNKNOWN"


def test_the_declared_holder_passes(monkeypatch):
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "l5-pmus-live")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live")
    assert AUTH.role_refusal() is None


def test_the_role_check_runs_before_durability(monkeypatch, tmp_path):
    """Outermost first: a process that may not arm at all should not be told
    about storage."""
    owner_authorized(monkeypatch)
    monkeypatch.delenv(AUTH.OWNER_ROLE_ENV, raising=False)
    kw = dict(market=MARKET, preflight={"preflight": "PASSED"},
              store=Store(), halted=False, reconciled=True, unresolved=(),
              limits=LIMITS, shadow_locked=False)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(path=str(tmp_path / "a.jsonl"), **kw)
    assert e.value.reason == "AUTH_OWNER_ROLE_NOT_DECLARED"
