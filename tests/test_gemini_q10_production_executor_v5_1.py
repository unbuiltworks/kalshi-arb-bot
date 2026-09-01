from decimal import Decimal as D

from src import gemini_q10_production_executor as v1
from src import gemini_q10_production_executor_v5_1 as p


def model(yes='0.55', no='0.33', edge='1.11'):
    return {
        'yes_quote': D(yes), 'no_quote': D(no), 'locked_edge': D(edge),
        'max_one_sided_premium': max(D(yes), D(no)) * D('10'),
    }


def test_v51_accepts_rescue_safe_geometry_legacy_principal_gate_rejects():
    m = model()
    # V1 rejects because 55c x q10 = $5.50 > its legacy $5 cap.
    assert v1._entry_model_ok(m) is False
    # V5.1 lets the standing <=30c rescue invariant define loss-at-risk instead.
    assert p.entry_model_ok(m) is True


def test_v51_keeps_pair_edge_and_rebate_band_gates():
    assert not p.entry_model_ok(model(edge='0.49'))
    assert not p.entry_model_ok(model(yes='0.81', no='0.10', edge='0.80'))
    assert not p.entry_model_ok(model(yes='0.19', no='0.70', edge='0.80'))


def test_v51_install_raises_cash_preflight_floor(monkeypatch):
    monkeypatch.setattr(p.v5.v1, 'MIN_AVAILABLE', D('15'))
    original = p.v5.v1._entry_model_ok
    try:
        p.install()
        assert p.v5.v1.MIN_AVAILABLE == D('22.10')
        assert p.v5.v1._entry_model_ok is p.entry_model_ok
    finally:
        p.v5.v1.MIN_AVAILABLE = D('15')
        p.v5.v1._entry_model_ok = original
