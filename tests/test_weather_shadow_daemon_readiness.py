from types import SimpleNamespace

from src.weather_shadow_daemon import _research_blockers, _side_readiness_blockers


def _row(**overrides):
    values = {
        "settlement_status": "TWC_SOURCE_ACCESS_REQUIRED",
        "model_status": "MODEL_RESEARCH_ONLY",
        "data_quality_status": "FRESH",
        "exact_market_source_audited": False,
        "authoritative_fee_audited": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fill(**overrides):
    values = {
        "fully_fillable": True,
        "filled_contracts": 1.0,
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


def test_certified_settlement_model_still_require_exact_source_and_fee_audits():
    blockers = _research_blockers(
        _row(
            settlement_status="SETTLEMENT_CERTIFIED",
            model_status="MODEL_CERTIFIED",
            data_quality_status="FRESH",
        )
    )
    assert blockers == [
        "EXACT_MARKET_SOURCE_AUDIT_REQUIRED",
        "AUTHORITATIVE_FEE_AUDIT_REQUIRED",
    ]


def test_only_literal_true_source_and_fee_audits_clear_research_blockers():
    blockers = _research_blockers(
        _row(
            settlement_status="SETTLEMENT_CERTIFIED",
            model_status="MODEL_CERTIFIED",
            data_quality_status="FRESH",
            exact_market_source_audited=True,
            authoritative_fee_audited=True,
        )
    )
    assert blockers == []

    stringly = _research_blockers(
        _row(
            settlement_status="SETTLEMENT_CERTIFIED",
            model_status="MODEL_CERTIFIED",
            data_quality_status="FRESH",
            exact_market_source_audited="true",
            authoritative_fee_audited="true",
        )
    )
    assert "EXACT_MARKET_SOURCE_AUDIT_REQUIRED" in stringly
    assert "AUTHORITATIVE_FEE_AUDIT_REQUIRED" in stringly


def test_side_readiness_requires_q1_depth_fillability():
    row = _row(
        settlement_status="SETTLEMENT_CERTIFIED",
        model_status="MODEL_CERTIFIED",
        data_quality_status="FRESH",
        exact_market_source_audited=True,
        authoritative_fee_audited=True,
    )
    assert _side_readiness_blockers(row, _fill()) == []
    assert "Q1_DEPTH_NOT_FULLY_FILLABLE" in _side_readiness_blockers(
        row, _fill(fully_fillable=False, filled_contracts=0.8)
    )
    assert "Q1_DEPTH_UNDER_ONE_CONTRACT" in _side_readiness_blockers(
        row, _fill(fully_fillable=True, filled_contracts=0.5)
    )
