"""Synthetic-row tests for the R4 fidelity decomposition (no real dumps)."""
import json
import os
from decimal import Decimal as D

from src.l5_r4_action_comparison import harvest
from src.l5_r4_fidelity_decomposition import (CAP, campaign, coverage, expand_logs,
                                              market_decomposition, paying_side,
                                              refused_order, runs)

SHELL = "R4/cap7.50/unmatched2.50/complete1.00/conc1/maker-only/rest30s/action2.50"
T0 = "2026-09-07T10:00:%02d+00:00"


def _dec(idx, sampled, acted, refusal=None, t=None, inv=None, planned=None,
         before=None, after=None, cf=None, wait=30.0, would=None):
    return {
        "decision_id": f"d{idx:03d}", "decision_idx": idx, "session_id": "s1",
        "t_utc": t or (T0 % idx), "state": "p45-55c|tte>10m|flat",
        "safety_shell_version": SHELL, "v2_recommended": sampled,
        "shadow": {"stochastic": {"sampled": sampled}},
        "would_act_without_safety_cap": would or (sampled if refusal != "off_distribution_hold" else "hold"),
        "acted": acted, "refusal": refusal,
        "cap_binding": [CAP] if (refusal or "").startswith(CAP) else [],
        "inventory_before": inv or {"up": "0", "dn": "0", "unmatched_usd": "0.0000"},
        "planned_safe_size": planned or {"contracts": "5.00", "usd": "2.5000"},
        "unmatched_usd_before": before, "unmatched_usd_after_if_filled": after,
        "max_unmatched_usd": "2.50", "next_reevaluation_s": wait,
        "counterfactual_l5_price": cf,
    }


def _session_end(slug, decisions, orders=(), inventory=None, ts="2026-09-07T10:01:00Z"):
    body = {"kind": "session_end", "slug": slug, "status": "SPRING_RESIDUAL_HOLD",
            "decisions": decisions, "orders": list(orders), "inventory": inventory}
    return {"timestamp": ts, "message": "PMUS_L5_CAMPAIGN " + json.dumps(body)}


def _settlement(slug, realized, bought_yes, bought_no, cost):
    body = {"kind": "settlement", "slug": slug, "settled": True, "status": "REALIZED",
            "realized_usd": realized, "settled_by": "position_resolution_activity",
            "resolution": {"side": "POSITION_RESOLUTION_SIDE_LONG"},
            "breakdown": {"bought_yes": bought_yes, "bought_no": bought_no, "cost_basis_usd": cost,
                          "matched_pairs": bought_no, "directional_residual": "0"}}
    return {"timestamp": "2026-09-07T11:00:00Z", "message": "PMUS_L5_CAMPAIGN " + json.dumps(body)}


def _synthetic_market(slug):
    inv = {"up": "5.00", "dn": "0", "unmatched_usd": "2.5000", "avg_up": "0.50"}
    decs = [
        _dec(0, "open", "open"),
        _dec(1, "extend", "hold", f"{CAP}:5.0000>2.50", inv=inv, before="2.5000", after="5.0000",
             planned={"contracts": "5.00", "usd": "2.5000"}),
        _dec(2, "extend", "hold", f"{CAP}:5.0000>2.50", inv=inv, before="2.5000", after="5.0000",
             planned={"contracts": "5.00", "usd": "2.5000"}),
        _dec(3, "complete", "complete", inv=inv,
             cf={"l5_touch_px": "0.40", "executed_px": "0.40", "class": "no_difference"}),
        _dec(4, "open", "hold", "off_distribution_hold", inv=inv, would="hold"),
        # idx 5 missing on purpose: a coverage gap must break the run below
        _dec(6, "extend", "hold", f"{CAP}:5.0000>2.50", inv=inv, before="2.5000", after="5.0000",
             planned={"contracts": "4.00", "usd": "2.4000"}, wait=2.0),
    ]
    for d in decs:                       # ids are globally unique in the real ledger
        d["decision_id"] = f"{slug}:{d['decision_id']}"
        d["session_id"] = f"{slug}:s1"
    orders = [{"order_id": f"{slug}:o1", "stage": "spring_open_1", "side": "YES", "px": "0.50", "qty": "5.00",
               "filled_qty": "5.00", "relation": "open", "end_reason": "filled"}]
    return _session_end(slug, decs, orders, inventory=inv)


def _write_dump(path, events):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"deploy": events}, f)


def test_refused_order_reconstruction_and_runs_break_on_idx_gap():
    inv = {"up": "5.00", "dn": "0", "unmatched_usd": "2.5000"}
    d = _dec(1, "extend", "hold", f"{CAP}:4.9>2.50", inv=inv, before="2.5000", after="4.9000",
             planned={"contracts": "5.00", "usd": "2.4000"})
    o = refused_order(d)
    assert o["side"] == "YES" and o["qty"] == D("5.00") and o["px"] == D("0.48")
    assert o["usd"] == D("2.4000") and o["unmatched_increment"] == D("2.4000")
    decs = _synthetic_market("m")["message"]
    decs = json.loads(decs[len("PMUS_L5_CAMPAIGN "):])["decisions"]
    rr = runs(decs, CAP)
    assert [[x["decision_idx"] for x in r] for r in rr] == [[1, 2], [6]]


