"""OWNER chat 2026-09-07 ~11:00Z: parallel research while R4 runs untouched.
Deterministic tests for the cap-override study, the R4 action-by-action
harvester/comparison, and the concurrency study's idle-gap extension."""
from __future__ import annotations

import json

from src import l5_cap_override_study as cap
from src import l5_concurrency_study as conc
from src import l5_r4_action_comparison as cmp


# ---------------------------------------------------------------- cap study

def _row(wid, seq, t, outcome, price, qty, cum_up, cum_up_spend, cum_dn, cum_dn_spend,
         resid, side="BUY", tts=600, asset="BTC", dur="15"):
    return {"window_id": wid, "market_slug": f"{asset.lower()}-updown-{dur}m-x", "asset": asset,
            "duration_minutes": dur, "trade_seq": str(seq), "timestamp": str(t),
            "seconds_to_settlement": str(tts), "outcome": outcome, "side": side,
            "price": str(price), "quantity": str(qty), "dollars": str(round(price * qty, 6)),
            "cum_up_qty": str(cum_up), "cum_up_spend": str(cum_up_spend),
            "cum_down_qty": str(cum_dn), "cum_down_spend": str(cum_dn_spend),
            "residual_qty_signed": str(resid), "residual_side": "Up" if resid > 0 else ("Down" if resid < 0 else ""),
            "settlement_outcome": "Up", "maker_taker": "maker"}


def _window():
    # open Up 10@0.50 ($5) -> extend Up 10@0.50 ($5, unmatched $10 = 2 units)
    # -> complete Down 10@0.50 ($5, unmatched back to $5 = 1 unit)
    # -> extend Up 10@0.50 (unmatched $10, 2 units) -> sell row skipped
    return [
        _row("w", 1, 1000, "Up", 0.50, 10, 10, 5.0, 0, 0, 10),
        _row("w", 2, 1010, "Up", 0.50, 10, 20, 10.0, 0, 0, 20),
        _row("w", 3, 1020, "Down", 0.50, 10, 20, 10.0, 10, 5.0, 10),
        _row("w", 4, 1030, "Up", 0.50, 10, 30, 15.0, 10, 5.0, 20),
        _row("w", 5, 1040, "Up", 0.60, 1, 29, 14.4, 10, 5.0, 19, side="SELL"),
    ]


def test_relations_come_from_inventory_columns_and_sells_are_skipped():
    acts = cap.window_actions(_window())
    assert [a["relation"] for a in acts] == ["open", "extend", "complete", "extend"]
    assert acts[1]["unmatched_before"] == 5.0 and acts[1]["unmatched_after"] == 10.0
    assert acts[2]["unmatched_after"] == 5.0


def test_a_one_unit_cap_refuses_both_extends_and_a_two_unit_cap_neither():
    w = cap.window_study(_window())
    assert w["action_unit_usd"] == 5.0 and w["extends"] == 2
    assert w["extend_units_after"] == [2.0, 2.0]
    assert w["extends_refused_at"]["1.0"] == 2
    assert w["extends_refused_at"]["2.0"] == 0
    assert w["extended_beyond_one_unit"] is True
    # after the first over-unit extend, the complete brought it back under
    assert w["reduced_back_under_one_unit_before_settlement"] is True
    s = cap.summary([w])
    assert s["relation_mix"] == {"open": 1, "extend": 2, "complete": 1,
                                 "extend_share_of_actions": 0.5}
    assert s["extends_refused_by_cap_in_action_units"]["1.0"]["share_of_extends"] == 1.0
    assert s["extends_refused_by_cap_in_action_units"]["2.0"]["share_of_extends"] == 0.0


def test_live_refusals_are_read_from_the_harvest_and_measured_in_action_units():
    decs = [
        {"state": "s", "acted": "hold", "refusal": "unmatched_usd_cap:4.9>2.50",
         "would_act_without_safety_cap": "extend", "slug": "m",
         "unmatched_usd_before": "2.45", "unmatched_usd_after_if_filled": "4.9"},
        {"state": "s", "acted": "extend", "refusal": None, "slug": "m"},
        {"state": "s", "acted": "complete", "refusal": None, "slug": "m"},
    ]
    live = cap.live_refusals(decs)
    assert live["refusals"] == 1 and live["extend_draws"] == 2
    assert live["extend_draws_refused_share"] == 0.5
    assert live["cap_in_action_units"] == 1.0
    assert live["unmatched_after_in_action_units"]["max"] == 1.96
    assert live["by_state"] == {"s": 1} and live["by_draw"] == {"extend": 1}


