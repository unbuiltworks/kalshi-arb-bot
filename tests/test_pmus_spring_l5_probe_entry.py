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
        # admission requires OBSERVED authenticated provenance; the guard
        # reads it itself rather than trusting the readiness report to
        # have done so
        "evidence_provenance": "authenticated",
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


# ---- provenance is read by the GUARD, not just by the report ----

def test_admission_refuses_evidence_with_no_provenance():
    """Every other field can be perfect. Admission still refuses: this
    guard is what lets a live probe run, and it used to accept any
    payload whose fields looked right without reading provenance."""
    ev = evidence()
    ev.pop("evidence_provenance")
    reason = entry.strict_evidence_reason("mkt", ev, now=1000.0)
    assert reason == "strict_evidence_provenance_not_authenticated:missing"


def test_admission_refuses_inferred_provenance():
    ev = evidence(evidence_provenance="inferred")
    reason = entry.strict_evidence_reason("mkt", ev, now=1000.0)
    assert reason == "strict_evidence_provenance_not_authenticated:inferred"


def test_the_report_path_may_skip_provenance_but_admission_may_not():
    """readiness reports provenance in its own layer, so its replication
    gate asks only replication questions. Admission asks both."""
    ev = evidence(evidence_provenance="inferred")
    assert entry.strict_evidence_reason("mkt", ev, now=1000.0,
                                        require_provenance=False) is None
    assert entry.strict_evidence_reason("mkt", ev, now=1000.0) is not None


# ---- the automatic evidence feed ----

def _recorder_status(slug="mkt", t=1000.0, **row):
    base_row = {
        "slug": slug, "bid": "0.50", "trades_1h": 25,
        "two_sided_trades": True, "off_distribution": False,
        "both_sides_served": True,
        "spring_pair_target": "0.98", "spring_matched_slots": 3,
        "spring_repeated_completion": True,
        "spring_category": "mlb",
        "spring_category_evidence": {"completions_60m": 3,
                                     "distinct_markets_60m": 2,
                                     "markets": ["mkt", "mkt2"],
                                     "bar_met": True},
        "spring_category_bar_met": True,
        "spring_fee_safe_completions": 2,
        "probe_evidence": {"status": "READY_FOR_REVIEW", "reasons": [],
                           "min_completions_required": 2},
    }
    base_row.update(row)
    return {"t": t, "spring_watch": [base_row]}


def test_status_path_removes_the_human_from_the_freshness_window(tmp_path):
    """The pasted-JSON path required a human to copy a payload and
    redeploy inside its own 120s bound -- which the paste itself spent.
    Read from a status document, the evidence is as fresh as the file."""
    now = 1000.0
    p = tmp_path / "status.jsonl"
    p.write_text(json.dumps(_recorder_status(t=now - 5)) + "\n",
                 encoding="utf-8")
    ev, source = entry.evidence_from_status_path("mkt", str(p), now=now)
    assert ev is not None
    assert ev["evidence_provenance"] == entry.PROVENANCE_AUTHENTICATED
    assert str(p) in source
    assert entry.strict_evidence_reason("mkt", ev, now=now) is None


def test_a_stale_status_file_is_still_refused(tmp_path):
    """Automating the feed must not smuggle staleness past the bound."""
    now = 1000.0
    p = tmp_path / "status.jsonl"
    p.write_text(json.dumps(_recorder_status(t=now - 600)) + "\n",
                 encoding="utf-8")
    ev, _ = entry.evidence_from_status_path("mkt", str(p), now=now)
    reason = entry.strict_evidence_reason("mkt", ev, now=now)
    assert reason and reason.startswith("strict_evidence_stale")


def test_a_configured_path_is_never_backfilled_by_a_stale_paste(
        tmp_path, monkeypatch):
    """A broken feed must fail closed, not silently fall back to whatever
    an operator pasted hours ago -- that is the failure being replaced."""
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, json.dumps(evidence()))
    monkeypatch.setenv(entry.EVIDENCE_STATUS_PATH_ENV,
                       str(tmp_path / "does_not_exist.jsonl"))
    ev, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert ev is None
    assert source.startswith("status_unreadable")


def test_an_absent_slug_says_why_rather_than_just_missing(tmp_path,
                                                          monkeypatch):
    p = tmp_path / "status.jsonl"
    p.write_text(json.dumps(_recorder_status(slug="other")) + "\n",
                 encoding="utf-8")
    monkeypatch.setenv(entry.EVIDENCE_STATUS_PATH_ENV, str(p))
    ev, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert ev is None
    assert source.startswith("slug_absent:")


def test_the_pasted_path_still_works_when_no_path_is_configured(monkeypatch):
    monkeypatch.delenv(entry.EVIDENCE_STATUS_PATH_ENV, raising=False)
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, json.dumps(evidence()))
    ev, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert ev is not None
    assert source == "operator_pasted_json"


def test_evidence_age_is_reported_not_invented():
    assert entry.evidence_age_s(evidence(evidence_t=900.0), 1000.0) == 100.0
    assert entry.evidence_age_s({"slug": "mkt"}, 1000.0) is None
    assert entry.evidence_age_s(None, 1000.0) is None
