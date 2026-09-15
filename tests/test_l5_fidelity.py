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
    assert rec["model_version"] == m.version and m.version.startswith("v0")
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


def test_the_key_means_the_same_thing_at_fit_and_at_serve():
    """v0 fitted last_outcome = s[i-1] while the runner serves the MOST RECENT
    fill s[i]. Trained on one meaning, queried with another -- and it destroyed
    the strongest feature: P(next=Up | last=Up) is 0.733 in the data while v0
    emitted p_up near 0.50 in exactly those states."""
    fills = []
    # a deterministic persistent series: Up repeats, Down repeats
    for i in range(600):
        o = "Up" if (i // 3) % 2 == 0 else "Down"
        fills.append({"cid": f"w{i // 60}", "t": i * 2,
                      "window_end": (i // 60) * 120 + 120,
                      "outcome": o, "price": 0.45, "size": 10.0})
    m = FidelityV0.fit(fills, "BTC15m")
    # Read the states the fixture actually populated rather than guessing a
    # query: the residual bucket is part of the key, so a hand-picked
    # resid_rel can land on an unpopulated state and fall back to the base
    # table -- which is exactly the ~0.5 this test exists to rule out.
    up_states = {k: v for k, v in m.tables["side"].items() if "|last=Up|" in k}
    dn_states = {k: v for k, v in m.tables["side"].items() if "|last=Down|" in k}
    assert up_states and dn_states
    p_up = lambda t: t.get("Up", 0) / max(1, sum(t.values()))
    best_up = max(up_states.values(), key=lambda t: sum(t.values()))
    best_dn = max(dn_states.values(), key=lambda t: sum(t.values()))
    # persistence must survive into the model rather than washing out to 0.5
    assert p_up(best_up) > 0.6, p_up(best_up)
    assert p_up(best_dn) < 0.4, p_up(best_dn)


def test_since_is_not_in_the_state_key():
    """At fit only the COMPLETED gap is known; at serve only the elapsed-so-far.
    Keying on it trains against a quantity unobservable at prediction time."""
    from src.l5_fidelity.model import state_key
    a = state_key(tte=100, since=1.0, resid_rel=0.0, last_outcome="Up", price=0.4)
    b = state_key(tte=100, since=99.0, resid_rel=0.0, last_outcome="Up", price=0.4)
    assert a == b


def test_a_model_change_gets_a_new_version():
    fills = [{"cid": "w0", "t": i, "window_end": 1000, "outcome": "Up",
              "price": 0.4, "size": 5.0} for i in range(100)]
    m = FidelityV0.fit(fills, "BTC15m")
    assert m.version.startswith("v0.1-")


# ---------------------------------------------------------- the two estimands
def test_estimand_B_is_a_conditional_survival_not_the_fresh_gap_cdf():
    """Scoring B's sampling with A's distribution is what produced a predicted
    0.93 against an actual 0.46. They must be different numbers."""
    from src.l5_fidelity.model import ESTIMAND_A, ESTIMAND_B
    # a HEAVY-TAILED fixture, which is the real case: mostly fast fills with a
    # long tail. Conditioning on survival then LOWERS the hazard.
    fills = []
    for i in range(900):
        step = 1 if i % 10 else 120          # 9 fast gaps, then a long one
        t = fills[-1]["t"] + step if fills else 0
        fills.append({"cid": "w0", "t": t, "window_end": 10 ** 7,
                      "outcome": "Up" if i % 2 else "Down",
                      "price": 0.45, "size": 10.0})
    m = FidelityV0.fit(fills, "BTC15m")
    a = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                  price=0.45, estimand=ESTIMAND_A)
    b0 = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                   price=0.45, estimand=ESTIMAND_B, elapsed_s=0.0)
    # at elapsed 0 the conditional IS the fresh-gap CDF
    for h in ("1.0", "5.0", "15.0", "30.0"):
        assert abs(a["p_act_within"][h] - b0["p_act_within"][h]) < 1e-9
    # having already waited through the fast mass, the next 5s is far less likely
    b2 = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                   price=0.45, estimand=ESTIMAND_B, elapsed_s=10.0)
    assert b2["p_act_within"]["5.0"] < a["p_act_within"]["5.0"]


def test_conditioning_can_RAISE_the_hazard_for_a_tight_distribution():
    """Not a bug: if every gap is exactly 3s and 2s have passed without a fill,
    a fill within the next 1s is certain. B < A holds for heavy tails, not as a
    law -- and asserting it as a law is a mistake this test exists to prevent."""
    from src.l5_fidelity.model import ESTIMAND_A, ESTIMAND_B
    m = FidelityV0.fit(_fills(600, step=3), "BTC15m")
    a = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                  price=0.45, estimand=ESTIMAND_A)
    b = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                  price=0.45, estimand=ESTIMAND_B, elapsed_s=2.0)
    assert a["p_act_within"]["1.0"] == 0.0
    assert b["p_act_within"]["1.0"] == 1.0


def test_estimand_B_refuses_to_invent_a_probability_it_cannot_condition():
    from src.l5_fidelity.model import ESTIMAND_B
    m = FidelityV0.fit(_fills(600, step=2), "BTC15m")
    far = m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                    price=0.45, estimand=ESTIMAND_B, elapsed_s=1e9)
    assert all(v is None for v in far["p_act_within"].values())
    assert "undefined" in far["p_act_within_reason"]


