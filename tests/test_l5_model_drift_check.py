from src import l5_model_drift_check as drift


def test_compare_scores_only_states_with_enough_new_transitions():
    model = {"_sha256_16": "abc", "generated_utc": "x",
             "base": {"s1": {"open|q<5": 60, "extend|q<5": 40},
                      "s2": {"complete|q<5": 50, "extend|q<5": 50}}}
    new = ([{"t0": 1, "base": "s1", "action": "open|q<5"}] * 30
           + [{"t0": 1, "base": "s1", "action": "extend|q<5"}] * 10
           + [{"t0": 1, "base": "s2", "action": "complete|q<5"}] * 5
           + [{"t0": 1, "base": "s9", "action": "open|q<5"}] * 40)
    rep = drift.compare(model, new, "2026-09-01T00:00:00+00:00")
    assert rep["states_scored"] == 1                 # s2 too thin, s9 unseen
    assert rep["states_new_unseen_by_model"] == 1
    s1 = rep["top_states"][0]
    assert s1["state"] == "s1" and s1["n_new"] == 40
    assert s1["new"]["open"] == 0.75 and s1["frozen"]["open"] == 0.6
    assert abs(s1["l1"] - 0.3) < 1e-9 and s1["flag"] is True
    assert rep["verdict"] == "DRIFT_CANDIDATE_VALIDATE_OOS"
    assert "never changed" in rep["rule"]
    assert drift.compare(model, [], "2026-09-01T00:00:00+00:00")["verdict"] == \
        "INSUFFICIENT_NEW_DATA"
