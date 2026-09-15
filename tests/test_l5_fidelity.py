"""The experiment is only meaningful if a prediction cannot move after the fact."""
import tempfile

from src.l5_fidelity import runner, store
from src.l5_fidelity.model import HORIZONS_S, FidelityV0


def _fills(n=400, step=2):
    out = []
    for i in range(n):
        out.append({"cid": f"w{i // 40}", "t": i * step, "window_end": (i // 40) * 80 + 80,
                    "outcome": "Up" if i % 3 else "Down", "price": 0.45,
                    "size": 10.0 + (i % 5), "fee_bearing": i % 7 == 0})
    return out


def test_horizons_are_preregistered_in_the_module():
    """Fixed before any score exists, so they cannot be re-chosen afterwards."""
    assert HORIZONS_S == (1.0, 5.0, 15.0, 30.0)


def test_a_frozen_prediction_cannot_be_amended():
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    st = runner.build_state(cohort="BTC15m", now=100.0, window_end=200.0,
                            fills=_fills(60), book=None)
    rec = runner.predict_and_freeze(d, m, st, now=100.0)
    runner.resolve_one(d, rec, _fills(120), now=140.0)
    # a second resolution cannot overwrite the first
    runner.resolve_one(d, rec, [], now=200.0)
    L = store.load(d)
    assert L["n_predictions"] == 1 and L["n_resolved"] == 1
    assert L["outcomes"][rec["prediction_id"]]["resolved_utc"] == 140.0


def test_state_cannot_contain_a_fill_from_after_the_prediction():
    """Defensive re-filter: a leaked future fill must not reach the state."""
    fs = _fills(60)
    st = runner.build_state(cohort="BTC15m", now=50.0, window_end=200.0,
                            fills=fs, book=None)
    assert st["n_fills_so_far"] == len([f for f in fs if f["t"] <= 50.0])
    assert st["since_last_fill_s"] >= 0


def test_resolution_ignores_fills_at_or_before_the_prediction():
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    st = runner.build_state(cohort="BTC15m", now=100.0, window_end=300.0,
                            fills=_fills(60), book=None)
    rec = runner.predict_and_freeze(d, m, st, now=100.0)
    stale = [{"t": 99.0, "outcome": "Up", "size": 5.0, "price": 0.4}]
    out = runner.resolve_one(d, rec, stale, now=200.0)
    assert out["actual"]["next_fill_gap_s"] is None
    assert out["actual"]["no_fill_within_max_horizon"] is True
    assert out["disagreement"]["outcome"] is None


def test_no_fill_means_side_and_size_are_not_scored():
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    st = runner.build_state(cohort="BTC15m", now=10.0, window_end=300.0,
                            fills=_fills(6), book=None)
    rec = runner.predict_and_freeze(d, m, st, now=10.0)
    out = runner.resolve_one(d, rec, [], now=100.0)
    assert out["disagreement"]["outcome"] is None
    assert "not scored" in out["disagreement"]["outcome_reason"]
    # ACT/WAIT is still scored -- that is the whole point of Stage A
    for h in HORIZONS_S:
        assert out["disagreement"]["act"][str(h)]["actual"] is False


def test_act_probabilities_are_monotone_in_the_horizon():
    m = FidelityV0.fit(_fills(), "BTC15m")
    p = m.predict(tte=100, since=2.0, resid_rel=0.0, last_outcome="Up", price=0.45)
    v = [p["p_act_within"][str(h)] for h in HORIZONS_S]
    assert v == sorted(v)


def test_the_model_carries_a_version_and_the_record_pins_it():
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    st = runner.build_state(cohort="BTC15m", now=100.0, window_end=300.0,
                            fills=_fills(60), book=None)
    rec = runner.predict_and_freeze(d, m, st, now=100.0)
    assert rec["model_version"] == m.version and m.version.startswith("v0-")
    assert rec["prediction"]["model_version"] == m.version


def test_baselines_are_recorded_beside_every_prediction():
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    st = runner.build_state(cohort="BTC15m", now=100.0, window_end=300.0,
                            fills=_fills(60), book=None)
    rec = runner.predict_and_freeze(d, m, st, now=100.0)
    got = set(rec["prediction"]["baselines"])
    from src.l5_fidelity import baselines as B
    assert got == set(B.ALL)


def test_the_runner_has_no_order_path():
    import ast, pathlib
    for name in ("model.py", "store.py", "runner.py", "baselines.py"):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "src/l5_fidelity" / name).read_text()
        tree = ast.parse(src)
        called = {(n.func.attr if isinstance(n.func, ast.Attribute)
                   else getattr(n.func, "id", ""))
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for verb in ("place_limit_buy", "place_order", "submit", "cancel", "sign"):
            assert verb not in called, (name, verb)
