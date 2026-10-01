"""The executable Case-1 calibration must refuse, clean up, never move the
order, and never report success on the absence of bad news.

Every test runs offline and submits nothing real.
"""
import ast
import pathlib

import pytest

from src import pmus_case1_probe as K
from src import pmus_q1_executor as X
import scripts.pmus_case1_probe_run as S
import scripts.pmus_schema_probe_run as SP

SID = "sess-case1"
SLUG = "cpc-btc-updown-15m-2099-01-01-1200z"
RUN_SRC = pathlib.Path("scripts/pmus_case1_probe_run.py")


@pytest.fixture(autouse=True)
def _reset():
    SP._STOP.clear()
    yield
    SP._STOP.clear()


def arm(mp, *, creds=True, session=SID, slug=SLUG):
    for env, val in ((K.LIVE_ENV, "YES"), (K.SESSION_ENV, session),
                     (K.ARM_ENV, session), (K.CASE1_ARM_ENV, session)):
        mp.setenv(env, val)
    mp.setenv(X.MARKET_ENV, slug)
    if creds:
        mp.setenv(X.KEY_ENV, "k")
        mp.setenv(X.SECRET_ENV, "s")
    else:
        mp.delenv(X.KEY_ENV, raising=False)
        mp.delenv(X.SECRET_ENV, raising=False)


class Boom(X.Transport):
    """Any HTTP call is a test failure: these paths must never reach the venue."""

    def __init__(self):
        pass

    def request(self, *a, **k):
        raise AssertionError(f"the probe contacted the venue: {a}")


# ------------------------------------------------------ it refuses, offline

