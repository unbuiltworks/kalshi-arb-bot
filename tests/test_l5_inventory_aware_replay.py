"""Clone-state replay: baseline reconciliation and policy semantics."""

from __future__ import annotations

import pytest

from src.polymarket_l5.fill_toxicity import RECENT_START_TS
from src.polymarket_l5.inventory_aware_replay import (
    CloneState,
    POLICIES,
    replay,
)

W = "0xw"
T0 = RECENT_START_TS + 100


def fill(ts, outcome, price, shares, tx):
    return {
        "window_id": W, "timestamp": str(ts), "outcome": outcome,
        "price": str(price), "shares": str(shares), "dollars": str(price * shares),
        "side": "BUY", "transaction_hash": tx, "condition_id": W, "token_id": "t",
    }


def fp(row):
    from src.polymarket_l5.inventory_aware_replay import _fingerprint_of_row
    return _fingerprint_of_row(row)


def test_baseline_reproduces_scaled_settlement_pnl():
    fills = {W: [fill(T0 + 1, "Up", 0.4, 100, "0x1"), fill(T0 + 2, "Down", 0.5, 100, "0x2")]}
    res = replay(fills, {W: "Up"}, set(), POLICIES["baseline_accept_all"], scale=0.1)
    # 10% clone: up 10sh cost $4 -> +$6; down 10sh cost $5 -> -$5; net +$1
    assert res["all"]["trading_pnl"] == pytest.approx(1.0)
    assert res["all"]["turnover_dollars"] == pytest.approx(9.0)


def test_clone_state_not_l5_state_drives_the_filter():
    # L5's book would be Up-heavy after fill 1; the clone REJECTS fill 1
    # (extends residual at price < floor is fine here: price 0.4 >= 0.30, but
    # empty book -> imbalance 0, not < -0.1, and residual grows) so fill 2
    # (Down) is then ALSO evaluated against a still-empty clone book.
    fills = {W: [
        fill(T0 + 1, "Up", 0.40, 100, "0x1"),
        fill(T0 + 2, "Down", 0.55, 100, "0x2"),
    ]}
    res = replay(fills, {W: "Up"}, set(), POLICIES["A_clone_inventory_aware"], scale=0.1)
    # Both maker fills extend residual of an empty/balanced clone book -> both rejected.
    assert res["all"]["maker_n"] == 0
    assert res["all"]["rejected_n"] == 2


def test_taker_fills_pass_through_and_seed_clone_inventory():
    f1 = fill(T0 + 1, "Up", 0.40, 100, "0x1")     # taker: passes through
    f2 = fill(T0 + 2, "Down", 0.55, 100, "0x2")   # maker: now reduces clone residual
    res = replay({W: [f1, f2]}, {W: "Up"}, {fp(f1)}, POLICIES["A_clone_inventory_aware"], scale=0.1)
    assert res["all"]["taker_n"] == 1
    assert res["all"]["maker_n"] == 1
    assert res["all"]["rejected_n"] == 0


def test_price_floor_rejects_cheap_maker_fills():
    f1 = fill(T0 + 1, "Up", 0.40, 100, "0x1")
    f2 = fill(T0 + 2, "Down", 0.25, 100, "0x2")   # would reduce residual but < 0.30
    res = replay({W: [f1, f2]}, {W: "Up"}, {fp(f1)}, POLICIES["B_A_and_price_floor_0.30"], scale=0.1)
    assert res["all"]["maker_n"] == 0
    assert res["all"]["rejected_n"] == 1


def test_tape_streak_counts_rejected_fills_too():
    # Three same-side maker fills are all rejected by the inventory rule, but the
    # TAPE streak still builds; a fourth fill on the other side that would
    # otherwise pass policy C must still pass (its own streak is 0).
    fs = [fill(T0 + i, "Up", 0.40, 100, f"0x{i}") for i in range(1, 5)]
    f_down = fill(T0 + 9, "Down", 0.55, 100, "0xd")
    taker = {fp(fs[0])}
    res = replay({W: fs + [f_down]}, {W: "Up"}, taker, POLICIES["C_B_and_tape_streak_lt_3"], scale=0.1)
    # fill 1 taker accepted; fills 2-4 maker extend residual -> rejected;
    # down fill reduces residual, price ok, own-side streak 0 -> accepted.
    assert res["all"]["taker_n"] == 1
    assert res["all"]["maker_n"] == 1
    assert res["all"]["rejected_n"] == 3


def test_clone_state_helpers():
    s = CloneState()
    s.apply("Up", 10)
    assert s.imbalance_toward("Up") == 1.0
    assert s.imbalance_toward("Down") == -1.0
    assert s.reduces_residual("Down", 5) is True
    assert s.reduces_residual("Up", 5) is False
