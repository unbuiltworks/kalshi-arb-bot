"""The 0145z/0200z cancel-verification incident, reproduced and fixed.

THE INCIDENT. Ten requotes produced not one clean cancel:

    SEVEN  came back `verify_error: HTTPError`
    THREE  came back `still_open` with ORDER_STATE_NEW and the full leaves_qty

WHERE IT CAME FROM. Not the venue's decision. `deadman` sent the cancel once
and then re-read the order EXACTLY ONCE. `GET /v1/order/{id}` is an
idempotent read, so a single transient HTTP error on it left the order's fate
permanently unknown, marked the whole book uncertain, and that uncertainty is
what blocks every later submission -- `SafetyLimits.refuse()` returns
ORDER_STATE_UNCERTAIN before any other check. Seven of the ten were lost to a
missing retry on a free read.

The three `still_open` rows are the other half: a venue's order query can lag
its own cancel, so an immediate re-read can report ORDER_STATE_NEW on an
order that is already gone. The reference quoter allows 30s of indexing grace
for exactly this.

THE FIX. `_verify_cancelled` retries the READ, bounded at
VERIFY_ATTEMPTS=3 with VERIFY_BACKOFF_S=(0.20, 0.40). The CANCEL is still
sent exactly once -- re-issuing it would not be idempotent, because by then a
replacement may hold the same slot and a second cancel could retire the wrong
order.

WHAT CANNOT HAPPEN. Retrying a read can only turn a FALSE still_open into a
proven cancellation. It can never invent a terminal state: the verdict is
always the venue's own last word, and exhausting the attempts leaves the
order `still_open` or `unverified` exactly as before, with the book marked
uncertain. Fail-closed is preserved in every branch.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX

MKT = "cpc-btc-updown-15m-2026-11-02-2215z"
VID = "DFWP527012WE"
COID = "l5f-0145z-requote-1"


class FakeTransport:
    """Scripted venue. `order_script` is consumed one entry per re-read:
    an Exception instance is raised, a dict is returned."""

    market_slug = MKT

    def __init__(self, order_script):
        self.order_script = list(order_script)
        self.cancels = []
        self.reads = 0

    def cancel(self, vid, market_slug=None):
        self.cancels.append((vid, market_slug))
        return {"ok": True}

    def order(self, vid):
        self.reads += 1
        if not self.order_script:
            raise AssertionError("read past the end of the script")
        nxt = self.order_script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _resting(state="ORDER_STATE_NEW", cum="0", leaves="1.92"):
    """The venue's order row as order_progress() consumes it: camelCase,
    unwrapped, leavesQuantity deciding."""
    return {"orderId": VID, "state": state, "cumQuantity": cum,
            "leavesQuantity": leaves, "avgPx": "0.3900", "quantity": "1.92"}


def _terminal(state="ORDER_STATE_CANCELED", cum="0", leaves="0"):
    return {"orderId": VID, "state": state, "cumQuantity": cum,
            "leavesQuantity": leaves, "avgPx": "0.3900", "quantity": "1.92"}


def store_with_live_order(tmp_path, qty="1.92", px="0.39"):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state.setdefault("live_orders", {})[COID] = {
        "venue_order_id": VID, "market": MKT, "side": "DOWN",
        "qty": qty, "price": px}
    st.save()
    return st


def run(st, tp, attempts=EX.VERIFY_ATTEMPTS):
    return EX.deadman(st, tp, reason="test", verify_attempts=attempts,
                      verify_sleeper=lambda _s: None)


# ============================== 1. the seven HTTPError rows, reproduced

def test_a_single_transient_read_error_used_to_strand_the_order(tmp_path):
    """THE DEFECT, at attempts=1: one HTTPError and the fate is unknown."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("HTTPError")])
    out = run(st, tp, attempts=1)
    assert len(out["unverified"]) == 1
    assert out["unverified"][0]["verify_error"] == "OSError"
    assert st.orders_uncertain()


