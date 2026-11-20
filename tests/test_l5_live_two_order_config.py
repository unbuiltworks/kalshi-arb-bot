"""Executable regression for the live two-order pilot's fail-closed slot limit.

This deliberately extracts the actual production function without importing the
networked runner. It tests the production implementation, not a duplicate.
"""
import ast
import os
from pathlib import Path
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / "src" / "l5_pmus_live_runner.py"


def _production_max_live_orders():
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RUNNER))
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "max_live_orders"
    ]
    assert len(functions) == 1, "production max_live_orders function missing"
    module = ast.Module(body=[functions[0]], type_ignores=[])
    ns = {"os": os}
    exec(compile(ast.fix_missing_locations(module), str(RUNNER), "exec"), ns)
    return ns["max_live_orders"]


def test_two_sided_live_slot_limit_can_admit_two_orders():
    with patch.dict(os.environ, {"L5_MAX_LIVE_ORDERS": "2"}):
        assert _production_max_live_orders()() == 2


def test_live_slot_limit_defaults_to_one_and_fails_closed_on_bad_values():
    fn = _production_max_live_orders()
    with patch.dict(os.environ, {}, clear=True):
        assert fn() == 1
    for value in ("", "nonsense", "0", "-2"):
        with patch.dict(os.environ, {"L5_MAX_LIVE_ORDERS": value}):
            assert fn() == 1


def test_live_slot_limit_never_silently_increases_on_unreadable_input():
    fn = _production_max_live_orders()
    for value in ("2.0", "nan", "inf"):
        with patch.dict(os.environ, {"L5_MAX_LIVE_ORDERS": value}):
            assert fn() == 1
