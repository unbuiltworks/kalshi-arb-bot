"""LIVE 16:33-16:43Z (mil-cin 9.5): the complement-acceptance probe's own
post-only NO was rejected one tick from the ask and wrote proven=false; the
venue then held and FILLED three post-only NO completes built by this code
on the same market; the market matched out -> flat account, admitted
candidates, campaign stuck in CAMPAIGN_WAITING:COMPLEMENT_UNPROVEN with no
held market to re-probe on. A venue-held/filled COMPLETE is the same
acceptance evidence as a held/filled probe (OWNER 5556038561 #3: "the venue
has accepted an opposite-side order built by this code"); a not-proven
record yields only to a STRICTLY LATER acceptance. No cap, gate or
threshold change."""
from __future__ import annotations

import json

from src import pmus_micro_executor as micro
from src import pmus_spring_l5_session as spring

q1 = micro.q1
MIL_CIN = "tsc-mlb-mil-cin-2026-09-06-9pt5"

LIVE_PROBE_REJECTED = {
    "proven": False, "slug": MIL_CIN,
    "attempted_utc": "2026-09-06T16:33:16.734654+00:00",
    "attempted_epoch": 1788712396.734669, "residual": "0.02",
    "plan": {"side": "OUTCOME_SIDE_NO", "px": "0.565", "opp_ask": "0.5700",
             "regime": "spring_target", "chosen_px": "0.565"},
    "book": {"yes_bid": "0.4300", "yes_ask": "0.4650"},
    "order_id": "CB1F66N44Q45",
    "submitted": {"side": "OUTCOME_SIDE_NO", "px": "0.565", "qty": "0.01",
                  "post_only": True},
    "end_reason": "rejected:ORDER_REJECTED_ZERO_FILL:CB1F66N44Q45",
    "filled_qty": "0", "proven_utc": None,
    "reason": "rejected:ORDER_REJECTED_ZERO_FILL:CB1F66N44Q45"}


def _state(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))


def _proof_file(tmp_path, doc):
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).write_text(json.dumps(doc))


def _raw_ledger(tmp_path, rows):
    with (tmp_path / "ledger.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _sub(t, stage, oid, px, side="OUTCOME_SIDE_NO"):
    return {"t": t, "kind": "submitted", "stage": stage, "slug": MIL_CIN,
            "side": side, "price": px, "order_id": oid}


def _term(t, oid, px, end, filled="0"):
    return {"t": t, "kind": "order_terminal", "spring": True, "slug": MIL_CIN,
            "order_id": oid, "filled_qty": filled, "price": px,
            "rest_s": 1.0, "end_reason": end}


PROBE_ROWS = [_sub("2026-09-06T16:33:15+00:00", "complement_acceptance",
                   "CB1F66N44Q45", "0.565"),
              _term("2026-09-06T16:33:16.7+00:00", "CB1F66N44Q45", "0.565",
                    "rejected:ORDER_REJECTED_ZERO_FILL:CB1F66N44Q45")]
FILLED_COMPLETE_ROWS = [
    _sub("2026-09-06T16:39:20+00:00", "spring_complete_6", "CB1HTNJZYQ46",
         "0.550"),
    _term("2026-09-06T16:39:25+00:00", "CB1HTNJZYQ46", "0.550", "filled",
          "0.02")]
EARLY_FILLED_ROWS = [
    _sub("2026-09-06T16:20:00+00:00", "spring_complete_1", "CB1EARLY0Q46",
         "0.540"),
    _term("2026-09-06T16:20:03+00:00", "CB1EARLY0Q46", "0.540", "filled",
          "0.02")]


def test_filled_complete_after_rejected_probe_reproves(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    _raw_ledger(tmp_path, PROBE_ROWS + FILLED_COMPLETE_ROWS)
    assert spring.retired_rule_write(LIVE_PROBE_REJECTED) is None
    assert spring.complement_acceptance_proven()
    proof = spring.load_complement_proof()
    assert proof["superseded_by"] == "later_venue_acceptance"
    assert proof["order_id"] == "CB1HTNJZYQ46"
    assert proof["proven_utc"] == "2026-09-06T16:39:25+00:00"
    assert proof["superseded_record"]["order_id"] == "CB1F66N44Q45"
    ev = proof["ledger_evidence"]
    assert ev["events"] == 2 and ev["last_event"]["why"] == "complete_accepted"
    # persisted once: the next load is the proven record as written
    again = json.loads((tmp_path / spring.COMPLEMENT_PROOF_FILE).read_text())
    assert again["proven"] is True and again["order_id"] == "CB1HTNJZYQ46"


def test_older_acceptance_never_outranks_newer_rejection(monkeypatch,
                                                         tmp_path):
    """Ledger holds only a 16:20Z filled complete (the 16:33Z rejection was
    written to the proof file by a path that left no ledger row): the
    record is newer than every ledger acceptance -> it stands."""
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    _raw_ledger(tmp_path, EARLY_FILLED_ROWS)
    assert spring.complement_proof_from_ledger()["proven"] is True
    assert spring.complement_acceptance_proven() is False
    assert spring.load_complement_proof()["order_id"] == "CB1F66N44Q45"


def test_rejected_probe_after_filled_complete_still_fails_closed(
        monkeypatch, tmp_path):
    """Ledger order: filled complete, THEN the probe rejection -> the last
    venue event wins, not proven."""
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    _raw_ledger(tmp_path, EARLY_FILLED_ROWS + PROBE_ROWS)
    assert spring.complement_acceptance_proven() is False


def test_complete_rested_to_deadline_counts_as_venue_acceptance(monkeypatch,
                                                                tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    rested = [_sub("2026-09-06T16:35:00+00:00", "spring_complete_3",
                   "CB1FB50XJQ48", "0.570"),
              _term("2026-09-06T16:36:05+00:00", "CB1FB50XJQ48", "0.570",
                    "deadline")]
    _raw_ledger(tmp_path, PROBE_ROWS + rested)
    assert spring.complement_acceptance_proven()
    assert spring.load_complement_proof()["order_id"] == "CB1FB50XJQ48"


def test_later_complete_rejection_still_revokes(monkeypatch, tmp_path):
    """A filled complete followed by a complete rejected >= 2 ticks from
    the fresh ask (session_report row): the rejection is the last event."""
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    report = {"t": "2026-09-06T16:41:00+00:00", "kind": "session_report",
              "spring": True, "slug": MIL_CIN, "session_id": "s9",
              "orders": [{"stage": "spring_complete_7",
                          "order_id": "CB1LATE00Q46", "side": "NO",
                          "px": "0.560", "qty": "0.02", "filled_qty": "0",
                          "relation": "complete",
                          "end_reason": "rejected:ORDER_REJECTED_ZERO_FILL",
                          "fresh_opp_ask": "0.5800", "tick": "0.005"}],
              "decisions": []}
    _raw_ledger(tmp_path, PROBE_ROWS + FILLED_COMPLETE_ROWS + [report])
    assert spring.complement_acceptance_proven() is False


def test_no_ledger_evidence_leaves_current_rule_record_untouched(monkeypatch,
                                                                 tmp_path):
    _state(monkeypatch, tmp_path)
    _proof_file(tmp_path, LIVE_PROBE_REJECTED)
    assert spring.complement_acceptance_proven() is False
    assert spring.load_complement_proof() == LIVE_PROBE_REJECTED