def test_the_headline_on_canonical_history_a_one_unit_cap_refuses_nearly_every_extend():
    out = cap.study(live_path="/nonexistent")
    o = out["overall"]
    assert o["windows"] == 2216
    assert 0.45 <= o["relation_mix"]["extend_share_of_actions"] <= 0.55
    k1 = o["extends_refused_by_cap_in_action_units"]["1.0"]["share_of_extends"]
    k4 = o["extends_refused_by_cap_in_action_units"]["4.0"]["share_of_extends"]
    assert k1 > 0.95 and k4 > 0.75
    assert o["peak_units_per_window"]["p50"] >= 3.0
    assert out["mechanism"]["cap_in_action_units"] == 1.0
    assert out["implied_vs_observed"]["live"]["refusals"] == 0     # no harvest given


# ------------------------------------------------------ action comparison

def _dump(tmp_path, events):
    p = tmp_path / "dump.json"
    p.write_text(json.dumps({"deploy": [
        {"timestamp": t, "message": "PMUS_L5_CAMPAIGN " + json.dumps(j)} for t, j in events]}))
    return str(p)


def _dec(idx, acted, sess="s1", refusal=None, inv=None, cf=None, rel=None, sampled=None):
    return {"decision_idx": idx, "decision_id": f"d{idx}", "session_id": sess,
            "safety_shell_version": "R4/cap7.50/unmatched2.50/complete1.00/conc1",
            "state": "p45-55c|tte>10m|imb1-10", "acted": acted, "refusal": refusal,
            "would_act_without_safety_cap": acted if acted != "hold" else "extend",
            "t_utc": f"2026-09-07T09:00:{idx:02d}+00:00",
            "relation_ranked": rel or [{"relation": "extend", "p": 0.6},
                                       {"relation": "complete", "p": 0.4}],
            "shadow": {"stochastic": {"sampled": sampled or (acted if acted != "hold" else "extend")},
                       "l5_target_size": {"shares_p50": 10.0}},
            "planned_safe_size": {"contracts": "5.0"},
            "inventory_before": inv or {}, "counterfactual_l5_price": cf,
            "off_distribution": False, "divergence_class": "x"}


def _order(stage, side, px, qty, filled, oid):
    return {"stage": stage, "order_id": oid, "side": side, "px": px, "qty": qty,
            "filled_qty": filled, "relation": stage.split("_")[1],
            "end_reason": "filled" if float(filled) > 0 else "deadline"}


def test_orders_sort_by_action_slot_not_alphabetically(tmp_path):
    """spring_complete_6 comes AFTER spring_open_3 in time; a string sort
    puts it first and would pair the open against the complement's lot."""
    decs = [_dec(i, "open") for i in range(3)] + [_dec(3, "hold", refusal="x"),
                                                 _dec(4, "hold", refusal="x"),
                                                 _dec(5, "complete", inv={"avg_up": "0.47"})]
    orders = [_order("spring_complete_6", "NO", "0.51", "5", "5", "late"),
              _order("spring_open_3", "YES", "0.47", "5", "5", "early")]
    ev = [("2026-09-07T09:01:00Z", {"kind": "session_end", "slug": "m", "status": "x",
                                   "decisions": decs, "orders": orders})]
    h = cmp.harvest([_dump(tmp_path, ev)])
    assert [o["order_id"] for o in h["orders"]] == ["early", "late"]
    comps = cmp.completions(h["orders"], cmp.pair_orders_with_decisions(h["orders"], h["decisions"]))
    assert len(comps) == 1 and comps[0]["order_id"] == "late" and comps[0]["engine_pair"] == "0.98"


