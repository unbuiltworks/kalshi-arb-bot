"""Synthetic-row tests for the R5F sequence ledger and the hourly
utilization table (no real dumps)."""
import json
from decimal import Decimal as D

from src.l5_hourly_utilization import hourly_table, read_rows
from src.l5_r4_action_comparison import harvest
from src.l5_r5f_sequence_ledger import (band_quantification, campaign_report,
                                        ledger_rows, loss_partition,
                                        market_economics, market_report)

SHELL = "R5F/cap7.50/unmatched-none/complete-l5touch/conc1/maker-only/rest-model5s/action2.50"
SLUG = "aec-test-loser-2026-09-07"
T = "2026-09-07T19:%02d:00+00:00"
P = "PMUS_L5_CAMPAIGN "


def _dec(idx, sampled, acted, refusal=None, inv=None, cf=None, planned=None, open_side=None,
         wait=30.0, v2=None, off=False):
    return {"decision_id": f"{SLUG}:d{idx}", "decision_idx": idx, "session_id": f"{SLUG}:s1",
            "t_utc": T % idx, "state": "p25-45c|tte>10m|imb10-50", "off_distribution": off,
            "safety_shell_version": SHELL, "v2_recommended": v2 or sampled,
            "shadow": {"stochastic": {"sampled": sampled}},
            "would_act_without_safety_cap": sampled, "acted": acted, "refusal": refusal,
            "inventory_before": inv or {"up": "0", "dn": "0", "residual": "0", "unmatched_usd": "0.0000"},
            "planned_safe_size": planned or {"contracts": "5.00", "usd": "2.5000"},
            "next_reevaluation_s": wait, "counterfactual_l5_price": cf, "open_side": open_side}


def _order(stage, side, px, qty, filled, relation, end_reason="deadline", rest=30.0, **kw):
    return {"order_id": f"{SLUG}:{stage}", "stage": stage, "side": side, "px": px, "qty": qty,
            "filled_qty": filled, "relation": relation, "end_reason": end_reason, "rest_s": rest, **kw}


def _events():
    inv_after_open = {"up": "5.00", "dn": "0", "residual": "5.00", "avg_up": "0.40", "unmatched_usd": "2.0000"}
    decs = [
        # 0 open YES 5 @0.40 filled (yes bid 0.40)
        _dec(0, "open", "open", open_side={"yes_bid": "0.4000", "yes_ask": "0.4100", "yes_touch": "0.4000",
                                           "no_touch": "0.5900", "executed_side": "YES", "convention_side": "YES"}),
        # 1 L5 draws complete; quoted NO 5 @0.59 (touch), deadline -> execution/queue failure for 5 contracts
        _dec(1, "complete", "complete", inv=inv_after_open,
             cf={"l5_touch_px": "0.59", "executed_px": "0.59", "pair_at_touch": "0.99", "fee_safe_at_touch": False,
                 "complement_pricing": "l5_touch", "l5_consistent_above_par": False, "class": "no_difference"}),
        # 2 L5 draws extend; the bid ran to 0.15 -> entry_band_left (shell stopped adding more YES)
        _dec(2, "extend", "hold", "entry_band_left:bid=0.1500:q1_entry_ok=False", inv=inv_after_open,
             planned={"contracts": "4.00", "usd": "0.6000"}),
        # 3 L5 draws complete; touch 0.85 on NO is outside 0.20-0.80 -> no_legal_maker_price
        _dec(3, "complete", "hold", "complement_not_priceable:no_legal_maker_price", inv=inv_after_open,
             cf={"l5_touch_px": "0.85", "executed_px": "None", "pair_at_touch": "1.25", "fee_safe_at_touch": False,
                 "complement_pricing": "l5_touch", "l5_consistent_above_par": True,
                 "class": "safety_override:fee_safe_complement_bound"}, planned={"contracts": "5.00", "usd": "4.25"}),
    ]
    orders = [_order("spring_open_1", "YES", "0.40", "5.00", "5.00", "open", "filled", 3.0),
              _order("spring_complete_2", "NO", "0.59", "5.00", "0", "complete", "deadline", 31.0,
                     rest_book={"max_touch": "0.59", "min_opp_ask": "0.60", "polls": 20}, l5_touch_px="0.59")]
    session = {"kind": "session_end", "slug": SLUG, "status": "SPRING_RESIDUAL_HOLD", "submitted": 2,
               "filled_actions": 1, "decisions": decs, "orders": orders, "inventory": inv_after_open}
    settle = {"kind": "settlement", "slug": SLUG, "settled": True, "status": "REALIZED",
              "realized_usd": "-2.0000", "settled_by": "position_resolution_activity",
              "resolution": {"side": "POSITION_RESOLUTION_SIDE_SHORT"},
              "trades": {"rows": [{"t": "2026-09-07T19:00:03Z", "px": "0.4000", "qty": "5.0000", "aggr": False}]},
              "breakdown": {"bought_yes": "5.00", "bought_no": "0", "avg_yes": "0.40", "avg_no": None,
                            "cost_basis_usd": "2.0000", "matched_pairs": "0", "matched_pair_cost": None,
                            "matched_contribution_usd": "0", "directional_residual": "5.00",
                            "residual_contribution_usd": "-2.0000", "fees_usd": "venue_unreported"}}
    sel = {"kind": "selection", "n_rows": 10, "chosen": SLUG, "game_progress": {"live_events": 7},
           "admitted": [{"slug": SLUG, "rank": 1}],
           "refused": [{"slug": "x", "reason": "strict_evidence_thin_flow:1"},
                       {"slug": "y", "reason": "strict_evidence_off_distribution_or_unknown"}]}
    sel_idle = {"kind": "selection", "n_rows": 8, "chosen": None, "game_progress": {"live_events": 9},
                "admitted": [], "refused": [{"slug": "x", "reason": "strict_evidence_thin_flow:1"},
                                            {"slug": "z", "reason": "strict_evidence_thin_flow:0"},
                                            {"slug": "w", "reason": "strict_evidence_stale"}]}
    funnel = {"kind": "funnel", "cycles": 2, "universe_rows": 18, "in_distribution": 18, "admitted": 1,
              "sessions": 1, "submitted": 2, "filled": 1, "not_started": {"no_candidate": 1}}
    return [
        {"timestamp": "2026-09-07T18:58:00.000000000Z", "message": P + json.dumps(sel)},
        {"timestamp": "2026-09-07T18:58:01.000000000Z", "message": P + json.dumps({"kind": "session_start", "slug": SLUG})},
        {"timestamp": "2026-09-07T19:03:30.123456789Z", "message": P + json.dumps(session)},
        {"timestamp": "2026-09-07T19:03:30.123456789Z", "message": P + json.dumps(session)},   # duplicate pull
        {"timestamp": "2026-09-07T19:10:00.000000000Z", "message": P + json.dumps(sel_idle)},
        {"timestamp": "2026-09-07T19:10:00.100000000Z", "message": P + json.dumps(funnel)},
        {"timestamp": "2026-09-07T19:40:00.000000000Z", "message": P + json.dumps(settle)},
    ]


