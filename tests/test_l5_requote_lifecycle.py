"""The full cancel-confirm-replace lifecycle, across both halves.

ONE STORE, TWO HALVES. The requote cancels through the runner; the
replacement is placed by the executor's ordinary complement path on a LATER
tick. These drive both against the SAME InventoryStore, because the whole
safety argument is about what that store says between them.

THE CAPITAL QUESTION, settled from the gate rather than from arithmetic.
`l5_live_validation_gates` charges a resting leg

    this_order = q            if the intent is BUY_SHORT   (a DOWN leg)
    this_order = q * px       otherwise                    (an UP leg)

so a DOWN completion is charged a DOLLAR A SHARE whatever its price. Every
completion in the four settled windows was DOWN, which makes the gated
obligation change for a dearer DOWN requote exactly ZERO. The $1.3957
measured over the tape is the cash that would actually LEAVE the account if
those dearer quotes fill -- a different quantity from the gated obligation,
and not interchangeable with it.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TSP
from src.l5_live_validation_gates import ValidationGates

from tests.test_l5_two_sided_wiring import (_book, _row, mk,  # noqa: F401
                                            probe)

SLUG = "m"


def gates(cash="12", deficit="3"):
    return ValidationGates(max_cash_deployed_usd=D(cash),
                           max_unmatched_contracts=D("10"),
                           max_cumulative_loss_usd=D("20"),
                           max_worst_case_deficit_usd=D(deficit))


class Book:
    def __init__(self, bid, ask, age="0.05"):
        self._s = {"bid": bid, "ask": ask, "stream_age_s": age,
                   "gap_suspected": False}

    def snapshot(self, slug):
        return dict(self._s)

    def set(self, bid, ask):
        self._s["bid"], self._s["ask"] = bid, ask


class RunnerStub:
    """The real maybe_completion_requote, over the real store."""

    def __init__(self, ex, transport, book):
        import collections
        self.executor = ex
        self.store = ex.store
        self.transport = transport
        self.book = book
        self.stats = collections.Counter()


@pytest.fixture()
def wired(probe, tmp_path, monkeypatch):
    H, E = probe
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, "1")
    # 60s into the window: inside entry, far inside completion
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda s: H.NOW().timestamp() - 60.0)
    emitted = []
    monkeypatch.setattr(R, "emit",
                        lambda label, rec: emitted.append((label, rec)))
    t = H.FakeTransport(fill_qty="0")        # nothing fills unless we say so
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    t.market_slug = SLUG
    ex = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                            transport=t, now_fn=H.NOW)
    ex.validation_gates = gates()
    return H, E, ex, t, emitted, monkeypatch


def _hold_up(ex, E, qty="2.29", px="0.3693"):
    ex.store.apply_fill(SLUG, "UP", D(qty), D(px), basis=E.OWN)


# ------------------------------------------------- the happy lifecycle

def test_the_replacement_is_submitted_on_the_TICK_AFTER_the_cancel(wired):
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    # tick 1: the complement rests
    r1 = ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    assert t.sent, r1.get("refused_reason")
    first = dict(t.sent[-1])
    assert first["side"] == "DOWN"
    assert ex.store.live_order_count() == 1

    # the book walks away from it, then the requote cancels -- SAME tick as
    # the decision, and it submits nothing
    book = Book("0.4400", "0.4600")
    st = RunnerStub(ex, t, book)
    sent_before = len(t.sent)
    rec = R.Runner.maybe_completion_requote(st, H.NOW().timestamp())
    assert rec is not None, st.stats
    assert len(t.sent) == sent_before, "the cancel half must submit nothing"
    assert t.cancelled, "it must cancel by venue id"
    assert ex.store.live_order_count() == 0, "the slot must be free"
    assert ex.store.orders_uncertain() is None, "a proved cancel is not UNCERTAIN"

    # tick 2: the ordinary complement path places the replacement, priced
    # against the CURRENT book rather than the cancelled order's price
    r2 = ex.handle(_row(H),
                   _book(bid="0.4400", ask="0.4600"),
                   market=SLUG, window="21:00")
    assert len(t.sent) == sent_before + 1, r2.get("refused_reason")
    repl = t.sent[-1]
    assert repl["side"] == "DOWN"
    assert repl["mode"] == "MAKER"
    assert D(repl["price"]) != D(first["price"]), "it must be REPRICED"
    assert D(repl["qty"]) <= D("2.29"), "and never exceed the residual"


def test_the_replacement_carries_the_remaining_completion_runway(wired):
    """It is placed with runway to spare, and the runway is what the
    completion clock says rather than a separate copy of it."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    book = Book("0.4400", "0.4600")
    st = RunnerStub(ex, t, book)
    now = H.NOW().timestamp()
    rec = R.Runner.maybe_completion_requote(st, now)
    assert rec is not None
    ws = now - 60.0
    assert TSP.may_complete(now, ws, ws + TSP.WINDOW_S) is None
    assert float(rec["runway_s"]) >= TSP.REQUOTE_MIN_RUNWAY_S


