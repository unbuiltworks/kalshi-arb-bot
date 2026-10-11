"""Regression for issue #167 comment 5518964002: build_status hard-capped
the emitted rows to the five highest-trade markets, so a Q1_GATE_CLEARED
market outside that display window was invisible to the 24/7 AWS
supervisor. The recorder must gate the FULL slate every cycle and emit
every cleared row in `eligible_markets`; the supervisor must read that
field (falling back to `top_markets` for older status lines)."""
from __future__ import annotations

from src import pmus_ws_evidence_recorder as rec
from src import pmus_aws_supervisor as sup


def _book(slug: str, bid: str, ask: str, qty: str = "500") -> dict:
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": {"value": bid}, "qty": qty}],
                           "offers": [{"px": {"value": ask}, "qty": qty}]}}


def _trade(slug: str, price: str, side: str) -> dict:
    return {"trade": {"marketSlug": slug, "price": {"value": price},
                      "quantity": "1", "taker": {"side": side}}}


def _slate_state() -> tuple[dict, float, str]:
    """Nine markets: eight busy but NOT cleared (one-directional flow),
    one quiet market that genuinely clears the honest gate with MATCHED=2
    yet sits far outside the top five by trade count."""
    st: dict = {}
    base = 1_700_000_000.0
    for i in range(8):
        slug = f"aec-busy-{i}"
        rec.apply_events(st, _book(slug, "0.5000", "0.5050"), base - 10)
        for k in range(20):
            rec.apply_events(st, _trade(slug, "0.5050", "BUY"), base + k * 10)
    quiet = "aec-quiet-cleared"
    rec.apply_events(st, _book(quiet, "0.4500", "0.4550"), base - 10)
    # slot 1: maker fill at the 0.45 join, then a >= join+1c complement print
    rec.apply_events(st, _trade(quiet, "0.4500", "SELL"), base)
    rec.apply_events(st, _trade(quiet, "0.5500", "BUY"), base + 100)
    # slot 2 (next 300s slot): same honest fill+complement shape
    rec.apply_events(st, _trade(quiet, "0.4500", "SELL"), base + 310)
    rec.apply_events(st, _trade(quiet, "0.5500", "BUY"), base + 400)
    return st, base + 700, quiet


def test_recorder_emits_cleared_market_outside_top_five():
    st, now, quiet = _slate_state()
    status = rec.build_status(st, True, 1000, 40, now)
    top_slugs = [r["slug"] for r in status["top_markets"]]
    assert len(top_slugs) == 5                      # display behavior kept
    assert quiet not in top_slugs                   # outside by trade count
    assert all(s.startswith("aec-busy-") for s in top_slugs)
    eligible = status["eligible_markets"]
    assert [r["slug"] for r in eligible] == [quiet]
    row = eligible[0]
    assert row["bid"] == "0.4500" and row["ask"] == "0.4550"
    assert row["bid_qty"] == "500" and row["ask_qty"] == "500"
    assert row["both_sides_served"] is True
    assert row["trades_1h"] == 4
    assert row["slot_counts_theoretical"].get("MATCHED", 0) >= 2
    assert row["gate_status"] == "Q1_GATE_CLEARED"


def test_supervisor_selects_full_slate_candidate_end_to_end():
    st, now, quiet = _slate_state()
    status = rec.build_status(st, True, 1000, 40, now)
    rows = sup.eligible_candidates(status)
    assert [r["slug"] for r in rows] == [quiet]


def test_gate_itself_is_not_weakened_by_full_slate_emission():
    st, now, _ = _slate_state()
    status = rec.build_status(st, True, 1000, 40, now)
    for row in status["top_markets"]:
        assert row["gate_status"] != "Q1_GATE_CLEARED"
    assert sup.eligible_candidates(
        {**status, "eligible_markets": []}) == []


def test_eligible_markets_takes_precedence_over_top_markets():
    cleared = {"slug": "aec-hidden", "bid": "0.55", "ask": "0.555",
               "bid_qty": "50", "ask_qty": "50", "both_sides_served": True,
               "trades_1h": 3, "slot_counts_theoretical": {"MATCHED": 2},
               "gate_status": "Q1_GATE_CLEARED"}
    status = {"top_markets": [], "eligible_markets": [cleared]}
    assert [r["slug"] for r in sup.eligible_candidates(status)] == \
        ["aec-hidden"]


def test_backward_compatible_fallback_to_top_markets():
    cleared = {"slug": "aec-legacy", "bid": "0.55", "ask": "0.555",
               "bid_qty": "50", "ask_qty": "50", "both_sides_served": True,
               "trades_1h": 3, "slot_counts_theoretical": {"MATCHED": 2},
               "gate_status": "Q1_GATE_CLEARED"}
    status = {"top_markets": [cleared]}       # old status line, no new field
    assert [r["slug"] for r in sup.eligible_candidates(status)] == \
        ["aec-legacy"]
