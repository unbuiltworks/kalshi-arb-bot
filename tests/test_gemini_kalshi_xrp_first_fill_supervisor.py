import pytest

from src import gemini_kalshi_xrp_first_fill_supervisor as supervisor


def test_cycle_root_is_deterministic_and_unique():
    assert supervisor.cycle_root("demo-run", 1) == "demo-run-c01"
    assert supervisor.cycle_root("demo-run", 1) != supervisor.cycle_root("demo-run", 2)


def test_zero_fill_campaign_terminal_can_continue():
    stop, reason = supervisor.terminal_requires_stop({"status": "MAX_REPRICE_ROUNDS_REACHED"})
    assert not stop
    assert reason == "MAX_REPRICE_ROUNDS_REACHED"


def test_any_terminal_round_stops_supervisor():
    stop, reason = supervisor.terminal_requires_stop({
        "status": "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND",
        "terminal_status": "BOTH_FILLED_RECONCILED_NOT_SETTLED",
    })
    assert stop
    assert reason == "BOTH_FILLED_RECONCILED_NOT_SETTLED"


def test_duplicate_fails_closed():
    stop, _ = supervisor.terminal_requires_stop({"status": "DUPLICATE_CAMPAIGN_BLOCKED"})
    assert stop


@pytest.mark.asyncio
async def test_read_only_rollover_is_zero_mutation_not_exception():
    async def no_pair():
        raise RuntimeError(supervisor.NO_PAIR_TEXT)

    result = await supervisor._run_after_rollover(
        no_pair, cycle=0, root_trade_id="READ_ONLY", armed=False
    )
    assert result["status"] == "NO_VIABLE_PAIR_YET"
    assert result["order_entry"] is False
