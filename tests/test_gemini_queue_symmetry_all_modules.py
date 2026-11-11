"""No module may model one leg's queue more generously than the other's.

THE BUG HAD THREE COPIES. `tape_replay` fed the live shadow; `complement`
and `completion` each carried their own `_size_ahead` returning None for
every NO leg, and each then filled anyway with `ahead or D("0")`. The
completion copy even documented the opposite of what it did. These pin that
there is now ONE implementation and that an unknown queue fills nothing.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import complement as CM
from src.gemini_execution import completion as CO
from src.gemini_execution import tape_replay as TR

BOOK = {"readable": True,
        "bids": {"levels": [{"price": "0.40", "qty": "500"}]},
        "asks": {"levels": [{"price": "0.60", "qty": "500"}]}}

MODULES = (("complement", CM._size_ahead), ("completion", CO._size_ahead))


@pytest.mark.parametrize("name,fn", MODULES)
def test_EVERY_MODULE_QUEUES_BOTH_LEGS(name, fn):
    assert fn(BOOK, "YES", "0.40") == D("500"), name
    assert fn(BOOK, "NO", "0.40") == D("500"), name


@pytest.mark.parametrize("name,fn", MODULES)
def test_EVERY_MODULE_AGREES_WITH_THE_ONE_IMPLEMENTATION(name, fn):
    """Delegation, not a fourth copy that can drift."""
    for leg in ("YES", "NO"):
        for px in ("0.10", "0.40", "0.55", "0.90"):
            assert fn(BOOK, leg, px) == TR.size_ahead(BOOK, leg, px), (name,
                                                                       leg, px)


@pytest.mark.parametrize("name,fn", MODULES)
def test_UNKNOWN_STAYS_UNKNOWN_ON_BOTH_LEGS(name, fn):
    for depth in ({"readable": False}, None, {}):
        assert fn(depth, "YES", "0.40") is None, name
        assert fn(depth, "NO", "0.40") is None, name


def test_AN_UNKNOWN_QUEUE_FILLS_NOTHING():
    """`ahead or D("0")` made an unreadable queue an empty one, so every NO
    rung filled from the front of a queue that was never checked."""
    hits = [{"amount": "999"}]
    assert CO._fill_from(hits, None, D("0.01")) == D("0")
    # a KNOWN queue still fills normally once cleared
    assert CO._fill_from(hits, D("500"), D("0.01")) == D("0.01")
    assert CO._fill_from(hits, D("999"), D("0.01")) == D("0")


def _code_only(path):
    """Source with every docstring removed.

    A guard that greps raw text matches the PROSE describing the bug -- the
    fix's own explanation names `ahead or D("0")` so that a reader knows what
    went wrong. Scanning the code alone is the difference between a guard and
    a tripwire on its own documentation.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_NO_MODULE_STILL_CARRIES_THE_OLD_PATTERN():
    """A repo-wide guard: the exact idiom that caused this must not return."""
    import pathlib
    bad = []
    for path in pathlib.Path("src").rglob("*.py"):
        try:
            code = _code_only(path)
        except SyntaxError:
            continue
        for idiom in ('ahead or D("0")', "ahead or D('0')", "ahead or 0"):
            if idiom in code:
                bad.append("%s: %s" % (path, idiom))
    assert bad == [], bad


def test_THE_GUARD_ACTUALLY_CATCHES_THE_IDIOM_IN_CODE():
    """Negative control: a guard that can never fire is not a guard."""
    import ast
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "x.py"
        f.write_text('"""ahead or D(\"0\") in prose only."""\n', encoding="utf-8")
        assert 'ahead or D("0")' not in _code_only(f)   # prose ignored
        f.write_text("def f(ahead, reached):\n    return reached - (ahead or 0)\n",
                     encoding="utf-8")
        assert "ahead or 0" in _code_only(f)            # code caught


def test_NO_MODULE_STILL_RETURNS_NONE_FOR_EVERY_NO_LEG():
    """The YES-only guard is gone from the queue functions. mechanism.py
    keeps its own guard deliberately -- it reports observable=False and
    computes no fill from it, which is the honest branch, not this bug."""
    import inspect
    for fn in (TR.size_ahead, CM._size_ahead, CO._size_ahead):
        src = inspect.getsource(fn)
        assert 'if str(leg).upper() != "YES":\n        return None' not in src
