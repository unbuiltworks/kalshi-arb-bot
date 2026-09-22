"""The augmented collector must not be able to touch the sealed holdout.

`analysis/gemini/forward/` is the 09-23-onward holdout. A collector that reads
it could leak it into development; one that writes into it would change what
the holdout contains. Neither is allowed, and neither is left to discipline.
"""
import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUG = os.path.join(ROOT, "analysis", "gemini", "p10f_forward_aug.py")


def test_the_augmented_collector_refuses_to_write_into_the_holdout():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "analysis", "gemini"))
    import p10f_forward_aug as A
    with pytest.raises(SystemExit):
        A._guard(os.path.join(A.PROTECTED, "2026-09-25.json"))
    with pytest.raises(SystemExit):
        A._guard(A.PROTECTED)
    ok = A._guard(os.path.join(A.OUT, "2026-09-25.jsonl"))
    assert ok.endswith("forward_aug/2026-09-25.jsonl")


def test_the_augmented_collector_writes_somewhere_else_entirely():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "analysis", "gemini"))
    import p10f_forward_aug as A
    assert os.path.abspath(A.OUT) != os.path.abspath(A.PROTECTED)
    assert not os.path.abspath(A.OUT).startswith(os.path.abspath(A.PROTECTED) + os.sep)


def test_no_module_in_the_p10_capture_path_opens_the_holdout():
    """A read is as disqualifying as a write: the holdout must not be seen."""
    for name in ("p10a_capture.py", "p10b_decode.py", "p10c_mirror.py",
                 "p10e_audit_decode.py", "p10f_forward_aug.py"):
        path = os.path.join(ROOT, "analysis", "gemini", name)
        if not os.path.exists(path):
            continue
        src = open(path).read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                # the guard's own constant is the one legitimate mention
                if "forward" in v and "forward_aug" not in v:
                    assert name == "p10f_forward_aug.py", (name, v)
        assert "load_forward" not in src, name
