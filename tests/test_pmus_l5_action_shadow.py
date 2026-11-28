"""Regressions for the per-market L5 action shadow (issue #167 comments
5519672916/5519746304): read-only clone state machine + ranked
next-action emission from the frozen canonical model, alongside — never
instead of — the pair gate."""
from __future__ import annotations

import re
import time
from decimal import Decimal as D
from pathlib import Path

from src import pmus_l5_action_shadow as l5s
from src import pmus_aws_supervisor as sup
from src import pmus_ws_evidence_recorder as rec


def _tiny_model(modal_rel="open"):
    """Injected model with known modals so tests never depend on the
    trained artifact's data."""
    dist = {f"{modal_rel}|q<5": 0.6, "complete|q<5": 0.3, "sell|q<5": 0.1}
    states = {}
    from src.polymarket_l5.action_model import (state_key, PRICE_BANDS,
                                                TTE_BUCKETS,
                                                IMBALANCE_BUCKETS, _bucket)
    for band, _ in [(0.3, None), (0.5, None), (0.6, None), (0.8, None)]:
        for imb in (0.0, 1.5, 20.0):
            sk = state_key(band, l5s.SPORTS_TTE_DEFAULT_S, imb)
            states[sk] = {"n": 100, "action_probs": dict(dist),
                          "modal_action": f"{modal_rel}|q<5"}
    return {"states": states, "_marginal": dict(dist),
            "_sha256_16": "testmodel0000000"}


def _trade(t, px, direction):
    return {"t": t, "px": px, "dir": direction}


def test_frozen_canonical_model_loads_with_provenance():
    m = l5s.load_model()
    assert len(m["_sha256_16"]) == 16
    rels = {a.split("|")[0] for a in m["_marginal"]}
    assert rels <= {"open", "extend", "complete", "sell"}
    assert m["n_states"] == len(m["states"]) > 0


def test_clone_open_then_complete_builds_matched_inventory():
    m = _tiny_model("open")
    # flat -> modal 'open' works the YES side; a SELL print at 0.45 fills
    trades = [_trade(1.0, "0.4500", "sell")]
    st = l5s.simulate_clone(trades, m)
    assert (st.up_qty, st.dn_qty, st.residual) == (1, 0, 1)
    assert st.last_rel == "open"
    # with residual +1 the tiny model's modal is still 'open' -> treated
    # as YES side again; a BUY print cannot fill a YES join -> untouched
    st2 = l5s.simulate_clone(trades + [_trade(2.0, "0.5000", "buy")], m)
    assert (st2.up_qty, st2.dn_qty) == (1, 0)
    # a model whose modal is 'complete' works the deficit (NO) side and a
    # BUY print at 0.55 fills the NO join at 1-0.55=0.45
    m2 = _tiny_model("complete")
    trades3 = [_trade(1.0, "0.4500", "sell"), _trade(2.0, "0.5500", "buy")]
    # first print: flat -> complete abstains (no deficit side yet)
    st3 = l5s.simulate_clone(trades3, m2)
    assert st3.n_fills == 0
    # seed an open first via mixed model states: open-modal model for leg1
    st4 = l5s.CloneState()
    st4.record_fill(True, D("0.45"), 1.0)
    st4.last_px = D("0.45")
    assert st4.residual == 1 and st4.last_rel == "open"
    st4.record_fill(False, D("0.45"), 2.0)
    assert st4.matched == 1 and st4.residual == 0
    assert st4.last_rel == "complete"
    assert st4.pair_cost() == "0.90"


def test_extend_records_persistence_and_last_rel():
    st = l5s.CloneState()
    st.record_fill(True, D("0.40"), 1.0)
    st.record_fill(True, D("0.41"), 2.0)      # surplus side again
    assert st.last_rel == "extend"            # persist = extend after extend
    assert st.residual == 2
    st.record_fill(False, D("0.55"), 3.0)
    assert st.last_rel == "complete"          # switch = complete after extend
    assert st.matched == 1


