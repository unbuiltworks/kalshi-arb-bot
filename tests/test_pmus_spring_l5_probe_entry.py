"""Fail-closed tests for the PMUS spring L5 probe deployment guard."""
from __future__ import annotations

import json

import pytest
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
        # OPEN-relevant facts (OWNER 5551236878). All REQUIRED: absent is
        # a blocker, never a skipped check.
        "both_sides_served": True,
        "bid_touch_qty": "500",
        "collector_gaps": 0,
        "market_state": {"active": True, "archived": False, "closed": False,
                         "status": "MARKET_STATUS_OPEN",
                         "endDate": "2026-09-16T09:30:00Z"},
        "probe_entry_action": {"decision": "quote", "relation": "open",
                               "side": "YES", "target_price": "0.5000"},
    }
    row.update(overrides)
    return row


# ---- completion evidence: measured and reported, never an entry bar ----

def test_completion_evidence_no_longer_blocks_an_open():
    """OWNER DECISION 5551236878. READY_FOR_REVIEW and the category bar
    are COMPLETION evidence. Requiring proof of past completions before a
    first OPEN is circular -- nothing completes if nothing may open."""
    ev = evidence(spring_repeated_completion=False, spring_matched_slots=0,
                  spring_category_bar_met=False,
                  spring_category_evidence={"completions_60m": 0,
                                            "distinct_markets_60m": 0,
                                            "markets": [], "bar_met": False},
                  probe_evidence={"status": "INSUFFICIENT_EVIDENCE",
                                  "reasons": ["insufficient_completions:0<3"],
                                  "min_completions_required": 3})
    assert entry.strict_evidence_reason("mkt", ev, now=1001.0) is None


def test_completion_evidence_is_still_measured_and_reported():
    """It ranks a candidate now; it does not gate one. Losing the
    measurement entirely would be the opposite error."""
    ev = evidence(probe_evidence={"status": "INSUFFICIENT_EVIDENCE",
                                  "reasons": ["insufficient_completions:0<3"],
                                  "min_completions_required": 3})
    assert entry.completion_evidence_reason(ev).startswith(
        "probe_evidence_not_ready:")
    assert entry.completion_evidence_reason(evidence()) is None


def test_completion_evidence_still_checks_the_raw_category_counts():
    lying = evidence(spring_category_evidence={"completions_60m": 3,
                                               "distinct_markets_60m": 1,
                                               "markets": ["mkt"],
                                               "bar_met": True})
    assert entry.completion_evidence_reason(lying) == \
        "spring_category_distinct_markets_lt_2:1"
    short = evidence(spring_category_evidence={"completions_60m": 2,
                                               "distinct_markets_60m": 2,
                                               "markets": ["mkt", "mkt2"],
                                               "bar_met": True})
    assert entry.completion_evidence_reason(short) == \
        "spring_category_completions_lt_required:2<3"
    missing = evidence()
    del missing["spring_category_evidence"]
    assert entry.completion_evidence_reason(missing) == \
        "spring_category_evidence_missing"


def test_a_fully_valid_open_payload_is_admitted():
    assert entry.strict_evidence_reason("mkt", evidence(), now=1001.0) is None


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
        # the OPEN-relevant facts the recorder must carry end to end
        "bid_touch_qty": "500",
        "collector_gaps": 0,
        "market_state": {"active": True, "archived": False, "closed": False,
                         "status": "MARKET_STATUS_OPEN",
                         "endDate": "2026-09-16T09:30:00Z"},
        "probe_entry_action": {"decision": "quote", "relation": "open",
                               "side": "YES", "target_price": "0.5000"},
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


# ---- OPEN safety facts: absent must BLOCK, never skip (OWNER 5551236878) ----

def test_every_open_safety_fact_is_required_not_optional():
    """`if present: check` lets a payload that simply omits a field walk
    past a safety gate. Each of these must fail closed when absent."""
    for field, expected in (
            ("both_sides_served", "strict_evidence_book_not_two_sided"),
            ("bid_touch_qty", "strict_evidence_touch_depth_missing"),
            ("market_state", "strict_evidence_market_state_missing"),
            ("probe_entry_action",
             "strict_evidence_probe_entry_action_missing")):
        ev = evidence()
        del ev[field]
        reason = entry.strict_evidence_reason("mkt", ev, now=1001.0)
        assert reason is not None, f"{field} absent was admitted"
        assert reason.startswith(expected), f"{field} -> {reason}"