def _dump(tmp_path):
    p = tmp_path / "dump.txt"
    p.write_text(json.dumps({"deploy": _events()}), encoding="utf-8")
    return str(p)


def test_ledger_rows_touch_movement_and_l5_question(tmp_path):
    h = harvest([_dump(tmp_path)], "R5F/")
    rows = ledger_rows(SLUG, h)
    assert [r["idx"] for r in rows] == [0, 1, 2, 3]
    assert rows[0]["did_l5_action"] and rows[0]["touch"]["yes_equiv"] == "0.40"
    # the complete's touch is the NO order at 0.59 -> yes-equivalent 0.41 (a yes ask)
    assert rows[1]["touch"] == {"source": "order", "side": "NO", "px": "0.59", "yes_equiv": "0.41", "basis": "yes_ask"}
    assert rows[1]["outcome"] == "deadline" and rows[1]["touch_move"]["during_rest"]["max_touch"] == "0.59"
    assert rows[0]["touch_move"]["cents"] == "1.0"
    # the band refusal quotes its bid; the yes-equivalent moved 0.41 -> 0.15
    assert rows[2]["refusal_class"] == "entry_band_left" and rows[2]["touch"]["px"] == "0.1500"
    assert rows[1]["touch_move"]["cents"] == "-26.0"
    assert rows[3]["refusal_class"] == "no_legal_maker_price" and not rows[3]["did_l5_action"]
    assert rows[3]["why_not"].startswith("complement_not_priceable")
    assert rows[3]["touch"]["side"] == "NO" and rows[3]["pair_economics"]["above_par"] is True
    assert rows[0]["inventory_after"]["up"] == "5.00" and rows[3]["inventory_after"]["residual"] == "5.00"


