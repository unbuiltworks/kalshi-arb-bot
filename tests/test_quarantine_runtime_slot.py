"""Quarantine's RUNTIME half: a dormant residual stops occupying the slot.

THE DEFECT. 2026-09-11, deployment e48dcf90 on 383a69631848. With
PMUS_RESIDUAL_QUARANTINE=1 the BOOT gate did exactly what it was taught to
do -- `flat_held_state` went from

    ok=false got ['aec-county-not-ham-2026-09-08']      (quarantine OFF)
    ok=true  got []                                     (quarantine ON)

-- all eleven boot assertions passed, `combined_loss_above_stop` stayed true,
and the campaign reported mode: ARMED. And then it acquired nothing, forever:

    resume_start  selection_skipped: "CAMPAIGN_HOLD:POSITION_HELD
                  (1/1 markets held, worked one session at a time)"
                  funnel: {admitted: 0, selection_skipped_position_held: 1}

Quarantine freed the ARM and nothing else. The scheduler had its own copy of
"who occupies a slot", inline, in three coupled places inside a 900-line
loop, and none of them had heard of dormancy. Arming a campaign that cannot
open a market is indistinguishable from not arming it.

That is the same shape as the 300 s fill band (one rule written twice, the
copies disagreed) and the book reader (ditto). So dormancy now has ONE
definition, `dormant_held`, and the slot arithmetic has ONE definition,
`acquisition_state`, which is a pure function specifically so these tests can
drive it instead of asserting on the text of a loop.

WHAT MUST STAY TRUE, and each has a test that fails if it is removed:
  * quarantine OFF is byte-identical to the pre-quarantine behaviour;
  * a held market stays in `held` -- combined_loss, settlement monitoring and
    the round-robin all read `held` and are untouched;
  * a foreign position still HALTS the campaign, quarantine or not;
  * an open order anywhere still refuses a new first leg, whatever is
    declared dormant;
  * a dormant market is never re-opened as a new first leg.
"""
from __future__ import annotations

import ast
import inspect
import re
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as C
from src import pmus_micro_executor as micro
from src import pmus_residual_quarantine as rq

SLUG = "aec-county-not-ham-2026-09-08"


class _Inv:
    def __init__(self, up="16.67", dn="28.34"):
        self.up, self.dn = D(up), D(dn)


@pytest.fixture
def live(monkeypatch):
    """The live 11:40Z state: one held market, zero open orders, quantities
    reconciling exactly, still settlement-monitored."""
    monkeypatch.setattr(C, "held_markets", lambda t: [SLUG])
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": 0, "foreign": 0})
    monkeypatch.setattr(C, "unsettled_slugs", lambda: [SLUG])
    monkeypatch.setattr(C.base, "held_inventory_from_ledger", lambda s: _Inv())
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": "45.0100"})
    return monkeypatch


# ------------------------------------------------ the arithmetic that broke
def test_the_live_deadlock_frees_the_slot():
    """1 held, 1 dormant, concurrency 1: exactly the 11:40Z state. Before
    this change capacity was False and the campaign never selected."""
    s = C.acquisition_state([SLUG], {SLUG}, 1, False)
    assert s["acquiring"] == []
    assert s["capacity"] is True
    assert s["drain"] is False
    assert s["quarantined"] == [SLUG]


def test_with_nothing_dormant_it_is_the_old_arithmetic():
    """Quarantine OFF means dormant_held returns empty, and then every field
    is what the loop computed before quarantine existed."""
    s = C.acquisition_state([SLUG], (), 1, False)
    assert s["acquiring"] == [SLUG]
    assert s["capacity"] is False, "a worked market still holds the slot"
    assert s["declared_owned"] == [], "concurrency-1 declared nothing before"
    assert s["drain"] is False


def test_a_dormant_market_does_not_buy_a_second_acquisition():
    """The slot is freed ONCE. Holding a dormant residual and a live market
    on a concurrency-1 rung is full: quarantine is not a concurrency dial."""
    s = C.acquisition_state([SLUG, "mkt-b"], {SLUG}, 1, False)
    assert s["acquiring"] == ["mkt-b"]
    assert s["capacity"] is False
    assert s["drain"] is False, "one acquiring market on a 1-rung is not over"


