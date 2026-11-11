"""The settlement-aware loss stop must be able to SEE a settled loss.

WHAT THESE TESTS EXIST TO PREVENT, stated as it actually happened. Probe A
(2026-09-23, OWNER-2026-09-23-GEMINI-Q1-AB-1) took five NO fills across two
BTC15m contracts for 2.19 of spend. Both contracts resolved `yes`, so every
contract it held paid nothing. It reported:

    loss 0 of 10.00 ... payout 0  fees 0  rebates 0  implied net 0

That report was arithmetically correct and completely misleading.
`cumulative_economic_loss_usd` sums SETTLED windows only, and BOTH windows
were still PENDING -- because the settle pass sat at the TOP of the run loop
and every exit from that loop is a `break`. The final window of every cohort
was therefore never settled, whatever the venue said, and a loss that settled
at or after it could not reach the only field the stop reads.

The tests below drive the runner with an injected resolver, so none of this
depends on wall-clock timing or on a live venue.
"""
import asyncio
import os

import pytest

from src.gemini_execution import cohort as CO
from src.gemini_execution import cohort_economics as CE
from src.gemini_execution import cohort_runner as CR

from tests.test_cohort_runner import (BTC15M, NEXT15M, THIRD15M, _READY,
                                      _chain, _plan, _run)

SETTLED_YES = {"status": "settled", "resolutionSide": "yes",
               "resolvedAt": "2026-09-23T04:45:06.820Z",
               "settlementValue": "87159.55722222224"}
ACTIVE = {"status": "active", "resolutionSide": None}


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    os.makedirs(tmp_path / "out", exist_ok=True)
    os.makedirs(tmp_path / "latch", exist_ok=True)


def _resolver(calls):
    """ACTIVE on the first read of a symbol, SETTLED on every read after.

    This is the real shape, not a convenience: a window ends at tte 60 and
    the contract does not expire for another minute, so the read taken
    immediately after the window is genuinely PENDING. The venue publishes
    the resolution afterwards. The runner's own comment says as much -- "it
    will usually be PENDING -- and the settle pass at the top of the next
    iteration converts it once the venue publishes."
    """
    async def _resolve(symbol):
        calls.setdefault(symbol, 0)
        calls[symbol] += 1
        return dict(ACTIVE) if calls[symbol] == 1 else dict(SETTLED_YES)
    return _resolve


def _losing_window(spend_per_window="1.12", no_qty="3", n_fills=3):
    """A window that buys NO only -- the shape Probe A actually traded."""
    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None, status=None):
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol,
                           "inventory": {"yes_qty": "0", "no_qty": no_qty,
                                         "total_spend_usd": spend_per_window,
                                         "n_fills": n_fills,
                                         "matched_qty": "0",
                                         "directional_residual": no_qty},
                           "orders_live_after_stop": 0,
                           "stop_why": "window withdrawn"}}
    return _window


def _discoverer():
    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M)
               if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}
    return _discover


async def _gate(symbol, **k):
    return {"ok": True, "verdict": "CLEAR", "open_orders_after": 0}


def _run_two_losing_windows(tmp_path, *, loss_cap="10.00"):
    root, ids = _chain(tmp_path, 2)
    plan = _plan(tmp_path, root, ids[0], windows=2, loss=loss_cap, chain=ids)
    calls = {}
    rep = _run(CR.run_cohort(plan, readiness=True,
                             window_fn=_losing_window(),
                             discover_fn=_discoverer(),
                             gate_fn=_gate, readiness_fn=_READY,
                             resolution_fn=_resolver(calls),
                             latch_fn=lambda *a, **k: {"ok": True}))
    return rep, calls


