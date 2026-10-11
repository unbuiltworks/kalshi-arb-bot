"""Regressions for the two defects the 2026-10-11 BTC15m audit found.

1. The recorder inherited `btc5m_recorder.SUBSCRIBE_LEAD_S = 420.0`, sized for
   a 300 s window, and applied it to a 900 s one. Result: streams opened at
   TTE 420 and the opening 58% of every window was never recorded, while the
   tape still looked healthy and `pass: true`.
2. `btc15m_shadow_policy` read `settled["resolution_side"]`, which the
   two-sided recorder's schema does not carry, and then emitted no settlement
   block -- so a traded window reported blank columns and the cohort totalled
   0, indistinguishable from a flat result.

Both must now fail CLOSED.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.gemini_research import btc15m_recorder as rec
from src.gemini_research import btc5m_recorder as rec5


# --------------------------------------------------------------- defect 1
def test_lead_covers_the_whole_window_plus_margin():
    assert rec.SUBSCRIBE_LEAD_S >= rec.WINDOW_S, (
        "a lead shorter than the window cannot record the window's opening")
    assert rec.SUBSCRIBE_LEAD_S >= 960.0
    assert rec.COVERAGE_MIN_TTE_S <= rec.SUBSCRIBE_LEAD_S


def test_the_15m_recorder_does_not_inherit_the_5m_lead():
    assert rec.SUBSCRIBE_LEAD_S != rec5.SUBSCRIBE_LEAD_S
    assert rec5.SUBSCRIBE_LEAD_S == 420.0, (
        "the 5m value is deliberately unchanged; only the 15m caller overrides")


def test_book_task_takes_an_explicit_lead_and_the_15m_caller_passes_it():
    sig = inspect.signature(rec5.book_task)
    assert "subscribe_lead_s" in sig.parameters
    src = Path(rec.__file__).read_text()
    assert "subscribe_lead_s=SUBSCRIBE_LEAD_S" in src, (
        "book_task must be given the 15m lead explicitly, not left to inherit")


def _tape(tmp_path: Path, max_tte: float, sub_tte: float) -> Path:
    """One market, expiry at t=10000, rows placed at a chosen TTE."""
    expiry = 10000.0
    m = "GEMI-BTC15M2610060030-UP"
    rows = [
        {"t_ns": 1, "kind": "recorder_start"},
        {"t_ns": int((expiry - sub_tte) * 1e9), "kind": "subscribe",
         "markets": [m], "subscribe_lead_s": rec.SUBSCRIBE_LEAD_S,
         "tte_s": {m: sub_tte}},
        {"t_ns": int((expiry - max_tte) * 1e9), "kind": "p_depth",
         "market": m, "E_ns": 123, "b": [["0.50", "10"]], "a": []},
        {"t_ns": int((expiry - max_tte) * 1e9), "kind": "p_trade",
         "market": m, "price": "0.50", "qty": "1", "buyer_is_maker": True},
        {"t_ns": int((expiry - max_tte) * 1e9), "kind": "pair_snapshot",
         "market": m, "duration": "BTC15M", "expiry_t": expiry,
         "book_state": "TWO_SIDED",
         "up_bid": ["0.50", "10"], "up_ask": ["0.60", "10"],
         "down_bid": ["0.40", "10"], "down_ask": ["0.50", "10"],
         "single_leg_spread": 0.10,
         "pair_cost_up_ask_plus_down_ask": 1.10},
        {"t_ns": 2, "kind": "window_poll", "market": m, "duration": "BTC15M",
         "strike": "85840.28"},
        {"t_ns": 3, "kind": "down_probe", "phase": "reply", "raw": {}},
    ]
    p = tmp_path / "seg.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_validator_fails_closed_when_the_early_window_is_missing(tmp_path):
    v = rec.validate(_tape(tmp_path, max_tte=419.9, sub_tte=419.9))
    assert v["pass"] is False
    assert "early_window_streamed" in v["missing"]
    assert "subscribe_lead_verified" in v["missing"]
    assert v["coverage"]["max_streamed_tte_s"] == pytest.approx(419.9, abs=0.2)
    assert v["coverage"]["policy_band_fraction_observed"] < 0.5


def test_validator_passes_when_the_window_opening_is_received(tmp_path):
    v = rec.validate(_tape(tmp_path, max_tte=905.0, sub_tte=958.0))
    assert v["missing"] == [], v["missing"]
    assert v["pass"] is True
    cov = v["coverage"]
    assert cov["policy_band_fraction_observed"] == pytest.approx(1.0)
    assert cov["markets_streamed_from_above_min_tte"] == 1
    assert cov["subscribe_lead_s_observed_on_tape"] == [rec.SUBSCRIBE_LEAD_S]


def test_subscribing_early_is_not_enough_on_its_own(tmp_path):
    """The lead can be right while the data never arrives. Both are checked."""
    v = rec.validate(_tape(tmp_path, max_tte=400.0, sub_tte=958.0))
    assert v["checks"]["subscribe_lead_verified"] is True
    assert v["checks"]["early_window_streamed"] is False
    assert v["pass"] is False


# --------------------------------------------------------------- defect 2
def test_the_fix_stays_out_of_the_fingerprinted_policy_file():
    """The CI15M live-probe authorization is granted against these bytes.

    `test_ci15m_probe.py` asserts the fingerprint still matches the grant.
    This asserts the narrower thing the fix must respect: the settlement
    bridge is NOT inside the file the grant is hashed over.
    """
    from src.gemini_execution import ci15m_policy as pol

    src = Path(pol.POLICY_PATH).read_text()
    assert "settlement_bridge" not in src, (
        "editing the fingerprinted policy silently invalidates the owner's "
        "live-probe authorization; the bridge belongs upstream of it")
    assert "_sb" not in src


def test_missing_settlement_is_INCOMPLETE_and_never_zero(tmp_path):
    from analysis.gemini.info_edge import btc15m_shadow_eval as ev

    # a tape with a book but NO settlement evidence at all
    expiry = 10000.0
    m = "GEMI-BTC15M2610060030-UP"
    rows = [
        {"t_ns": 2, "kind": "window", "market": m, "duration": "BTC15M",
         "strike": "85840.28", "expiry": "2026-10-06T00:30:00.000Z"},
        {"t_ns": int((expiry - 500) * 1e9), "kind": "p_snapshot", "market": m,
         "lastUpdateId": 1, "bids": [["0.50", "10"]], "asks": [["0.60", "10"]]},
    ]
    d = tmp_path / "tape"
    d.mkdir()
    (d / "ie_01.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))

    r = ev.evaluate(str(d))
    assert r["windows_incomplete"] >= 1
    assert r["windows_resolved"] == 0
    bad = r["per_window"]["EXTEND"][0]["settlement"]
    assert bad["status"].startswith("INCOMPLETE")
    for k in ("outcome", "matched_pair_pnl", "directional_residual_pnl",
              "net_settlement_pnl"):
        assert bad[k] is None, (
            "%s must be None, not 0 and not absent: a zero here is a false "
            "claim about a position that was really taken" % k)
    assert "EXCLUDED" in bad["labels"]
    assert r["arms"]["EXTEND"]["net_settlement_pnl"] == "0"
    assert r["arms"]["EXTEND"]["settled_windows"] == 0, (
        "an unresolved window must not be counted as a settled one")


def test_the_bridge_is_in_the_evaluation_path_not_a_manual_pre_step():
    from analysis.gemini.info_edge import btc15m_shadow_eval as ev

    src = inspect.getsource(ev.bridged_windows)
    assert "sb.table" in src
    assert "windows_from_tape" in src, (
        "the eval path must call the frozen replay, not restate it")
    # and it must refuse rather than silently report zeros
    assert "Refusing to" in src