def test_the_retry_recovers_a_transient_read_error(tmp_path):
    """THE FIX: the same first error, then the venue answers."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("HTTPError"), _terminal()])
    out = run(st, tp)
    assert out["unverified"] == []
    assert len(out["cancelled"]) == 1
    assert out["cancelled"][0]["verify_attempts"] == 2
    assert out["cancelled"][0]["verify_errors"] == ["OSError"]
    assert not st.orders_uncertain()


def test_it_recovers_on_the_last_permitted_attempt(tmp_path):
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("e1"), OSError("e2"), _terminal()])
    out = run(st, tp)
    assert len(out["cancelled"]) == 1
    assert out["cancelled"][0]["verify_attempts"] == 3


def test_exhausting_every_attempt_still_fails_closed(tmp_path):
    """Fail-closed is NOT relaxed by the retry."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("e1"), OSError("e2"), OSError("e3")])
    out = run(st, tp)
    assert out["cancelled"] == []
    assert len(out["unverified"]) == 1
    assert out["unverified"][0]["verify_attempts"] == 3
    assert out["unverified"][0]["verify_errors"] == ["e1" and "OSError"] * 3
    assert st.orders_uncertain()


def test_the_read_is_retried_but_the_CANCEL_IS_SENT_EXACTLY_ONCE(tmp_path):
    """The safety crux. A second cancel could retire a replacement that now
    holds the same slot, so the cancel is never re-issued."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("e1"), OSError("e2"), _terminal()])
    run(st, tp)
    assert len(tp.cancels) == 1
    assert tp.cancels[0] == (VID, MKT)
    assert tp.reads == 3


# ============================== 2. the three still_open rows, reproduced

def test_a_lagging_order_query_used_to_read_as_still_open(tmp_path):
    """THE DEFECT, at attempts=1: ORDER_STATE_NEW with the full leaves_qty."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_resting()])
    out = run(st, tp, attempts=1)
    assert len(out["still_open"]) == 1
    assert out["still_open"][0]["venue_state"] == "ORDER_STATE_NEW"
    assert D(out["still_open"][0]["leaves_qty"]) == D("1.92")
    assert st.orders_uncertain()


def test_the_retry_resolves_read_after_write_lag(tmp_path):
    """THE FIX: lagging query first, then the venue reports it gone."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_resting(), _terminal()])
    out = run(st, tp)
    assert out["still_open"] == []
    assert len(out["cancelled"]) == 1
    assert out["cancelled"][0]["verify_attempts"] == 2
    assert not st.orders_uncertain()


def test_a_genuinely_live_order_is_still_reported_still_open(tmp_path):
    """Retrying must not invent a terminal state. Resting on every attempt
    stays still_open and keeps the book uncertain."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_resting(), _resting(), _resting()])
    out = run(st, tp)
    assert out["cancelled"] == []
    assert len(out["still_open"]) == 1
    assert out["still_open"][0]["verify_attempts"] == 3
    assert st.orders_uncertain()


# ============================== 3. partial fills during cancellation

def test_a_partial_filled_during_the_cancel_is_booked_with_its_basis(tmp_path):
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_terminal(state="ORDER_STATE_CANCELED",
                                  cum="0.92", leaves="0")])
    out = run(st, tp)
    assert len(out["cancelled"]) == 1
    assert len(out["filled"]) == 1
    assert D(out["filled"][0]["qty"]) == D("0.92")
    assert st.position(MKT, "DOWN") == D("0.92")
    assert st.leg_cost(MKT)["DOWN"] > 0          # a cost basis, not a bare qty


