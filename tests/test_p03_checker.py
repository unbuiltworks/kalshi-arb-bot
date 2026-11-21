"""The corrected P0.3 harness, and proof it catches what it is for.

The harness itself was wrong once: it flagged four correct live rows
(executable residual 0, behavioural residual None, pre-fill) as violations.
These tests pin the fixed definition in both directions -- it must accept the
valid shape AND still catch every invalid one, because a harness that only
ever passes is not a harness.

The module under test lives in analysis/, NOT src/. The probe image COPYs
named src/ modules one at a time, so nothing here can reach the live
container. A test below asserts that separation stays true.
"""
import importlib.util
import pathlib

import pytest

_P = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "canary_f7a3e224" / "p03_checker.py")
_spec = importlib.util.spec_from_file_location("p03_checker", _P)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def row(ex, bh, state="p25-45c|tte>10m|imb>50", idx=0):
    return {"idx": idx, "executable_residual": ex, "behavioral_residual": bh,
            "state": state}


# ---------------- RULE 1: the shape that was wrongly flagged ----------------

def test_zero_executable_with_none_behavioral_is_VALID():
    """atc-lg1-lmf-rcl decisions 0-3. No fill yet, so no behavioural
    quantity. This is the exact case the first harness got wrong."""
    c = cp.check_decision(row("0", None, "p45-55c|tte>10m|flat"))
    assert c["ok"] is True and c["material"] is False and c["violations"] == []


def test_zero_executable_with_zero_behavioral_is_VALID():
    c = cp.check_decision(row("0", 0.0, "p45-55c|tte>10m|flat"))
    assert c["ok"] is True


def test_sub_tolerance_residual_is_not_material():
    c = cp.check_decision(row("0.00005", None, "p45-55c|tte>10m|flat"))
    assert c["ok"] is True and c["material"] is False


def test_flat_bucket_is_fine_when_the_book_really_is_flat():
    assert cp.check_decision(row("0", None, "p45-55c|tte>10m|flat"))["ok"]


def test_the_inverse_defect_is_still_caught():
    """Book flat, model not. The mirror of a false-flat, and also wrong."""
    c = cp.check_decision(row("0", -50.0, "p45-55c|tte>10m|flat"))
    assert c["ok"] is False
    assert c["violations"] == ["behavioral_nonzero_with_zero_executable"]


# ---------------- RULES 2-4: material residual ----------------

def test_behavioral_none_with_material_residual_is_INVALID():
    c = cp.check_decision(row("-9.60", None))
    assert c["ok"] is False
    assert "behavioral_none_with_material_executable" in c["violations"]


def test_behavioral_zero_with_material_residual_is_INVALID():
    """THE COV-BHA DEFECT: behavioural exactly 0.0 while the book is long."""
    c = cp.check_decision(row("1.02", 0.0))
    assert c["ok"] is False
    assert "behavioral_zero_with_material_executable" in c["violations"]


@pytest.mark.parametrize("ex,bh", [("-9.60", 55.8), ("9.60", -55.8),
                                   ("-0.5", 1.0), ("0.5", -1.0)])
def test_sign_mismatch_with_material_residual_is_INVALID(ex, bh):
    c = cp.check_decision(row(ex, bh))
    assert c["ok"] is False
    assert "sign_mismatch_with_material_executable" in c["violations"]


def test_false_flat_state_with_material_residual_is_INVALID():
    c = cp.check_decision(row("-9.60", -55.8, "p25-45c|tte>10m|flat"))
    assert c["ok"] is False
    assert "false_flat_state_with_material_executable" in c["violations"]


def test_a_row_can_break_more_than_one_rule_and_names_them_all():
    c = cp.check_decision(row("1.02", 0.0, "p25-45c|tte>10m|flat"))
    assert set(c["violations"]) == {
        "behavioral_zero_with_material_executable",
        "false_flat_state_with_material_executable"}


def test_matching_signs_with_material_residual_pass():
    assert cp.check_decision(row("-9.60", -55.81395348837209))["ok"]
    assert cp.check_decision(row("8.11", 47.151162790697676))["ok"]


def test_unreadable_executable_residual_is_a_violation_not_a_pass():
    c = cp.check_decision(row("not-a-number", None))
    assert c["ok"] is False
    assert c["violations"] == ["executable_residual_unreadable"]
    assert cp.check_decision(row(None, None))["ok"] is False


# ---------------- the live corpus ----------------

def test_the_preserved_live_decisions_pass():
    rows = cp.load(_P.with_name("p03_live_verification.json"))
    out = cp.check_all(rows)
    assert out["verdict"] == "PASS", out["failing"]
    assert out["material_decisions"] == 16
    assert out["decisions"] == 20
    assert out["violations_total"] == 0


def test_check_all_reports_every_failing_row_not_just_the_first():
    out = cp.check_all([row("-9.60", None, idx=1), row("1.02", 0.0, idx=2),
                        row("0", None, idx=3)])
    assert out["verdict"] == "FAIL"
    assert {f["idx"] for f in out["failing"]} == {1, 2}


def test_tolerance_agrees_with_the_engines_own_constant():
    from src import pmus_spring_l5_session as s
    assert cp.TOL == s.BEHAVIORAL_FLAT_QTY_TOL


def test_the_harness_is_not_shipped_in_the_probe_image():
    """Not COPYed into the container, and not in src/.

    The image DOES copy frozen analysis/l5/*.json model artifacts -- the
    fidelity pass, the action model, the gate. That is intended and is why
    this test cannot simply forbid the string "analysis/". What it forbids is
    narrower and is the thing that matters: no CODE from analysis/ is shipped,
    so nothing there can execute in the live container."""
    root = pathlib.Path(__file__).resolve().parents[1]
    dockerfile = (root / "deploy" / "pmus_spring_l5" / "Dockerfile").read_text()
    assert "p03_checker" not in dockerfile
    copied = [ln.strip() for ln in dockerfile.splitlines()
              if ln.strip().startswith("COPY ") and "analysis/" in ln]
    assert copied, "expected the frozen json artifacts to still be copied"
    for ln in copied:
        assert ".py" not in ln, f"code shipped from analysis/: {ln}"
    assert not (root / "src" / "p03_checker.py").exists()


def test_no_live_module_imports_the_harness():
    root = pathlib.Path(__file__).resolve().parents[1]
    for py in (root / "src").rglob("*.py"):
        assert "p03_checker" not in py.read_text(encoding="utf-8"), py
