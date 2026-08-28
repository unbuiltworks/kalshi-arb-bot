import pytest

from src import universal_research_tick


@pytest.mark.asyncio
async def test_run_lane_returns_error_without_raising():
    async def boom():
        raise RuntimeError("x")

    result = await universal_research_tick._run_lane("boom", boom)
    assert result["status"] == "error"
    assert result["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_run_lane_returns_result():
    async def ok():
        return {"read_only": True}

    result = await universal_research_tick._run_lane("ok", ok)
    assert result == {"status": "ok", "result": {"read_only": True}}
