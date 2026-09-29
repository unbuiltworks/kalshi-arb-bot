"""P2: the decision shadow logs, and submits nothing."""
import ast
import pathlib
from decimal import Decimal as D

from src import l5_decision_shadow as DS

SRC = pathlib.Path("src/l5_decision_shadow.py")
BOOK = {"bid": "0.41", "ask": "0.42", "bid_qty": "100", "ask_qty": "50"}


def rest(side):
    return {"side": side, "kind": "REST"}


def _code_only(path):
    """Source with docstrings removed, so prose cannot pass or fail a scan."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_it_cannot_submit():
    src = _code_only(SRC)
    # CALL-shaped, not substring: the row key "submitted": False is the
    # PROOF that nothing was sent, and must not trip its own guard.
    for bad in ("submit(", ".submit", "submit_body", "_post(", "/v1/orders",
                "on_fill(", "mark_terminal", "release_terminal"):
        assert bad not in src, bad
    tree = ast.parse(SRC.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    for m in mods:
        for banned in ("money_path", "live_mandate", "transport"):
            assert banned not in m, m


def test_every_row_records_submitted_false():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    r = s.compare(frozen_intents=[rest("up"), rest("down")],
                  residual=D(10), matched=D(30), book=BOOK)
    assert r["submitted"] is False
    assert s.report()["submits"] is False


def test_it_records_frozen_and_candidate_side():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    s.observe_fill("up", held="up")            # we just extended
    r = s.compare(frozen_intents=[rest("up"), rest("down")],
                  residual=D(10), matched=D(30), book=BOOK)
    assert r["frozen_sides"] == ["down", "up"]
    assert r["frozen_two_sided"] is True
    assert r["candidate_side"] == "up"         # persistence -> keep going
    assert r["candidate_mode"] == "ACCUMULATE_UP"
    assert r["disagrees"] is True


def test_after_a_rebalance_the_candidate_flips():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    s.observe_fill("down", held="up")          # we rebalanced
    r = s.compare(frozen_intents=[rest("up"), rest("down")],
                  residual=D(10), matched=D(30), book=BOOK)
    assert r["candidate_mode"] == "REBALANCE"
    assert r["candidate_side"] == "down"


def test_a_balanced_book_has_no_mode_and_no_disagreement():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    r = s.compare(frozen_intents=[rest("up"), rest("down")],
                  residual=D(0), matched=D(30), book=BOOK)
    assert r["held"] is None
    assert r["candidate_side"] is None
    assert r["disagrees"] is False


def test_a_fill_on_a_balanced_book_sets_no_mode():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    s.observe_fill("up", held="up")
    s.observe_fill("down", held=None)
    r = s.compare(frozen_intents=[rest("up"), rest("down")],
                  residual=D(10), matched=D(30), book=BOOK)
    assert r["prev_extended"] is None


def test_report_counts_only_imbalanced_states():
    s = DS.DecisionShadow(clock=lambda: 1.0)
    s.compare(frozen_intents=[rest("up"), rest("down")],
              residual=D(0), matched=D(0), book=BOOK)
    s.observe_fill("up", held="up")
    s.compare(frozen_intents=[rest("up"), rest("down")],
              residual=D(10), matched=D(10), book=BOOK)
    rep = s.report()
    assert rep["decisions"] == 2 and rep["imbalanced"] == 1
    assert rep["would_narrow_to_one_side"] == 1
    assert rep["narrow_rate"] == 1.0


def test_no_disagreement_when_frozen_already_quoted_one_side():
    """If frozen only offered one side there is nothing to narrow."""
    s = DS.DecisionShadow(clock=lambda: 1.0)
    s.observe_fill("up", held="up")
    r = s.compare(frozen_intents=[rest("up")], residual=D(10),
                  matched=D(30), book=BOOK)
    assert r["disagrees"] is False


def test_the_shadow_flag_is_still_off_by_default(monkeypatch):
    from src import l5_mode_switch as SW
    monkeypatch.delenv(SW.SHADOW_ENV, raising=False)
    assert SW.shadow_enabled() is False
