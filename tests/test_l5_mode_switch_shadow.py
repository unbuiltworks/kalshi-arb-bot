"""The mode-switch candidate is SHADOW ONLY and must stay unwired."""
import math

from src import l5_mode_switch as SW


def test_it_is_not_wired_into_any_production_decision_path():
    """The whole safety property of a shadow candidate."""
    import pathlib
    # `l5_decision_shadow` is the SHADOW CONSUMER: it logs frozen-vs-candidate
    # and submits nothing, which is the only legitimate reader.
    allowed = {"l5_mode_switch.py", "l5_decision_shadow.py"}
    hits = []
    for p in pathlib.Path("src").glob("*.py"):
        if p.name in allowed:
            continue
        if "l5_mode_switch" in p.read_text():
            hits.append(p.name)
    for p in pathlib.Path("scripts").glob("l5_pmus_live*.py"):
        if "l5_mode_switch" in p.read_text():
            hits.append(p.name)
    assert hits == [], "shadow candidate is referenced by: %s" % (hits,)


def test_the_shadow_consumer_is_itself_not_wired_into_production():
    """The candidate must not reach production THROUGH the shadow either."""
    import pathlib
    hits = []
    for p in list(pathlib.Path("src").glob("*.py")) + \
            list(pathlib.Path("scripts").glob("l5_pmus_live*.py")):
        if p.name == "l5_decision_shadow.py":
            continue
        if "l5_decision_shadow" in p.read_text():
            hits.append(p.name)
    assert hits == [], "decision shadow is referenced by: %s" % (hits,)


def test_shadow_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(SW.SHADOW_ENV, raising=False)
    assert SW.shadow_enabled() is False
    for v in ("0", "false", "False", ""):
        monkeypatch.setenv(SW.SHADOW_ENV, v)
        assert SW.shadow_enabled() is False
    monkeypatch.setenv(SW.SHADOW_ENV, "1")
    assert SW.shadow_enabled() is True


def test_mode_is_persistence_not_residual():
    assert SW.mode_of(held="up", prev_extended=True) == SW.ACCUMULATE_UP
    assert SW.mode_of(held="down", prev_extended=True) == SW.ACCUMULATE_DOWN
    assert SW.mode_of(held="up", prev_extended=False) == SW.REBALANCE
    # a balanced book has no overweight side and so no mode
    assert SW.mode_of(held=None, prev_extended=True) == SW.REBALANCE


def test_preferred_side():
    assert SW.preferred_side(held="up", prev_extended=True) == "up"
    assert SW.preferred_side(held="up", prev_extended=False) == "down"
    assert SW.preferred_side(held="down", prev_extended=True) == "down"
    assert SW.preferred_side(held="down", prev_extended=False) == "up"
    # frozen behaviour is preserved where there is nothing to prefer
    assert SW.preferred_side(held=None, prev_extended=None) is None


def test_the_fitted_model_loads_and_prev_ext_dominates():
    m = SW.load_model()
    w = m["w"]
    assert abs(w["f_prev_ext"]) > abs(w["f_resid"])
    assert abs(w["f_prev_ext"]) > abs(w["f_ratio"])
    # and it is the largest coefficient in the model
    assert max(w, key=lambda k: abs(w[k])) == "f_prev_ext"


def test_p_extend_is_a_probability():
    m = SW.load_model()
    for feats in ({}, {"f_prev_ext": 1.0}, {"f_prev_ext": 0.0, "f_resid": 9.0}):
        p = SW.p_extend(m, feats)
        assert 0.0 < p < 1.0
    # persistence raises it
    assert SW.p_extend(m, {"f_prev_ext": 1.0}) > SW.p_extend(m, {"f_prev_ext": 0.0})
