"""No money-path module may reference a name that does not exist.

Python resolves a name a function never assigns as an IMPLICIT GLOBAL,
so `reconcile_pending(ex, t)` inside `main` compiles, imports, and
passes `ast.parse` -- then raises when the line finally runs. In a loop
entered once every five minutes that is minutes after launch.

Three such faults occurred in one piece of work. Two survived a green
suite because no test executed the persistent branch, and one survived a
preflight whose "call chain" check matched the exact broken line as a
string. This test asks the compiler's own symbol tables.
"""
from __future__ import annotations

import os

import pytest

from src import l5_undefined_names as UN

MODULES = [
    "src/l5_btc5m_runner.py",
    "src/l5_btc5m_execution.py",
    "src/l5_btc5m_controller.py",
    "src/l5_persistent_mode.py",
    "src/l5_shared_reservation.py",
    "src/l5_late_favorite.py",
    "src/l5_strategy_arbiter.py",
    "src/l5_straddle_ev.py",
    "src/l5_series_registry.py",
    "src/l5_5m_fleet.py",
]


@pytest.mark.parametrize("path", MODULES)
def test_no_undefined_names(path):
    assert os.path.exists(path), path
    got = UN.undefined(path)
    assert got == [], "\n".join(
        "%s  %s  name=%r  near line %d" % (path, w, n, l) for w, n, l in got)


class TestTheCheckerItself:
    """A checker that cannot fail is not a check."""

    def test_it_finds_an_implicit_global_that_does_not_exist(self, tmp_path):
        p = tmp_path / "bad.py"
        p.write_text("def f():\n    return missing_thing\n", encoding="utf-8")
        got = UN.undefined(str(p))
        assert [g[1] for g in got] == ["missing_thing"]

    def test_it_finds_one_nested_two_deep(self, tmp_path):
        """Exactly the shape that bit: a name used in an outer function
        that only an inner function ever assigns."""
        p = tmp_path / "nested.py"
        p.write_text(
            "def outer():\n"
            "    def inner():\n"
            "        t = 1\n"
            "        return t\n"
            "    inner()\n"
            "    return helper(t)\n"
            "def helper(x):\n"
            "    return x\n", encoding="utf-8")
        got = UN.undefined(str(p))
        assert [g[1] for g in got] == ["t"]

    def test_a_module_level_name_is_not_undefined(self, tmp_path):
        p = tmp_path / "ok.py"
        p.write_text("THING = 1\ndef f():\n    return THING\n",
                     encoding="utf-8")
        assert UN.undefined(str(p)) == []

    def test_a_closure_over_an_enclosing_local_is_not_undefined(self, tmp_path):
        p = tmp_path / "closure.py"
        p.write_text(
            "def outer():\n"
            "    val = 1\n"
            "    def inner():\n"
            "        return val\n"
            "    return inner()\n", encoding="utf-8")
        assert UN.undefined(str(p)) == []

    def test_builtins_and_imports_are_not_undefined(self, tmp_path):
        p = tmp_path / "bi.py"
        p.write_text("import os\ndef f():\n    return len(os.sep)\n",
                     encoding="utf-8")
        assert UN.undefined(str(p)) == []
