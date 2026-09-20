"""The position-semantics probe reads, classifies, and cannot mutate."""
import ast
import pathlib

from src import gemini_position_semantics_probe as P

SRC = pathlib.Path("src/gemini_position_semantics_probe.py")


def test_module_has_no_order_transport():
    """Read-only structurally, not by intention."""
    tree = ast.parse(SRC.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        if isinstance(node, ast.Name):
            names.add(node.id)
    for forbidden in ("place", "cancel", "submit_once", "Driver", "order_place"):
        assert forbidden not in names, forbidden
    text = SRC.read_text()
    for path in ("order.place", "order.cancel", "makerOrCancel"):
        assert path not in text, path


def test_it_satisfies_the_repo_wide_write_path_guard():
    """The guard matches on SUBSTRING, so an orders/history read would trip
    it: that path has the placement path as a prefix. This module therefore
    reads no order endpoint at all, and this pins the reason."""
    from src.gemini_execution import spec
    text = SRC.read_text()
    for marker in tuple(spec.MUTATION_PATHS) + tuple(spec.WS_MUTATION_METHODS):
        assert marker not in text, marker


def test_every_path_it_touches_is_a_read():
    for p in (P.SETTLED_PATH, P.OPEN_PATH, P.TRADES_PATH, P.TRANSFERS_PATH):
        assert "order" not in p


def test_redaction_removes_account_identity():
    got = P._redact({"accountId": 12345, "rows": [{"accountId": 1, "q": "2"}]})
    assert got == {"accountId": "REDACTED",
                   "rows": [{"accountId": "REDACTED", "q": "2"}]}


def test_two_rows_one_yes_one_no_is_model_A():
    rows = [{"outcome": "yes", "position": "0.01"},
            {"outcome": "no", "position": "-0.01"}]
    assert P.classify(rows, [])["model"] == "A"


def test_one_signed_row_is_model_B():
    rows = [{"outcome": "no", "position": "-0.01"}]
    assert P.classify(rows, [])["model"] == "B"


def test_a_cash_out_row_wins_and_is_model_C():
    """A recorded early exit names the mechanism, whatever else settled."""
    rows = [{"outcome": "no", "position": "-0.01"}]
    v = P.classify(rows, [{"instrumentSymbol": P.SYMBOL, "proceeds": "0.0086"}])
    assert v["model"] == "C"


def test_no_settled_row_is_explicitly_UNRESOLVED_between_B_and_C():
    """Absence must not be read as a finding for either one."""
    v = P.classify([], [])
    assert v["model"] == "B_OR_C"
    assert "UNRESOLVED" in v["why"]


def test_reconciliation_reports_venue_nulls_as_nulls_not_zero():
    r = P.reconcile([], [], [])
    assert r["settlement_payout_usd"] is None
    assert r["venue_net_profit_usd"] is None
    assert r["fees_usd"] is None
    # our own arithmetic over the two known legs is still reported, labelled
    assert r["gross_cash_debit_usd"] == "-0.0106"


def test_known_fills_match_the_captured_venue_order_history():
    """The anchor must not drift from the artifact it was taken from."""
    ids = {f["orderId"] for f in P.KNOWN_FILLS}
    assert ids == {"145828836246611835", "145828836246613472"}
    by_id = {f["orderId"]: f for f in P.KNOWN_FILLS}
    assert by_id["145828836246611835"]["outcome"] == "no"
    assert by_id["145828836246611835"]["price"] == "0.14"
    assert by_id["145828836246613472"]["outcome"] == "yes"
    assert by_id["145828836246613472"]["price"] == "0.92"
