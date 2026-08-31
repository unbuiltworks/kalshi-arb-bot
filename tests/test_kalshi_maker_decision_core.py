"""The quoting brain: frozen selection, envelope, and risk bounds."""

from __future__ import annotations

import pytest

from src.kalshi_maker.decision_core import (
    Book, Decision, Inventory, Tape, completion_action, decide,
    MAX_SPEND_PER_WINDOW, MAX_WORST_LOSS_PER_WINDOW, TAPE_WINDOW,
)


def warm_tape(direction="Up", n=TAPE_WINDOW):
    tape = Tape()
    for _ in range(n):
        tape.add_print(direction, 10.0)
    return tape


def test_warmup_blocks_quoting():
    d = decide(warm_tape(n=TAPE_WINDOW - 1), Book(0.45, 0.45), Inventory(), 600)
    assert d.cancel_all and d.reason == "TAPE_WARMUP"


def test_quotes_only_the_contra_side_at_or_above_floor():
    d = decide(warm_tape("Up"), Book(0.55, 0.40), Inventory(), 600)
    assert not d.cancel_all
    assert [q.side for q in d.quotes] == ["Down"]      # flow is Up-heavy -> quote Down
    assert d.quotes[0].price == 0.40


def test_price_floor_blocks_cheap_contra_side():
    d = decide(warm_tape("Up"), Book(0.75, 0.22), Inventory(), 600)
    assert d.cancel_all and d.reason == "NO_ELIGIBLE_SIDE"


def test_balanced_tape_quotes_nothing():
    tape = Tape()
    for i in range(TAPE_WINDOW):
        tape.add_print("Up" if i % 2 else "Down", 10.0)
    d = decide(tape, Book(0.50, 0.48), Inventory(), 600)
    assert d.cancel_all


def test_expiry_stop_cancels_everything():
    d = decide(warm_tape("Up"), Book(0.55, 0.40), Inventory(), 89)
    assert d.cancel_all and d.reason == "EXPIRY_STOP"


def test_spend_cap_halts_quoting():
    inv = Inventory()
    inv.apply_fill("Down", 25, 0.40)   # $10 spent
    d = decide(warm_tape("Up"), Book(0.55, 0.40), inv, 600)
    assert d.cancel_all and d.reason == "SPEND_CAP"
    assert inv.spend == pytest.approx(MAX_SPEND_PER_WINDOW)


def test_worst_loss_bound_shrinks_size():
    # Empty book, quoting Down at 0.45 with size 10 would risk $4.50 if Up wins;
    # the $1 worst-loss cap must shrink the quote to 2 contracts (0.90 risk).
    d = decide(warm_tape("Up"), Book(0.55, 0.45), Inventory(), 600, quote_size=10)
    assert not d.cancel_all
    q = d.quotes[0]
    assert q.size == 2
    assert Inventory().worst_loss_after(q.side, q.size, q.price) <= MAX_WORST_LOSS_PER_WINDOW + 1e-9


def test_worst_loss_bound_refuses_stacking_one_sided_exposure():
    inv = Inventory()
    inv.apply_fill("Down", 2, 0.40)   # $0.80 already at risk if Up wins
    d = decide(warm_tape("Up"), Book(0.55, 0.45), inv, 600, quote_size=10)
    # even 1 more Down contract would breach the $1 worst-loss cap -> no quote
    assert d.cancel_all and d.reason == "NO_ELIGIBLE_SIDE"


def test_halt_dominates_everything():
    d = decide(warm_tape("Up"), Book(0.55, 0.40), Inventory(), 600, halted=True)
    assert d.cancel_all and d.reason == "HALTED_BY_WRAPPER"


def test_worst_settlement_loss_math():
    inv = Inventory()
    inv.apply_fill("Up", 10, 0.40)     # $4; if Down wins lose $4
    assert inv.worst_settlement_loss() == pytest.approx(4.0)
    inv.apply_fill("Down", 10, 0.45)   # matched pair cost 8.5 -> pays 10 either way
    assert inv.worst_settlement_loss() == pytest.approx(0.0)


def test_completion_fires_only_late_and_only_if_it_reduces_worst_loss():
    inv = Inventory()
    inv.apply_fill("Up", 5, 0.40)
    book = Book(up_bid=0.55, down_bid=0.40)
    assert completion_action(inv, book, 600) is None            # too early
    act = completion_action(inv, book, 200)
    assert act is not None and act.side == "Down" and act.qty if hasattr(act, "qty") else True
    assert act.side == "Down"
    assert act.price == pytest.approx(0.45)                     # 1 - up_bid
    after = inv.worst_loss_after(act.side, act.size, act.price)
    assert after < inv.worst_settlement_loss()


def test_completion_refuses_to_pay_above_cap():
    inv = Inventory()
    inv.apply_fill("Up", 5, 0.10)
    book = Book(up_bid=0.12, down_bid=0.05)   # missing Down would cost 0.88
    assert completion_action(inv, book, 200) is None


def test_completion_noop_when_balanced():
    inv = Inventory()
    inv.apply_fill("Up", 5, 0.40)
    inv.apply_fill("Down", 5, 0.45)
    assert completion_action(inv, Book(0.55, 0.40), 200) is None