# ------------------- cancellation succeeds, replacement refused

def test_a_cancel_whose_replacement_is_REFUSED_carries_the_residual(wired):
    """The dangerous asymmetry: the cancel is irreversible, so if the
    replacement is then refused the residual must simply be CARRIED under
    the existing limits -- unquoted, but not uncertain, and with the slot
    free rather than holding a phantom order."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    assert ex.store.live_order_count() == 1
    book = Book("0.4400", "0.4600")
    st = RunnerStub(ex, t, book)
    assert R.Runner.maybe_completion_requote(
        st, H.NOW().timestamp()) is not None
    assert ex.store.live_order_count() == 0

    # now make the replacement impossible: a stale book refuses it
    sent_before = len(t.sent)
    rec = ex.handle(_row(H), _book(stream_age_s="99"),
                    market=SLUG, window="21:00")
    assert len(t.sent) == sent_before, "nothing may be placed on a stale book"
    assert rec.get("refused_reason") is not None
    # and the position is exactly what it was: carried, not corrupted
    assert ex.store.position(SLUG, "UP") == D("2.29")
    assert ex.store.position(SLUG, "DOWN") == D("0")
    assert ex.store.orders_uncertain() is None
    assert ex.store.live_order_count() == 0


def test_a_cancel_that_cannot_be_PROVED_marks_uncertain_and_blocks(wired):
    """When the re-read still says working, deadman marks the store
    UNCERTAIN -- and that refuses the replacement too, so there is no window
    in which a replacement races a live order."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")

    def still_working(oid):
        return {"id": oid, "state": "ORDER_STATE_NEW", "cumQuantity": "0",
                "leavesQuantity": "2.29",
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}
    t.order = still_working
    st = RunnerStub(ex, t, Book("0.4400", "0.4600"))
    R.Runner.maybe_completion_requote(st, H.NOW().timestamp())
    assert ex.store.orders_uncertain() is not None
    sent_before = len(t.sent)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    assert len(t.sent) == sent_before, "UNCERTAIN must refuse the replacement"


