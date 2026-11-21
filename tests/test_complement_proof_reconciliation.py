"""LIVE 11:26Z (post-recovery): the campaign sat in
CAMPAIGN_WAITING:COMPLEMENT_UNPROVEN with four admitted markets because the
complement-acceptance proof on disk was the 03:54Z "no_legal_maker_price"
write -- a record f465fbc9 stopped persisting, left there by the build
before it -- and the proof can only be re-proven on a HELD market's resume
path (nothing was held). A record the current rule never writes is
re-derived from the durable ledger under the current rule; no evidence =
still not proven (fail closed). No cap, gate or threshold changes."""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import pmus_micro_executor as micro
from src import pmus_spring_l5_session as spring

q1 = micro.q1


def _state(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))


def _proof_file(tmp_path, doc):
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).write_text(json.dumps(doc))


def _ledger(tmp_path, reports):
    with (tmp_path / "ledger.jsonl").open("w") as f:
        for i, orders in enumerate(reports):
            f.write(json.dumps({"t": f"2026-09-06T0{i}:00:00+00:00",
                                "kind": "session_report", "spring": True,
                                "slug": "aec-mlb-wsh-lad-2026-09-05",
                                "session_id": f"s{i}", "orders": orders,
                                "decisions": []}) + "\n")


RETIRED_NO_LEGAL = {
    "proven": False, "slug": "aec-mlb-wsh-lad-2026-09-05",
    "attempted_utc": "2026-09-06T03:54:46+00:00",
    "residual": "-0.03",
    "plan": {"px": None, "regime": "no_legal_maker_price"},
    "book": {"yes_bid": "0.0500", "yes_ask": "0.0600"},
    "reason": "no_fee_safe_complement:no_legal_maker_price"}

RESTING_PROBE = {"stage": "complement_acceptance", "order_id": "CAN500DWJPQ1",
                 "side": "NO", "px": "0.615", "qty": "0.01",
                 "filled_qty": "0", "relation": "complete",
                 "end_reason": "resting:ORDER_STATE_NEW"}

# LIVE 03:22:56Z: YES 0.225 rejected, fresh ask 0.230 = ONE tick (0.005)
ONE_TICK_REJECTION = {"stage": "spring_complete_1", "order_id": "CAP5E358WPQ1",
                      "side": "YES", "px": "0.225", "qty": "0.02",
                      "filled_qty": "0", "relation": "complete",
                      "end_reason": "rejected:ORDER_REJECTED_ZERO_FILL",
                      "rejection_class": "implementation bug candidate:"
                                         "rejected_at_non_crossing_price",
                      "fresh_opp_ask": "0.2300"}


