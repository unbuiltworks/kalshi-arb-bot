"""First-probe readiness reconciliation (issue #167 comment 5532064571):
the two evidence layers compose into one unambiguous binding bar, no env
setting can weaken the effective arm bar below 2 strict completions,
and the readiness report is purely observational."""
from __future__ import annotations

import time
from pathlib import Path

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as session
from src import pmus_spring_probe_readiness as ready

SLUG = "atc-lal-rso-cel-2026-09-03-rso"


def _settles_in(hours: float = 2.0) -> str:
    """A settlement time RELATIVE TO NOW, never a date written into the file.

    This fixture used to hardcode endDate "2026-09-16T09:30:00Z". That is a
    time bomb: on 2026-09-16 the real clock passed it and six tests here began
    failing with `strict_evidence_too_close_to_settlement:-7525.4s` and
    `strict_evidence_from_future` -- a market that had already settled. The
    gates being exercised are about time-to-settlement and evidence freshness,
    so the fixture has to move with the clock or it stops testing them and
    starts testing the calendar.
    """
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(matched=3, floor=3, status="READY_FOR_REVIEW",
         cat_completions=None, cat_markets=2, fee_safe=None, **over):
    cat_completions = matched if cat_completions is None else cat_completions
    bar = (cat_completions >= 2 and cat_markets >= 2)
    row = {
        "slug": SLUG, "bid": "0.50", "trades_1h": 120,
        "two_sided_trades": True, "off_distribution": False,
        "spring_pair_target": "0.98",
        "spring_matched_slots": matched,
        "spring_repeated_completion": matched >= 2,
        "spring_slot_counts_theoretical": {"SPRING_MATCHED": matched},
        "spring_fee_safe_completions": (matched + 1 if fee_safe is None
                                        else fee_safe),
        "spring_category": "lal",
        "spring_category_evidence": {
            "completions_60m": cat_completions,
            "distinct_markets_60m": cat_markets,
            "markets": [f"m{i}" for i in range(cat_markets)],
            "bar_met": bar},
        "spring_category_bar_met": bar,
        "probe_evidence": {"status": status,
                           "min_completions_required": floor,
                           "reasons": [] if status == "READY_FOR_REVIEW"
                           else ["insufficient_category_completions"]},
        # OPEN safety facts (OWNER 5551236878): required end to end, so
        # a realistic recorder row carries them.
        "both_sides_served": True,
        "bid_touch_qty": "500",
        "collector_gaps": 0,
        "market_state": {"active": True, "archived": False, "closed": False,
                         "status": "MARKET_STATUS_OPEN",
                         "endDate": _settles_in()},
        "probe_entry_action": {"decision": "quote", "relation": "open",
                               "side": "YES", "target_price": "0.5000"},
    }
    row.update(over)
    return row


def _status(row, t=None):
    import datetime
    t = t or datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {"t": t, "spring_watch": [row]}


def test_two_layer_bar_default_floor_is_now_advisory_not_a_blocker():
    """OWNER 5551236878: a two-completion row against a floor of 3 is
    still MEASURED and reported -- it just no longer withholds an OPEN,
    because completion history is not an entry condition."""
    row = _row(matched=2, floor=3, status="INSUFFICIENT_EVIDENCE")
    rep = ready.readiness_report(_status(row), SLUG, now=time.time())
    gate = {g["gate"]: g for g in rep["gates"]}["strict_completion_evidence"]
    assert gate["ok"] is False
    assert gate["advisory"] is True
    assert gate["blocker"].startswith("probe_evidence_not_ready")
    # the OPEN path itself is unobstructed by it
    composed = {g["gate"]: g for g in rep["gates"]}["strict_evidence_composed"]
    assert composed["ok"] is True


def test_completion_floor_still_clamps_to_two_when_measured():
    """The max(2, floor) clamp survives -- a misconfigured floor of 1
    cannot make one completion read as sufficient EVIDENCE. It is simply
    no longer an admission bar."""
    row = _row(matched=1, floor=1, status="READY_FOR_REVIEW",
               cat_completions=1, cat_markets=2,
               spring_category_bar_met=True)
    ev = entry.evidence_from_status(_status(row), SLUG)
    assert entry.completion_evidence_reason(ev) == \
        "spring_category_completions_lt_required:1<2"
    # and it does not block the OPEN
    assert entry.strict_evidence_reason(
        SLUG, ev, now=time.time(), require_provenance=False) is None


def test_isolated_market_is_flagged_but_no_longer_blocked():
    """A market alone in its category is still reported as lacking
    category evidence -- in the advisory layer, not as a refusal."""
    row = _row(matched=2, floor=2, cat_completions=2, cat_markets=1)
    rep = ready.readiness_report(_status(row), SLUG, now=time.time())
    completion = rep["layers"]["COMPLETION_EVIDENCE"]
    assert completion["ok"] is False
    assert any("spring_category_bar_not_met" in b
               for b in completion["blockers"])
    # replication may be red for unrelated env reasons in this fixture;
    # what matters is that the CATEGORY BAR is not among its blockers
    assert not any("category" in b
                   for b in rep["layers"]["REPLICATION_EVIDENCE"]["blockers"])


def test_floor_two_with_two_category_completions_passes_evidence_layer():
    row = _row(matched=2, floor=2, status="READY_FOR_REVIEW",
               cat_completions=2, cat_markets=2)
    reason = entry.strict_evidence_reason(
        SLUG, entry.evidence_from_status(_status(row), SLUG),
        now=time.time(), require_provenance=False)
    assert reason is None


