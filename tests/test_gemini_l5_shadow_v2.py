from decimal import Decimal as D
import json

from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb

SYM = "GEMI-BTC05M2609051200-UP"
EXP = tb.ticker_expiry_epoch("BTC05M2609051200")
T0 = EXP - 290.0                     # inside MIN_TTE..ENTRY_MAX_TTE


def _bbo(t, bid="0.48", ask="0.50"):
    return {"symbol": SYM, "t": t, "bid": bid, "bid_sz": "500",
            "ask": ask, "ask_sz": "500"}


def _p(t, typ, px):
    return {"timestampms": int(t * 1000), "type": typ, "price": px, "amount": "1"}


def _model(**full):
    """Tiny v2-shaped model: full-state counts by key, marginal fallback."""
    return {"_sha256_16": "test", "marginal": {"extend|q<5": 5, "complete|q<5": 5,
                                                "open|q<5": 1},
            "base": {}, "full": dict(full), "cadence_modal": {}}


def test_strict_matched_pair_sequence_open_then_complete():
    # after the first YES fill (residual 1 unit) the state's modal action
    # is COMPLETE; the complement rests as a YES ask at 1-0.49=0.51
    model = _model(**{"p45-55c|tte2-5m|imb1-10|open|q<5": {"complete|q<5": 10}})
    bbo = [_bbo(T0 - 5), _bbo(T0 + 60), _bbo(T0 + 120)]
    prints = [_p(T0 + 5, "sell", "0.47"), _p(T0 + 20, "buy", "0.52")]
    r = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                         policy="modal", mode="strict")
    assert r["status"] == "MATCHED"
    assert [d["acted"] for d in r["decisions"][:2]] == ["open", "complete"]
    assert [o["side"] for o in r["orders"]] == ["YES", "NO"]
    assert r["orders"][1]["px"] == "0.49"
    assert r["pair_cost_after_fees"] == "0.99" and r["fees_theoretical"] == "0.02"
    assert r["pair_pnl_theoretical"] == "0.01"
    assert all(d["venue"] == "GEMINI" for d in r["decisions"])
    assert r["fills"][0]["adverse"] is False    # mid 0.49 > 0.48 join


def test_strict_needs_a_print_through_price_optimistic_accepts_at_price():
    model = _model()
    bbo = [_bbo(T0 - 5), _bbo(T0 + 120)]
    prints = [_p(T0 + 5, "sell", "0.48")]
    strict = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                              policy="modal", mode="strict")
    opt = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                           policy="modal", mode="optimistic")
    assert strict["status"] == "NO_FILL" and strict["orders"]
    assert opt["status"] == "RESIDUAL_HOLD"
    assert opt["outcome_proxy"] == "AMBIGUOUS" and opt["hold_pnl_theoretical"] is None


def test_touch_moved_cancels_resting_order_before_a_late_print():
    model = _model()
    bbo = [_bbo(T0 - 5), _bbo(T0 + 3, bid="0.47"), _bbo(T0 + 120, bid="0.47")]
    prints = [_p(T0 + 10, "sell", "0.40")]
    r = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                         policy="modal", mode="strict")
    o = r["orders"][0]
    assert o["filled"] is False and o["cancel_reason"] == "touch_moved"
    assert o["rest_until"] == T0 + 3


def test_residual_cap_holds_after_three_units_and_sell_is_noop():
    key = "p45-55c|tte2-5m|imb1-10|%s|q<5"
    model = _model(**{key % "open": {"extend|q<5": 10},
                      key % "extend": {"extend|q<5": 10}})
    bbo = [_bbo(T0 - 5), _bbo(T0 + 200)]
    prints = [_p(T0 + 5, "sell", "0.47"), _p(T0 + 12, "sell", "0.47"),
              _p(T0 + 20, "sell", "0.47"), _p(T0 + 40, "sell", "0.47")]
    r = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                         policy="modal", mode="strict")
    assert len(r["orders"]) == 3 and r["inventory"]["behavioral_residual"] == 3.0
    capped = [d for d in r["decisions"] if str(d["refusal"]).startswith("residual_cap")]
    assert capped and capped[0]["acted"] == "hold"
    assert r["status"] == "RESIDUAL_HOLD"
    # hold settles by the mid proxy only when it is unambiguous
    bbo2 = bbo + [_bbo(EXP - 1, bid="0.85", ask="0.90")]
    r2 = gs.replay_window(SYM, bbo2, prints, model=model, art=None,
                          policy="modal", mode="strict")
    assert r2["outcome_proxy"] == "UP" and D(r2["hold_pnl_theoretical"]) > 0


