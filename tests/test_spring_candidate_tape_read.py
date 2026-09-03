"""Exact-candidate tape read regressions (issue #167 comments
5521435419/5521463344): slug-scoped tape extraction, deterministic clone
timeline + completion executability, canonical strict spring replay,
price-aware A-vs-B adapter counters, verdict rules, and read-only purity."""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from scripts import spring_candidate_tape_read as tape
from src.polymarket_l5.action_model import state_key

SLUG = "aec-cs2-nexv-cw-2026-09-03"
TICK = D("0.005")


def _model():
    """Injected multi-state model: flat states propose OPEN, imbalanced
    states propose COMPLETE — so a sell print opens YES and the next buy
    print completes the pair deterministically."""
    states = {}
    for band in (0.3, 0.45, 0.5, 0.55, 0.6, 0.8):
        for imb, rel in ((0.0, "open"), (1.5, "complete"),
                         (20.0, "complete")):
            dist = {f"{rel}|q<5": 0.7, "extend|q<5": 0.2, "sell|q<5": 0.1}
            states[state_key(band, tape.shadow.SPORTS_TTE_DEFAULT_S,
                             imb)] = {"n": 100, "action_probs": dist,
                                      "modal_action": f"{rel}|q<5"}
    return {"states": states, "_marginal": {"open|q<5": 1.0},
            "_sha256_16": "testmodel0000000"}


def _book_msg(slug, bid, ask, qty="100"):
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": {"value": bid}, "qty": qty}],
                           "offers": [{"px": {"value": ask}, "qty": qty}]}}


def _trade_msg(slug, px, side):
    return {"trade": {"marketSlug": slug, "price": {"value": px},
                      "quantity": "1", "taker": {"side": side}}}


def test_slug_events_extracts_and_scopes_with_rolling_bbo():
    rows = [
        (1.0, _book_msg(SLUG, "0.5000", "0.5200")),
        (2.0, _book_msg("other-slug", "0.1000", "0.9000")),
        (3.0, _trade_msg(SLUG, "0.5000", "SELL")),
        (4.0, _trade_msg("other-slug", "0.1000", "BUY")),
    ]
    events = tape.slug_events(rows, SLUG)
    assert [e["kind"] for e in events] == ["book", "trade"]
    book, tr = events
    assert book["ask_book_missing"] is False
    assert tr["px"] == "0.5000" and tr["dir"] == "sell"
    assert tr["bid"] == "0.5000" and tr["ask"] == "0.5200"


def _pair_events(base):
    """One book then sell@0.50 (clone OPENs YES) + buy@0.55 (clone
    COMPLETEs NO at 0.45; pair 0.95 <= spring 0.96)."""
    return [
        {"kind": "book", "t": base, "bid": "0.5000", "ask": "0.5200",
         "bid_qty": "100", "ask_qty": "100", "ask_book_missing": False},
        {"kind": "trade", "t": base + 10, "px": "0.5000", "dir": "sell",
         "bid": "0.5000", "ask": "0.5200"},
        {"kind": "trade", "t": base + 40, "px": "0.5500", "dir": "buy",
         "bid": "0.5000", "ask": "0.5200"},
    ]


def test_clone_timeline_completion_and_probe_verdict():
    # two matched pairs in two distinct 300s slots -> gate-met shape
    events = _pair_events(1000.0) + _pair_events(1400.0)
    r = tape.analyze(events, SLUG, TICK, model=_model(), v2={})
    assert r["clone"]["n_fills"] == 4
    assert r["clone"]["final_residual"] == 0
    # record_fill relations are cumulative-inventory semantics: only the
    # very first fill of the market is OPEN; re-balancing fills at
    # residual 0 read as COMPLETE
    assert [f["relation"] for f in r["clone"]["fills"]] == \
        ["open", "complete", "complete", "complete"]
    assert D(r["clone"]["pair_cost_path"][0]["pair_cost"]) == D("0.95")
    ce = r["completion_executability"]
    assert ce["spring"] >= 2 and ce["hard"] >= 2
    assert ce["first_spring_latency_s"] == 30.0
    strict = r["strict_spring_replay"]
    assert strict["spring_repeated_completion"] is True
    assert strict["spring_matched_slots"] >= 2
    assert all(D(x) <= D("0.96") for x in strict["spring_pair_costs"])
    gaps = r["opp_side_gaps_s"]
    # 30s inside each pair; the 370s middle gap spans pair-to-pair
    assert gaps["gaps"] == [30.0, 370.0, 30.0] and gaps["p50"] == 30.0
    assert r["no_fill_explanation"]["slots_with_clone_fill"] == 2
    assert r["verdict"] == "BOUNDED_PROBE_GATE_MET"
    assert "SHADOW/THEORETICAL" in r["labels"]