def test_liquidity_facts_block_on_bad_values_too():
    assert entry.strict_evidence_reason(
        "mkt", evidence(both_sides_served=False), now=1001.0).startswith(
            "strict_evidence_book_not_two_sided")
    assert entry.strict_evidence_reason(
        "mkt", evidence(bid_touch_qty="0"), now=1001.0).startswith(
            "strict_evidence_no_q1_touch_depth")
    # OWNER 5552475548 #2: recorder cadence is a ranking feature, not an
    # OPEN veto -- the executor rechecks the live book/open state itself
    assert entry.strict_evidence_reason(
        "mkt", evidence(collector_gaps=3), now=1001.0) is None
    assert entry.strict_evidence_reason(
        "mkt", evidence(collector_gaps=None), now=1001.0) is None


def test_market_close_safety_uses_the_venue_authoritative_fields():
    """PMUS serves no settlement_ts for live sports, so the guard uses
    active/archived/closed/status/endDate -- verified live 2026-09-05,
    where a resolved market reports closed=True and
    status=MARKET_STATUS_RESOLVED."""
    resolved = evidence(market_state={"active": True, "archived": False,
                                      "closed": True,
                                      "status": "MARKET_STATUS_RESOLVED",
                                      "endDate": "2026-09-16T09:30:00Z"})
    assert entry.strict_evidence_reason("mkt", resolved, now=1001.0) == \
        "strict_evidence_market_closed:True"
    inactive = evidence(market_state={"active": False, "archived": False,
                                      "closed": False,
                                      "status": "MARKET_STATUS_OPEN",
                                      "endDate": "2026-09-16T09:30:00Z"})
    assert entry.strict_evidence_reason("mkt", inactive, now=1001.0) == \
        "strict_evidence_market_active:False"
    archived = evidence(market_state={"active": True, "archived": True,
                                      "closed": False,
                                      "status": "MARKET_STATUS_OPEN",
                                      "endDate": "2026-09-16T09:30:00Z"})
    assert entry.strict_evidence_reason("mkt", archived, now=1001.0) == \
        "strict_evidence_market_archived:True"


def test_each_market_state_field_is_itself_required():
    for field in ("active", "archived", "closed", "status", "endDate"):
        state = {"active": True, "archived": False, "closed": False,
                 "status": "MARKET_STATUS_OPEN",
                 "endDate": "2026-09-16T09:30:00Z"}
        del state[field]
        reason = entry.strict_evidence_reason(
            "mkt", evidence(market_state=state), now=1001.0)
        assert reason is not None and field.lower() in reason.lower(), \
            f"{field} absent -> {reason}"


def test_never_opens_into_a_market_about_to_settle():
    import datetime as _dt
    now = 1_757_000_000.0
    soon = _dt.datetime.fromtimestamp(
        now + 30.0, _dt.timezone.utc).isoformat()
    ev = evidence(market_state={"active": True, "archived": False,
                                "closed": False,
                                "status": "MARKET_STATUS_OPEN",
                                "endDate": soon})
    assert entry.strict_evidence_reason("mkt", ev, now=now).startswith(
        "strict_evidence_too_close_to_settlement")


def test_the_model_must_actually_recommend_an_open():
    """In-distribution is not enough: a state v2 has visited but would
    not open in is not an entry."""
    assert entry.strict_evidence_reason(
        "mkt", evidence(probe_entry_action={"decision": "no_action",
                                            "reason": "v2_off_distribution"}),
        now=1001.0) == "strict_evidence_model_no_open:v2_off_distribution"
    assert entry.strict_evidence_reason(
        "mkt", evidence(probe_entry_action={"decision": "quote",
                                            "relation": "complete"}),
        now=1001.0) == "strict_evidence_first_action_not_an_open:complete"


def test_pair_economics_stay_out_of_open_but_bind_a_complete():
    """<=0.98 / >=1c belong to the complement, never to the entry."""
    from src import pmus_sports_l5_decision as l5d
    inv = l5d.Inventory()
    inv.record(True, l5d.D("0.24"))
    px, econ = l5d.complement_target(inv.avg(True), l5d.D("0.80"))
    assert px == l5d.D("0.74")                     # the 0.98 cap, tighter
    assert econ["max_pair_cost"] == "0.98"
    assert l5d.hard_pair_bound() == l5d.D("0.99")
    # and the entry payload carries no pair-economics prerequisite
    assert entry.strict_evidence_reason("mkt", evidence(), now=1001.0) is None


def test_a_loosened_pair_target_is_still_refused():
    """The <=0.98 cap may not be widened by a payload claiming otherwise."""
    assert entry.strict_evidence_reason(
        "mkt", evidence(spring_pair_target="0.995"), now=1001.0) == \
        "spring_pair_target_too_loose:0.995"


# ---- recorder HTTP handoff: the probe and recorder share no volume ----