def test_retired_no_legal_price_record_is_rederived_from_ledger_evidence(
        monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    _ledger(tmp_path, [[RESTING_PROBE],
                       [ONE_TICK_REJECTION, ONE_TICK_REJECTION]])
    assert spring.retired_rule_write(RETIRED_NO_LEGAL) == \
        "no_legal_price_write_retired_by_f465fbc9"
    assert spring.complement_acceptance_proven()
    proof = spring.load_complement_proof()
    assert proof["proven"] is True
    assert proof["reconciled_from"] == "ledger"
    assert proof["order_id"] == "CAN500DWJPQ1"
    assert proof["superseded_record"]["reason"] == \
        "no_fee_safe_complement:no_legal_maker_price"
    # the 1-tick rejections after the proof are crosses under the current
    # rule (f465fbc9), not revocations: only the resting probe is an event
    assert proof["ledger_evidence"]["events"] == 1
    # persisted once, printed for the audit trail, then read back as is
    out = capsys.readouterr().out
    assert out.count("PMUS_COMPLEMENT_ACCEPTANCE") == 1
    assert "reconciled_from" in out
    again = spring.load_complement_proof()
    assert again == proof
    assert capsys.readouterr().out == ""


def test_two_tick_rejection_after_the_proof_keeps_it_revoked(
        monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    two_ticks = {**ONE_TICK_REJECTION, "fresh_opp_ask": "0.2350"}
    _ledger(tmp_path, [[RESTING_PROBE], [two_ticks]])
    assert not spring.complement_acceptance_proven()
    proof = spring.load_complement_proof()
    assert proof["reason"] == "ledger:complete_rejected_at_fresh_non_crossing_price"
    assert D(proof["ledger_evidence"]["last_event"]["margin_ticks"]) == 2
    assert proof["ledger_evidence"]["last_proven_event"]["order_id"] == \
        "CAN500DWJPQ1"


def test_rejection_without_a_fresh_ask_revokes_fail_closed(
        monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    blind = {k: v for k, v in ONE_TICK_REJECTION.items()
             if k not in ("fresh_opp_ask", "rejection_class")}
    _ledger(tmp_path, [[RESTING_PROBE, blind]])
    assert not spring.complement_acceptance_proven()


def test_recorded_margin_and_tick_take_precedence_over_the_legacy_tick(
        monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    # a 0.01-tick market: 0.22 vs 0.23 is ONE tick when the tick is
    # recorded, TWO under the fail-closed legacy tick
    row = {**ONE_TICK_REJECTION, "px": "0.22", "fresh_opp_ask": "0.23"}
    assert spring.rejection_margin_ticks(row) == D("2")
    assert spring.rejection_margin_ticks({**row, "tick": "0.01"}) == D("1")
    assert spring.rejection_margin_ticks(
        {**row, "rejection_margin_ticks": "1"}) == D("1")
    assert spring.rejection_margin_ticks({"px": "0.22"}) is None


def test_no_ledger_evidence_stays_unproven(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    assert not spring.complement_acceptance_proven()
    proof = spring.load_complement_proof()
    assert proof["proven"] is False
    assert proof["reason"].startswith("no_ledger_evidence:")
    assert proof["ledger_evidence"]["events"] == 0


def test_ledger_with_only_a_rejected_probe_stays_unproven(
        monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    _ledger(tmp_path, [[{**RESTING_PROBE, "end_reason": "rejected:X"}]])
    assert not spring.complement_acceptance_proven()
    assert spring.load_complement_proof()["reason"] == "ledger:probe_rejected"


def test_current_rule_records_are_never_reconciled(monkeypatch, tmp_path,
                                                   capsys):
    _state(monkeypatch, tmp_path)
    _ledger(tmp_path, [[RESTING_PROBE]])
    for doc in ({"proven": False, "reason": "rejected:ORDER_REJECTED"},
                {"proven": False, "reason": "revoked:complete_rejected_at_"
                                            "fresh_non_crossing_price",
                 "revoked_order": {"px": "0.225", "fresh_opp_ask": "0.2350"}},
                {"proven": False, "reason": "preview_or_preflight:X"}):
        _proof_file(tmp_path, doc)
        assert spring.retired_rule_write(doc) is None
        assert spring.load_complement_proof() == doc
        assert not spring.complement_acceptance_proven()
    assert "PMUS_COMPLEMENT_ACCEPTANCE" not in capsys.readouterr().out
    # a proven record is taken as is too
    _proof_file(tmp_path, {"proven": True, "order_id": "X"})
    assert spring.complement_acceptance_proven()


def test_one_tick_revocation_record_is_retired_and_rederived(
        monkeypatch, tmp_path):
    """The 03:22Z revocations themselves (px 0.225 vs fresh ask 0.230)
    were written under the retired rule; if one of them were the record
    on disk it is re-derived the same way."""
    _state(monkeypatch, tmp_path)
    doc = {"proven": False,
           "reason": "revoked:complete_rejected_at_fresh_non_crossing_price",
           "revoked_order": {"order_id": "CAP5E358WPQ1", "px": "0.225",
                             "fresh_opp_ask": "0.2300"}}
    assert spring.retired_rule_write(doc) == \
        "one_tick_revocation_retired_by_f465fbc9"
    _proof_file(tmp_path, doc)
    _ledger(tmp_path, [[RESTING_PROBE], [ONE_TICK_REJECTION]])
    assert spring.complement_acceptance_proven()
    assert spring.load_complement_proof()["retired_rule_write"] == \
        "one_tick_revocation_retired_by_f465fbc9"


# ---- rows older than session_report (02:19Z): submitted + order_terminal ----

def _raw_ledger(tmp_path, rows):
    with (tmp_path / "ledger.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


SUBMITTED_PROBE = {"t": "2026-09-06T01:47:09+00:00", "kind": "submitted",
                   "stage": "complement_acceptance",
                   "slug": "aec-mlb-wsh-lad-2026-09-05",
                   "side": "OUTCOME_SIDE_NO", "price": "0.625", "qty": "0.01",
                   "micro": True, "post_only": True,
                   "order_id": "CAMTQDEYWPPY"}
TERMINAL_PROBE = {"t": "2026-09-06T01:47:11+00:00", "kind": "order_terminal",
                  "spring": True, "slug": "aec-mlb-wsh-lad-2026-09-05",
                  "order_id": "CAMTQDEYWPPY", "filled_qty": "0",
                  "price": "0.625", "rest_s": 1.323,
                  "end_reason": "resting:ORDER_STATE_NEW"}
SUBMITTED_COMPLETE = {**SUBMITTED_PROBE, "t": "2026-09-06T03:22:55+00:00",
                      "stage": "spring_complete_1", "side": "OUTCOME_SIDE_YES",
                      "price": "0.225", "qty": "0.02",
                      "order_id": "CAP5E358WPQ1"}
TERMINAL_COMPLETE = {**TERMINAL_PROBE, "t": "2026-09-06T03:22:56+00:00",
                     "order_id": "CAP5E358WPQ1", "price": "0.225",
                     "end_reason": "rejected:ORDER_REJECTED_ZERO_FILL"}


def test_pre_session_report_probe_rows_prove_from_submitted_plus_terminal(
        monkeypatch, tmp_path):
    """The 01:47Z/02:11Z resting probes exist only as submitted +
    order_terminal rows (session_report rows began at 02:19Z)."""
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    _raw_ledger(tmp_path, [SUBMITTED_PROBE, TERMINAL_PROBE])
    assert spring.complement_acceptance_proven()
    proof = spring.load_complement_proof()
    assert proof["order_id"] == "CAMTQDEYWPPY"
    assert proof["proven_utc"] == "2026-09-06T01:47:11+00:00"
    assert proof["ledger_evidence"]["events"] == 1


def test_terminal_only_complete_rejection_revokes_but_its_report_row_refines_it(
        monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    # terminal row alone: no fresh ask recorded -> fail closed (revoked)
    _raw_ledger(tmp_path, [SUBMITTED_PROBE, TERMINAL_PROBE,
                           SUBMITTED_COMPLETE, TERMINAL_COMPLETE])
    assert spring.load_complement_proof()["proven"] is False
    # the same order's session_report row carries the fresh ask: ONE tick
    # -> a cross under the current rule, the proof stands
    _proof_file(tmp_path, RETIRED_NO_LEGAL)
    report = {"t": "2026-09-06T03:30:00+00:00", "kind": "session_report",
              "spring": True, "slug": "aec-mlb-wsh-lad-2026-09-05",
              "session_id": "s9", "orders": [ONE_TICK_REJECTION],
              "decisions": []}
    _raw_ledger(tmp_path, [SUBMITTED_PROBE, TERMINAL_PROBE,
                           SUBMITTED_COMPLETE, TERMINAL_COMPLETE, report])
    assert spring.complement_acceptance_proven()
    ev = spring.complement_proof_from_ledger()
    assert ev["events"] == 1 and ev["last_event"]["order_id"] == "CAMTQDEYWPPY"