def test_loose_two_fill_slots_do_not_clear_without_repeated_strict_spring():
    # This was the old false-positive shape: two clone-fill slots and one
    # executable <=0.96 completion, but only ONE canonical strict spring
    # pair completion. Advisory readiness must now fail closed.
    events = _pair_events(1000.0) + [
        {"kind": "book", "t": 1400.0, "bid": "0.5000", "ask": "0.5200",
         "bid_qty": "100", "ask_qty": "100", "ask_book_missing": False},
        {"kind": "trade", "t": 1410.0, "px": "0.5000", "dir": "sell",
         "bid": "0.5000", "ask": "0.5200"},
    ]
    r = tape.analyze(events, SLUG, TICK, model=_model(), v2={})
    assert r["no_fill_explanation"]["slots_with_clone_fill"] == 2
    assert r["completion_executability"]["spring"] >= 1
    assert r["strict_spring_replay"]["spring_matched_slots"] == 1
    assert r["strict_spring_replay"]["spring_repeated_completion"] is False
    assert r["verdict"] == "KEEP_SHADOWING"


def test_single_slot_pair_keeps_shadowing():
    r = tape.analyze(_pair_events(1000.0), SLUG, TICK,
                     model=_model(), v2={})
    assert r["clone"]["matched"] == 1
    assert r["strict_spring_replay"]["spring_repeated_completion"] is False
    assert r["verdict"] == "KEEP_SHADOWING"


def test_two_hours_without_any_fill_rejects():
    # complete-modal everywhere: flat book -> clone abstains forever
    m = _model()
    for sk in m["states"]:
        m["states"][sk]["action_probs"] = {"complete|q<5": 1.0}
    events = [{"kind": "trade", "t": 1000.0 + i * 400, "px": "0.5000",
               "dir": "buy", "bid": "0.5000", "ask": "0.5200"}
              for i in range(20)]
    r = tape.analyze(events, SLUG, TICK, model=m, v2={})
    assert r["clone"]["n_fills"] == 0
    assert r["strict_spring_replay"]["spring_repeated_completion"] is False
    assert r["verdict"] == "REJECT"


def test_adapter_b_one_tick_improvement_price_aware():
    # all-open model: the proposed join stays the YES bid at every epoch,
    # so the A/B counters depend only on print price vs the join levels
    m = _model()
    for sk in m["states"]:
        m["states"][sk]["action_probs"] = {"open|q<5": 1.0}
    base = 1000.0
    events = [
        {"kind": "book", "t": base, "bid": "0.5000", "ask": "0.5200",
         "bid_qty": "100", "ask_qty": "100", "ask_book_missing": False},
        # sell print BETWEEN the levels: A (join 0.50) misses, B (0.505)
        # fills — this is the epoch the diagnostic exists for
        {"kind": "trade", "t": base + 5, "px": "0.5050", "dir": "sell",
         "bid": "0.5000", "ask": "0.5200"},
        # sell print at the join: both fill
        {"kind": "trade", "t": base + 10, "px": "0.5000", "dir": "sell",
         "bid": "0.5000", "ask": "0.5200"},
        # tight book (one tick wide): no inside room for B
        {"kind": "trade", "t": base + 20, "px": "0.5000", "dir": "sell",
         "bid": "0.5000", "ask": "0.5050"},
    ]
    r = tape.analyze(events, SLUG, TICK, model=m, v2={})
    ab = r["adapter_a_vs_b"]
    assert ab["b_only_fills"] == 1
    assert ab["a_fills"] == 2 and ab["b_fills"] == 3
    assert ab["b_room_blocked"] == 1
    assert "diagnostic only" in ab["note"]


def test_reader_is_read_only_and_never_promotes_b():
    src = Path(tape.__file__).read_text()
    for banned in ("urllib", "websockets", "requests", "Transport(",
                   "/v1/orders", "POST", "place_order", "submit"):
        assert banned not in src, banned
    assert "DIAGNOSTIC ONLY" in src or "diagnostic only" in src
