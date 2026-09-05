"""The submission boundary report for spring probe #1.

Its whole job is to be believed at arm time, so the things pinned here
are the ones that would quietly mislead: a blocker with no reason, a
collector candidate passing as authenticated, and risk quoted as a price
rather than as dollars.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D

from src import pmus_spring_probe_boundary as bd
from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as session

T0 = 1_757_000_000.0


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _tape(slug="aec-mkt-a", event="evt-a", n=40, step=6.0, bid="0.4500",
          ask="0.4700"):
    rows = [{"kind": "discovery", "t": _iso(T0),
             "targets": [{"slug": slug, "event_slug": event,
                          "league": "cfb"}],
             "diagnostics": {"rotation_cycle": 0}}]
    cum = 1000.0
    for i in range(n):
        cum += 25.0
        rows.append({"kind": "bbo_book", "t": _iso(T0 + i * step),
                     "slug": slug, "league": "cfb", "bid": bid,
                     "bid_touch_qty": "500", "ask": ask,
                     "ask_touch_qty": "500", "ask_book_missing": False,
                     "last_trade_px": bid if i % 2 == 0 else ask,
                     "shares_traded_cum": f"{cum:.2f}",
                     "market_state": "MARKET_STATE_OPEN"})
    return rows


def _write(tmp_path, rows, now):
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    p = (tmp_path / "raw" /
         f"pmus_{datetime.fromtimestamp(now, timezone.utc):%Y%m%d}.jsonl")
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return tmp_path


# ---- a blocker must always say why ----

def test_a_blocker_never_reads_unstated():
    """`stale_reason` covers only staleness. When the model itself
    declines, it is null -- and the blocker used to say "unstated"."""
    nom = {"candidate": {"slug": "mkt", "executable_now": False,
                         "stale_reason": None,
                         "probe_entry_action": {"decision": "no_action",
                                                "reason": "v2_off_distribution"}}}
    out = bd.blockers(nom, {})
    assert "candidate_not_executable_now:v2_off_distribution" in out


def test_a_blocker_falls_back_to_the_decision_not_to_silence():
    nom = {"candidate": {"slug": "mkt", "executable_now": False,
                         "stale_reason": None,
                         "probe_entry_action": {"decision": "no_action"}}}
    assert "candidate_not_executable_now:model_no_action" in bd.blockers(
        nom, {})


def test_no_candidate_is_itself_a_named_blocker():
    assert bd.blockers({"candidate": None}, {}) == ["no_candidate_on_tape"]


# ---- collector nominates, recorder authenticates ----

def test_collector_evidence_alone_can_never_admit(tmp_path):
    """The collector is keyless: its direction is INFERRED. However good
    the candidate looks, admission needs the authenticated half."""
    now = T0 + 40 * 6.0
    root = _write(tmp_path, _tape(), now)
    out = bd.run(root, None, now=now)
    assert out["evidence"]["collector"]["provenance"] == "inferred"
    assert out["evidence"]["recorder"]["present"] is False
    assert any("authenticated_recorder_evidence_absent" in b
               for b in out["blockers"])


def test_the_report_separates_provenance_from_the_other_reasons(tmp_path):
    """So an operator can see whether provenance is the ONLY thing left,
    or whether the candidate would fail on its merits anyway."""
    now = T0 + 40 * 6.0
    root = _write(tmp_path, _tape(), now)
    coll = bd.run(root, None, now=now)["evidence"]["collector"]
    assert "admission_reason" in coll
    assert "admission_reason_ignoring_provenance" in coll


def test_a_recorder_status_reaches_the_admission_guard(tmp_path):
    """End to end: a recorder status document is read, composed as
    authenticated, and judged by the same guard that admits a probe."""
    now = T0 + 240
    root = _write(tmp_path, _tape(), now)
    status = tmp_path / "status.jsonl"
    status.write_text(json.dumps({
        "t": now - 5,
        "spring_watch": [{
            "slug": "aec-mkt-a", "bid": "0.50", "trades_1h": 25,
            "two_sided_trades": True, "off_distribution": False,
            "both_sides_served": True, "spring_pair_target": "0.98",
            "spring_matched_slots": 3, "spring_repeated_completion": True,
            "spring_category": "cfb",
            "spring_category_evidence": {"completions_60m": 3,
                                         "distinct_markets_60m": 2,
                                         "bar_met": True},
            "spring_category_bar_met": True,
            "probe_evidence": {"status": "READY_FOR_REVIEW", "reasons": [],
                               "min_completions_required": 2},
        }]}) + "\n", encoding="utf-8")
    out = bd.run(root, status, now=now)
    rec = out["evidence"]["recorder"]
    assert rec["present"] is True
    assert rec["provenance"] == entry.PROVENANCE_AUTHENTICATED
    assert rec["admission_reason"] is None, rec["admission_reason"]
    assert not any(b.startswith("recorder_evidence_refused")
                   for b in out["blockers"])


# ---- risk is dollars, not a price ----

def test_max_loss_is_notional_not_the_contract_price():
    cand = {"probe_entry_action": {"decision": "quote", "side": "YES",
                                   "target_price": "0.4500"}}
    risk = bd.first_action_risk(cand, D("0.01"))
    assert risk["sizing"] is not None
    assert D(risk["max_loss_this_action"]) < D("0.45")
    assert D(risk["max_loss_this_action"]) <= mx_max()
    assert risk["max_loss_this_event"] == str(session.SESSION_NOTIONAL_CAP)


def mx_max():
    from src import pmus_micro_executor as mx
    return mx.DEFAULT_MAX_LEG_NOTIONAL


def test_the_first_action_is_the_flat_one_not_a_continued_inventory():
    """`next_action` continues the replayed inventory; a first probe
    holds nothing, so quoting its price as the entry would be wrong."""
    cand = {"probe_entry_action": {"decision": "quote", "side": "YES",
                                   "target_price": "0.4000"},
            "next_action": {"decision": "quote", "side": "NO",
                            "target_price": "0.6000"}}
    risk = bd.first_action_risk(cand, D("0.01"))
    assert risk["sizing"]["price"] == "0.4000"


def test_an_unpriceable_action_reports_why_and_sizes_nothing():
    cand = {"probe_entry_action": {"decision": "no_action",
                                   "reason": "v2_off_distribution"}}
    risk = bd.first_action_risk(cand, D("0.01"))
    assert risk["sizing"] is None
    assert risk["reason"] == "no_priced_first_action"


# ---- the report can never be the thing that arms ----

def test_report_is_disarmed_and_carries_no_order_path():
    src = open(bd.__file__, encoding="utf-8").read()
    for banned in ("urllib", "requests", "POST", "/v1/orders", "X-PM-"):
        assert banned not in src, banned


def test_report_states_caps_and_never_a_realized_pnl(tmp_path):
    now = T0 + 240
    root = _write(tmp_path, _tape(), now)
    out = bd.run(root, None, now=now)
    assert out["armed"] is False
    assert out["realized_pnl"] is None