def test_a_partial_discovered_only_on_a_RETRIED_read_is_booked_once(tmp_path):
    """The fill must be booked exactly once even though the read repeated."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("transient"),
                        _terminal(cum="0.92", leaves="0")])
    out = run(st, tp)
    assert len(out["filled"]) == 1
    assert st.position(MKT, "DOWN") == D("0.92")


def test_a_partial_on_a_still_open_order_is_booked_and_stays_uncertain(tmp_path):
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_resting(cum="0.50", leaves="1.42")] * 3)
    out = run(st, tp)
    assert len(out["still_open"]) == 1
    assert st.position(MKT, "DOWN") == D("0.50")
    assert st.orders_uncertain()


# ============================== 4. delayed acks and unknown state

def test_a_raising_cancel_whose_reread_is_terminal_counts_as_cancelled(tmp_path):
    """A cancel can raise on an order the venue has already retired."""
    st = store_with_live_order(tmp_path)

    class Raising(FakeTransport):
        def cancel(self, vid, market_slug=None):
            super().cancel(vid, market_slug)
            raise OSError("404")

    tp = Raising([_terminal()])
    out = run(st, tp)
    assert len(out["cancelled"]) == 1
    assert out["cancelled"][0]["cancel_error"] == "OSError"
    assert out["errors"][0]["resolved_by_reread"] is True
    assert not st.orders_uncertain()


def test_an_order_with_no_venue_id_is_uncertainty_not_a_no_op(tmp_path):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state.setdefault("live_orders", {})["no-id"] = {
        "venue_order_id": "", "market": MKT, "side": "UP"}
    st.save()
    tp = FakeTransport([])
    out = run(st, tp)
    assert len(out["no_venue_id"]) == 1
    assert tp.cancels == []
    assert st.orders_uncertain()


# ============================== 5. no replacement while uncertain

def test_an_uncertain_book_blocks_the_next_submission(tmp_path):
    """The invariant the whole retry exists to protect: a replacement is
    never sent while the old order's state is unknown."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("e1"), OSError("e2"), OSError("e3")])
    run(st, tp)
    assert st.orders_uncertain()
    from src import l5_two_sided_probe as TS
    r = TS.completion_requote(
        side="UP", resting_own_px="0.55", resting_qty="1.92",
        bid="0.50", ask="0.51", tick="0.01", min_qty="0.01",
        held_qty="1.92", held_cost_usd="0.7488", residual_budget_usd="1.50",
        now_s=100.0, window_start_s=0.0, window_end_s=900.0,
        book_age_s=0.1, orders_uncertain=st.orders_uncertain())
    assert r["act"] is False
    assert r["reason"] == "ORDERS_UNCERTAIN"


def test_a_recovered_cancel_leaves_the_book_clean_for_a_replacement(tmp_path):
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([OSError("transient"), _terminal()])
    run(st, tp)
    assert not st.orders_uncertain()


# ============================== 6. restart recovery and duplicates

def test_the_retry_policy_is_bounded_and_small(tmp_path):
    """A long stall on the deadman path is itself a hazard."""
    assert EX.VERIFY_ATTEMPTS == 3
    assert sum(EX.VERIFY_BACKOFF_S) <= 1.0


def test_a_second_deadman_pass_does_not_re_cancel_a_resolved_order(tmp_path):
    """Duplicate-replacement prevention at the cancel layer: once the venue
    has reported terminal the order leaves live_orders, so a second sweep
    sends nothing."""
    st = store_with_live_order(tmp_path)
    tp = FakeTransport([_terminal()])
    run(st, tp)
    tp2 = FakeTransport([])
    out2 = run(st, tp2)
    assert tp2.cancels == []
    assert out2["cancelled"] == [] and out2["still_open"] == []


def test_restart_recovery_rereads_from_the_durable_store(tmp_path):
    """A restart reopens the same file and finds the same live order, so the
    cancel is attempted against the venue id the store persisted."""
    st = store_with_live_order(tmp_path)
    reopened = EX.InventoryStore(str(tmp_path / "inv.json"))
    assert COID in reopened.live_orders()
    tp = FakeTransport([_terminal()])
    out = run(reopened, tp)
    assert out["cancelled"][0]["venue_order_id"] == VID


def test_scoping_to_one_order_leaves_a_sibling_untouched(tmp_path):
    st = store_with_live_order(tmp_path)
    st.state["live_orders"]["sibling"] = {
        "venue_order_id": "OTHER", "market": MKT, "side": "UP"}
    st.save()
    tp = FakeTransport([_terminal()])
    out = EX.deadman(st, tp, reason="scoped", only=[COID],
                     verify_sleeper=lambda _s: None)
    assert out["scoped_to"] == [COID]
    assert [c[0] for c in tp.cancels] == [VID]