def test_off_distribution_with_inventory_holds():
    model = _model()                     # no full states -> backed off
    bbo = [_bbo(T0 - 5), _bbo(T0 + 200)]
    prints = [_p(T0 + 5, "sell", "0.47")]
    r = gs.replay_window(SYM, bbo, prints, model=model, art=None,
                         policy="modal", mode="strict")
    assert r["decisions"][1]["refusal"] == "off_distribution_hold"
    assert len(r["orders"]) == 1


def test_summarize_counts_campaigns_fills_and_holds():
    model = _model(**{"p45-55c|tte2-5m|imb1-10|open|q<5": {"complete|q<5": 10}})
    bbo = [_bbo(T0 - 5), _bbo(T0 + 120)]
    rows = [gs.replay_window(SYM, bbo, [_p(T0 + 5, "sell", "0.47"),
                                        _p(T0 + 20, "buy", "0.52")],
                             model=model, art=None, policy="modal", mode="strict"),
            gs.replay_window(SYM, bbo, [], model=model, art=None,
                             policy="modal", mode="strict"),
            {"symbol": "x", "status": "NO_QUOTE_COVERAGE"}]
    s = gs.summarize(rows)
    assert s["windows_replayed"] == 3 and s["campaigns"] == 2
    assert s["two_sided_fill_campaigns"] == 1 and s["one_sided_holds"] == 0
    assert s["campaign_first_fill_rate"] == 50.0
    assert s["net_theoretical_total"] == "0.01"


def test_live_shadow_step_places_nothing_and_reports_executability():
    from datetime import datetime, timezone
    sym = gs.nearest_windows(datetime.fromtimestamp(T0, timezone.utc))[0]
    raw = {"bids": [{"price": "0.48", "amount": "500"}],
           "asks": [{"price": "0.50", "amount": "500"}]}
    rec = gs.live_shadow_step(sym, raw, T0, model=_model(), art=None)
    assert rec["status"] == "SHADOW_ONLY" and rec["orders_placed"] == 0
    assert rec["would_side"] == "YES" and rec["would_price"] == "0.48"
    assert rec["complement_bound"] == "0.49"
    ex = rec["executability"]
    assert ex["priceable_side"] and ex["in_band"] and ex["post_only_ok"]
    assert ex["fee_safe_complement_exists"] and ex["tte_ok"]
    assert "l5_target_size" in rec["shadow"]


def test_run_live_counts_fetch_errors_and_never_orders(tmp_path):
    clock = iter([0.0, 0.0, 100.0, 100.0]).__next__
    def fetch(url):
        raise RuntimeError("blocked")
    rep = gs.run_live(1.0, fetch, out=tmp_path / "live.jsonl", poll_s=0,
                      sleep=lambda s: None, clock=clock)
    assert rep["fetch_errors"] == 2 and rep["orders_placed"] == 0
    assert rep["executable_now"] == 0


def test_one_sided_book_rows_never_crash_the_replay():
    """LIVE 21:10:23Z: the Gemini book went one-sided near expiry and the
    vendored outcome proxy raised on a None side."""
    model = _model()
    bbo = [_bbo(T0 - 5), {"symbol": SYM, "t": T0 + 30, "bid": None, "bid_sz": None,
                          "ask": "0.50", "ask_sz": "5"},
           _bbo(T0 + 100), {"symbol": SYM, "t": EXP - 1, "bid": "0.90",
                            "bid_sz": "1", "ask": None, "ask_sz": None}]
    r = gs.replay_window(SYM, bbo, [_p(T0 + 5, "sell", "0.47")], model=model,
                         art=None, policy="modal", mode="strict")
    assert r["status"] == "RESIDUAL_HOLD"
    assert r["one_sided_book_waits"] >= 1
    assert r["outcome_proxy"] == "AMBIGUOUS"      # last TWO-SIDED mid 0.49
    assert gs._mid_at(bbo, EXP) == D("0.49")


