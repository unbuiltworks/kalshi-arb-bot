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


# --------------------------------------------------------------------------
# CONTENT pinning (OWNER 2026-09-16 P0): the allow-list must not rubber-stamp
# arbitrary future commits.

def test_every_allow_listed_delta_pins_the_exact_reviewed_blob():
    for f, meta in L.ALLOWED.items():
        sha = str(meta.get("sha") or "")
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), f


def test_editing_an_allow_listed_file_again_fails_until_re_reviewed(monkeypatch):
    """The whole point of the pin. A filename allow-list would silently accept
    this; the content check must not."""
    drifted = {k: dict(v) for k, v in L.ALLOWED.items()}
    drifted["src/pmus_spring_l5_session.py"]["sha"] = "0" * 40
    monkeypatch.setattr(L, "ALLOWED", drifted)
    v = L.verify()
    assert v["ACCEPTANCE"] is False
    assert [d["file"] for d in v["content_drift"]] == \
        ["src/pmus_spring_l5_session.py"]
    assert v["content_drift"][0]["reason"] == \
        "content differs from the reviewed blob"


def test_an_unpinned_delta_fails_rather_than_defaulting_open(monkeypatch):
    unpinned = {k: dict(v) for k, v in L.ALLOWED.items()}
    unpinned["src/pmus_incentive_audit.py"]["sha"] = ""
    monkeypatch.setattr(L, "ALLOWED", unpinned)
    v = L.verify()
    assert v["ACCEPTANCE"] is False
    assert v["content_drift"][0]["reason"] == "no reviewed blob pinned"


def test_pin_shas_is_never_called_by_verify():
    """A pin that refreshes itself is not a pin."""
    src = open(L.__file__, encoding="utf-8").read()
    body = src.split("def verify(")[1]
    assert "pin_shas" not in body


def test_the_risk_bound_delta_is_declared_and_is_not_an_execution_class():
    meta = L.ALLOWED["src/pmus_spring_l5_session.py"]
    assert meta["class"] == "RISK_BOUND_TIGHTENING"
    assert meta["class"] not in L.FORBIDDEN_CLASSES
    for token in ("cap_admits", "counts_against_market_cap",
                  "complement-acceptance"):
        assert token in meta["why"], token
