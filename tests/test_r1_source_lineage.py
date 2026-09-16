"""The R1 lineage claim must be recomputable, and must fail on a real delta."""
from analysis.l5.lanes import r1_source_lineage as L


def test_the_acceptance_condition_holds_on_the_current_tree():
    v = L.verify()
    assert v["ACCEPTANCE"] is True, v["why"]
    assert v["undisclosed"] == []
    assert v["absent_from_reviewed"] == []


def test_all_four_deltas_are_disclosed_and_none_is_execution_class():
    v = L.verify()
    assert len(v["differing"]) == 4
    for f in v["differing"]:
        assert f in L.ALLOWED, f
        assert L.ALLOWED[f]["class"] not in L.FORBIDDEN_CLASSES, f


def test_an_undisclosed_delta_fails_acceptance(monkeypatch):
    """The check must be capable of failing, or it proves nothing. Drop one
    file from the allow-list and acceptance must go False."""
    trimmed = {k: v for k, v in L.ALLOWED.items()
               if k != "src/pmus_spring_l5_session.py"}
    monkeypatch.setattr(L, "ALLOWED", trimmed)
    v = L.verify()
    assert v["ACCEPTANCE"] is False
    assert "src/pmus_spring_l5_session.py" in v["undisclosed"]


def test_an_execution_class_delta_fails_acceptance(monkeypatch):
    relabelled = dict(L.ALLOWED)
    relabelled["src/pmus_spring_l5_session.py"] = {
        "class": "EXECUTION", "why": "hypothetical"}
    monkeypatch.setattr(L, "ALLOWED", relabelled)
    assert L.verify()["ACCEPTANCE"] is False


def test_the_scope_is_the_image_copy_set_not_the_branch():
    """The branch carries thousands of unrelated lines; only what the image
    COPIes can affect the probe."""
    srcs = L.copy_sources()
    assert len(srcs) == 36
    assert all(not s.startswith("tests/") for s in srcs)