def test_action_scoring_measures_distance_and_post_fill_calibration():
    decs = ([{"p_complete": 0.4, "p_extend": 0.6, "sampled": "complete", "acted": "complete",
              "residual_units": 1.0, "off_distribution": False}] * 4 +
            [{"p_complete": 0.4, "p_extend": 0.6, "sampled": "extend", "acted": "hold",
              "refusal": "residual_cap:3", "residual_units": 1.0, "off_distribution": False}] * 6)
    rows = [{"decisions": decs, "status": "RESIDUAL_HOLD"}]
    sc = gs.action_scoring(rows)
    assert sc["scored_decisions"] == 10
    assert sc["expected_relation_freq_l5"]["complete"] == 0.4
    assert sc["sampled_relation_freq"]["complete"] == 0.4 and sc["l1_sampled_vs_l5"] == 0.0
    assert sc["acted_relation_freq"]["complete"] == 1.0          # only non-hold acts count
    assert sc["hold_share_of_decisions"] == 0.6 and sc["hold_refusals"] == {"residual_cap": 6}
    assert sc["post_fill"]["complete_freq_gap_pp_sampled"] == 0.0
    assert sc["calibration_by_p_complete_bin"]["0.4"]["sampled_complete_rate"] == 0.4


def test_research_arm_verdict_reads_only_the_frozen_spec():
    spec = json.load(open("analysis/l5/gemini_research_arm_stochastic.json"))
    assert spec["arm"] == "reprice_180s:stochastic" and spec["policy"]["cap_fallback"] == "NONE (not declared)"
    v = gs.research_arm_verdict([], spec)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE" and v["spec_sha"] == spec["_sha256_16"]


def _row(decs, status="NO_FILL", entry_t=1_788_700_000.0):
    return {"status": status, "entry_t": entry_t, "symbol": "GEMI-X",
            "shell": "reprice_180s", "policy": "stochastic", "decisions": decs}


def _d(state, off, refusal=None, acted="hold", sampled="extend", residual=1.0,
       tte_s=120.0, yes_bid="0.45"):
    return {"state": state, "off_distribution": off, "refusal": refusal,
            "acted": acted, "sampled": sampled, "v2_recommended": "extend",
            "p_complete": 0.4, "p_extend": 0.6, "state_n": 3 if off else 40,
            "residual_units": residual, "tte_s": tte_s, "yes_bid": yes_bid,
            "last_rel": "extend"}


def test_off_distribution_attribution_names_component_and_cause():
    """OWNER 5558167805 #2/#4: state key, absent component, observation and
    cause class per off-distribution decision; cross-tab with the entry
    band and post-fill; nothing added or smoothed."""
    model = {"base": {"p25-45c|tte30-120s|flat": {"open|q<5": 5},
                      "p25-45c|tte2-5m|imb1-10": {"extend|q<5": 5},
                      "p45-55c|tte30-120s|imb1-10": {"extend|q<5": 5}},
             "full": {"p45-55c|tte30-120s|imb1-10|extend|q5-15": {"complete|q<5": 3},
                      "p45-55c|tte30-120s|imb1-10|open|q5-15": {"extend|q<5": 3}}}
    rows = [_row([
        _d("p25-45c|tte>10m|imb1-10", True, refusal="off_distribution_hold"),        # (a) impossible TTE
        _d("p25-45c|tte30-120s|imb1-10", True, refusal="entry_band_left:0.19"),       # (b) pair unseen
        _d("p25-45c|tte30-120s|imb1-10", True, refusal="entry_band_left:0.18"),
        _d("p<10c|tte30-120s|imb1-10", True, refusal="off_distribution_hold",
           yes_bid="0.05"),                                                       # (b) price band unseen
        _d("p45-55c|tte30-120s|imb1-10", True, residual=0.0,
           refusal="off_distribution_hold"),                                      # (c) imbalance w/o residual
        _d("p45-55c|tte30-120s|imb1-10", True, refusal="off_distribution_hold"),  # (b) thin full key
        _d("p25-45c|tte2-5m|imb1-10", False, acted="extend"),
        _d("p25-45c|tte2-5m|imb1-10", False, acted="hold", refusal="entry_band_left:0.20"),
    ])]
    out = gs.off_distribution_attribution(rows, model)
    assert out["scored_decisions"] == 8 and out["off_distribution_decisions"] == 6
    assert out["off_distribution_rate_pct"] == 75.0
    bs = out["by_state"]
    assert bs["p25-45c|tte>10m|imb1-10"]["cause"].startswith("(a) adapter")
    assert bs["p25-45c|tte30-120s|imb1-10"]["absent_components"] == ["full_combination"]
    assert bs["p25-45c|tte30-120s|imb1-10"]["cause"].startswith("(b) genuinely unseen")
    assert bs["p25-45c|tte30-120s|imb1-10"]["refusals"] == {"entry_band_left": 2}
    assert bs["p<10c|tte30-120s|imb1-10"]["absent_components"] == ["price_band"]
    assert bs["p45-55c|tte30-120s|imb1-10"]["n"] == 2
    assert sum(1 for c in out["causes"] if c.startswith("(c)")) == 1
    # post-fill, base seen, clone key |extend|q<5 absent while |extend|q5-15
    # exists -> the size-cap override leaked into last_size
    assert any(c.startswith("(a) translation: the size-cap")
               for c in bs["p45-55c|tte30-120s|imb1-10"]["causes"])
    assert sum(bs["p45-55c|tte30-120s|imb1-10"]["causes"].values()) == 2
    # pre-fill in a base state seen only via post-completion flat states
    pre = gs.off_distribution_attribution([_row([
        {**_d("p25-45c|tte30-120s|flat", True, residual=0.0, refusal="entry_band_left:0.19"),
         "last_rel": None}])],
        {"base": {"p25-45c|tte30-120s|flat": {"open|q<5": 5}},
         "full": {"p25-45c|tte30-120s|flat|complete|q5-15": {"open|q<5": 5}}})
    e = pre["by_state"]["p25-45c|tte30-120s|flat"]
    assert e["cause"].startswith("(b) unseen transfer state: L5 never OPENED")
    assert e["absent_components"] == ["pre-fill open in price_band+tte"]
    assert out["cross_tab"] == {"entry_band_left_decisions": 3,
                                "entry_band_left_and_off_distribution": 2,
                                "off_states_also_entry_band_states": 1,
                                "off_decisions_post_fill": 5,
                                "off_decisions_pre_fill": 1}
    assert bs["p25-45c|tte30-120s|imb1-10"]["observation"]["yes_bid_example"] == "0.45"
    # no decisions -> empty, never an error
    assert gs.off_distribution_attribution([], model)["off_distribution_decisions"] == 0


