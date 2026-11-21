from pathlib import Path
import os
import runpy

import pytest


def _apply_in_tmp(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "src" / "pmus_spring_l5_session.py"
    patch = repo / "deploy" / "pmus_spring_l5" / "apply_complete_reject_retry.py"
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


def test_transform_applies_once_and_generated_session_compiles(tmp_path):
    target, _ = _apply_in_tmp(tmp_path)
    text = target.read_text(encoding="utf-8")
    compile(text, str(target), "exec")
    assert text.count('"same_decision_retry"') == 1
    assert text.count('"execution_retry"') == 1
    assert text.count("_retry1") >= 2
    assert "post_only_cross_race" in text
    assert "pair_invariant_ok(\n                                    inv, deficit_up, retry_px, qty)" in text
    assert "retry_px != px" not in text
    assert "THIS SAME COMPLETE exactly once" in text


def test_transform_fails_closed_if_applied_twice(tmp_path):
    target, patch = _apply_in_tmp(tmp_path)
    old = Path.cwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(SystemExit, match="expected block exactly once; found 0"):
            runpy.run_path(str(patch), run_name="__main__")
    finally:
        os.chdir(old)
    compile(target.read_text(encoding="utf-8"), str(target), "exec")