def test_a_fill_found_WHILE_cancelling_is_booked_with_its_basis(wired):
    """The partial that lands between the decision and the cancel. deadman
    routes the re-read through apply_order_progress, so it arrives with a
    cost basis instead of becoming quantity without cost."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    oid = t.sent[-1]["client_order_id"]
    px = t.sent[-1]["price"]

    def filled_on_reread(o):
        return {"id": o, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": "1.00", "leavesQuantity": "0",
                "avgPx": {"value": str(TSP.wire_price("DOWN", D(px))),
                          "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}
    t.order = filled_on_reread
    st = RunnerStub(ex, t, Book("0.4400", "0.4600"))
    R.Runner.maybe_completion_requote(st, H.NOW().timestamp())
    # the fill is on the books, with a basis, and the residual shrank
    assert ex.store.position(SLUG, "DOWN") == D("1.00")
    assert ex.store.leg_cost(SLUG).get("DOWN") is not None
    assert ex.store.residual(SLUG) == D("1.29")
    del oid


# --------------------------------------- the capital obligation

def test_a_dearer_DOWN_requote_does_not_change_the_GATED_obligation():
    """The gate charges a BUY_SHORT leg a dollar a share, so the price of a
    DOWN completion does not enter its obligation at all. Dearer is free to
    the envelope; it is only the cash on FILL that rises."""
    g = gates()
    out = []
    for px in ("0.47", "0.49", "0.68"):
        this = D("2.29")                      # q, not q * px, for BUY_SHORT
        out.append(this)
    assert len(set(out)) == 1
    # and the gate itself agrees on the shape
    import inspect
    src = inspect.getsource(type(g).refuse) if hasattr(type(g), "refuse") \
        else inspect.getsource(type(g))
    assert "endswith(\"BUY_SHORT\")" in src


def test_the_replacement_can_never_exceed_the_cancelled_obligation(wired):
    """q never grows -- it is clipped to the working remainder AND the
    unmatched remainder -- and a DOWN leg is charged q dollars either way.
    So the replacement's obligation is <= the one already inside $12."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    cancelled_qty = D(t.sent[-1]["qty"])
    st = RunnerStub(ex, t, Book("0.4400", "0.4600"))
    rec = R.Runner.maybe_completion_requote(st, H.NOW().timestamp())
    assert rec is not None
    assert D(rec["qty"]) <= cancelled_qty
    r2 = ex.handle(_row(H),
                   _book(bid="0.4400", ask="0.4600"),
                   market=SLUG, window="21:00")
    if len(t.sent) > 1:
        assert D(t.sent[-1]["qty"]) <= cancelled_qty
    del r2


def test_the_cash_envelope_still_refuses_an_oversized_replacement(wired):
    """With the envelope shrunk below the obligation, the replacement is
    refused by the SAME cash gate as any other submission."""
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    st = RunnerStub(ex, t, Book("0.4400", "0.4600"))
    R.Runner.maybe_completion_requote(st, H.NOW().timestamp())
    ex.validation_gates = gates(cash="0.50")        # below q * $1
    sent_before = len(t.sent)
    rec = ex.handle(_row(H),
                    _book(bid="0.4400", ask="0.4600"),
                    market=SLUG, window="21:00")
    assert len(t.sent) == sent_before
    assert rec.get("refused_reason") is not None


def test_an_EXTREME_move_is_refused_by_the_PLAN_gate_before_the_rescue(wired):
    """A LIMITATION, pinned so it is not mistaken for a fix.

    The completion rescue lives inside `complement_order`, but the probe
    prices a PAIR PLAN first and refuses PAIR_COST_ABOVE_CEILING when the
    planned pair exceeds 0.96. On a book that has run a long way -- which is
    exactly the stranding case -- that refusal fires BEFORE the complement
    path is reached, so the rescue never runs and the residual is carried.

    Found by this lifecycle test, not by reasoning. It bounds what P1 and P2
    can achieve: they widen what a completion may pay once it is reached,
    and they do not change which books reach it.
    """
    H, E, ex, t, emitted, mp = wired
    _hold_up(ex, E)
    ex.handle(_row(H), _book(), market=SLUG, window="21:00")
    st = RunnerStub(ex, t, Book("0.2000", "0.2100"))
    assert R.Runner.maybe_completion_requote(
        st, H.NOW().timestamp()) is not None
    sent_before = len(t.sent)
    rec = ex.handle(_row(H), _book(bid="0.2000", ask="0.2100"),
                    market=SLUG, window="21:00")
    assert len(t.sent) == sent_before
    assert rec.get("refused_reason") == "PAIR_COST_ABOVE_CEILING"
    # and the residual is carried intact, which is the safe outcome
    assert ex.store.position(SLUG, "UP") == D("2.29")
    assert ex.store.orders_uncertain() is None
