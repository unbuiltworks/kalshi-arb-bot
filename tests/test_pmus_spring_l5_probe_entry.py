"""Fail-closed tests for the PMUS spring L5 probe deployment guard."""
from __future__ import annotations

import json
from pathlib import Path

from src import pmus_spring_l5_probe_entry as entry


def evidence(**overrides):
    row = {
        "slug": "mkt",
        "evidence_t": 1000.0,
        "bid": "0.50",
        "trades_1h": 25,
        "two_sided_trades": True,
        "off_distribution": False,
        "spring_pair_target": "0.98",
        "spring_matched_slots": 3,
        "spring_repeated_completion": True,
        "spring_slot_counts_theoretical": {"SPRING_MATCHED": 3},
        # category bar (OWNER DECISION 5533476555): >=2 completions from
        # >=2 distinct same-category markets in the rolling hour
        "spring_category": "mlb",
        "spring_category_evidence": {"completions_60m": 3,
                                     "distinct_markets_60m": 2,
                                     "markets": ["mkt", "mkt2"],
                                     "bar_met": True},
        "spring_category_bar_met": True,
        "probe_evidence": {"status": "READY_FOR_REVIEW", "reasons": [],
                           "min_completions_required": 3},
    }
    row.update(overrides)
    return row


def test_loose_q1_match_does_not_substitute_for_strict_readiness():
    ev = evidence(spring_repeated_completion=False, spring_matched_slots=0,
                  slot_counts_theoretical={"MATCHED": 2},
                  probe_evidence={"status": "INSUFFICIENT_EVIDENCE",
                                  "reasons": ["insufficient_completions:0<3"],
                                  "min_completions_required": 3})
    assert entry.strict_evidence_reason("mkt", ev, now=1001.0).startswith(
        "probe_evidence_not_ready:")


def test_one_or_two_strict_matches_are_blocked_at_default_three_floor():
    for n in (1, 2):
        ev = evidence(spring_matched_slots=n,
                      probe_evidence={"status": "INSUFFICIENT_EVIDENCE",
                                      "reasons": [f"insufficient_completions:{n}<3"],
                                      "min_completions_required": 3})
        assert entry.strict_evidence_reason("mkt", ev, now=1001.0).startswith(
            "probe_evidence_not_ready:")


def test_ready_category_bar_met_passes_evidence_layer():
    assert entry.strict_evidence_reason("mkt", evidence(), now=1001.0) is None


def test_ready_label_cannot_contradict_raw_category_counts():
    # a READY probe label cannot override the raw category counts
    ev = evidence(spring_category_evidence={"completions_60m": 2,
                                            "distinct_markets_60m": 2,
                                            "markets": ["mkt", "mkt2"],
                                            "bar_met": True})
    assert entry.strict_evidence_reason("mkt", ev, now=1001.0) == \
        "spring_category_completions_lt_required:2<3"


def test_isolated_market_cannot_pass_entry_guard():
    """OWNER DECISION 5533476555 item 3: category bar unmet (single
    market) fails closed, and even a bar_met=True label cannot smuggle
    an isolated market past the distinct-markets floor."""
    ev = evidence(spring_category_bar_met=False,
                  spring_category_evidence={"completions_60m": 3,
                                            "distinct_markets_60m": 1,
                                            "markets": ["mkt"],
                                            "bar_met": False})
    assert entry.strict_evidence_reason("mkt", ev, now=1001.0) == \
        "spring_category_bar_not_met"
    lying = evidence(spring_category_evidence={"completions_60m": 3,
                                               "distinct_markets_60m": 1,
                                               "markets": ["mkt"],
                                               "bar_met": True})
    assert entry.strict_evidence_reason("mkt", lying, now=1001.0) == \
        "spring_category_distinct_markets_lt_2:1"


def test_missing_category_evidence_fails_closed():
    ev = evidence()
    del ev["spring_category_evidence"]
    assert entry.strict_evidence_reason("mkt", ev, now=1001.0) == \
        "spring_category_evidence_missing"


def test_missing_stale_and_wrong_slug_fail_closed():
    assert entry.strict_evidence_reason("mkt", None, now=1001.0) == \
        "strict_evidence_missing"
    assert entry.strict_evidence_reason("mkt", evidence(slug="other"),
                                        now=1001.0) == \
        "strict_evidence_slug_mismatch"
    assert entry.strict_evidence_reason("mkt", evidence(evidence_t=1.0),
                                        now=1000.0, max_age_s=120.0).startswith(
                                            "strict_evidence_stale:")


