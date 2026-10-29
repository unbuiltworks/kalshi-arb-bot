"""The pre-send crossing re-check: book movement between planning and the wire.

WHAT IT IS FOR. A post-only leg the book has already left is rejected by the
venue outright, and the submission claim it spent is never returned -- "not by
a cancellation, a partial or zero fill, a reconnect, a restart, or a rejected
or ambiguous acknowledgement". The first live campaign spent 3 of its first 12
submissions on exactly that:

    00:39:03  l5f-c57c734c  DOWN  ORDER_STATE_REJECTED  cum_qty 0
    00:49:41  l5f-0d3bbd04  DOWN  ORDER_STATE_REJECTED  cum_qty 0
    00:50:46  l5f-49f3944d  UP    ORDER_STATE_REJECTED  cum_qty 0

`plan_executable` already computed LEG_WOULD_CROSS, but it judges the plan at
PLANNING time; the book moves between then and the send. This re-reads the
book immediately before the claim is consumed.

WHAT IT MUST NOT DO, and each is a test here: fail closed on a book it cannot
read, change a price, size, mode, depth, TTL or cutoff, run after the claim is
spent, or touch a taker order.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS


class _Mirror:
    """AuthBook's shape: snapshot(slug), None unless connected and 2-sided."""

    def __init__(self, snap=None, raises=False):
        self._snap, self._raises, self.calls = snap, raises, []

    def snapshot(self, slug):
        self.calls.append(slug)
        if self._raises:
            raise RuntimeError("feed down")
        return self._snap


def _book(bid="0.49", ask="0.51", age="0.05"):
    return {"bid": bid, "ask": ask, "bid_shares": "800", "ask_shares": "800",
            "stream_age_s": age, "source": "authenticated_ws"}


def _ex(tmp_path, mirror=None):
    from src import l5_pmus_live_runner as R
    ex = LX.FaithfulExecutor(LX.InventoryStore(str(tmp_path / "i.json")),
                             LX.SafetyLimits(**R.PROBE_LIMITS))
    ex.ts_book = mirror
    return ex


def _intent(side, own):
    return {"client_order_id": "c1", "market": "m", "side": side,
            "price": D(str(own)), "qty": D("1.61"), "mode": "MAKER"}


MAKER = {"mode": "MAKER"}


# --------------------------------------------- the book moved after planning

@pytest.mark.parametrize("side,own,bid,ask", [
    # UP's wire IS its own price, so it crosses once own >= ask
    ("UP", "0.51", "0.49", "0.51"),
    ("UP", "0.55", "0.49", "0.51"),
    # DOWN's wire is 1 - own, so it crosses once 1 - own <= bid
    ("DOWN", "0.51", "0.49", "0.51"),
    ("DOWN", "0.60", "0.49", "0.51"),
])
def test_a_leg_the_book_has_left_is_not_sent(tmp_path, side, own, bid, ask):
    """Priced when it rested; by the wire the book had moved onto it."""
    ex = _ex(tmp_path, _Mirror(_book(bid, ask)))
    got = ex.presend_cross_refusal("m", _intent(side, own), MAKER)
    assert got is not None, (side, own, bid, ask)
    assert got["reason"] == "PRESEND_WOULD_CROSS:%s" % side
    assert got["side"] == side
    assert got["bid"] == bid and got["ask"] == ask
    assert got["own_px"] == own


@pytest.mark.parametrize("side,own", [
    ("UP", "0.48"),      # wire 0.48 < ask 0.51
    ("UP", "0.45"),
    ("DOWN", "0.45"),    # wire 0.55 > bid 0.49
    ("DOWN", "0.40"),
])
def test_a_leg_that_still_rests_is_sent_untouched(tmp_path, side, own):
    ex = _ex(tmp_path, _Mirror(_book()))
    assert ex.presend_cross_refusal("m", _intent(side, own), MAKER) is None