def test_sampled_to_acted_funnel_separates_policy_from_shell():
    rows = [_row([
        _d("s", False, acted="extend", sampled="extend"),
        _d("s", False, acted="extend", sampled="extend"),
        _d("s", False, acted="hold", sampled="extend", refusal="entry_band_left:0.19"),
        _d("s", False, acted="hold", sampled="extend", refusal="residual_cap:3>=3"),
        _d("s", False, acted="complete", sampled="complete"),
        _d("s", False, acted="complete", sampled="extend"),
    ])]
    f = gs.sampled_to_acted_funnel(rows)
    assert f["scored_decisions"] == 6
    e = f["per_sampled_relation"]["extend"]
    assert e == {"sampled_n": 5, "acted_as_sampled": 2,
                 "converted_to": {"complete": 1},
                 "held_by_reason": {"entry_band_left": 1, "residual_cap": 1}}
    assert f["acted_as_sampled_share"] == 0.5 and f["held_share"] == round(2 / 6, 4)
    assert f["held_by_reason"] == {"entry_band_left": 1, "residual_cap": 1}


def _book2(bid, ask, tte=200):
    from src import l5_venue_adapter as va
    return va.GeminiAdapter().normalize(
        {"symbol": "GEMI-BTC05M2608302330-UP",
         "t": tb.ticker_expiry_epoch("BTC05M2608302330") - tte,
         "bid": bid, "ask": ask})


def test_behavioral_state_key_is_independent_of_the_safety_cap():
    """OWNER 5558477213 A: changing ONLY the safety cap (the executable
    unit size) cannot change the behavioral state key, the behavioral
    last_size or the off-distribution classification."""
    from src import l5_venue_adapter as va
    model = {"_sha256_16": "t", "marginal": {"extend|q<5": 5, "complete|q<5": 5, "open|q<5": 1},
             "base": {"p45-55c|tte2-5m|imb1-10": {"extend|q5-15": 4, "complete|q5-15": 3}},
             "full": {"p45-55c|tte2-5m|imb1-10|open|q5-15": {"extend|q5-15": 3}},
             "cadence_modal": {}}
    art = {"size_policy": {"by_state_relation": {
        "p45-55c|tte2-5m|flat|open": {"n": 50, "shares_p50": 10.0}}}}
    book = _book2("0.48", "0.50")
    keys = []
    for unit in (D("0.02"), D("1"), D("25")):        # three different caps
        inv = va.L5Inventory()
        inv.record(True, D("0.48"), unit, "open")
        inv.last_size_behavioral = gs.behavioral_size_bucket(art, "p45-55c|tte2-5m|flat", "open")
        prop = gs.proposal_v2(model, book, inv)
        lk = prop["lookup"]
        keys.append((lk["state"], lk["last_size_behavioral"], prop["off_distribution"],
                     lk["off_distribution_v1"]))
    assert len(set(keys)) == 1
    st, lsb, off2, off1 = keys[0]
    assert st == "p45-55c|tte2-5m|imb1-10" and lsb == "q5-15"
    assert off2 is False                                # canonical key |open|q5-15 exists
    assert off1 is True                                 # v1 key |open|q<5 does not
    assert gs.behavioral_size_bucket(None, "x", "open") == "q<5"