def test_current_spring_shape_is_part_of_evidence_gate():
    assert entry.strict_evidence_reason(
        "mkt", evidence(bid="0.70"), now=1001.0).startswith(
            "strict_evidence_outside_band:")
    assert entry.strict_evidence_reason(
        "mkt", evidence(trades_1h=9), now=1001.0) == \
            "strict_evidence_thin_flow:9"
    assert entry.strict_evidence_reason(
        "mkt", evidence(two_sided_trades=False), now=1001.0) == \
            "strict_evidence_not_two_sided"
    assert entry.strict_evidence_reason(
        "mkt", evidence(off_distribution=True), now=1001.0) == \
            "strict_evidence_off_distribution_or_unknown"


def test_pair_target_cannot_be_loosened():
    assert entry.strict_evidence_reason(
        "mkt", evidence(spring_pair_target="0.99"), now=1001.0) == \
            "spring_pair_target_too_loose:0.99"


def test_ephemeral_state_is_blocked(tmp_path: Path):
    assert entry.durable_state_reason(tmp_path, mount_check=lambda _: False) == \
        f"spring_state_dir_not_persistent_mount:{tmp_path}"


def test_writable_mount_passes_durability_layer(tmp_path: Path):
    assert entry.durable_state_reason(tmp_path, mount_check=lambda _: True) is None


def test_evidence_from_status_copies_probe_and_strict_fields():
    status = {"t": "2026-09-03T13:00:00+00:00", "spring_watch": [{
        "slug": "mkt", "bid": "0.50", "trades_1h": 22,
        "two_sided_trades": True, "off_distribution": False,
        "spring_pair_target": "0.98", "spring_matched_slots": 3,
        "spring_repeated_completion": True,
        "spring_slot_counts_theoretical": {"SPRING_MATCHED": 3},
        "spring_category": "mlb",
        "spring_category_evidence": {"completions_60m": 3,
                                     "distinct_markets_60m": 2,
                                     "markets": ["mkt", "mkt2"],
                                     "bar_met": True},
        "spring_category_bar_met": True,
        "probe_evidence": {"status": "READY_FOR_REVIEW", "reasons": [],
                           "min_completions_required": 3},
    }]}
    ev = entry.evidence_from_status(status, "mkt")
    assert ev is not None
    assert ev["spring_repeated_completion"] is True
    assert ev["spring_matched_slots"] == 3
    assert ev["spring_category"] == "mlb"
    assert ev["spring_category_bar_met"] is True
    assert ev["spring_category_evidence"]["distinct_markets_60m"] == 2
    assert ev["probe_evidence"]["status"] == "READY_FOR_REVIEW"
    assert ev["evidence_t"] == status["t"]


def test_disarmed_path_delegates_without_requiring_evidence(monkeypatch):
    monkeypatch.setattr(entry.base, "armed", lambda: False)
    sentinel = {"status": "DISARMED", "realized_pnl": None}
    monkeypatch.setattr(entry.base, "run_session", lambda **_: sentinel)
    assert entry.run_session() is sentinel


def test_armed_missing_strict_evidence_blocks_before_durability(monkeypatch):
    monkeypatch.setattr(entry.base, "armed", lambda: True)
    monkeypatch.setenv(entry.base.SPRING_MARKET_ENV, "mkt")
    monkeypatch.delenv(entry.STRICT_EVIDENCE_ENV, raising=False)
    called = {"durable": False, "base": False}
    monkeypatch.setattr(entry, "durable_state_reason",
                        lambda *a, **k: called.__setitem__("durable", True))
    monkeypatch.setattr(entry.base, "run_session",
                        lambda **_: called.__setitem__("base", True))
    report = entry.run_session(wall=lambda: 1001.0)
    assert report["status"] == "STRICT_GATE_NOT_CLEARED"
    assert report["reason"] == "strict_evidence_missing"
    assert called == {"durable": False, "base": False}


def test_armed_valid_evidence_but_ephemeral_state_blocks_before_base(monkeypatch):
    monkeypatch.setattr(entry.base, "armed", lambda: True)
    monkeypatch.setenv(entry.base.SPRING_MARKET_ENV, "mkt")
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, json.dumps(evidence()))
    monkeypatch.setattr(entry, "durable_state_reason",
                        lambda *a, **k: "spring_state_dir_not_persistent_mount:/state/pmus-spring")
    called = {"base": False}
    monkeypatch.setattr(entry.base, "run_session",
                        lambda **_: called.__setitem__("base", True))
    report = entry.run_session(wall=lambda: 1001.0)
    assert report["status"] == "DURABLE_STATE_REQUIRED"
    assert called["base"] is False