def _serve(tmp_path, text):
    from src import pmus_ws_evidence_recorder as rec
    root = tmp_path / "root"
    rec.write_status_doc(root, {"placeholder": True})
    rec.status_doc_path(root).write_text(text, encoding="utf-8")
    srv = rec.serve_status_doc(root, 0)       # 0 -> disabled by contract
    assert srv is None
    # bind an ephemeral port instead: pick one the OS hands us
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = rec.serve_status_doc(root, port)
    assert srv is not None
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/status"


def test_status_url_composes_the_same_evidence_as_the_file(tmp_path):
    now = 1000.0
    srv, url = _serve(tmp_path, json.dumps(_recorder_status(t=now - 5)) + "\n")
    try:
        ev, source = entry.evidence_from_status_path("mkt", url, now=now)
    finally:
        srv.shutdown()
    assert ev is not None
    assert source == f"status_url:{url}"
    assert ev["evidence_provenance"] == entry.PROVENANCE_AUTHENTICATED
    assert entry.strict_evidence_reason("mkt", ev, now=now) is None


def test_status_url_serves_only_the_status_document(tmp_path):
    import urllib.request
    import urllib.error
    srv, url = _serve(tmp_path, json.dumps(_recorder_status()) + "\n")
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(url.replace("/status", "/state"),
                                   timeout=5)
        assert ei.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(urllib.request.Request(
                url, data=b"x", method="POST"), timeout=5)
        assert ei.value.code in (404, 501)
    finally:
        srv.shutdown()


def test_an_unreachable_status_url_fails_closed(monkeypatch):
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, json.dumps(evidence()))
    monkeypatch.setenv(entry.EVIDENCE_STATUS_URL_ENV,
                       "http://127.0.0.1:9/status")   # discard port
    ev, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert ev is None
    assert source.startswith("status_unreadable")


def test_a_stale_document_over_http_is_still_refused(tmp_path):
    now = 1000.0
    srv, url = _serve(tmp_path,
                      json.dumps(_recorder_status(t=now - 600)) + "\n")
    try:
        ev, _ = entry.evidence_from_status_path("mkt", url, now=now)
    finally:
        srv.shutdown()
    reason = entry.strict_evidence_reason("mkt", ev, now=now)
    assert reason and reason.startswith("strict_evidence_stale")


def test_the_url_outranks_the_path_which_outranks_the_paste(monkeypatch,
                                                            tmp_path):
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, json.dumps(evidence()))
    monkeypatch.setenv(entry.EVIDENCE_STATUS_PATH_ENV,
                       str(tmp_path / "missing.jsonl"))
    monkeypatch.setenv(entry.EVIDENCE_STATUS_URL_ENV,
                       "http://127.0.0.1:9/status")
    _, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert source.startswith("status_unreadable")
    monkeypatch.delenv(entry.EVIDENCE_STATUS_URL_ENV)
    _, source = entry.resolve_strict_evidence("mkt", now=1000.0)
    assert source.startswith("status_unreadable")


def test_disarmed_boot_previews_admission_from_the_url(tmp_path,
                                                       monkeypatch):
    """The handoff must be verifiable from a DISARMED boot log: the
    report says which source it read, how old it was, and what the guard
    would decide -- while the dual key stays absent and nothing arms."""
    now = 1000.0
    srv, url = _serve(tmp_path, json.dumps(_recorder_status(t=now - 5)) + "\n")
    for k in ("PMUS_SPRING_LIVE", "PMUS_SPRING_SESSION_ID",
              "PMUS_SPRING_ARM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(entry.base.SPRING_MARKET_ENV, "mkt")
    monkeypatch.setenv(entry.EVIDENCE_STATUS_URL_ENV, url)
    try:
        report = entry.run_session(wall=lambda: now)
    finally:
        srv.shutdown()
    assert report["status"] == "DISARMED"
    pv = report["admission_preview"]
    assert pv["evidence_source"] == f"status_url:{url}"
    assert pv["evidence_age_s"] == 5.0
    assert pv["strict_evidence_reason"] is None
    assert "DISARMED" in pv["label"]


def test_disarmed_boot_without_a_source_has_no_preview(monkeypatch):
    for k in ("PMUS_SPRING_LIVE", "PMUS_SPRING_SESSION_ID",
              "PMUS_SPRING_ARM", entry.EVIDENCE_STATUS_URL_ENV,
              entry.EVIDENCE_STATUS_PATH_ENV):
        monkeypatch.delenv(k, raising=False)
    report = entry.run_session(wall=lambda: 1000.0)
    assert report["status"] == "DISARMED"
    assert "admission_preview" not in report