def test_pre_fill_lookup_is_open_and_off_means_base_state_absent():
    """OWNER 5558477213 B: the canonical history keys no first-in-window
    transition, so pre-fill the faithful relation is OPEN and
    off-distribution means the BASE state is absent; v1's flag is kept."""
    from src import l5_venue_adapter as va
    model = {"_sha256_16": "t", "marginal": {"extend|q<5": 5, "complete|q<5": 5, "open|q<5": 1},
             "base": {"p<10c|tte2-5m|flat": {"open|q>40": 6, "open|q15-40": 3}},
             "full": {"p<10c|tte2-5m|flat|complete|q>40": {"open|q>40": 6}},
             "cadence_modal": {}}
    inv = va.L5Inventory()
    seen = gs.proposal_v2(model, _book2("0.05", "0.06"), inv)      # p<10c, base seen
    assert seen["recommended_relation"] == "open"
    assert seen["relation_ranked"] == [{"relation": "open", "p": 1.0}]
    assert seen["off_distribution"] is False and seen["lookup"]["off_distribution_v1"] is True
    assert seen["lookup"]["pre_fill"] and seen["lookup"]["lookup_tier"].startswith("pre_fill")
    assert seen["state_n"] == 9
    unseen = gs.proposal_v2(model, _book2("0.30", "0.31"), inv)     # p25-45c, base absent
    assert unseen["off_distribution"] is True and unseen["recommended_relation"] == "open"


def test_row_level_before_after_under_the_canonical_lookup():
    model = {"base": {"p<10c|tte2-5m|flat": {"open|q>40": 6},
                      "p45-55c|tte30-120s|imb1-10": {"extend|q<5": 5}},
             "full": {"p45-55c|tte30-120s|imb1-10|open|q5-15": {"extend|q<5": 3}},
             "marginal": {"open|q<5": 1}}
    art = {"size_policy": {"by_state_relation": {
        "p45-55c|tte30-120s|imb1-10|open": {"n": 50, "shares_p50": 10.0}}}}
    legacy_pre = {**_d("p<10c|tte2-5m|flat", True, refusal="entry_band_left:0.05",
                       residual=0.0, tte_s=200.0, yes_bid="0.05"), "yes_ask": "0.06",
                  "last_rel": None}
    legacy_post = _d("p45-55c|tte30-120s|imb1-10", True, refusal="off_distribution_hold",
                     residual=1.0, tte_s=60.0)
    legacy_post["last_rel"] = "open"
    v2_row = {**_d("p25-45c|tte30-120s|flat", False, acted="open", residual=0.0),
              "lookup": {"lookup_version": 2, "state": "p25-45c|tte30-120s|flat",
                         "state_v1": "p55-75c|tte30-120s|flat", "off_distribution_v1": True,
                         "lookup_tier": "pre_fill:first_action_is_open", "pre_fill": True}}
    out = gs.off_distribution_attribution([_row([legacy_pre, legacy_post, v2_row])], model, art=art)
    ba = out["before_after_canonical_lookup"]
    assert ba["legacy_rows"] == 2 and ba["recorded_v2_rows"] == 0   # v2_row is not off
    assert ba["off_v1_rows"] == 2 and ba["off_corrected_rows"] == 0
    assert ba["rows_with_both_flags"] == 1
    assert ba["off_v1_rate_pct_on_both"] == 100.0 and ba["off_v2_rate_pct_on_both"] == 0.0
    rows = {r["state"]: r for r in out["off_distribution_rows"]}
    assert rows["p<10c|tte2-5m|flat"]["state_corrected"] == "p<10c|tte2-5m|flat"
    assert rows["p<10c|tte2-5m|flat"]["off_corrected"] is False
    assert rows["p<10c|tte2-5m|flat"]["price_input"]["cheaper_touch"] == 0.05
    assert rows["p45-55c|tte30-120s|imb1-10"]["last_size_behavioral"] == "q5-15"
    assert rows["p45-55c|tte30-120s|imb1-10"]["off_corrected"] is False


