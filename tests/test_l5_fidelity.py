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
    """This test used to assert only the version PREFIX, which is how the v0.1
    hash shipped while covering none of the model's numbers. It now asserts the
    property its name claims: fit two genuinely different models and the
    versions must differ."""
    def fills(size, n=100):
        return [{"cid": "w0", "t": i, "window_end": 1000, "outcome": "Up",
                 "price": 0.4, "size": size} for i in range(n)]
    a = FidelityV0.fit(fills(5.0), "BTC15m")
    b = FidelityV0.fit(fills(50.0), "BTC15m")
    assert a.version.startswith("v0.2-") and b.version.startswith("v0.2-")
    assert a.version != b.version, "different models shared one version"
    assert FidelityV0.fit(fills(5.0), "BTC15m").version == a.version, (
        "the same model must be reproducible to the same version")


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
    assert m.version.startswith("v0.2-")
    from src.l5_fidelity.model import version_of
    assert version_of(m.cohort, m.tables) == m.version, (
        "the shipped artifact's version does not match its own contents")
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


# ----------------------------------------------------- v0.2: identity + size

def test_version_is_a_content_hash_of_the_whole_model():
    """v0.1's version covered the state-key schema and none of the numbers, so
    every distribution could be replaced without the version moving. That made
    "a model change gets a new version" false. Any change to any number must
    now change the version."""
    import copy
    from src.l5_fidelity.model import version_of
    m = FidelityV0.fit(_fills(), "BTC15m")
    v = version_of("BTC15m", m.tables)
    assert v == m.version and v.startswith("v0.2-")

    for mutate in (
        lambda t: t["gaps"].__setitem__(next(iter(t["gaps"])), [999.0]),
        lambda t: t["size"].__setitem__(next(iter(t["size"])), [1.0]),
        lambda t: t["base_side"].__setitem__("Up", t["base_side"].get("Up", 0) + 1),
        lambda t: t["base_gaps"].__setitem__(0, t["base_gaps"][0] + 1e-9),
        lambda t: t["base_size_bins"].__setitem__("<1", 0.123456),
    ):
        t = copy.deepcopy(m.tables)
        mutate(t)
        assert version_of("BTC15m", t) != v, "a changed model kept its version"


def test_size_bins_are_left_closed_right_open_and_preregistered():
    from src.l5_fidelity.model import SIZE_BIN_EDGES, SIZE_BIN_LABELS, size_bin
    assert SIZE_BIN_EDGES == (1.0, 3.0, 10.0, 30.0, 60.0)
    assert len(SIZE_BIN_LABELS) == len(SIZE_BIN_EDGES) + 1
    for v, exp in [(0.0, "<1"), (0.999, "<1"), (1.0, "1-3"), (2.999, "1-3"),
                   (3.0, "3-10"), (10.0, "10-30"), (30.0, "30-60"),
                   (59.999, "30-60"), (60.0, ">=60"), (1e9, ">=60")]:
        assert size_bin(v) == exp, (v, size_bin(v), exp)


def test_size_prediction_is_a_distribution_not_a_median():
    """The scored size prediction must be a proper distribution over the
    preregistered bins: normalised, strictly positive everywhere (so one
    surprise cannot score as log(0)), and the p50 demoted to a summary."""
    from src.l5_fidelity.model import SIZE_BIN_LABELS
    m = FidelityV0.fit(_fills(), "BTC15m")
    p = m.predict(tte=600, since=None, resid_rel=0.0,
                  last_outcome="Up", price=0.5)
    b = p["predicted_size_bins"]
    assert set(b) == set(SIZE_BIN_LABELS)
    assert abs(sum(b.values()) - 1.0) < 1e-9
    assert all(v > 0 for v in b.values()), "a zero bin makes log(0) reachable"
    assert p["predicted_size_mode_bin"] == max(b, key=b.get)
    assert "predicted_size_p50" not in p, "the median must not look like THE prediction"
    assert p["predicted_size_p50_summary_only"] is not None


