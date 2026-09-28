"""The R1 lineage claim must be recomputable, and must fail on a real delta."""
from analysis.l5.lanes import r1_source_lineage as L


def test_the_acceptance_condition_holds_on_the_current_tree():
    v = L.verify()
    assert v["ACCEPTANCE"] is True, v["why"]
    assert v["undisclosed"] == []
    assert v["absent_from_reviewed"] == []


def test_every_disclosed_delta_is_named_and_none_is_execution_class():
    """Against the CURRENT baseline (OWNER A2, REVIEWED advanced to the CP1
    candidate) the only legitimate mover is the authorization directory --
    granting or revoking an authorization IS a change to it."""
    v = L.verify()
    assert v["differing"] == ["analysis/l5/authorization/"], v["differing"]
    for f in v["differing"]:
        assert f in L.ALLOWED, f
        assert L.ALLOWED[f]["class"] not in L.FORBIDDEN_CLASSES, f


def test_the_gate_still_forbids_sizing_and_the_prior_review_is_preserved():
    """A2 advanced the baseline; it did NOT weaken the gate. Both halves are
    asserted, because 'we reviewed it' must not become 'we removed the check'."""
    assert "SIZING" in L.FORBIDDEN_CLASSES
    assert set(L.FORBIDDEN_CLASSES) == {"EXECUTION", "DECISION", "SIZING",
                                        "SUBMISSION"}
    # A3 advanced the chain; no earlier review may be erased by doing so.
    assert L.PREVIOUS_REVIEWED == "03f881ca"
    assert L.REVIEWED != L.PREVIOUS_REVIEWED
    assert L.REVIEWED_CHAIN == ("6adcc4c4", "03f881ca", "04cf5295")
    assert L.REVIEWED == L.REVIEWED_CHAIN[-1]
    assert "6adcc4c4" in L.REVIEWED_CHAIN, "the A1 review must stay on record"
    assert "03f881ca" in L.REVIEWED_CHAIN, "the A2 review must stay on record"
    # every delta accepted under the old baseline is still on the record
    assert set(L.PRIOR_DELTAS) >= {
        "src/pmus_l5_continuous_session.py", "src/pmus_live_mandate.py",
        "src/pmus_spring_l5_session.py", "src/l5_decision_observability.py",
        "src/pmus_incentive_audit.py", "analysis/l5/venue_maintenance_windows.json"}
    for f, meta in L.PRIOR_DELTAS.items():
        assert meta["class"] not in L.FORBIDDEN_CLASSES, f
        assert len(str(meta.get("sha") or "")) == 40, f


def test_an_undisclosed_delta_fails_acceptance(monkeypatch):
    """The check must be capable of failing, or it proves nothing. Drop one
    file from the allow-list and acceptance must go False."""
    trimmed = {k: v for k, v in L.PRIOR_DELTAS.items()
               if k != "src/pmus_micro_executor.py"}
    monkeypatch.setattr(L, "ALLOWED", trimmed)
    v = L.verify(reviewed=L.PREVIOUS_REVIEWED)
    assert v["ACCEPTANCE"] is False
    assert "src/pmus_micro_executor.py" in v["undisclosed"]


def test_an_execution_class_delta_fails_acceptance(monkeypatch):
    relabelled = dict(L.ALLOWED)
    relabelled["src/pmus_micro_executor.py"] = {
        "class": "EXECUTION", "why": "hypothetical"}
    monkeypatch.setattr(L, "ALLOWED", relabelled)
    assert L.verify(reviewed=L.PREVIOUS_REVIEWED)["ACCEPTANCE"] is False


def test_the_scope_is_the_image_copy_set_not_the_branch():
    """The branch carries thousands of unrelated lines; only what the image
    COPIes can affect the probe."""
    srcs = L.copy_sources()
    # 39, not 38: `src/pmus_fees.py` was added on 2026-09-23. The image
    # already COPIes `src/pmus_q1_executor.py`, and that module's taker fee
    # now reads the one authoritative schedule instead of a local `0.06` that
    # PMUS had superseded. A copied module whose import cannot resolve is a
    # crash waiting for the first thing that imports it, so the file travels
    # with it. The new file is a pure fee table with no venue client, no
    # credential and no order path; the pin is bumped deliberately and the
    # change is disclosed rather than absorbed.
    assert len(srcs) == 39
    assert "src/pmus_fees.py" in srcs
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
    drifted = {k: dict(v) for k, v in L.PRIOR_DELTAS.items()}
    drifted["src/pmus_spring_l5_session.py"] = {
        **drifted["src/pmus_spring_l5_session.py"], "sha": "0" * 40}
    monkeypatch.setattr(L, "ALLOWED", drifted)
    v = L.verify(reviewed=L.PREVIOUS_REVIEWED)
    assert v["ACCEPTANCE"] is False
    # Measured against the SUPERSEDED baseline, the authorized CP1 changes
    # legitimately drift too, so the deliberately-broken pin is one of several.
    # The property under test is that the pin CATCHES it -- membership, not
    # exclusivity. Asserting exclusivity here would be asserting that no other
    # authorized change exists, which is a different and false claim.
    drift = {d["file"]: d["reason"] for d in v["content_drift"]}
    assert "analysis/l5/authorization/" in drift, sorted(drift)
    assert drift["analysis/l5/authorization/"] == "content differs from the reviewed blob"


def test_an_unpinned_delta_fails_rather_than_defaulting_open(monkeypatch):
    unpinned = {k: dict(v) for k, v in L.PRIOR_DELTAS.items()}
    unpinned["analysis/l5/authorization/"]["sha"] = ""
    monkeypatch.setattr(L, "ALLOWED", unpinned)
    v = L.verify(reviewed=L.PREVIOUS_REVIEWED)
    assert v["ACCEPTANCE"] is False
    assert v["content_drift"][0]["reason"] == "no reviewed blob pinned"


def test_pin_shas_is_never_called_by_verify():
    """A pin that refreshes itself is not a pin."""
    src = open(L.__file__, encoding="utf-8").read()
    body = src.split("def verify(")[1]
    assert "pin_shas" not in body


def test_the_risk_bound_delta_is_declared_and_is_not_an_execution_class():
    """Now in PRIOR_DELTAS: the file stopped DIFFERING when A2 advanced the
    baseline to include it, but the reviewed record of what it was and why
    must survive that move -- the point of preserving the prior deltas."""
    meta = L.PRIOR_DELTAS["src/pmus_spring_l5_session.py"]
    assert meta["class"] == "RISK_BOUND_TIGHTENING"
    assert meta["class"] not in L.FORBIDDEN_CLASSES
    for token in ("cap_admits", "counts_against_market_cap",
                  "complement-acceptance"):
        assert token in meta["why"], token