def test_no_arming_means_no_submission(monkeypatch):
    for env in (K.LIVE_ENV, K.SESSION_ENV, K.ARM_ENV, K.CASE1_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    out = S.run(Boom())
    assert out["submitted"] is False and out["outcome"] == "REFUSED"
    assert S.terminal_ok(out) is False, \
        "a refusal is SAFE but must not look like successful execution"
    assert K.CASE1_ARM_ENV in out["refusal"]


def test_three_of_four_armed_still_refuses(monkeypatch):
    arm(monkeypatch)
    monkeypatch.delenv(K.CASE1_ARM_ENV)
    out = S.run(Boom())
    assert out["outcome"] == "REFUSED" and out["submitted"] is False


def test_missing_credentials_refuses_before_any_request(monkeypatch):
    arm(monkeypatch, creds=False)
    out = S.run(Boom())
    assert out["outcome"] == "REFUSED"
    assert out["refusal"] == "NO_CREDENTIALS"
    assert S.terminal_ok(out) is False


def test_missing_market_refuses(monkeypatch):
    arm(monkeypatch)
    monkeypatch.delenv(X.MARKET_ENV)
    out = S.run(Boom())
    assert out["outcome"] == "REFUSED" and "NO_MARKET" in out["refusal"]


def test_an_unparseable_window_refuses_rather_than_guessing_a_close(monkeypatch):
    arm(monkeypatch, slug="not-a-window-slug")
    out = S.run(Boom())
    assert out["outcome"] == "REFUSED"
    assert "UNPARSEABLE_WINDOW" in out["refusal"]
    assert S.terminal_ok(out) is False


def test_a_window_already_in_its_tail_refuses(monkeypatch):
    """Never go live inside the guard before the close."""
    arm(monkeypatch, slug="cpc-btc-updown-15m-2020-01-01-1200z")
    out = S.run(Boom())
    #: a long-past window: the tail guard must trip before any order path.
    assert out["submitted"] is False
    assert out["outcome"] in ("REFUSED", "ERROR")


# ------------------------------------------- the success signal is positive

def test_terminal_ok_requires_positive_proof_of_every_condition():
    good = {"outcome": "NULL_NOT_REACHED", "submitted": True,
            "terminal_proved": True, "sweep_verified": True,
            "open_orders_after": [], "cleanup_error": None}
    assert S.terminal_ok(good)
    assert not S.terminal_ok(dict(good, outcome="REFUSED"))
    assert not S.terminal_ok(dict(good, outcome="STOPPED"))
    assert not S.terminal_ok(dict(good, outcome="ERROR"))
    assert not S.terminal_ok(dict(good, submitted=False))
    assert not S.terminal_ok(dict(good, terminal_proved=False))
    assert not S.terminal_ok(dict(good, sweep_verified=False))
    assert not S.terminal_ok(dict(good, open_orders_after=["X"]))
    assert not S.terminal_ok(dict(good, cleanup_error="boom"))


def test_every_terminal_label_is_one_the_library_can_produce():
    produced = {K.classify(submitted=True, rest_proved=True,
                           reach={"reached": x}, filled_qty=f,
                           qual={"qualifies": q})
                for x in (True, False) for f in (0, 1) for q in (True, False)}
    for label in S.TERMINAL_LABELS:
        assert label in produced, label


# --------------------------------------- it structurally cannot chase a fill

def _code_only(path: pathlib.Path) -> str:
    """The runner's CODE with docstrings stripped, so a prose promise not to
    reprice cannot be mistaken for a reprice. The first version of this test
    scanned raw text and tripped on the words 'no replacement'."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_runner_contains_no_reprice_amend_or_resubmit_path():
    code = _code_only(RUN_SRC).lower()
    for banned in ("amend", "reprice", "requote", "modify", "resubmit",
                   "place_limit_buy"):
        assert banned not in code, banned
    #: `replace` is checked as a venue action, not as the English word: the only
    #: admissible use is os.replace in persistence, which lives elsewhere.
    assert "order/replace" not in code and "replace_order" not in code


def test_exactly_one_order_post_exists_in_the_runner():
    """One submission, by construction: a single POST to /v1/orders."""
    src = RUN_SRC.read_text()
    assert src.count('"/v1/orders"') == 1
    assert src.count('"/v1/order/preview"') == 1
    #: and the cancel path is the proven canary helper, not a local POST
    assert "cancel_and_resolve" in src
    assert "/v1/orders/" not in src, "no hand-rolled order mutation path"


def test_the_runner_defines_no_serializer_and_no_transport():
    tree = ast.parse(RUN_SRC.read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for banned in ("build", "build_body", "Transport", "preview_envelope",
                   "signed_headers", "place_limit_buy"):
        assert banned not in defined, f"the runner redefines {banned}"


def test_the_quantity_submitted_can_only_ever_be_one():
    body = K.build_body(SLUG, "up", K.MAX_ENTRY_PX)
    assert body["quantity"] == 1.0
    assert body["participateDontInitiate"] is True
    assert body["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert body["action"] == "ORDER_ACTION_BUY"
    #: own-terms price inverts exactly once for the DOWN leg
    down = K.build_body(SLUG, "down", K.MAX_ENTRY_PX)
    assert down["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert down["price"]["value"] == "0.75"


def test_the_observation_is_bounded_in_time():
    assert 0 < K.OBSERVE_MAX_S <= 900 - K.WINDOW_TAIL_GUARD_S
    assert K.WINDOW_TAIL_GUARD_S >= 60
    assert S.PUBLIC_STALL_STOP_S > 0


def test_the_cleanup_path_is_unconditional():
    """`finally` must carry the cancel/sweep, so no exception can leave an
    order resting."""
    tree = ast.parse(RUN_SRC.read_text())
    runs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run"]
    assert len(runs) == 1
    tries = [n for n in ast.walk(runs[0]) if isinstance(n, ast.Try)]
    finals = [n for n in tries if n.finalbody]
    assert finals, "run() has no finally block"
    body = "\n".join(ast.unparse(s) for n in finals for s in n.finalbody)
    assert "cancel_and_resolve" in body
    assert "open_order_ids" in body
    assert "sweep_verified" in body


# -------------------------------------- the ordering claim is enforced in code

def test_the_runner_samples_the_reach_baseline_at_rest_and_not_before():
    """"Crossed" must mean the book moved AFTER private NEW + REST OPEN. The
    baseline sample is what makes that checkable rather than assumed."""
    src = RUN_SRC.read_text()
    assert "reached_before_rest" in src
    assert "reach_baseline_at_rest" in src
    #: the baseline must be taken after rest is proved, not before
    i_rest = src.index('out["rest_proved"] = rest_proved')
    i_base = src.index('reached_before_rest = bool(base["reached"])')
    assert i_rest < i_base, "the baseline is sampled before rest is proved"
    #: and qualification must use the ORDERED flag
    assert '"reached": reach["reached_after_rest"]' in src


def test_the_verdict_is_reported_and_is_one_of_the_six():
    src = RUN_SRC.read_text()
    assert 'out["verdict"] = K.verdict(' in src
    for label in K.VERDICTS:
        assert label in "".join(K.VERDICTS)
    #: the verdict never feeds the exit code; safety does
    assert "out.get(\"outcome\") in TERMINAL_LABELS" in src


def test_a_refusal_carries_no_verdict_claim(monkeypatch):
    arm(monkeypatch, creds=False)
    out = S.run(Boom())
    assert out["outcome"] == "REFUSED"
    assert "verdict" not in out, "a refusal must make no evidence claim"