def test_the_second_market_does_not_immediately_trip_the_drain():
    """THE COUPLING. Freeing capacity without fixing the drain count opens
    market B and then declares CAMPAIGN_DRAIN:OVER_CONCURRENCY on the next
    cycle, which stops opening again -- quarantine would buy exactly one
    market and re-deadlock. len(held) is 2 > 1 here; len(acquiring) is not."""
    s = C.acquisition_state([SLUG, "mkt-b"], {SLUG}, 1, False)
    assert len([SLUG, "mkt-b"]) > 1, "the held count IS over the rung"
    assert s["drain"] is False, "but the ACQUIRING count is not"


def test_two_live_markets_on_a_one_rung_still_drain():
    s = C.acquisition_state(["mkt-a", "mkt-b"], (), 1, False)
    assert s["drain"] is True


def test_a_spent_allowance_still_refuses_capacity():
    """Quarantine frees a scheduling slot. It does not restore a spent rung
    allowance, and it does not touch the daily stop."""
    s = C.acquisition_state([SLUG], {SLUG}, 1, True)
    assert s["capacity"] is False


def test_the_declared_set_under_concurrency_one_is_exactly_the_dormant_set():
    """The account-wide preflight is not otherwise altered: what changes is
    that a market established as dormant is DECLARED, through the same
    allow_slugs channel the concurrency>1 rung already used."""
    s = C.acquisition_state([SLUG, "mkt-b"], {SLUG}, 1, False)
    assert s["declared_owned"] == [SLUG]
    assert "mkt-b" not in s["declared_owned"], "a live sibling is not declared"


def test_a_concurrency_rung_still_declares_every_held_market():
    s = C.acquisition_state([SLUG, "mkt-b"], {SLUG}, 2, False)
    assert sorted(s["declared_owned"]) == sorted([SLUG, "mkt-b"])


def test_held_is_never_filtered():
    """combined_loss, settlement monitoring and the round-robin all read
    `held`. acquisition_state must not mutate or shrink it."""
    held = [SLUG, "mkt-b"]
    C.acquisition_state(held, {SLUG}, 1, False)
    assert held == [SLUG, "mkt-b"]


# ------------------------------------------- one definition of "dormant"
def test_blocking_held_is_written_in_terms_of_dormant_held():
    """The boot gate and the scheduler must not each carry their own copy of
    the eligibility rule. That is the defect this project has already paid
    for twice (the 300 s band, the book reader)."""
    src = inspect.getsource(C.blocking_held)
    assert "dormant_held(" in src
    assert "rq.eligibility" not in src, "the rule lives in ONE place"


def test_the_loop_uses_the_shared_helpers_not_its_own_inline_copy():
    loop = inspect.getsource(C.run_campaign)
    assert "dormant_held(held_all, t)" in loop
    assert "acquisition_state(held_all, dormant" in loop
    assert 'capacity = slots["capacity"]' in loop
    assert 'set_concurrent_owned(slots["declared_owned"])' in loop
    assert 'if slots["drain"]:' in loop


def test_the_breach_check_still_reads_every_held_market():
    """BREACH is not the same question as DRAIN. A position the campaign did
    not open must halt everything whether or not something else is dormant,
    so it is asked over held_all -- never over `acquiring`."""
    loop = inspect.getsource(C.run_campaign)
    assert "over_concurrency_action(held_all, filled_markets," in loop
    assert "over_concurrency_action(acquiring" not in loop


def test_a_dormant_market_is_still_excluded_from_new_selection():
    """Quarantine frees the slot for a DIFFERENT market. The dormant market
    itself is continued by the resume path, never opened again as a first
    leg."""
    loop = inspect.getsource(C.run_campaign)
    assert 'float("inf") for s in held_all' in loop


def test_the_round_robin_still_works_the_dormant_market():
    """OWNER, in the quarantine module: "its own state machine may still
    COMPLETE later." The alternation reads held_all, not acquiring."""
    loop = inspect.getsource(C.run_campaign)
    assert "_next_held(held_all)" in loop


def test_acquisition_state_prices_nothing():
    """Quarantine changes WHEN we may acquire, never HOW MUCH we may lose.
    The slot arithmetic must not mention a dollar."""
    tree = ast.parse(inspect.getsource(C.acquisition_state))
    for node in ast.walk(tree):           # strip docstrings: prose is not code
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    src = ast.unparse(ast.fix_missing_locations(tree))
    # whole words: "cap" is a substring of "capacity", which is the one thing
    # this function IS allowed to compute. The first version of this test
    # failed on exactly that and would have been easy to "fix" by deleting
    # the term instead of matching it properly.
    for banned in ("combined_loss", "daily_loss_stop", "cap", "budget",
                   "worst_case", "realized", "stop"):
        assert not re.search(rf"\b{banned}\b", src), banned