def test_market_decomposition_settled_yes_paid(tmp_path):
    slug = "mkt-settled"
    _write_dump(tmp_path / "a.txt", [_synthetic_market(slug),
                                     _settlement(slug, "2.5000", "5.00", "0", "2.5000")])
    h = harvest([str(tmp_path / "a.txt")])
    m = market_decomposition(slug, h)
    conf = m["confusion_sampled_x_outcome"]["rows"]
    assert conf["extend"] == {"hold:unmatched_usd_cap": 3}
    assert conf["open"] == {"acted:open": 1, "hold:off_distribution_hold": 1}
    assert conf["complete"] == {"acted:complete": 1}
    assert m["coverage"]["missing_ranges"] == [[5, 5]]
    cap = m["refusals"][CAP]
    assert cap["decisions"] == 3 and cap["runs"] == 2
    assert cap["share_of_l5_extend_decisions_suppressed"]["sampled_extend"] == {"n_extend": 3, "suppressed": 3, "share": 1.0}
    dm = cap["dollars_and_inventory"]
    assert dm["all_refusals_fill"]["usd_qty_x_px"] == "7.4000"
    assert dm["all_refusals_fill"]["contracts_by_side"] == {"YES": "14.00", "NO": "0.00"}
    assert dm["first_of_each_run_fills"]["usd_qty_x_px"] == "4.9000"
    # run 1-2 spans 1 s + 30 s wait; run 6 spans 0 s + 2 s wait
    assert cap["time"]["strict_s"] == 1.0 and cap["time"]["inclusive_s"] == 33.0
    # YES paid (realized 2.50 = 5.00 x 1 - 2.50): each refused YES buy gains qty x (1 - px)
    assert m["settlement"]["side"] == "YES" and m["settlement"]["basis"].startswith("derived")
    cf = cap["counterfactual"]
    assert cf["status"] == "COUNTERFACTUAL"
    assert cf["all_refusals_fill"]["held_to_settlement_pnl_usd"] == "6.6000"   # 2.5+2.5+1.6
    assert cf["all_refusals_fill"]["held_if_opposite_side_paid_usd"] == "-7.4000"
    # the first two refusals see the complete at idx 3 (touch 0.40 -> pair 0.90); idx 6 has no later touch
    assert cf["rows"][0]["paired_at_later_touch_pnl_usd"] == "0.5000"
    assert cf["rows"][2]["later_complement_touch"] is None
    assert cf["all_refusals_fill"]["paired_at_later_touch_pnl_usd"] == "1.0000"
    assert m["refusals"]["off_distribution_hold"]["decisions"] == 1


def test_open_market_is_pending_and_campaign_totals(tmp_path):
    settled, pending = "mkt-settled", "mkt-open"
    _write_dump(tmp_path / "a.txt", [_synthetic_market(settled), _synthetic_market(pending),
                                     _settlement(settled, "2.5000", "5.00", "0", "2.5000")])
    logs = expand_logs([str(tmp_path / "a.txt")])
    h = harvest(logs)
    rep = campaign(h, coverage(logs, []), logs)
    assert rep["markets_settled"] == [settled]
    assert rep["per_market"][pending]["refusals"][CAP]["counterfactual"]["status"] == "PENDING"
    assert rep["cap"]["counterfactual"]["pending_markets"] == [pending]
    assert rep["cap"]["decisions"] == 6 and rep["cap"]["runs"] == 4
    assert rep["cap"]["counterfactual"]["all_refusals_fill"]["orders"] == 3
    assert rep["cap"]["share_of_l5_extend_decisions_suppressed"]["sampled_extend"]["share"] == 1.0
    assert set(rep["labels"]) >= {"realized", "derived", "counterfactual", "PENDING"}
    assert "PENDING" in rep["verdict"] and "counterfactual" in rep["verdict"]


def test_coverage_counts_truncated_rows_and_supplements(tmp_path):
    slug = "mkt"
    good = _synthetic_market(slug)
    cut = dict(good, message=good["message"][:200])          # truncated mid-JSON
    _write_dump(tmp_path / "a.txt", [good, cut])
    _write_dump(tmp_path / "supplement_x.json", [_settlement(slug, "2.5000", "5.00", "0", "2.5000")])
    logs = expand_logs([str(tmp_path / "a.txt")])
    assert [os.path.basename(p) for p in logs] == ["a.txt", "supplement_x.json"]
    cov = coverage(logs, ["d000"])
    assert cov["campaign_rows"] == 3 and cov["parsed_rows"] == 2 and cov["truncated_rows"] == 1
    t = cov["truncated"][0]
    assert t["kind"] == "session_end" and t["slug"] == slug and t["decision_ids_seen"] >= 1
    h = harvest(logs)
    assert slug in h["settlements"]                      # the supplement was read


def test_paying_side_from_public_settlement_px_is_flagged_unconfirmed():
    s = {"settled": True, "realized_usd": None, "breakdown": {"bought_yes": "5", "bought_no": "5", "cost_basis_usd": "5"},
         "resolution": {"side": None, "public_settlement_px": "0.0000"}}
    w = paying_side(s)
    assert w["side"] == "NO" and w["unconfirmed"] is True
    assert paying_side({})["basis"] == "PENDING"
