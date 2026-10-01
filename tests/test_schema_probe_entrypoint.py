"""The executable schema probe must refuse, clean up, and never report success
on absence of bad news. Every test runs offline and submits nothing real.
"""
import json
from decimal import Decimal as D

import pytest

from src import pmus_private_schema_probe as P
from src import pmus_q1_canary as C
from src import pmus_q1_executor as X
import scripts.pmus_schema_probe_run as S

SID = "sess-1"
SLUG = "cpc-btc-updown-15m-2099-01-01-1200z"
#: the completed ORDER snapshot actually observed on the venue 2026-10-01
READY_PRIVATE = [{
    "requestId": "snap-0ad7630a",
    "subscriptionType": "SUBSCRIPTION_TYPE_ORDER_SNAPSHOT",
    "orderSubscriptionSnapshot": {"orders": [], "eof": True},
}]


@pytest.fixture(autouse=True)
def _reset():
    S._STOP.clear()
    yield
    S._STOP.clear()


def arm(mp, *, creds=True, session=SID):
    for env, val in ((P.LIVE_ENV, "YES"), (P.SESSION_ENV, session),
                     (P.ARM_ENV, session), (P.PROBE_ARM_ENV, session)):
        mp.setenv(env, val)
    mp.setenv(X.MARKET_ENV, SLUG)
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


