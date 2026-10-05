"""The recorder's book, derived NO ladder, book state and validator."""
import json
from pathlib import Path

import pytest

from src.gemini_research import btc5m_recorder as R


def test_no_order_path_in_the_module():
    """Read-only is a CHECKED property, and the check is over the AST.

    A substring test would fail on the docstring, which legitimately says the
    module imports nothing from the execution package -- so the import graph
    is what gets asserted, and the credential/mutation strings separately.
    """
    import ast
    src = Path("src/gemini_research/btc5m_recorder.py").read_text()
    for needle in ("/order", "hmac", "X-GEMINI-APIKEY", "signature",
                   "nonce", "GEMINI_API"):
        assert needle not in src, needle
    imported = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            imported.append(getattr(n, "module", None) or "")
            imported += [a.name for a in n.names]
    for bad in ("gemini_execution", "arming", "transport", "payload",
                "sell_path"):
        assert not any(bad in (m or "") for m in imported), bad


def test_book_states():
    b = R.Book()
    assert b.state() == "EMPTY" and b.resync_pending is True
    b.seed([["0.40", "100"]], [], 1, 1)
    assert b.state() == "ONE_SIDED" and b.resync_pending is False
    b.seed([["0.40", "100"]], [["0.60", "5"]], 1, 1)
    assert b.state() == "TWO_SIDED"


def test_ladder_is_ordered_and_keeps_sizes():
    b = R.Book()
    b.seed([["0.39", "50"], ["0.40", "100"]],
           [["0.61", "7"], ["0.60", "80"]], 1, 1)
    lad = b.ladder()
    assert [p for p, _ in lad["bids"]] == ["0.4", "0.39"]
    assert [p for p, _ in lad["asks"]] == ["0.6", "0.61"]
    assert lad["bids"][0][1] == "100" and lad["asks"][0][1] == "80"


def test_zero_size_deletes_a_level():
    b = R.Book()
    b.seed([["0.40", "100"]], [["0.60", "80"]], 10, 1)
    b.apply(11, 12, [["0.40", "0"]], [], None, 2)
    assert b.ladder()["bids"] == []
    assert b.state() == "ONE_SIDED"


def test_sequence_gap_is_reported_not_repaired():
    b = R.Book()
    b.seed([["0.40", "100"]], [["0.60", "80"]], 10, 1)
    b.apply(11, 12, [], [], None, 2)
    assert b.gaps == 0 and b.resync_pending is False
    b.apply(99, 100, [], [], None, 3)
    assert b.gaps == 1 and b.resync_pending is True


def test_derived_no_ladder_is_the_complement():
    yes = {"bids": [["0.4", "100"]], "asks": [["0.6", "80"]]}
    no = R.derived_no_ladder(yes)
    assert no["asks"] == [["0.6", "100"]]     # NO ask = 1 - YES bid
    assert no["bids"] == [["0.4", "80"]]      # NO bid = 1 - YES ask


def test_checkpoint_grids_cover_the_required_ttes():
    req5 = (240, 180, 120, 90, 60, 45, 30, 20, 15, 10, 5, 2, 1)
    assert R.CHECKPOINTS["BTC05M"] == req5
    for t in req5:
        assert t in R.CHECKPOINTS["BTC15M"]
    assert R.CHECKPOINTS["BTC05M"][0] > R.CHECKPOINTS["BTC05M"][-1]


def _row(**kw):
    return json.dumps(kw) + "\n"


def test_validator_passes_only_on_a_complete_tape(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        _row(kind="window", availableAt="2026-10-05T00:00:00Z")
        + _row(kind="p_depth", E_ns=1759000000000, U=1, u=2)
        + _row(kind="index_price", raw={"p": "123"}, parsed_value="123")
        + _row(kind="checkpoint", book_state="TWO_SIDED", checkpoint_s=120,
               yes_ladder={"bids": [["0.4", "100"]], "asks": [["0.6", "9"]]}))
    v = R.validate(p)
    assert v["pass"] is True and v["missing"] == []
    assert v["book_states"] == {"TWO_SIDED": 1}


@pytest.mark.parametrize("drop", ["window", "p_depth", "index_price",
                                  "checkpoint"])
def test_validator_fails_closed_on_each_missing_requirement(tmp_path, drop):
    rows = {
        "window": _row(kind="window", availableAt="2026-10-05T00:00:00Z"),
        "p_depth": _row(kind="p_depth", E_ns=1759000000000),
        "index_price": _row(kind="index_price", raw={"p": "1"},
                            parsed_value="1"),
        "checkpoint": _row(kind="checkpoint", book_state="TWO_SIDED",
                           yes_ladder={"bids": [["0.4", "100"]],
                                       "asks": []}),
    }
    p = tmp_path / "t.jsonl"
    p.write_text("".join(v for k, v in rows.items() if k != drop))
    v = R.validate(p)
    assert v["pass"] is False and v["missing"]


def test_validator_rejects_a_ladder_with_no_sizes(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        _row(kind="window", availableAt="x")
        + _row(kind="p_depth", E_ns=1)
        + _row(kind="index_price", raw={}, parsed_value=None)
        + _row(kind="checkpoint", book_state="EMPTY",
               yes_ladder={"bids": [], "asks": []}))
    v = R.validate(p)
    assert "book_with_sizes" in v["missing"]