# -------------------------------------------------- a foreign position halts
def test_a_market_that_is_not_ours_can_never_be_dormant(live, monkeypatch):
    """TWO independent reasons, both required by rq.eligibility: it has no
    ledger inventory, and it is not settlement-monitored. The breach branch
    does not depend on this -- it reads held_all -- but a quarantined foreign
    position would be a hole even so."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    monkeypatch.setattr(C, "held_markets", lambda t: [SLUG, "somebody-elses"])
    monkeypatch.setattr(
        C.base, "held_inventory_from_ledger",
        lambda s: _Inv() if s == SLUG else _Inv(up="0", dn="0"))
    monkeypatch.setattr(
        C.sa, "traded_qty_with_evidence",
        lambda s, t=None: {"qty": "45.0100" if s == SLUG else "0"})
    assert C.dormant_held([SLUG, "somebody-elses"], object()) == frozenset(
        {SLUG})


def test_over_concurrency_still_halts_on_a_foreign_market():
    """Unchanged, and asked over the full held set by the loop."""
    assert C.over_concurrency_action(
        [SLUG, "somebody-elses"], {SLUG}, 1) == "halt"
    assert C.over_concurrency_action([SLUG, "mkt-b"], {SLUG, "mkt-b"}, 1) \
        == "drain"


# ------------------------------------------- an open order still refuses
def test_an_open_order_refuses_before_the_declared_set_is_consulted():
    """THE BACKSTOP THAT MAKES THE CACHE SAFE. dormant_held's answer is
    cached for DORMANT_TTL_S, so a stale entry could in principle say
    "dormant" shortly after an order appeared. It cannot authorise anything:
    account_block_reason returns open_orders:N for ANY open order in the
    account, before it looks at allow_slugs at all."""
    class T:
        def request(self, method, path, body=None, base=None):
            if path.startswith("/v1/orders/open"):
                return {"orders": [{"marketSlug": SLUG, "orderId": "o1"}]}
            raise AssertionError(
                "positions must not be read: the open-order refusal comes "
                "first, whatever is declared")

    assert micro.account_block_reason(T(), allow_slugs=(SLUG,)) \
        == "open_orders:1"


# --------------------------------------------- dormancy is never remembered
def test_dormancy_is_re_established_every_time_it_is_asked(live, monkeypatch):
    """NO CACHE, ON PURPOSE. A short-lived cache was written here and then
    removed: it saved a handful of read GETs and bought a window in which
    venue_confirmed could already be false while this still answered
    "dormant" -- the 774.62 shape, on the money path, in the one function
    whose job is establishing that a position is safe to stop watching.

    The property that matters is behavioural, not structural: the moment the
    venue's answer changes, so does this one."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    calls = []
    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: (calls.append(1) or {"ours": 0,
                                                       "foreign": 0}))
    assert C.dormant_held([SLUG], object()) == frozenset({SLUG})
    assert C.dormant_held([SLUG], object()) == frozenset({SLUG})
    assert len(calls) == 2, "each call asks the venue again"

    monkeypatch.setattr(C, "_fresh_open_orders",
                        lambda t: {"ours": 1, "foreign": 0})
    assert C.dormant_held([SLUG], object()) == frozenset(), (
        "an order appearing must be visible on the NEXT call, not 45s later")


def test_an_unreconciled_quantity_is_seen_immediately(live, monkeypatch):
    """The condition a cache would have hidden longest."""
    monkeypatch.setenv(rq.QUARANTINE_ENV, "1")
    assert C.dormant_held([SLUG], object()) == frozenset({SLUG})
    monkeypatch.setattr(C.sa, "traded_qty_with_evidence",
                        lambda s, t=None: {"qty": "774.6200"})
    assert C.dormant_held([SLUG], object()) == frozenset()


def test_off_is_off_and_asks_the_venue_nothing(live, monkeypatch):
    monkeypatch.delenv(rq.QUARANTINE_ENV, raising=False)

    def boom(t):
        raise AssertionError("quarantine OFF must not read the venue at all")

    monkeypatch.setattr(C, "_fresh_open_orders", boom)
    assert C.dormant_held([SLUG], object()) == frozenset()
    assert C.blocking_held(object()) == [SLUG]