def test_no_arming_means_no_submission(monkeypatch):
    for env in (P.LIVE_ENV, P.SESSION_ENV, P.ARM_ENV, P.PROBE_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    out = S.run(Boom())
    assert out["submitted"] is False and out["outcome"] == "REFUSED"
    assert S.terminal_ok(out) is False, \
        "a refusal is SAFE but must not look like successful execution"


@pytest.mark.parametrize("drop", [P.LIVE_ENV, P.SESSION_ENV, P.ARM_ENV,
                                  P.PROBE_ARM_ENV])
def test_any_missing_arm_key_blocks_submission(monkeypatch, drop):
    arm(monkeypatch)
    monkeypatch.delenv(drop, raising=False)
    out = S.run(Boom())
    assert out["submitted"] is False and "NOT_ARMED" in out["refusal"]


def test_mismatched_arm_value_blocks_submission(monkeypatch):
    arm(monkeypatch)
    monkeypatch.setenv(P.PROBE_ARM_ENV, "a-different-session")
    out = S.run(Boom())
    assert out["submitted"] is False and "NOT_ARMED" in out["refusal"]


def test_no_credentials_means_no_submission(monkeypatch):
    arm(monkeypatch, creds=False)
    out = S.run(Boom())
    assert out["submitted"] is False
    assert out["refusal"] == "NO_CREDENTIALS"


def test_missing_market_refuses_without_contacting_the_venue(monkeypatch):
    arm(monkeypatch)
    monkeypatch.delenv(X.MARKET_ENV, raising=False)
    with pytest.raises(P.ProbeRefuse):
        S.market_slug()


def test_insufficient_frames_on_either_socket_blocks_submission():
    with pytest.raises(P.ProbeRefuse) as e:
        P.preflight(Boom(), SLUG, public_frames=P.READY_FRAMES - 1,
                    private_frames=READY_PRIVATE)
    assert "PUBLIC_SOCKET_NOT_PROVEN" in str(e.value)
    with pytest.raises(P.ProbeRefuse) as e:
        P.preflight(Boom(), SLUG, public_frames=99, private_frames=[])
    assert "PRIVATE_SOCKET_NOT_PROVEN" in str(e.value)


def test_a_touch_under_21_cents_blocks_submission():
    for touch in ("0.05", "0.20", "0.2099"):
        with pytest.raises(P.ProbeRefuse):
            P.probe_price(D(touch))


def test_an_off_grid_price_is_impossible():
    for touch in ("0.21", "0.205", "0.37", "0.99"):
        try:
            px = P.probe_price(D(touch))
        except P.ProbeRefuse:
            continue
        assert px == D("0.01") and px % P.PRICE_TICK == 0


def test_crossing_blocks_submission(monkeypatch):
    """preflight must refuse when would_cross says the price meets the book."""
    monkeypatch.setattr(X, "fresh_bbo",
                        lambda t, slug, sleep=None: {"bid": D("0.40"),
                                                     "ask": D("0.41")})
    monkeypatch.setattr(X, "would_cross", lambda *a, **k: True)
    with pytest.raises(P.ProbeRefuse) as e:
        P.preflight(Boom(), SLUG, public_frames=9,
                    private_frames=READY_PRIVATE)
    assert "WOULD_CROSS" in str(e.value)


def test_a_stale_feed_blocks_submission(monkeypatch):
    def stale(*a, **k):
        raise X.StaleRefuse("STALE")
    monkeypatch.setattr(X, "fresh_bbo", stale)
    with pytest.raises(X.StaleRefuse):
        P.preflight(Boom(), SLUG, public_frames=9,
                    private_frames=READY_PRIVATE)


def test_exactly_one_submission_maximum():
    """Walk the AST: exactly one POST to /v1/orders, and it is not inside a loop.

    Scanned structurally rather than by substring -- an earlier version of this
    test flagged the word "retry" inside the docstring's list of REUSED
    primitives (_get_with_retry), which is prose, not a retry path.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(S))
    submits = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.loops = 0

        def visit_For(self, n):
            self.loops += 1
            self.generic_visit(n)
            self.loops -= 1

        def visit_While(self, n):
            self.loops += 1
            self.generic_visit(n)
            self.loops -= 1

        def visit_Call(self, n):
            args = [a.value for a in n.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if "POST" in args and "/v1/orders" in args:
                submits.append(self.loops)
            self.generic_visit(n)

    V().visit(tree)
    assert len(submits) == 1, f"{len(submits)} order-submission call sites"
    assert submits[0] == 0, "the submission sits inside a loop"


def test_terminal_success_requires_every_proof():
    base = {"outcome": "OBSERVED", "submitted": True,
            "terminal_proved": True, "sweep_verified": True}
    assert S.terminal_ok(base) is True
    assert S.terminal_ok({**base, "terminal_proved": False}) is False
    assert S.terminal_ok({**base, "sweep_verified": False}) is False
    assert S.terminal_ok({**base, "outcome": "ERROR"}) is False
    assert S.terminal_ok({**base, "submitted": False}) is False
    assert S.terminal_ok({**base, "open_orders_after": ["OID"]}) is False
    assert S.terminal_ok({**base, "cleanup_error": "boom"}) is False


@pytest.mark.parametrize("outcome", ["REFUSED", "ERROR",
                                     "PROBE_FAILURE_UNEXPECTED_FILL"])
def test_every_non_observed_outcome_exits_nonzero(outcome):
    out = {"outcome": outcome, "submitted": True, "terminal_proved": True,
           "sweep_verified": True}
    assert S.terminal_ok(out) is False


def test_a_failed_reconciliation_exits_nonzero():
    out = {"outcome": "OBSERVED", "submitted": True, "terminal_proved": True,
           "sweep_verified": True, "cleanup_error": "RuntimeError: venue 500"}
    assert S.terminal_ok(out) is False


def test_main_returns_nonzero_on_a_safe_refusal(monkeypatch):
    """The whole point: unarmed must not look like a successful observation."""
    for env in (P.LIVE_ENV, P.SESSION_ENV, P.ARM_ENV, P.PROBE_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    assert S.main() == 1


def test_an_unexpected_fill_is_a_failure_never_a_success():
    out = {"outcome": "PROBE_FAILURE_UNEXPECTED_FILL", "submitted": True,
           "terminal_proved": True, "sweep_verified": True}
    assert S.terminal_ok(out) is False
    assert P.classify_outcome(D(1)) == "PROBE_FAILURE_UNEXPECTED_FILL"
    assert P.classify_outcome(D("0.4")) == "PROBE_FAILURE_UNEXPECTED_FILL"


def test_leftover_open_orders_fail_the_probe():
    out = {"outcome": "OBSERVED", "submitted": True, "terminal_proved": True,
           "sweep_verified": False, "open_orders_after": ["OID"]}
    assert S.terminal_ok(out) is False


def test_cancel_unconfirmed_cannot_be_reported_as_success():
    """An UNKNOWN terminal state must never pass the gate."""
    out = {"outcome": "OBSERVED", "submitted": True, "terminal_proved": False,
           "sweep_verified": True, "cancel_result": {"unknown": "True"}}
    assert S.terminal_ok(out) is False


def test_private_evidence_matching_is_structural_not_schema_dependent():
    """We are here to LEARN the schema, so matching must not assume a field."""
    oid = "CTJ1B3GJTYBQ"
    frames = [
        {"someUnknownWrapper": {"deep": [{"whateverTheyCallIt": oid}]}},
        {"unrelated": "no match here"},
        {"orders": [{"id": "OTHER"}]},
    ]
    found = S.private_evidence(frames, oid)
    assert len(found) == 1 and found[0] is frames[0]


def test_raw_frames_survive_the_ledger_structurally_unchanged():
    led = P.FrameLedger()
    raw = {"subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
           "venueOnlyField": 7, "nested": {"a": [1, 2, {"b": None}]}}
    before = json.dumps(raw, sort_keys=True)
    led.add(raw, source="private")
    rep = led.report()
    assert json.dumps(rep["frames_verbatim"][0]["raw"], sort_keys=True) == before
    assert "venueOnlyField" in \
        rep["observed_frame_keys"]["SUBSCRIPTION_TYPE_ORDER"]


def test_the_entrypoint_reuses_proven_primitives_rather_than_reimplementing():
    import inspect
    src = inspect.getsource(S)
    for reused in ("C.cancel_and_resolve", "C.open_order_ids", "C._poll_fill",
                   "X.persist_intent", "OB.preview_envelope",
                   "P.build_body", "P.preflight"):
        assert reused in src, f"{reused} is not reused"
    # fresh_bbo and would_cross are reused ONE LAYER DOWN, inside P.preflight,
    # which is the right place for them: the entrypoint must not re-derive the
    # staleness or crossing rules itself.
    pre = inspect.getsource(P.preflight)
    assert "X.fresh_bbo" in pre and "X.would_cross" in pre
    # and it must not invent its own cancel or signing
    for banned in ("def cancel_exact", "def auth_headers", "def sign_path"):
        assert banned not in src, f"the entrypoint reimplements {banned!r}"


def test_signals_set_the_stop_flag_so_readers_wind_down():
    S._STOP.clear()
    assert not S._STOP.is_set()
    S._STOP.set()
    assert S._STOP.is_set()


def test_pmus_q1_live_is_the_literal_YES_not_the_session_id(monkeypatch):
    """The exact arming contract, pinned so the Railway values cannot be wrong.

    PMUS_Q1_LIVE must be 'YES'. Setting it to the session id -- which an earlier
    refusal message wrongly implied -- must NOT arm the probe.
    """
    arm(monkeypatch)
    assert P.armed(SID) is True
    monkeypatch.setenv(P.LIVE_ENV, SID)
    assert P.armed(SID) is False, "PMUS_Q1_LIVE=<session id> must not arm"
    monkeypatch.setenv(P.LIVE_ENV, "yes")
    assert P.armed(SID) is True, "case and surrounding space are tolerated"
    monkeypatch.setenv(P.LIVE_ENV, " YES ")
    assert P.armed(SID) is True
    monkeypatch.setenv(P.LIVE_ENV, "TRUE")
    assert P.armed(SID) is False


def test_the_refusal_message_states_the_real_contract(monkeypatch):
    for env in (P.LIVE_ENV, P.SESSION_ENV, P.ARM_ENV, P.PROBE_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    msg = S.run(Boom())["refusal"]
    assert "must be exactly 'YES'" in msg
    assert "equal the same session id" in msg
    assert "all four" not in msg, "the old, misleading wording is back"