def test_it_agrees_with_the_venues_post_only_rule_exactly(tmp_path):
    """The check must not have its own opinion: it is `leg_crosses`, which
    is the post-only rule, read against a fresher book."""
    ex = _ex(tmp_path, _Mirror(_book()))
    for side in ("UP", "DOWN"):
        for cents in range(30, 71):
            own = D(cents) / D(100)
            expect = TS.leg_crosses(side, own, bid=D("0.49"), ask=D("0.51"))
            got = ex.presend_cross_refusal("m", _intent(side, own), MAKER)
            assert (got is not None) is expect, (side, own)


def test_the_production_rejections_would_have_been_avoided(tmp_path):
    """The three ORDER_STATE_REJECTED legs of 2026-10-25, as book states that
    make each one crossing. Shows the guard fires on the shape that actually
    burned the submissions, not on an invented one."""
    cases = [("DOWN", "0.52", "0.49", "0.51"),
             ("DOWN", "0.55", "0.47", "0.49"),
             ("UP", "0.58", "0.55", "0.57")]
    for side, own, bid, ask in cases:
        ex = _ex(tmp_path, _Mirror(_book(bid, ask)))
        got = ex.presend_cross_refusal("m", _intent(side, own), MAKER)
        assert got is not None, (side, own, bid, ask)
        assert got["reason"].startswith("PRESEND_WOULD_CROSS")


# ------------------------------------------------------- it reads it FRESH

def test_the_book_is_re_read_and_not_the_planning_snapshot(tmp_path):
    """The planning book is exactly the stale input this replaces, so the
    mirror must be snapshotted per call."""
    m = _Mirror(_book())
    ex = _ex(tmp_path, m)
    for _ in range(3):
        ex.presend_cross_refusal("m", _intent("UP", "0.48"), MAKER)
    assert m.calls == ["m", "m", "m"]


def test_a_stale_plan_against_a_moved_book_is_the_whole_point(tmp_path):
    """Planned at 0.40 DOWN on a 0.57/0.58 book -- a resting price. By the
    wire the book is 0.59/0.60, and DOWN at own 0.40 is wire 0.60 <= bid
    0.59... not crossing. Move it one more tick and it is."""
    planned_own = D("0.40")
    ex = _ex(tmp_path, _Mirror(_book("0.57", "0.58")))
    assert ex.presend_cross_refusal(
        "m", _intent("DOWN", planned_own), MAKER) is None
    ex = _ex(tmp_path, _Mirror(_book("0.60", "0.61")))
    got = ex.presend_cross_refusal("m", _intent("DOWN", planned_own), MAKER)
    assert got is not None and got["reason"] == "PRESEND_WOULD_CROSS:DOWN"
    assert got["wire_px"] == "0.60"


# ------------------------------------------------------------- fails OPEN

@pytest.mark.parametrize("mirror", [
    None,                                   # no mirror at all
    _Mirror(None),                          # disconnected
    _Mirror({}),                            # no prices
    _Mirror({"bid": "0.49"}),               # one-sided
    _Mirror({"ask": "0.51"}),
    _Mirror("not-a-mapping"),
    _Mirror(raises=True),                   # feed down
])
def test_a_book_it_cannot_read_lets_the_order_through(tmp_path, mirror):
    """AN EFFICIENCY GUARD, NOT A SAFETY GATE. Every risk control has already
    run by the call site, so the only thing this can do is decline an order
    the venue was certain to reject. Failing closed would turn it into a new
    way to stop trading on a mirror hiccup."""
    ex = _ex(tmp_path, mirror)
    assert ex.presend_cross_refusal("m", _intent("UP", "0.60"), MAKER) is None


def test_an_unreadable_price_or_side_lets_the_order_through(tmp_path):
    ex = _ex(tmp_path, _Mirror(_book()))
    assert ex.presend_cross_refusal(
        "m", dict(_intent("UP", "0.60"), price=None), MAKER) is None
    assert ex.presend_cross_refusal(
        "m", dict(_intent("UP", "0.60"), side="SIDEWAYS"), MAKER) is None
    assert ex.presend_cross_refusal(
        "m", dict(_intent("UP", "0.60"), side=""), MAKER) is None


