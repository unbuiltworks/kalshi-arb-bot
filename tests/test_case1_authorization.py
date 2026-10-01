"""The narrow Case-1 authorization gate.

The repository-level HALT means "no orders". This probe must not bypass it: it
requires its own time-boxed, SHA-pinned artifact that states the override
explicitly and scopes it to this probe alone. The four properties the owner
required are each asserted here:

  1. Case-1 can arm ONLY under that narrow authorization;
  2. normal L5 trading remains halted;
  3. expired or missing authorization refuses;
  4. the risk envelope cannot exceed the preregistered $0.25 / q1 / one-order
     bounds -- the artifact must EQUAL them, so it cannot widen them either.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from src import pmus_case1_probe as K

#: the reviewed CONTENT digest, computed from the probe's own sources. Not a
#: commit SHA: the artifact lives in the commit it would name, so a commit pin
#: is circular.
SHA = K.code_digest()
LIVE = pathlib.Path(K.AUTH_PATH)
FUTURE = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()


def good(**over):
    a = json.loads(LIVE.read_text())
    a["expires_utc"] = FUTURE
    a.update(over)
    return a


def write(tmp_path, auth, *, halt=True):
    """Build a fake root. The authorization and HALT live in DIFFERENT
    directories now, which is the point of the relocation."""
    a = tmp_path / pathlib.Path(K.AUTH_PATH).parent
    a.mkdir(parents=True, exist_ok=True)
    if auth is not None:
        (tmp_path / K.AUTH_PATH).write_text(json.dumps(auth))
    h = tmp_path / pathlib.Path(K.HALT_PATH).parent
    h.mkdir(parents=True, exist_ok=True)
    if halt:
        (tmp_path / K.HALT_PATH).write_text("HALTED\n")
    return str(tmp_path)


# ---- 1. the committed artifact is itself valid and narrow ---------------

def test_the_committed_authorization_is_valid_and_pinned():
    a = K.authorization(sha=SHA)
    assert a["probe"] == "case1_resting_order_crossed"
    assert a["reviewed_code_sha256"] == SHA
    assert list(a["reviewed_code_files"]) == list(K.CODE_FILES)
    assert a["authorizes_l5_session_engine"] is False
    assert a["authorizes_further_probes"] is False
    assert D(str(a["max_entry_price_usd"])) == K.MAX_ENTRY_PX == D("0.25")
    assert D(str(a["max_loss_usd"])) == K.MAX_LOSS == D("0.25")
    assert a["quantity"] == K.QTY == 1
    assert a["max_submissions"] == 1 and a["max_markets"] == 1
    for k in ("allow_reprice", "allow_chase", "allow_replacement",
              "allow_complement_leg"):
        assert a[k] is False, k


# ---- 3. missing / expired / unparseable refuses -------------------------

def test_a_missing_authorization_refuses_absence_is_the_safe_state(tmp_path):
    root = write(tmp_path, None)
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "NO_AUTHORIZATION" in str(e.value)


def test_an_expired_authorization_refuses(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    root = write(tmp_path, good(expires_utc=past))
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "AUTHORIZATION_EXPIRED" in str(e.value)


def test_an_expiry_without_a_timezone_refuses(tmp_path):
    root = write(tmp_path, good(expires_utc="2099-01-01T00:00:00"))
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "NAIVE" in str(e.value)


def test_unparseable_or_non_object_refuses(tmp_path):
    write(tmp_path, None)                      # creates the dirs and HALT
    bad = tmp_path / K.AUTH_PATH
    bad.write_text("{not json")
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=str(tmp_path), sha=SHA)
    assert "UNPARSEABLE" in str(e.value)
    bad.write_text("[1,2]")
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=str(tmp_path), sha=SHA)
    assert "NOT_AN_OBJECT" in str(e.value)


def test_every_required_field_is_required(tmp_path):
    for field in K.AUTH_REQUIRED:
        a = good()
        a.pop(field)
        root = write(tmp_path / field, a)
        with pytest.raises(K.Case1Refuse) as e:
            K.authorization(root=root, sha=SHA)
        assert "AUTHORIZATION_INCOMPLETE" in str(e.value), field
        assert field in str(e.value), field


# ---- 4. the envelope cannot be widened ---------------------------------

def test_the_artifact_cannot_widen_the_price_or_loss_envelope(tmp_path):
    for field in ("max_entry_price_usd", "max_loss_usd"):
        for value in ("0.50", "1.00", "0.26"):
            root = write(tmp_path / f"{field}{value}", good(**{field: value}))
            with pytest.raises(K.Case1Refuse) as e:
                K.authorization(root=root, sha=SHA)
            assert "AUTHORIZATION_BOUND" in str(e.value), (field, value)


def test_the_artifact_cannot_narrow_them_either_equality_is_the_rule(tmp_path):
    root = write(tmp_path, good(max_entry_price_usd="0.10"))
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "must EQUAL" in str(e.value)


def test_quantity_and_order_counts_cannot_be_raised(tmp_path):
    for over in ({"quantity": 2}, {"max_submissions": 2}, {"max_markets": 2}):
        root = write(tmp_path / str(over), good(**over))
        with pytest.raises(K.Case1Refuse):
            K.authorization(root=root, sha=SHA)


def test_chasing_and_a_complement_cannot_be_authorized(tmp_path):
    for flag in ("allow_reprice", "allow_chase", "allow_replacement",
                 "allow_complement_leg"):
        root = write(tmp_path / flag, good(**{flag: True}))
        with pytest.raises(K.Case1Refuse) as e:
            K.authorization(root=root, sha=SHA)
        assert flag in str(e.value), flag


def test_a_non_numeric_bound_refuses_rather_than_coercing(tmp_path):
    root = write(tmp_path, good(max_loss_usd="lots"))
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "not a number" in str(e.value)


# ---- the SHA pin -------------------------------------------------------

def test_a_code_digest_mismatch_refuses(tmp_path):
    """Editing the probe after authorization invalidates the authorization."""
    root = write(tmp_path, good())
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha="deadbeef")
    assert "CODE_DIGEST_MISMATCH" in str(e.value)
    assert "edited after it was authorized" in str(e.value)


def test_the_digest_actually_changes_when_the_probe_changes(tmp_path):
    """Otherwise the pin would be decorative."""
    import shutil
    for rel in K.CODE_FILES:
        d = tmp_path / pathlib.Path(rel).parent
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(rel, tmp_path / rel)
    before = K.code_digest(str(tmp_path))
    assert before == K.code_digest(), "a faithful copy must hash the same"
    target = tmp_path / K.CODE_FILES[0]
    target.write_text(target.read_text() + "\n# one added comment\n")
    assert K.code_digest(str(tmp_path)) != before


def test_a_missing_probe_source_refuses(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(K.Case1Refuse) as e:
        K.code_digest(str(tmp_path))
    assert "CODE_MISSING" in str(e.value)


# ---- 2. the HALT override is explicit, scoped, and does not un-halt L5 --

def test_with_halt_present_the_override_must_be_explicit(tmp_path):
    root = write(tmp_path, good(overrides_global_halt=False), halt=True)
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "HALT_PRESENT" in str(e.value)
    #: and a truthy-but-not-True value is not an explicit override
    root = write(tmp_path / "truthy", good(overrides_global_halt="yes"),
                 halt=True)
    with pytest.raises(K.Case1Refuse):
        K.authorization(root=root, sha=SHA)


def test_an_unscoped_halt_override_refuses(tmp_path):
    root = write(tmp_path, good(overrides_global_halt_scope="  "), halt=True)
    with pytest.raises(K.Case1Refuse) as e:
        K.authorization(root=root, sha=SHA)
    assert "UNSCOPED" in str(e.value)


def test_the_global_halt_is_still_in_place_for_the_engine():
    """We did not un-halt unrelated trading to run this probe."""
    assert K.halt_present(), \
        "the repository HALT must remain; the probe is gated by its own artifact"


def test_the_authorization_does_not_authorize_the_l5_session_engine():
    a = json.loads(LIVE.read_text())
    assert a["authorizes_l5_session_engine"] is False
    assert a["authorizes_further_probes"] is False
    assert "THIS PROBE ONLY" in a["overrides_global_halt_scope"]


def test_no_other_module_reads_the_case1_authorization():
    """The narrow artifact must not become a general-purpose un-HALT."""
    hits = []
    for p in list(pathlib.Path("src").glob("*.py")) + \
            list(pathlib.Path("scripts").glob("*.py")):
        if p.name in ("pmus_case1_probe.py", "pmus_case1_probe_run.py"):
            continue
        if "CASE1_PROBE_AUTHORIZATION" in p.read_text():
            hits.append(p.name)
    assert hits == [], f"the Case-1 authorization leaked into {hits}"


def test_the_live_mandate_is_still_absent():
    """This probe is authorized by its own artifact, not by granting a mandate
    to the L5 session engine."""
    assert not pathlib.Path(
        "analysis/l5/authorization/live_mandate.json").exists()


# ---- 1. arming requires the authorization FIRST ------------------------

def test_the_runner_consults_the_authorization_before_arming():
    src = pathlib.Path("scripts/pmus_case1_probe_run.py").read_text()
    i_auth = src.index("auth = K.authorization()")
    i_arm = src.index("if not K.armed(")
    assert i_auth < i_arm, "arming must not precede the authorization check"


def test_the_runner_refuses_when_the_authorization_is_unreachable(monkeypatch,
                                                                 tmp_path):
    """An expired authorization refuses before any arming or transport."""
    import scripts.pmus_case1_probe_run as S
    from src import pmus_q1_executor as X
    monkeypatch.setattr(K, "AUTH_PATH",
                        "analysis/l5/calibration/__absent__.json")
    for env, val in ((K.LIVE_ENV, "YES"), (K.SESSION_ENV, "s"),
                     (K.ARM_ENV, "s"), (K.CASE1_ARM_ENV, "s")):
        monkeypatch.setenv(env, val)
    monkeypatch.setenv(X.MARKET_ENV, "cpc-btc-updown-15m-2099-01-01-1200z")
    monkeypatch.setenv(X.KEY_ENV, "k")
    monkeypatch.setenv(X.SECRET_ENV, "s")

    class Boom(X.Transport):
        def __init__(self): pass
        def request(self, *a, **k):
            raise AssertionError("reached the venue despite no authorization")

    out = S.run(Boom())
    assert out["outcome"] == "REFUSED"
    assert "NO_AUTHORIZATION" in out["refusal"]
    assert out["submitted"] is False
    assert S.terminal_ok(out) is False


# ---- the relocation itself is a property worth pinning ------------------

def test_the_authorization_is_not_in_the_r1_pinned_directory():
    """analysis/l5/authorization/ is COPYed into the owner-gated R1 lane image
    and its content is pinned by r1_source_lineage. A Case-1 authorization must
    not sit there: it would turn that gate red and would carry this
    authorization into a second money-moving image."""
    assert not K.AUTH_PATH.startswith("analysis/l5/authorization/")
    assert K.AUTH_PATH == "analysis/l5/calibration/CASE1_PROBE_AUTHORIZATION.json"
    assert pathlib.Path(K.AUTH_PATH).exists()
    assert not pathlib.Path(
        "analysis/l5/authorization/CASE1_PROBE_AUTHORIZATION.json").exists()


def test_the_r1_lane_image_does_not_carry_the_case1_authorization():
    r1 = pathlib.Path("deploy/l5_r1_probe/Dockerfile")
    if not r1.exists():
        pytest.skip("no R1 dockerfile")
    assert "CASE1_PROBE_AUTHORIZATION" not in r1.read_text()


def test_the_lock5_probe_image_does_carry_it():
    df = pathlib.Path("deploy/l5_pmus_lock5_probe/Dockerfile").read_text()
    assert f"COPY {K.AUTH_PATH} {K.AUTH_PATH}" in df


def test_halt_is_still_read_from_the_authorization_directory_unchanged():
    assert K.HALT_PATH == "analysis/l5/authorization/HALT"
    assert pathlib.Path(K.HALT_PATH).exists()