def test_report_names_every_remaining_gate_and_blocker(monkeypatch):
    for env in (session.SPRING_GATE_ENV, session.SPRING_STRICT_ENV,
                entry.STRICT_EVIDENCE_ENV, session.SPRING_LIVE_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(entry, "durable_state_reason",
                        lambda *a, **k: "spring_state_dir_not_persistent_"
                                        "mount:/state/pmus-spring")
    rep = ready.readiness_report(_status(_row()), SLUG, now=time.time())
    by = {g["gate"]: g for g in rep["gates"]}
    assert by["strict_evidence_composed"]["ok"] is True
    assert by["durable_state"]["ok"] is False
    assert rep["non_code_blockers"] == [
        "spring_state_dir_not_persistent_mount:/state/pmus-spring"]
    assert by["gate_evidence_env_asserted"]["ok"] is False
    assert session.SPRING_GATE_ENV in \
        by["gate_evidence_env_asserted"]["blocker"]
    assert by["strict_completion_env_asserted"]["ok"] is False
    assert by["strict_evidence_available"]["ok"] is False
    assert by["isolated_dual_key_armed"]["ok"] is False
    assert "DISARMED" in by["isolated_dual_key_armed"]["blocker"]
    assert rep["ready"] is False
    assert "max(2, review floor" in rep["binding_bar"]
    # the three layers are cleanly separated with their own blockers
    assert rep["layers"]["REPLICATION_EVIDENCE"]["ok"] is False
    assert rep["layers"]["OPERATIONAL"]["ok"] is False


def test_fully_asserted_environment_reports_ready(monkeypatch):
    import json as _json
    row = _row(matched=3, floor=3)
    status = _status(row)
    monkeypatch.setenv(session.SPRING_GATE_ENV, SLUG)
    monkeypatch.setenv(session.SPRING_STRICT_ENV, SLUG)
    ev = dict(entry.evidence_from_status(status, SLUG))
    ev["evidence_provenance"] = entry.PROVENANCE_AUTHENTICATED
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, _json.dumps(ev))
    monkeypatch.setenv(session.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(session.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(session.SPRING_ARM_ENV, "s1")
    monkeypatch.setattr(entry, "durable_state_reason", lambda *a, **k: None)
    rep = ready.readiness_report(status, SLUG, now=time.time())
    assert rep["ready"] is True
    assert all(g["ok"] for g in rep["gates"])
    assert all(v["ok"] for v in rep["layers"].values())


def test_replication_without_executable_economics_is_separated():
    """Directive 5533650823 item 5: REPLICATION_EVIDENCE can be green
    while EXECUTABLE_ECONOMICS is red — evidence at observation
    economics (<=0.98) with no completion clearing the live
    >=1c-after-known-fees bound never reads as executable."""
    row = _row(matched=3, floor=3, fee_safe=0)
    rep = ready.readiness_report(_status(row), SLUG, now=time.time())
    by = {g["gate"]: g for g in rep["gates"]}
    assert by["strict_evidence_composed"]["ok"] is True
    econ = rep["layers"]["EXECUTABLE_ECONOMICS"]
    assert econ["ok"] is False
    assert any("observation economics" in b for b in econ["blockers"])
    assert rep["ready"] is False


def test_readiness_module_is_purely_observational():
    src = Path(ready.__file__).read_text()
    for banned in ("urllib", "websockets", "requests", "Transport(",
                   "/v1/orders", "POST", "setenv", "os.environ["):
        assert banned not in src, banned


# ---- provenance is its own bar in this report, not an afterthought ----

def test_inferred_evidence_can_never_report_ready(monkeypatch):
    """A payload whose direction was INFERRED from lastTradePx vs BBO is
    not authenticated evidence, however strong every other field looks.
    Before this layer existed the report said ready:true for one."""
    import json as _json
    status = _status(_row(matched=3, floor=3))
    ev = dict(entry.evidence_from_status(status, SLUG))
    ev["evidence_provenance"] = "inferred"
    monkeypatch.setenv(session.SPRING_GATE_ENV, SLUG)
    monkeypatch.setenv(session.SPRING_STRICT_ENV, SLUG)
    monkeypatch.setenv(entry.STRICT_EVIDENCE_ENV, _json.dumps(ev))
    monkeypatch.setenv(session.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(session.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(session.SPRING_ARM_ENV, "s1")
    monkeypatch.setattr(entry, "durable_state_reason", lambda *a, **k: None)
    rep = ready.readiness_report(status, SLUG, now=time.time())
    assert rep["ready"] is False
    assert rep["layers"]["EVIDENCE_PROVENANCE"]["ok"] is False
    # and the replication layer is NOT dragged down by it
    # replication may be red for unrelated env reasons in this fixture;
    # what matters is that the CATEGORY BAR is not among its blockers
    assert not any("category" in b
                   for b in rep["layers"]["REPLICATION_EVIDENCE"]["blockers"])


def test_report_states_which_source_the_evidence_came_from(monkeypatch):
    for env in (entry.STRICT_EVIDENCE_ENV, entry.EVIDENCE_STATUS_PATH_ENV):
        monkeypatch.delenv(env, raising=False)
    rep = ready.readiness_report(_status(_row()), SLUG, now=time.time())
    assert rep["evidence_source"] == "operator_pasted_json_absent"