def test_harvest_keeps_only_r4_rows_dedupes_and_pairs_orders_with_decisions(tmp_path):
    # session: OPEN YES 5@0.47 filled (slot 1, idx 0), hold cap (slot 2, idx 1),
    # COMPLETE NO 5@0.51 filled (slot 3, idx 2) -- a hold consumes its slot
    decs = [_dec(0, "open"),
            _dec(1, "hold", refusal="unmatched_usd_cap:4.9>2.50"),
            _dec(2, "complete", inv={"avg_up": "0.47", "up": "5", "dn": "0"},
                 cf={"unlocked_by_rung_bound": False, "within_old_bound_0_98": True})]
    orders = [_order("spring_open_1", "YES", "0.47", "5", "5", "o1"),
              _order("spring_complete_3", "NO", "0.51", "5", "5", "o2")]
    old = dict(_dec(9, "open"), safety_shell_version="R3L/x")
    ev = [("2026-09-07T09:01:00Z", {"kind": "session_end", "slug": "m", "status": "SPRING_MATCHED",
                                   "decisions": decs, "orders": orders}),
          ("2026-09-07T09:01:00Z", {"kind": "session_end", "slug": "m", "status": "SPRING_MATCHED",
                                   "decisions": decs, "orders": orders}),        # duplicate dump
          ("2026-09-07T08:00:00Z", {"kind": "session_end", "slug": "old", "status": "x",
                                   "decisions": [old], "orders": []}),
          ("2026-09-07T09:05:00Z", {"kind": "settlement", "slug": "m", "settled": True,
                                   "status": "REALIZED", "realized_usd": "0.10",
                                   "settled_by": "position_resolution_activity",
                                   "breakdown": {"matched_contribution_usd": "0.10"}}),
          ("2026-09-07T08:59:00Z", {"kind": "selection", "chosen": "m", "admitted": [{"rank": 1, "slug": "m"}],
                                   "refused": []})]
    h = cmp.harvest([_dump(tmp_path, ev)])
    assert h["markets"] == ["m"]
    assert len(h["decisions"]) == 3 and len(h["orders"]) == 2 and len(h["sessions"]) == 1
    assert "m" in h["settlements"] and len(h["selections"]) == 1
    pairing = cmp.pair_orders_with_decisions(h["orders"], h["decisions"])
    assert pairing["o1"]["decision_idx"] == 0 and pairing["o2"]["decision_idx"] == 2
    comps = cmp.completions(h["orders"], pairing)
    assert len(comps) == 1
    c = comps[0]
    assert c["engine_pair"] == "0.98" and c["open_lots_pair"] == "0.98"
    assert c["marginal_lots"] == [{"matched_qty": "5", "lot_px": "0.47",
                                   "marginal_pair": "0.98", "over_par": False}]
    assert c["engine_within_old_bound_0_98"] is True and c["unlocked_by_rung_bound"] is False


def test_the_three_pair_bases_disagree_exactly_as_on_market_2():
    """Market 2: YES 5.31@0.47 / NO 5.20@0.51 / YES 4.90@0.51 / NO 5.01@0.51.
    Engine basis uses ALL YES ever bought (0.4892 avg) -> 0.9992 <= par;
    open lots (0.11@0.47 + 4.90@0.51) -> 1.0191 > par; marginal 4.90 lot -> 1.02."""
    orders = [dict(_order("spring_open_6", "YES", "0.47", "5.31", "5.31", "a"), session_id="s1"),
              dict(_order("spring_complete_3", "NO", "0.51", "5.20", "5.2", "b"), session_id="s2"),
              dict(_order("spring_extend_4", "YES", "0.51", "4.90", "4.9", "c"), session_id="s3"),
              dict(_order("spring_complete_4", "NO", "0.51", "5.01", "5.01", "d"), session_id="s4")]
    pairing = {"d": _dec(63, "complete", sess="s4",
                         inv={"avg_up": "0.4891968658178256611165523996", "up": "10.21", "dn": "5.2"},
                         cf={"unlocked_by_rung_bound": True, "within_old_bound_0_98": False})}
    comps = cmp.completions(orders, pairing)
    last = comps[-1]
    assert last["engine_pair"].startswith("0.9991968") and last["engine_within_par"] is True
    assert last["open_lots_pair"].startswith("1.0191") and last["open_lots_within_par"] is False
    assert [(r["lot_px"], r["over_par"]) for r in last["marginal_lots"]] == [("0.47", False), ("0.51", True)]
    assert last["unlocked_by_rung_bound"] is True
    # and the FIRST completion (5.20 against 5.31@0.47) is 0.98 on every basis
    first = comps[0]
    assert first["open_lots_pair"] == "0.98" and first["marginal_lots"][0]["marginal_pair"] == "0.98"