def test_state_n_reports_true_count_not_the_capped_sample():
    """gaps/size are subsampled per state for artifact size while side keeps
    full counts. Reporting len(gaps) as state_n would understate the evidence;
    the sampled figure is kept separately rather than discarded."""
    m = FidelityV0.fit(_fills(), "BTC15m")
    p = m.predict(tte=600, since=None, resid_rel=0.0,
                  last_outcome="Up", price=0.5)
    assert p["state_n"] >= p["state_n_sampled"]
    assert isinstance(p["state_n_sampled"], int)


# ------------------------------------------- v0.2 runtime proofs + durability

def test_self_check_proves_model_identity_at_runtime():
    """Each of these was broken at some point, so the production process asserts
    them at boot rather than trusting that the tests describe what shipped."""
    import pathlib
    from src.l5_fidelity.service import self_check
    art = (pathlib.Path(__file__).resolve().parents[1]
           / "artifacts/l5_fidelity/BTC15m.json.gz")
    c = self_check(FidelityV0.load(str(art)))
    assert c["content_hash_matches_artifact"]
    assert c["param_change_changes_version"]
    assert c["size_bins_sum_to_one"] and c["size_bins_all_positive"]
    assert c["state_n_separately_represented"]
    assert c["state_n_true"] >= c["state_n_sampled"]
    assert c["all_pass"]


def test_lineage_separates_versions_and_detects_a_changed_row(tmp_path):
    """A lineage's seal_digest is built from content-hash ids, so an edited row
    fails its seal, is excluded, and the digest moves. That is what makes
    "v0.1's rows are byte-identical after the v0.2 cutover" checkable rather
    than merely asserted."""
    import json
    from src.l5_fidelity import store
    from src.l5_fidelity.service import lineage
    d = str(tmp_path)
    for v, n in (("v0.1-aaa", 2), ("v0.2-bbb", 3)):
        for i in range(n):
            store.freeze(d, model_version=v, cohort="BTC15m",
                         state={"cid": f"c{i}", "anchor_t": i},
                         prediction={"x": i}, horizon_s=[1.0], now=1000.0 + i)
    lg = lineage(d)
    assert lg["v0.1-aaa"]["frozen"] == 2 and lg["v0.2-bbb"]["frozen"] == 3
    before = lg["v0.1-aaa"]["seal_digest"]

    p = tmp_path / "predictions.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    for r in rows:
        if r["model_version"] == "v0.1-aaa":
            r["prediction"]["x"] = 999          # silent edit
            break
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))

    after = lineage(d)
    assert after["v0.1-aaa"]["frozen"] == 1, "the edited row was still counted"
    assert after["v0.1-aaa"]["seal_digest"] != before
    assert after["v0.2-bbb"]["seal_digest"] == lg["v0.2-bbb"]["seal_digest"], (
        "an edit in one lineage must not disturb another")


def test_unresolved_predictions_survive_a_restart(tmp_path):
    """`pending` was in-memory only, so a restart orphaned every prediction
    frozen within MAX_H+margin of it -- and that loss is correlated with
    deploys, not random."""
    from src.l5_fidelity import anchored, store
    anchor = {"cid": "w1", "t": 1000, "window_end": 1900, "outcome": "Up",
              "price": 0.5, "size": 10.0}
    st = anchored.state_after_fill(cohort="BTC15m", anchor=anchor,
                                   window_fills=[anchor])
    assert st["cid"] == "w1", "without cid a restart cannot find the window"

    d = str(tmp_path)
    store.freeze(d, model_version="v0.2-x", cohort="BTC15m", state=st,
                 prediction={"p": 1}, horizon_s=[1.0], now=1000.0)
    L = store.load(d)
    recovered = [p for pid, p in L["predictions"].items()
                 if not L["outcomes"].get(pid)
                 and p["model_version"] == "v0.2-x"
                 and (p.get("state") or {}).get("cid")]
    assert len(recovered) == 1 and recovered[0]["state"]["cid"] == "w1"