def test_a_taker_order_is_not_checked_at_all(tmp_path):
    """post_only is what the venue rejects; a crossing taker is the point."""
    m = _Mirror(_book())
    ex = _ex(tmp_path, m)
    for mode in ("TAKER", "taker", "", None):
        assert ex.presend_cross_refusal(
            "m", _intent("UP", "0.60"), {"mode": mode}) is None
    assert m.calls == [], "a taker order should not even read the book"


# --------------------------------------------------------- it is measurable

def test_it_counts_what_it_avoided_per_side_and_in_total(tmp_path):
    ex = _ex(tmp_path, _Mirror(_book()))
    assert getattr(ex, "presend_cross_skips", None) is None
    ex.presend_cross_refusal("m", _intent("UP", "0.60"), MAKER)
    ex.presend_cross_refusal("m", _intent("UP", "0.60"), MAKER)
    got = ex.presend_cross_refusal("m", _intent("DOWN", "0.60"), MAKER)
    assert ex.presend_cross_skips == {"UP": 2, "DOWN": 1, "total": 3}
    assert got["skips"] == {"UP": 2, "DOWN": 1, "total": 3}


def test_a_skip_is_not_counted_when_nothing_was_avoided(tmp_path):
    ex = _ex(tmp_path, _Mirror(_book()))
    ex.presend_cross_refusal("m", _intent("UP", "0.45"), MAKER)
    assert getattr(ex, "presend_cross_skips", None) is None


# ------------------------------------------- it changes nothing it must not

def test_it_never_alters_the_intent(tmp_path):
    """It either lets the order through untouched or does not send it. No
    price, size, mode, depth, TTL or cutoff is changed."""
    ex = _ex(tmp_path, _Mirror(_book()))
    for own in ("0.45", "0.60"):
        intent = _intent("UP", own)
        before = dict(intent)
        ex.presend_cross_refusal("m", intent, MAKER)
        assert intent == before


def test_it_holds_no_transport_and_cannot_submit_or_cancel():
    src = textwrap.dedent(
        inspect.getsource(LX.FaithfulExecutor.presend_cross_refusal))
    tree = ast.parse(src)
    calls = {ast.unparse(n.func) for n in ast.walk(tree)
             if isinstance(n, ast.Call)}
    for forbidden in ("self.transport", "submit", "cancel", "record_submit",
                      "claim_submission", "claim_accumulation_leg"):
        assert not any(forbidden in c for c in calls), forbidden


def test_it_runs_BEFORE_the_submission_claim_is_consumed(tmp_path):
    """THE WHOLE POINT. A claim spent on a certain rejection is never
    returned, so the check has to precede it. Asserted on the source order
    in `handle`, because the saving is structural and not incidental."""
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    at_check = src.index("presend_cross_refusal")
    for claim in ("claim_submission(", "claim_accumulation_leg("):
        assert at_check < src.index(claim), claim
    # and after the gates it must never be able to overrule
    for gate in ("RESIDUAL_ABOVE_CAP", "VALIDATION_GATES",
                 "MAKER_ONLY_NO_TAKER"):
        assert src.index(gate) < at_check, gate


def test_the_refusal_is_in_the_executor_inventory():
    """A refusal added to `handle` must appear in the declared surface, or
    `cleared_here` quietly widens its gap with reality."""
    from src import l5_intent_gate as IG
    assert "PRESEND_CROSS:PRESEND_WOULD_CROSS" in \
        IG.EXECUTOR_GATES_NOT_EVALUATED_HERE


def test_the_pair_ceiling_and_the_limits_are_untouched(monkeypatch):
    """This change may not move any approved number."""
    from src import l5_campaign_baseline as CBL
    from src import l5_campaign_approval as CA
    monkeypatch.delenv("L5_TS_MAX_PAIR_COST", raising=False)
    assert TS.max_pair_cost() == D("0.96")
    assert CBL.MAX_ADDITIONAL_LOSS_USD == D("5.00")
    assert CA.MAX_DAILY_LOSS_USD == D("5")
    assert TS.entry_cutoff_s() == 600.0
    assert TS.order_ttl_s() == 120.0
    assert TS.opening_leg_max_contracts() <= D("3")
