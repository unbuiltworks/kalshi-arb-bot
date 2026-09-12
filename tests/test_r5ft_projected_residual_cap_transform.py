from pathlib import Path
import os
import runpy

import pytest


def _apply(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "src" / "pmus_spring_l5_session.py"
    patch = repo / "deploy" / "pmus_spring_l5" / "apply_projected_residual_cap.py"
    target = tmp_path / "src" / "pmus_spring_l5_session.py"
    target.parent.mkdir(parents=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    old = Path.cwd()
    os.chdir(tmp_path)
    try:
        runpy.run_path(str(patch), run_name="__main__")
    finally:
        os.chdir(old)
    return target, patch


def test_projected_cap_is_checked_before_extend_submission(tmp_path):
    target, _ = _apply(tmp_path)
    text = target.read_text(encoding="utf-8")
    compile(text, str(target), "exec")
    assert text.count("Hard PROJECTED imbalance bound") == 1
    assert 'float(spec["qty"] / inv.unit)' in text
    assert '> max_residual_units())):' in text
    assert 'f"residual_cap:{abs(inv.behavioral_residual())}"' in text


def test_projected_cap_transform_fails_closed_if_applied_twice(tmp_path):
    target, patch = _apply(tmp_path)
    old = Path.cwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(SystemExit, match="expected block exactly once; found 0"):
            runpy.run_path(str(patch), run_name="__main__")
    finally:
        os.chdir(old)
    compile(target.read_text(encoding="utf-8"), str(target), "exec")
