"""The P0: a fill total that is not split by side cannot answer the question."""
import io
import json

import pytest

from analysis.l5.gemini_surface import report as R


def _tape(tmp_path, rows):
    p = tmp_path / "gemini_surface_20260917T210000Z.jsonl"
    io.open(p, "w").write("".join(json.dumps(r) + "\n" for r in rows))
    return str(tmp_path)


def _fill(side, t, surface="touch_following", px="0.50"):
    return {"kind": "evidenced_fill", "surface": surface, "side": side,
            "px": px, "qty": "1", "evidence": R.STRICT, "t": t}


def test_no_tape_says_so():
    assert R.report("/definitely/not/here")["ok"] is False


def test_one_sided_fills_are_named_as_such_not_reported_as_a_total(tmp_path):
    """17 fills, all BUY, is a DIFFERENT finding from 17 fills across both
    sides -- and an unsplit total cannot tell them apart."""
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]}]
    rows += [_fill("BUY", 100 + i) for i in range(17)]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["evidenced_fills"] == 17
    assert rep["fills_BUY"] == 17 and rep["fills_SELL"] == 0
    assert rep["two_sided"] is False
    assert rep["verdict"] == "BUY_ONLY_FILLS"
    assert rep["recycling"]["p_opposite_within_300s"] == 0.0


def test_two_sided_fills_are_distinguished(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["touch_following"]},
            _fill("BUY", 100), _fill("SELL", 110), _fill("BUY", 500)]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["two_sided"] is True and rep["verdict"] == "TWO_SIDED_FILLS"
    assert rep["fills_BUY"] == 2 and rep["fills_SELL"] == 1
    assert rep["recycling"]["n_opposite_within_15s"] == 1
    # BUY@100 -> SELL@110 is inside every mark from 15s up; SELL@110 -> BUY@500
    # is 390s and therefore outside 300s, so only ONE fill has an opposite
    assert rep["recycling"]["n_opposite_within_300s"] == 1


def test_fills_are_attributed_per_surface(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["a", "b"]},
            _fill("BUY", 1, surface="a"), _fill("SELL", 2, surface="a"),
            _fill("BUY", 3, surface="b")]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["by_surface"]["a"] == {"BUY": 1, "SELL": 1}
    assert rep["by_surface"]["b"]["BUY"] == 1


def test_no_fills_is_its_own_verdict(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["a"]},
            {"kind": "observation", "rungs": {"RESTABLE": 10,
                                              "book_reached_quote": 4,
                                              "trade_flow_through_quote": 1,
                                              R.STRICT: 0}}]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["verdict"] == "NO_EVIDENCED_FILLS"
    assert rep["rung_ladder"]["RESTABLE"] == 10
    assert rep["rung_ladder"]["trade_flow_through_quote"] == 1
    assert rep["recycling"]["n_fills"] == 0
    assert rep["recycling"]["p_opposite_within_30s"] is None


def test_recycling_needs_both_legs_evidenced(tmp_path):
    """A resting opposite quote is not an interaction; only a fill is."""
    r = R.recycling([_fill("BUY", 100), _fill("BUY", 101)])
    assert r["n_opposite_within_300s"] == 0
    assert "not an interaction" in r["labels"]


def test_the_labels_state_what_the_split_is_for(tmp_path):
    rows = [{"kind": "session_start", "surfaces": ["a"]}, _fill("BUY", 1)]
    joined = " ".join(R.report(_tape(tmp_path, rows))["labels"])
    assert "broken complement" in joined
    assert "queue position is" in joined
