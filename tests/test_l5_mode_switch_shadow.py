"""The mode-switch candidate is SHADOW ONLY and must stay unwired."""
import math

from src import l5_mode_switch as SW


def test_the_only_production_references_are_observation_wiring():
    """NARROWED for R6, and still proving the thing that matters.

    The candidate and its shadow may now be CONSTRUCTED, ATTACHED,
    JOURNALLED and STOPPED by the probe process, because R6 must measure
    frozen-vs-candidate disagreement live. What must remain impossible is a
    path from either of them to submission, cancellation, canonical
    booking, inventory mutation, risk mutation or an execution decision.
    """
    import pathlib
    allowed_modules = {"l5_mode_switch.py", "l5_decision_shadow.py"}
    #: the ONLY production files permitted to name them, and why
    allowed_callers = {
        "pmus_native_money_path.py": "holds the observation hooks",
        "l5_pmus_live_oneshot.py": "constructs, attaches, journals, stops",
    }
    hits = []
    for p in list(pathlib.Path("src").glob("*.py")) + \
            list(pathlib.Path("scripts").glob("*.py")):
        if p.name in allowed_modules or p.name in allowed_callers:
            continue
        t = p.read_text()
        if "l5_mode_switch" in t or "l5_decision_shadow" in t:
            hits.append(p.name)
    assert hits == [], "unexpected production reference: %s" % (hits,)


def _shadow_call_sites(path):
    """Every line in a permitted caller that names the shadow/candidate."""
    out = []
    for i, line in enumerate(path.read_text().split("\n"), 1):
        if "decision_shadow" in line or "l5_mode_switch" in line:
            out.append((i, line.strip()))
    return out


def test_the_shadow_is_only_ever_constructed_attached_or_logged():
    """Line by line: no call site may read a value back into execution."""
    import pathlib
    forbidden = ("submit", "cancel", "on_fill", "mark_terminal",
                 "release_terminal", "registry.record", "_post(",
                 "eng.on_book", "intents =", "return ")
    for f in ("src/pmus_native_money_path.py",
              "scripts/l5_pmus_live_oneshot.py"):
        for ln, text in _shadow_call_sites(pathlib.Path(f)):
            for bad in forbidden:
                assert bad not in text, "%s:%d %r" % (f, ln, text)


def test_the_shadow_result_is_never_assigned_to_anything():
    """`compare()` and `observe_fill()` are called for effect, never read."""
    import ast
    import pathlib
    for f in ("src/pmus_native_money_path.py",
              "scripts/l5_pmus_live_oneshot.py"):
        tree = ast.parse(pathlib.Path(f).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.Return)):
                continue
            seg = ast.unparse(node)
            assert "decision_shadow.compare" not in seg, (f, seg[:120])
            assert "decision_shadow.observe_fill" not in seg, (f, seg[:120])


def test_the_shadow_is_called_after_the_frozen_decision_not_before():
    """It cannot influence a decision that has already been made."""
    import inspect
    from src import pmus_native_money_path as MP
    src = inspect.getsource(MP.NativeMoneyPath.on_book_state)
    assert src.index("self.eng.on_book(") < src.index("decision_shadow")


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