def test_rows_split_by_proven_lookup_version():
    v2 = {**_d("s", False, acted="open"), "lookup": {"lookup_version": gs.LOOKUP_VERSION}}
    legacy = _d("s", True)
    odd = {**_d("s", False), "lookup": {"lookup_version": 1}}
    rows = [_row([v2, legacy, odd]), _row([v2], entry_t=1_788_700_300.0)]
    sp = gs.split_rows_by_lookup(rows)
    assert sp["provenance"]["v2_decisions"] == 2 and sp["provenance"]["legacy_decisions"] == 1
    assert sp["provenance"]["unknown_decisions"] == 1 and sp["provenance"]["v2_windows"] == 2
    assert all(d.get("lookup", {}).get("lookup_version") == gs.LOOKUP_VERSION
               for r in sp["v2"] for d in r["decisions"])
    assert sp["provenance"]["legacy_label"].startswith("UNVERIFIABLE_LEGACY")
    assert len(gs.CODE_SHA) == 16


def test_lookup_v3_keeps_imbalance_and_size_in_the_same_model_scale():
    """The 400-window replay left 15 post-fill rows off under v2 on the
    impossible key p25-45c|tte2-5m|imb1-10|open|q>40 (a q>40 open never
    leaves imb1-10 in L5 history). With the residual in the same
    behavioral shares as the size the key is p25-45c|tte2-5m|imb10-50|
    open|q>40 (n 9) -> in-distribution; still independent of the cap."""
    from src import l5_venue_adapter as va
    model = {"_sha256_16": "t", "marginal": {"extend|q<5": 5, "complete|q<5": 5, "open|q<5": 1},
             "base": {"p25-45c|tte2-5m|imb10-50": {"extend|q>40": 4, "complete|q>40": 5}},
             "full": {"p25-45c|tte2-5m|imb10-50|open|q>40": {"extend|q>40": 9}},
             "cadence_modal": {}}
    art = {"size_policy": {"by_state_relation": {
        "p25-45c|tte2-5m|flat|open": {"n": 21, "shares_p50": 43.47}}}}
    keys = []
    for unit in (D("0.02"), D("1"), D("25")):
        inv = va.L5Inventory()
        inv.record(True, D("0.30"), unit, "open")
        gs.record_behavioral_fill(inv, art, "p25-45c|tte2-5m|flat", "open", True)
        assert inv.behavioral_residual() == 1.0                # units (safety scale)
        assert inv.behavioral_residual_shares() == 43.47      # model scale
        prop = gs.proposal_v2(model, _book2("0.30", "0.31"), inv)
        lk = prop["lookup"]
        keys.append((lk["state"], lk["last_size_behavioral"], prop["off_distribution"],
                     lk["residual_shares_behavioral"], lk["lookup_version"]))
    assert len(set(keys)) == 1
    st, lsb, off, shares, ver = keys[0]
    assert st == "p25-45c|tte2-5m|imb10-50" and lsb == "q>40" and off is False
    assert shares == 43.47 and ver == 3
    # a NO complete of behavioral size 10 flattens the model-scale residual
    inv = va.L5Inventory()
    inv.record(True, D("0.30"), D("1"), "open")
    gs.record_behavioral_fill(inv, art, "p25-45c|tte2-5m|flat", "open", True)
    inv.record(False, D("0.70"), D("1"), "complete")
    inv.record_behavioral_shares(False, 43.47, "q>40")
    assert inv.behavioral_residual_shares() == 0.0
    # superseded v2 rows are excluded from the gate and labelled
    sp = gs.split_rows_by_lookup([_row([{**_d("s", False, acted="open"), "lookup": {"lookup_version": 2}},
                                        {**_d("s", False, acted="open"), "lookup": {"lookup_version": 3}}])])
    assert sp["provenance"]["v2_decisions"] == 1 and sp["provenance"]["unknown_decisions"] == 1
    assert sp["provenance"]["superseded_versions"]["2"]["label"].startswith("superseded")
