"""A future-dated book must be refused at EVERY site that dates a book.

432ff7c3 added `age < 0` to `completion_decision` and e7177bb5 to
`complement_order`. Five further sites compared only the upper bound, so a
negative age -- a snapshot dated AHEAD of us -- passed as the freshest
possible book. Four of those are on the money path, including
`completion_requote`, which is enabled in the deployed configuration.

These tests are the completeness check: they walk every entry point that
takes a book age and assert the negative case is refused. No network.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_queue_measurement as QM
from src import l5_two_sided_probe as TS

NEG = D("-0.01")
MARKET = "cpc-btc-updown-15m-2026-11-06-0215z"


@pytest.fixture(autouse=True)
def _authorize_market(monkeypatch):
    """`build_orders` gates on the authorized market BEFORE the book age.

    Without this the call returns NOT_THE_AUTHORIZED_MARKET and never
    reaches the guard under test -- which is correct behaviour and the
    reason the fixture exists rather than the assertion being weakened.
    """
    # `active()` needs BOTH an experiment id and a matching market; there
    # is deliberately no partial arming.
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "test-negative-book-age")


def test_the_guard_is_present_at_every_upper_bound_comparison():
    """Structural: no site may compare only the upper bound.

    If a new site is added that dates a book, this fails until it carries
    the negative guard too.
    """
    import inspect
    for mod in (TS, QM):
        src = inspect.getsource(mod)
        for line in src.splitlines():
            t = line.strip()
            if "age > D(str(" in t or "age > _d(" in t:
                assert "age < 0" in t, t
    src = inspect.getsource(BC)
    for line in src.splitlines():
        t = line.strip()
        if "age > D(str(TS.MAX_BOOK_AGE_S))" in t:
            assert "age < 0" in t, t


# ------------------------------------------------- the two the owner fixed
def test_completion_decision_refuses_a_future_dated_book():
    r = TS.completion_decision(
        up_qty=D("1"), dn_qty=D("0"), up_cost_px=D("0.55"), dn_cost_px=D("0"),
        taker_price=D("0.35"), available_depth=D("1"), tick=D("0.01"),
        min_qty=D("1"), cash_headroom=D("1"), worst_case_ceiling=D("1"),
        book_age_s=NEG, gap_suspected=False)
    assert r["ok"] is False and r["reason"] == "BOOK_STALE"


def test_complement_order_refuses_a_future_dated_book():
    r = TS.complement_order(
        side=TS.DOWN, qty=D("1"), cost_px=D("0.55"), bid=D("0.60"),
        ask=D("0.62"), tick=D("0.01"), min_qty=D("1"), depth_ticks=0,
        book_age_s=NEG, held_side=TS.UP)
    assert r["ok"] is False and r["reason"] == "BOOK_STALE"


# --------------------------------------------- the five extended here
def _book(age):
    return {"bid": D("0.40"), "ask": D("0.42"), "stream_age_s": age,
            "gap_suspected": False}


def _build_orders(age):
    return TS.build_orders(
        market=MARKET,
        bid=D("0.44"), ask=D("0.46"), bid_depth=D("50"), ask_depth=D("50"),
        tick=D("0.01"), min_qty=D("1"), depth_ticks=0,
        cash_envelope=D("12"), max_worst_case_loss=D("1.50"),
        book_age_s=age)


def _requote(age):
    return TS.completion_requote(
        side=TS.DOWN, resting_own_px=D("0.40"), resting_qty=D("1"),
        bid=D("0.44"), ask=D("0.46"), tick=D("0.01"), min_qty=D("1"),
        held_qty=D("1"), held_cost_usd=D("0.55"),
        residual_budget_usd=D("1.50"),
        now_s=300.0, window_start_s=0.0, window_end_s=900.0,
        book_age_s=age)


def _qm(age):
    return QM.build_order(
        market=MARKET, bid=D("0.44"), ask=D("0.46"), bid_depth=D("50"),
        tick=D("0.01"), min_qty=D("1"), book_age_s=age)


def _decide(age):
    return BC.decide(
        market=MARKET, held_side="UP",
        held_qty=D("1"), held_cost_usd=D("0.55"), book=_book(age),
        now_t=300.0, window_start=0.0, residual_budget_usd=D("1.50"))


def _cooldown(age):
    return BC.cooldown_observation(
        held_side="UP", held_qty=D("1"), held_cost_usd=D("0.55"),
        deficient_qty=D("1"), book=_book(age), tick=D("0.01"),
        depth_ticks=0, now_t=300.0, last_decide_t=0.0)


def test_build_orders_refuses_a_future_dated_book():
    r = _build_orders(NEG)
    assert r["ok"] is False and r["reason"] == "BOOK_STALE"


def test_completion_requote_refuses_a_future_dated_book():
    """The deployed configuration has completion_requote_enabled true.

    This path answers `act`, not `ok`.
    """
    r = _requote(NEG)
    assert r["act"] is False and r["reason"] == "BOOK_STALE"


def test_queue_measurement_build_order_refuses_a_future_dated_book():
    r = _qm(NEG)
    assert r["ok"] is False and r["reason"] == "BOOK_STALE"


def test_book_completion_decide_refuses_a_future_dated_stream_age():
    r = _decide(NEG)
    assert r.get("ok") is False
    assert r.get("reason") == "STALE_BOOK"


def test_cooldown_observation_does_not_report_a_future_book_as_executable():
    """`fresh` reaches the caller as evidence.executable_evidence/basis.

    Left unguarded, a future-dated book reported executable_evidence true
    with basis AUTHENTICATED_WS -- telemetry a later audit reads as proof
    the price was executable.
    """
    ev = _cooldown(NEG)["evidence"]
    assert ev["executable_evidence"] is False
    assert ev["basis"] != "AUTHENTICATED_WS"


# ----------------------------------------- the positive cases still pass
def test_a_fresh_book_is_not_refused_for_staleness():
    """Not asserting ok=True -- other gates may decline on these fixtures.

    The claim is narrower and is the one that matters: a FRESH book is not
    refused for being stale, so the new guard has not broken the accept path.
    """
    for r in (_build_orders(D("0.5")), _requote(D("0.5")), _qm(D("0.5"))):
        assert r.get("reason") != "BOOK_STALE", r
    assert _decide(D("0.5")).get("reason") != "STALE_BOOK"
    assert _cooldown(D("0.5"))["evidence"]["executable_evidence"] is True


def test_zero_age_is_fresh_not_refused():
    """Exactly 0 is a book dated now: the freshest legitimate case."""
    for r in (_build_orders(D("0")), _requote(D("0")), _qm(D("0"))):
        assert r.get("reason") != "BOOK_STALE", r
    assert _decide(D("0")).get("reason") != "STALE_BOOK"
    assert _cooldown(D("0"))["evidence"]["executable_evidence"] is True