def test_the_final_window_is_settled_before_the_process_ends(tmp_path):
    """THE DEFECT. Without a terminal sweep the last window stays PENDING."""
    rep, _ = _run_two_losing_windows(tmp_path)
    assert rep["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    windows = rep["cohort_state"]["windows"]
    assert len(windows) == 2
    states = [w["economics"]["state"] for w in windows]
    assert states == [CE.SETTLED, CE.SETTLED], (
        "every window the venue reports settled must be settled before the "
        "process ends -- the last one included. %r" % (states,))


def test_a_settled_loss_reaches_the_field_the_stop_reads(tmp_path):
    """2 x 1.12 of NO fills against a `yes` resolution is 2.24 of loss."""
    rep, _ = _run_two_losing_windows(tmp_path)
    econ = rep["cohort_state"]["economics"]
    assert econ["cumulative_economic_loss_usd"] == "2.24"
    assert econ["settlement_implied_net_usd"] == "-2.24"
    assert rep["cohort_state"]["aggregate_loss_usd"] == "2.24"


def test_the_terminal_sweep_says_whether_the_cap_was_breached(tmp_path):
    """A cohort that ends over its cap must SAY so, not report `loss 0`."""
    rep, _ = _run_two_losing_windows(tmp_path, loss_cap="2.00")
    t = rep["terminal_settlement"]
    assert t["ran"] is True
    assert t["windows_settled_by_this_sweep"] >= 1
    assert t["loss_cap_breached_after_settlement"] is True
    assert t["cumulative_economic_loss_usd"] == "2.24"
    assert t["settlement_complete"] is True
    assert t["unsettled_at_exit"] == []


def test_a_window_that_never_settles_is_named_not_zeroed(tmp_path):
    """UNKNOWN is not 0.00. An unread settlement must be visible as unread."""
    root, ids = _chain(tmp_path, 2)
    plan = _plan(tmp_path, root, ids[0], windows=2, loss="10.00", chain=ids)

    async def _never(symbol):
        return dict(ACTIVE)

    rep = _run(CR.run_cohort(plan, readiness=True,
                             window_fn=_losing_window(),
                             discover_fn=_discoverer(),
                             gate_fn=_gate, readiness_fn=_READY,
                             resolution_fn=_never,
                             latch_fn=lambda *a, **k: {"ok": True}))
    t = rep["terminal_settlement"]
    assert t["settlement_complete"] is False
    assert len(t["unsettled_at_exit"]) == 2
    assert {w["gross_fill_spend_usd"] for w in t["unsettled_at_exit"]} == {
        "1.12"}
    # and the loss field is still honest about summing SETTLED only
    assert t["cumulative_economic_loss_usd"] == "0"
    assert t["loss_cap_breached_after_settlement"] is False


def test_the_sweep_is_read_only_and_consumes_no_ledger(tmp_path):
    """The accounting fix must not touch a mutation budget."""
    root, ids = _chain(tmp_path, 2)
    plan = _plan(tmp_path, root, ids[0], windows=2, loss="10.00", chain=ids)
    calls = {}
    _run(CR.run_cohort(plan, readiness=True,
                       window_fn=_losing_window(),
                       discover_fn=_discoverer(),
                       gate_fn=_gate, readiness_fn=_READY,
                       resolution_fn=_resolver(calls),
                       latch_fn=lambda *a, **k: {"ok": True}))
    for cid in ids:
        st = CO.state(cid, root=root)
        assert st["consumed"] == 0, (
            "the injected window places nothing, so the terminal sweep must "
            "not have moved any ledger either")


def test_a_profitable_window_still_reduces_loss_after_the_sweep(tmp_path):
    """The sweep must use the same economics, not a loss-only shortcut."""
    root, ids = _chain(tmp_path, 1)
    plan = _plan(tmp_path, root, ids[0], windows=1, loss="10.00", chain=ids)
    calls = {}

    async def _winner(plan_, symbol, *, readiness, latch_held_by,
                      cohort_id=None, status=None):
        # 3 YES at 0.20 = 0.60 spend, resolution `yes` pays 3 x 1.00
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol,
                           "inventory": {"yes_qty": "3", "no_qty": "0",
                                         "total_spend_usd": "0.60",
                                         "n_fills": 3, "matched_qty": "0",
                                         "directional_residual": "3"},
                           "orders_live_after_stop": 0,
                           "stop_why": "window withdrawn"}}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_winner,
                             discover_fn=_discoverer(),
                             gate_fn=_gate, readiness_fn=_READY,
                             resolution_fn=_resolver(calls),
                             latch_fn=lambda *a, **k: {"ok": True}))
    econ = rep["cohort_state"]["economics"]
    assert econ["settlement_implied_net_usd"] == "2.4"
    assert econ["cumulative_economic_loss_usd"] == "-2.4"
    assert rep["terminal_settlement"][
        "loss_cap_breached_after_settlement"] is False


def test_the_blind_spot_is_sized_not_argued_about(tmp_path):
    """An unsettled window's worst case must be a number, not a footnote."""
    root, ids = _chain(tmp_path, 2)
    plan = _plan(tmp_path, root, ids[0], windows=2, loss="10.00", chain=ids)

    async def _never(symbol):
        return dict(ACTIVE)

    rep = _run(CR.run_cohort(plan, readiness=True,
                             window_fn=_losing_window(),
                             discover_fn=_discoverer(),
                             gate_fn=_gate, readiness_fn=_READY,
                             resolution_fn=_never,
                             latch_fn=lambda *a, **k: {"ok": True}))
    t = rep["terminal_settlement"]
    # nothing settled, so the counted loss is 0 and the worst case is all of it
    assert t["cumulative_economic_loss_usd"] == "0"
    assert t["worst_case_loss_if_unsettled_lose_usd"] == "2.24"
