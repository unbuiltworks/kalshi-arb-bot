"""A submission claim must not be spent on an order the venue never saw.

THE DEFECT, OBSERVED LIVE. The submission claim is consumed before the wire
and is never returned -- correctly, because a claim released on a crash could
buy a second order into a venue that already holds the first. But a DISARMED
transport raises `NotArmed` out of `submit()` without attempting a POST, so
nothing reached the venue and nothing could have.

Campaign `camp1.87265e609a92b5a2889e5392` closed having spent 1 of 21
submissions and 1 of 6 OPENs on exactly that: a well-formed MAKER intent
(1715z DOWN 0.4500 x 1.54) refused `NOT_ARMED` after its claim was already
durable. `submitted:false`, `venue_order_id:null` -- and the budget counted a
submission anyway.

THE FIX IS ORDERING, NOT RELEASING. Nothing releases a claim; the guard
simply declines to TAKE one when the transport is provably disarmed. It is
the same class of check as `presend_cross_refusal` and sits beside it.

WHAT THESE TESTS ARE CAREFUL ABOUT. The guard must not become an
authorization. It cannot arm anything, it cannot submit anything, and the
transport's own arm check stays the authority -- so the tests pin that it
acts ONLY on a positive `armed is False` and fails open on everything else.
"""
from __future__ import annotations

import inspect

import pytest

from src import l5_live_execution as E


class _Stub:
    """Only what the guard reads."""

    def __init__(self, transport):
        self.transport = transport


class _T:
    def __init__(self, armed, name="FAKE"):
        self.armed = armed
        self.name = name


class _Raises:
    name = "RAISES"

    @property
    def armed(self):
        raise RuntimeError("telemetry read failed")


def guard(transport):
    return E.FaithfulExecutor.disarmed_refusal(_Stub(transport))


# ============================================== it catches the disarmed case
def test_a_disarmed_transport_is_refused_before_the_claim():
    r = guard(_T(False))
    assert r is not None
    assert r["reason"] == "NOT_ARMED_PRESEND"
    assert r["claim_consumed"] is False


def test_the_default_null_transport_is_caught():
    """`NullTransport.armed` is a class attribute False, and it is what runs
    on any deployment with no authorized transport installed."""
    r = guard(E.NullTransport())
    assert r is not None and r["claim_consumed"] is False


def test_the_refusal_names_no_credential_and_no_order_id():
    r = guard(_T(False))
    blob = repr(r).lower()
    for leak in ("key", "secret", "token", "credential", "private"):
        assert leak not in blob, leak


# ===================================================== it fails OPEN otherwise
def test_an_armed_transport_is_not_touched():
    assert guard(_T(True)) is None


@pytest.mark.parametrize("armed", [None, 1, "yes", 0, "", "False"])
def test_anything_that_is_not_exactly_False_falls_through(armed):
    """ONLY a positive `armed is False` may act. `0` and `""` are falsey but
    are not False, and an unusual transport using them must not be blocked
    by this guard -- failing open merely restores the previous behaviour."""
    assert guard(_T(armed)) is None


def test_a_transport_without_the_attribute_falls_through():
    class Bare:
        name = "BARE"
    assert guard(Bare()) is None


def test_a_transport_whose_attribute_RAISES_falls_through():
    """A telemetry fault must not halt trading."""
    assert guard(_Raises()) is None


def test_no_transport_at_all_falls_through():
    assert guard(None) is None


# ============================================ it runs BEFORE the claim is taken
def _body() -> str:
    return inspect.getsource(E.FaithfulExecutor.handle)


def test_the_guard_runs_before_either_claim_is_consumed():
    """THE WHOLE POINT. If it ran after, the claim would already be durable."""
    b = _body()
    i_guard = b.index("self.disarmed_refusal()")
    i_sub = b.index("AUTH.claim_submission(")
    i_acc = b.index("AUTH.claim_accumulation_leg(")
    assert i_guard < i_sub, "guard is after the single-submission claim"
    assert i_guard < i_acc, "guard is after the accumulation claim"


def test_the_guard_runs_after_every_risk_gate_it_must_not_pre_empt():
    """It is an efficiency guard, so it must be LAST -- it must not skip a
    risk control by returning before one has run. PRESEND_CROSS is the
    existing last-point-before-the-claim check, so this sits with it."""
    b = _body()
    assert b.index("self.presend_cross_refusal(") < b.index(
        "self.disarmed_refusal()")


def _guard_code() -> str:
    """The method's CODE, with its docstring removed.

    A plain keyword scan fails on its own false positive here: the docstring
    explains that a disarmed transport raises `NotArmed` from `submit()`, so
    scanning the raw source for "submit(" matches prose describing what this
    guard is for. The first version of this test did exactly that.
    """
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(E.FaithfulExecutor.disarmed_refusal)))
    fn = tree.body[0]
    body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)
                           and isinstance(fn.body[0].value.value, str)
                           ) else fn.body
    return "\n".join(ast.unparse(n) for n in body)


# A TEXTUAL scan for "submit" / "cancel" / "claim" is NOT a check on this
# method and was removed after failing twice on its own false positives:
# first on the docstring explaining that a disarmed transport raises
# `NotArmed` from `submit()`, then on the `labels` string the refusal
# RETURNS, which says the transport's arm check still decides whether an
# order may be sent. The method's whole output is explanatory prose about
# submission, so the structural call-scan below is the real guarantee.


def test_the_guard_makes_no_call_that_could_arm_anything():
    """Structural, not textual: every call it makes, by name."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(E.FaithfulExecutor.disarmed_refusal)))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            called.add(getattr(f, "attr", None) or getattr(f, "id", None))
    # getattr/str/type only -- it reads and formats, nothing else
    assert called <= {"getattr", "str", "type", None}, called


def test_the_guard_only_reads_and_never_assigns_to_the_transport():
    code = _guard_code()
    assert "tp.armed =" not in code
    assert "self.transport =" not in code
    assert "armed = True" not in code


# ======================================= the authoritative check is untouched
def test_submit_still_raises_NotArmed_on_a_disarmed_transport():
    """The guard is not a replacement for the real gate."""
    with pytest.raises(E.NotArmed):
        E.NullTransport().submit({"market": "m", "side": "UP"})


def test_the_not_armed_handler_still_undoes_the_store_reservation():
    """The existing NotArmed path is unchanged: it still terminates the
    reservation, which is what keeps `max_live_orders` honest."""
    b = _body()
    i = b.index("except NotArmed as exc:")
    seg = b[i:i + 400]
    assert 'terminate_order(coid, reason="NOT_ARMED")' in seg
