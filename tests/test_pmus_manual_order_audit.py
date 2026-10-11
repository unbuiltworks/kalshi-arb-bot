"""The manual-order audit must stay incapable of mutating, and must never
present a truncated walk as a history.

The image this script ships in has one property worth more than any feature:
it does not contain the code to place, cancel or amend an order. These tests
hold that line, and hold the walk contract that cost the settlement auditor
seven false "PENDING_SETTLEMENT" verdicts.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

IMAGE_FILES = (
    "scripts/pmus_readonly_recovery.py",
    "scripts/pmus_order_history_audit.py",
    "scripts/pmus_manual_order_audit.py",
    "src/pmus_auth.py",
)
MUTATING_MODULES = ("l5_pmus_transport", "pmus_q1_executor", "live_exec",
                    "gemini_execution", "l5_live_execution")


def _imports(path: str):
    for n in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            yield (getattr(n, "module", None) or "")
            for a in n.names:
                yield a.name


@pytest.mark.parametrize("path", IMAGE_FILES)
def test_no_mutating_module_is_reachable(path):
    for name in _imports(path):
        for bad in MUTATING_MODULES:
            assert bad not in (name or ""), "%s imports %s" % (path, name)


def test_the_dockerfile_ships_the_audit_and_no_mutating_module():
    df = Path("deploy/pmus_readonly_recovery/Dockerfile").read_text()
    assert "COPY scripts/pmus_manual_order_audit.py" in df
    # only the COPY lines decide what is IN the image; the comments
    # deliberately name the mutating modules in order to say they are absent.
    copied = [l for l in df.splitlines() if l.strip().startswith("COPY")]
    for bad in ("l5_pmus_transport", "pmus_q1_executor", "live_exec",
                "runner.py"):
        assert not any(bad in l for l in copied), (bad, copied)


def test_only_GET_is_issued():
    """Every request the audit makes is a GET, apart from the deliberate
    POST that PROVES the transport refuses one."""
    src = Path("scripts/pmus_manual_order_audit.py").read_text()
    tree = ast.parse(src)
    verbs = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "request":
            if n.args and isinstance(n.args[0], ast.Constant):
                verbs.append(n.args[0].value)
    assert verbs, "no transport calls found -- the test has gone stale"
    assert set(verbs) <= {"GET", "POST"}
    # the single POST must be the refusal proof, guarded by PermissionError
    assert verbs.count("POST") == 1
    assert "except PermissionError" in src
    assert '"post_blocked"' in src


def test_a_truncated_walk_is_never_called_complete():
    from scripts import pmus_manual_order_audit as m

    class T:
        """Serves one page with a cursor and then fails every cursored read,
        which is exactly the 429 shape that must not read as 'no more rows'."""

        def __init__(self):
            self.n = 0

        def request(self, method, path, body=None, base=None):
            assert method == "GET"
            self.n += 1
            if "cursor" in path or "Token" in path or "token" in path:
                raise RuntimeError("429 Too Many Requests")
            return {"activities": [{"type": "ACTIVITY_TYPE_TRADE",
                                    "createTime": "2026-10-10T00:00:00Z"}],
                    "nextCursor": "abc", "eof": False}

    m.RETRY_PAUSE_S = 0.0
    m.PAGE_PAUSE_S = 0.0
    w = m.walk_activities(T())
    assert w["complete"] is False
    assert w["ended"] == "error"
    assert w["errors"], "the failure must be reported, not swallowed"
    assert "LOWER BOUND" in w["completeness_note"]


def test_eof_is_the_only_clean_end():
    from scripts import pmus_manual_order_audit as m

    class T:
        def request(self, method, path, body=None, base=None):
            return {"activities": [{"type": "ACTIVITY_TYPE_TRADE"}],
                    "nextCursor": "", "eof": True}

    w = m.walk_activities(T())
    assert w["ended"] == "eof"
    assert w["complete"] is True


def test_maker_classification_comes_from_the_venue_not_from_price():
    from scripts import pmus_manual_order_audit as m

    o = m.slim_order({
        "id": "CQ5NPBADMYC3", "marketSlug": "s", "side": "ORDER_SIDE_BUY",
        "type": "ORDER_TYPE_LIMIT", "price": {"value": "0.29"},
        "quantity": 2.25, "cumQuantity": 2.25, "leavesQuantity": 0,
        "state": "ORDER_STATE_FILLED",
        "createTime": "2026-09-25T13:01:40.000000000Z",
        "lastTransactTime": "2026-09-25T13:01:46.847706680Z",
        "avgPx": {"value": "0.2900"},
        "commissionNotionalTotalCollected": {"value": "0.0300"},
        "participateDontInitiate": True})
    assert o["participateDontInitiate"] is True
    assert o["limit_price"] == "0.29"
    assert o["avgPx"] == "0.2900"
    assert o["commission"] == "0.0300"
    assert o["resting_s"] == pytest.approx(6.848, abs=0.01)


def test_order_ids_are_collected_from_nested_rows():
    from scripts import pmus_manual_order_audit as m

    ids = m._order_ids([
        {"type": "ACTIVITY_TYPE_TRADE", "orderId": "AAA111BBB222"},
        {"nested": {"fills": [{"makerOrderId": "CCC333DDD444"}]}},
        {"orderId": "AAA111BBB222"},
    ])
    assert ids == ["AAA111BBB222", "CCC333DDD444"]


def test_output_is_chunked_so_the_log_line_cap_cannot_eat_the_history():
    """Railway truncates one log line at 81,920 chars and returns only the
    most recent entries, so a single big blob loses its own beginning. That
    is not hypothetical: the first run came back starting mid-object."""
    import contextlib
    import io
    import json as _json

    from scripts import pmus_manual_order_audit as m

    out = {"audit": "X",
           "activities": [{"a": i, "pad": "x" * 200} for i in range(400)],
           "orders": [{"id": "O%d" % i, "slug": "s"} for i in range(300)],
           "balances": {"b": 1}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.emit(out)
    lines = buf.getvalue().strip().split("\n")
    assert lines[0].startswith("MA_HEAD ")
    assert lines[-1].startswith("MA_DONE ")
    assert all(len(l) < 81920 for l in lines), max(len(l) for l in lines)

    rows = [_json.loads(l[len("MA_ROW "):]) for l in lines
            if l.startswith("MA_ROW ")]
    # every row is independently parseable and self-locating
    assert all(("i" in r and "n" in r and "section" in r) for r in rows)
    for sect, n in (("activities", 400), ("orders", 300)):
        got = sum(len(r.get("items", [])) for r in rows if r["section"] == sect)
        assert got == n, (sect, got, n)
    head = _json.loads(lines[0][len("MA_HEAD "):])
    assert head["sections"]["activities"] == 400
    assert head["row_count"] == len(rows)