def test_campaign_report_separates_sampled_acted_expected_and_labels_money(tmp_path):
    decs = [_dec(0, "open", sampled="open"),
            _dec(1, "hold", refusal="unmatched_usd_cap:4.9>2.50", sampled="extend"),
            _dec(2, "hold", refusal="unmatched_usd_cap:4.9>2.50", sampled="extend"),
            _dec(3, "complete", sampled="complete", inv={"avg_up": "0.47"},
                 cf={"unlocked_by_rung_bound": False, "within_old_bound_0_98": True})]
    orders = [_order("spring_open_1", "YES", "0.47", "5", "5", "o1"),
              _order("spring_complete_4", "NO", "0.51", "5", "5", "o2")]
    ev = [("2026-09-07T09:01:00Z", {"kind": "session_end", "slug": "m", "status": "SPRING_MATCHED",
                                   "decisions": decs, "orders": orders}),
          ("2026-09-07T09:05:00Z", {"kind": "settlement", "slug": "m", "settled": True,
                                   "status": "SETTLED_REALIZED_UNCONFIRMED", "realized_usd": None,
                                   "settled_by": "venue_market_resolved_account_flat",
                                   "breakdown": {"matched_contribution_usd": "0.10"}})]
    rep = cmp.campaign_report(cmp.harvest([_dump(tmp_path, ev)]))
    assert rep["decisions"] == 4 and rep["submitted"] == 2 and rep["filled"] == 2
    assert rep["sampled_relation"] == {"complete": 0.25, "extend": 0.5, "open": 0.25}
    assert rep["acted_relation_excl_hold"] == {"complete": 0.5, "open": 0.5}
    assert rep["cap_refusals"] == 2 and rep["hold_share"] == 0.5
    assert rep["completions"] == {"filled": 1, "unlocked_by_rung_bound": 0, "engine_over_par": 0,
                                  "open_lots_over_par": 0, "marginal_lots_over_par": 0}
    assert rep["markets_settled"] == 1
    assert rep["realized_usd_venue_sum"] is None            # flat account: no venue number
    assert rep["derived_matched_contribution_sum"] == 0.1
    md = cmp.render_md(rep)
    assert "engine 0.98, open-lots 0.98" in md and "venue realized None" in md


# ------------------------------------------------------ concurrency: idle gaps

def _ep(wid, t_first, t_last, settle, asset="BTC", dur=15, resid=0.0):
    return {"window_id": wid, "market_slug": "x", "asset": asset, "duration_minutes": dur,
            "t_first": t_first, "t_last": t_last, "t_settlement": settle,
            "completed_inventory": resid == 0.0, "final_residual_qty_signed": resid}


def test_idle_gaps_report_waiting_and_pre_settlement_entry_separately():
    eps = [_ep("a", 0, 100, 300), _ep("b", 330, 400, 600),      # waited 30 s after a settled
           _ep("c", 550, 700, 900),                              # entered 50 s BEFORE b settled
           _ep("d", 900 + 2 * 86400, 900 + 2 * 86400 + 10, 900 + 2 * 86400 + 300)]  # harvest hole
    g = conc.idle_gaps(eps)["BTC-15m"]
    assert g["episodes"] == 4 and g["transitions"] == 2
    assert g["transitions_across_a_harvest_hole_excluded"] == 1
    assert g["entered_next_before_this_settled"] == 1
    assert g["share_entered_before_settlement"] == 0.5
    assert g["settlement_to_next_first_fill_s_when_waited"]["p50"] == 30
    assert g["share_next_entered_within_60s_of_settlement"] == 1.0
    ft = conc.flat_time_share(eps)
    assert ft["overall"]["covered_days"] == 2
    assert 0 < ft["overall"]["flat_share_of_covered_days"] < 1
