from types import SimpleNamespace

from src.weather_shadow_daemon import _research_blockers


def _row(**overrides):
    values = {
        "settlement_status": "TWC_SOURCE_ACCESS_REQUIRED",
        "model_status": "MODEL_RESEARCH_ONLY",
        "data_quality_status": "FRESH",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_twc_access_and_research_model_block_execution_eligibility():
    blockers = _research_blockers(_row())
    assert "TWC_SOURCE_ACCESS_REQUIRED" in blockers
    assert "MODEL_RESEARCH_ONLY" in blockers


def test_stale_data_is_an_independent_readiness_blocker():
    blockers = _research_blockers(_row(data_quality_status="STALE_LAST_GOOD"))
    assert "DATA_QUALITY_STALE_LAST_GOOD" in blockers


def test_only_explicit_certified_states_clear_research_blockers():
    blockers = _research_blockers(
        _row(
            settlement_status="SETTLEMENT_CERTIFIED",
            model_status="MODEL_CERTIFIED",
            data_quality_status="FRESH",
        )
    )
    assert blockers == []