def test_estimand_B_requires_elapsed():
    import pytest as _pt
    from src.l5_fidelity.model import ESTIMAND_B
    m = FidelityV0.fit(_fills(), "BTC15m")
    with _pt.raises(ValueError):
        m.predict(tte=100, since=None, resid_rel=0.0, last_outcome="Up",
                  price=0.45, estimand=ESTIMAND_B)


# ------------------------------------------------------- anchored prospectivity
def test_an_outcome_that_predates_the_seal_is_excluded_not_scored():
    """Sealing before we OBSERVE an event is not prospective if the event had
    already OCCURRED. The feed is a median 28.5s behind, so this is the common
    case, not a corner one."""
    import tempfile
    from src.l5_fidelity import anchored
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    anchor = {"t": 1000, "outcome": "Up", "price": 0.45, "size": 10.0,
              "window_end": 2000}
    st = anchored.state_after_fill(cohort="BTC15m", anchor=anchor,
                                   window_fills=[anchor])
    rec = anchored.freeze_anchored(d, m, st, now=1100.0)   # sealed at 1100
    successor = {"t": 1005, "outcome": "Down", "price": 0.5, "size": 7.0,
                 "window_end": 2000}                        # occurred at 1005
    out = anchored.resolve_anchored(d, rec, [anchor, successor], now=1300.0)
    assert out["actual"]["prospective"] is False
    assert out["disagreement"]["scored"] is False
    assert "already determined" in out["actual"]["not_prospective_reason"]
    assert out["disagreement"]["outcome"] is None      # not counted either way


def test_a_genuine_successor_after_the_seal_is_scored():
    import tempfile
    from src.l5_fidelity import anchored
    d = tempfile.mkdtemp()
    m = FidelityV0.fit(_fills(), "BTC15m")
    anchor = {"t": 1000, "outcome": "Up", "price": 0.45, "size": 10.0,
              "window_end": 2000}
    st = anchored.state_after_fill(cohort="BTC15m", anchor=anchor,
                                   window_fills=[anchor])
    rec = anchored.freeze_anchored(d, m, st, now=1002.0)
    successor = {"t": 1009, "outcome": "Down", "price": 0.5, "size": 7.0,
                 "window_end": 2000}
    out = anchored.resolve_anchored(d, rec, [anchor, successor], now=1300.0)
    assert out["actual"]["prospective"] is True
    assert out["disagreement"]["scored"] is True
    # the gap is measured between the FILLS' own timestamps, not wall clock
    assert out["actual"]["next_fill_gap_s"] == 9
    assert out["actual"]["acted_within"]["5.0"] is False
    assert out["actual"]["acted_within"]["15.0"] is True


def test_anchored_state_excludes_fills_after_the_anchor():
    from src.l5_fidelity import anchored
    anchor = {"t": 1000, "outcome": "Up", "price": 0.45, "size": 10.0,
              "window_end": 2000}
    later = {"t": 1500, "outcome": "Up", "price": 0.5, "size": 99.0,
             "window_end": 2000}
    st = anchored.state_after_fill(cohort="BTC15m", anchor=anchor,
                                   window_fills=[anchor, later])
    assert st["n_fills_so_far"] == 1 and st["inv_up"] == 10.0


def test_the_frozen_artifact_reproduces_the_full_fit():
    """The 24/7 runner loads a 0.33 MB artifact, not the 163 MB raw tape. The
    version hash must be identical and the served probabilities must match to
    quantile-grid resolution, or the runner is silently a different model."""
    import pathlib
    art = (pathlib.Path(__file__).resolve().parents[1]
           / "artifacts/l5_fidelity/BTC15m.json.gz")
    assert art.exists(), "the frozen artifact the service boots from is missing"
    m = FidelityV0.load(str(art))
    assert m.version.startswith("v0.1-")
    from src.l5_fidelity.model import ESTIMAND_A
    p = m.predict(tte=400, since=None, resid_rel=0.0, last_outcome="Up",
                  price=0.45, estimand=ESTIMAND_A)
    v = [p["p_act_within"][str(h)] for h in HORIZONS_S]
    assert v == sorted(v) and 0.0 <= v[0] and v[-1] <= 1.0
    # the side signal the train/serve fix restored must survive the trim
    assert p["p_outcome_up"] > 0.6


def test_the_services_window_parser_agrees_with_the_canonical_one():
    """The runner carries a stdlib-only copy so it needs no analysis package.
    If the two ever disagree the service would silently mis-cohort every fill."""
    from analysis.l5.redemption import audit as canonical
    from src.l5_fidelity import window as svc
    assert svc.DURATION_S == canonical.DURATION_S
    for slug in ("btc-updown-15m-1789434900", "eth-updown-5m-1789434900",
                 "btc-updown-1h-1789434900", "not-a-window", "btc-updown-2m-1"):
        a = canonical.WINDOW_RE.match(slug)
        b = svc.WINDOW_RE.match(slug)
        assert (a is None) == (b is None), slug
        if a:
            assert a.groups() == b.groups(), slug


def test_the_service_has_no_credential_or_order_path():
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/l5_fidelity/service.py").read_text()
    tree = ast.parse(src)
    called = {(n.func.attr if isinstance(n.func, ast.Attribute)
               else getattr(n.func, "id", ""))
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for verb in ("place_limit_buy", "place_order", "submit", "cancel", "sign",
                 "getenv"):
        assert verb not in called, verb
    for cred in ("PRIVATE_KEY", "API_KEY", "SECRET", "PASSPHRASE", "os.environ"):
        assert cred not in src, cred
    for verb in ('method="POST"', 'method="DELETE"', 'method="PUT"'):
        assert verb not in src