def test_market_shadow_row_emits_ranked_actions_and_comparison():
    m = _tiny_model("open")
    s = {"bid": "0.4500", "ask": "0.4550",
         "trades": [_trade(time.time(), "0.4500", "sell")]}
    row = l5s.market_shadow_row(s, time.time(),
                                {"gate_status": "NOT_READY"}, model=m)
    assert row["model_sha256_16"] == "testmodel0000000"
    assert row["sim_inventory"]["up"] == 1
    ranked = row["next_action_ranked"]
    assert ranked == sorted(ranked, key=lambda r: -r["p"])
    assert row["comparison"]["pair_gate_next"] == "wait"
    assert row["comparison"]["l5_next_relation"] == "open"
    assert row["comparison"]["divergent"] is True
    assert "THEORETICAL" in row["labels"]
    assert row["tte_adapter"] == "sports_default_coarsest_bucket"


def test_off_distribution_state_is_reported_honestly():
    m = {"states": {}, "_marginal": {"open|q<5": 1.0},
         "_sha256_16": "testmodel0000000"}
    s = {"bid": "0.5000", "trades": []}
    row = l5s.market_shadow_row(s, time.time(),
                                {"gate_status": "NOT_READY"}, model=m)
    assert row["off_distribution"] is True and row["state_n"] == 0


def test_recorder_rows_carry_l5_shadow_and_survive_shadow_failure(
        monkeypatch):
    st = {}
    now = time.time()
    rec.apply_events(st, {"marketData": {
        "marketSlug": "aec-l5", "bids": [{"px": {"value": "0.45"},
                                          "qty": "10"}],
        "offers": [{"px": {"value": "0.46"}, "qty": "10"}]}}, now)
    status = rec.build_status(st, True, 1, 1, now)
    shadow = status["top_markets"][0]["l5_shadow"]
    assert "l5_state" in shadow and "next_relation_ranked" in shadow
    assert shadow["labels"] == "THEORETICAL"

    def boom(*a, **k):
        raise RuntimeError("shadow bug")
    monkeypatch.setattr(l5s, "market_shadow_row", boom)
    status2 = rec.build_status(st, True, 1, 1, now)   # must not raise
    assert "error" in status2["top_markets"][0]["l5_shadow"]
    assert status2["top_markets"][0]["gate_status"]   # gate unaffected


def test_l5_ranking_term_never_admits_and_only_reorders_cleared():
    def row(slug, matched, act_p, gate="Q1_GATE_CLEARED", off=False):
        return {"slug": slug, "bid": "0.55", "ask": "0.555",
                "bid_qty": "50", "ask_qty": "50", "both_sides_served": True,
                "trades_1h": 100,
                "slot_counts_theoretical": {"MATCHED": matched},
                "gate_status": gate,
                "l5_shadow": {"off_distribution": off,
                              "next_relation_ranked": [
                                  {"relation": "extend", "p": act_p}]}}
    # equal pair evidence: the higher L5-activity market ranks first
    status = {"eligible_markets": [row("low", 2, 0.1), row("high", 2, 0.9)]}
    assert [r["slug"] for r in sup.eligible_candidates(status)] == \
        ["high", "low"]
    # an uncleared market with a perfect L5 score is still excluded
    status2 = {"eligible_markets": [row("nope", 2, 1.0, gate="NOT_READY")]}
    assert sup.eligible_candidates(status2) == []
    # the L5 term is bounded below one MATCHED slot's worth of evidence
    status3 = {"eligible_markets": [row("m3", 3, 0.0), row("m2", 2, 1.0)]}
    assert [r["slug"] for r in sup.eligible_candidates(status3)] == \
        ["m3", "m2"]
    # off-distribution and missing shadows contribute exactly zero
    assert sup._l5_score_term({"off_distribution": True,
                               "next_relation_ranked":
                               [{"relation": "extend", "p": 1.0}]}) == 0
    assert sup._l5_score_term(None) == 0
    assert sup._l5_score_term({"error": "x"}) == 0


def test_structurally_read_only():
    src = Path(l5s.__file__).read_text()
    for banned in ("urllib", "websockets", "http.client", "requests",
                   "Transport(", "/v1/orders", "\"POST\""):
        assert banned not in src, banned
    assert re.search(r"THEORETICAL", src)