def test_loss_partition_reconciles_and_names_categories(tmp_path):
    h = harvest([_dump(tmp_path)], "R5F/")
    rows = ledger_rows(SLUG, h)
    econ = market_economics(SLUG, h)
    assert econ["paying_side"] == "NO" and econ["venue_trades_n"] == 1
    assert "not present" in econ["credited_rebates"]
    part = loss_partition(SLUG, rows, econ)
    p = part["partition_of_residual_loss"]
    # all 5 residual contracts met the quoted complete at idx 1 first: execution/queue failure
    assert p["execution_queue_failure"]["contracts"] == "5.00" and p["execution_queue_failure"]["usd"] == "-2.0000"
    assert p["remaining_shell_distortion"]["usd"] == "0.0000"
    assert part["partition_check"]["reconciles"]
    # had the 0.59 complete filled: 5 x (1 - 0.59) = 2.05 saved (fill-assumed)
    assert part["counterfactual_saving_fill_assumed"]["execution_queue_failure"] == "2.0500"
    # the entry_band_left refusal is recorded as loss avoided, not as residual loss
    ev = p["remaining_shell_distortion"]["events"]
    assert ev and ev[0]["removed_residual"] is False and ev[0]["loss_avoided_by_not_extending_usd"] == "0.6000"
    assert p["execution_queue_failure"]["events"][0]["subclass"] == "sat_at_touch_unfilled"
    pr = part["price_run"]
    assert pr["yes_equiv_at_first_fill"] == "0.40" and pr["left_legal_range_0_20_0_80_at_idx"] == 2
    assert pr["minutes_first_fill_to_leaving_legal_range"] == 2.0
    m = market_report(SLUG, h)
    assert m["verdict"].startswith("VERDICT " + SLUG) and "execution/queue failure: -2.0000" in m["verdict"]


def test_complete_whose_touch_ran_away_during_rest_is_transfer(tmp_path):
    events = _events()
    # the same market, but the complete's rest_book shows the NO touch running from 0.59 to 0.70
    body = json.loads(events[2]["message"][len(P):])
    body["orders"][1]["rest_book"] = {"max_touch": "0.70", "min_opp_ask": "0.71", "polls": 27}
    events[2]["message"] = P + json.dumps(body)
    events[3] = events[2]
    p = tmp_path / "dump.txt"
    p.write_text(json.dumps({"deploy": events}), encoding="utf-8")
    h = harvest([str(p)], "R5F/")
    part = loss_partition(SLUG, ledger_rows(SLUG, h), market_economics(SLUG, h))
    parts = part["partition_of_residual_loss"]
    assert parts["market_selection_transfer"]["usd"] == "-2.0000"
    assert parts["market_selection_transfer"]["events"][0]["subclass"] == "touch_ran_away_during_rest"
    assert parts["execution_queue_failure"]["usd"] == "0.0000"
    assert part["partition_check"]["reconciles"]


def test_band_quantification_counterfactual_given_resolution(tmp_path):
    h = harvest([_dump(tmp_path)], "R5F/")
    rep = campaign_report(h)
    b = rep["band"]
    assert b["by_band"]["no_legal_maker_price"]["decisions"] == 1
    assert b["by_band"]["entry_band_left"]["decisions"] == 1
    rows = {x["band"]: x for x in b["rows"]}
    nl = rows["no_legal_maker_price"]
    # refused COMPLETE: NO 5 @0.85, pair 0.40 + 0.85 = 1.25 (above par); NO paid, so vs the actual
    # residual loss the pair would have been +5 x (1 - 0.85) = +0.75
    assert nl["order_refused"] == {"relation": "complete", "side": "NO", "px": "0.85", "qty": "5.00", "usd": "4.2500"}
    assert nl["pair_cost_if_filled"] == "1.2500" and nl["above_par"] is True
    assert nl["counterfactual_delta_vs_actual_usd"] == "0.7500"
    eb = rows["entry_band_left"]
    # refused EXTEND: YES 4 @0.15 held to a NO settlement = -0.60
    assert eb["order_refused"]["side"] == "YES" and eb["counterfactual_delta_vs_actual_usd"] == "-0.6000"
    assert b["counterfactual_total_delta_vs_actual_usd"] == "0.1500"
    assert b["recommendation"].startswith("REMOVE")
    assert rep["loss_partition_totals_usd"]["execution_queue_failure"] == "-2.0000"


def test_hourly_table_idle_and_binding_link(tmp_path):
    rows, stats = read_rows([_dump(tmp_path)])
    assert stats["duplicates"] == 1 and stats["truncated_rows"] == 0
    table = {h["hour_utc"]: h for h in hourly_table(rows)}
    h18 = table["2026-09-07T18:00Z"]
    assert h18["admissions_chosen"] == 1 and h18["sessions"]["started"] == 1
    assert h18["universe_rows_scored"] == 10 and h18["on_distribution_rows"] == 9
    assert h18["idle_minutes"] == 0
    h19 = table["2026-09-07T19:00Z"]
    assert h19["orders_submitted"] == 2 and h19["fills"] == 1 and h19["settlements"] == 1
    assert h19["top_refusals_in_session"][0][0] == "entry_band_left"
    # the session ran 18:58 -> 19:03; the 19:10 selection cycle was idle with live events -> admission link
    assert h19["idle_minutes"] > 0
    b = h19["binding_link_when_idle"]
    assert b["link"] == "admission" and b["dominant_refusal"] == "strict_evidence_thin_flow"
    assert b["share"] == round(2 / 3, 4) and b["live_events_max"] == 9
