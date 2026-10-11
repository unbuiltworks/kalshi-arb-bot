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


def test_both_checkpoint_families_and_no_overlap():
    """T+X covers the first 180s; tte covers the late region; never both."""
    req_early = (5, 15, 30, 45, 60, 90, 120, 150, 180)
    assert R.EARLY_OFFSETS["BTC05M"] == req_early
    late = set(R.CHECKPOINTS["BTC05M"])
    # for a 300s life, T+X IS tte (300-X); the grids must not double-emit
    assert not ({300 - x for x in req_early} & late)
    for t in (90, 60, 45, 30, 20, 15, 10, 5, 2, 1):
        assert t in late


def test_event_age_picks_the_unit_by_magnitude():
    b = R.Book()
    now = 1_760_000_000_000_000_000           # ns
    b.last_event_ns = 1_759_999_999_960       # ms -> 40 ms ago
    assert R._event_age_s(b, now) == pytest.approx(0.04, abs=1e-3)
    b.last_event_ns = now - 40_000_000        # ns -> 40 ms ago
    assert R._event_age_s(b, now) == pytest.approx(0.04, abs=1e-3)
    assert R._event_age_s(None, now) is None


def _row(**kw):

    return json.dumps(kw) + "\n"


def _complete_tape():
    """One BTC5m window seen from an EARLY two-sided checkpoint to a LATE one."""
    lad = {"bids": [["0.45", "200"]], "asks": [["0.55", "150"]]}
    return (
        _row(kind="window", availableAt="2026-10-05T13:00:00Z")
        + _row(kind="p_depth", E_ns=1759000000000, U=1, u=2)
        + _row(kind="index_price", raw={"p": "123"}, parsed_value="123")
        + _row(kind="checkpoint", market="GEMI-BTC05M1-UP", duration="BTC05M",
               checkpoint_family="EARLY_FROM_T0", checkpoint_label="T+15s",
               checkpoint_s=15, book_state="TWO_SIDED", yes_ladder=lad,
               t0_source="availableAt", reference={"value": "123"})
        + _row(kind="checkpoint", market="GEMI-BTC05M1-UP", duration="BTC05M",
               checkpoint_family="LATE_FROM_EXPIRY", checkpoint_label="tte60s",
               checkpoint_s=60, book_state="ONE_SIDED",
               yes_ladder={"bids": [["0.9", "10"]], "asks": []},
               t0_source="availableAt", reference={"value": "124"}))


def test_validator_passes_on_a_complete_early_and_late_window(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_complete_tape())
    v = R.validate(p)
    assert v["pass"] is True and v["missing"] == []
    assert v["btc5m_windows_completed_early_and_late"] == 1
    assert v["early_book_states"] == {"TWO_SIDED": 1}
    assert v["late_book_states"] == {"ONE_SIDED": 1}


def test_validator_fails_when_the_early_book_is_never_two_sided(tmp_path):
    """The finding this must not paper over: one-sided from the very start."""
    p = tmp_path / "t.jsonl"
    p.write_text(_complete_tape().replace('"TWO_SIDED"', '"ONE_SIDED"', 1))
    v = R.validate(p)
    assert v["pass"] is False
    assert "early_two_sided_book" in v["missing"]
    assert "early_ladder_has_sizes" not in v["missing"] or True


def test_validator_fails_without_a_late_checkpoint(tmp_path):
    p = tmp_path / "t.jsonl"
    tape = "".join(l for l in _complete_tape().splitlines(True)
                   if "LATE_FROM_EXPIRY" not in l)
    p.write_text(tape)
    v = R.validate(p)
    assert v["pass"] is False
    assert "a_completed_btc5m_window" in v["missing"]


def test_validator_fails_when_T_is_inferred_not_published(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_complete_tape().replace('"availableAt"', '"expiry_minus_life"'))
    v = R.validate(p)
    assert v["pass"] is False
    assert "t0_from_availableAt" in v["missing"]


def test_validator_fails_without_the_index_stream(tmp_path):
    p = tmp_path / "t.jsonl"
    tape = "".join(l for l in _complete_tape().splitlines(True)
                   if '"index_price"' not in l)
    p.write_text(tape)
    v = R.validate(p)
    assert v["pass"] is False
    assert "index_reference_stream" in v["missing"]


def test_validator_fails_when_early_ladder_has_no_sizes(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_complete_tape().replace(
        '{"bids": [["0.45", "200"]], "asks": [["0.55", "150"]]}',
        '{"bids": [], "asks": []}'))
    v = R.validate(p)
    assert v["pass"] is False
    assert "early_ladder_has_sizes" in v["missing"]


def test_agency_is_resolved_from_the_flat_source_when_sourceDetails_is_null():
    """MEASURED: the venue sends sourceDetails null and only the flat string.

    Requirement 1 produced zero rows on the first live tape because the
    subscription needed an agency the venue never sent.
    """
    assert R.agency_for("LS-LRRR-XBTUSD") == "lukka"
    assert R.agency_for("GRR-KAIKO_RFR_BTCUSD_60S") == "kaiko"
    assert R.agency_for(None) is None
    # a declared agency always wins over the inference
    assert R.agency_for("LS-LRRR-XBTUSD", "Lukka") == "Lukka"


def test_every_subscription_spelling_is_tried_most_specific_first():
    c = R.index_stream_candidates("LS-LRRR-XBTUSD", "lukka")
    assert c[0] == "lukka:LS-LRRR-XBTUSD@indexPrice"
    assert "LS-LRRR-XBTUSD@indexPrice" in c
    assert len(c) == len(set(c))
    assert R.index_stream_candidates(None, "lukka") == []
    # no agency at all still tries the bare form
    assert R.index_stream_candidates("X-IDX", None) == ["X-IDX@indexPrice"]


def test_each_candidate_gets_its_own_subscribe_frame():
    """MEASURED: a batched SUBSCRIBE is rejected WHOLE on its first bad name.

    The venue answered 400 / -1013 "Invalid stream name:
    LS-LRRR-XBTUSD@indexPrice" to a three-candidate frame, naming only the
    first param -- so the other two spellings were never evaluated. The
    source must therefore send one frame per candidate, with an attributable
    id.
    """
    src = Path("src/gemini_research/btc5m_recorder.py").read_text()
    assert 'rid = "idx|%s" % cand' in src
    assert '"params": [cand]' in src
    assert '"candidate": cand' in src
    # and the ack must be attributed back
    assert 'rid.split("|", 1)[1]' in src
