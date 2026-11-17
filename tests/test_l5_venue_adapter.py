from decimal import Decimal as D

from src import l5_venue_adapter as va
from src.polymarket_l5 import transfer_backtest as tb


def test_normalized_book_derives_no_side_and_tick_floors_join():
    b = va.NormalizedBook("X", "m", 0.0, D("0.4867"), D("0.5150"), D("10"),
                          D("20"), D("0.005"), D("0.20"), D("0.80"), 100.0)
    assert b.no_bid == D("0.4850") and b.no_ask == D("0.5133")
    assert b.join_price(True) == D("0.485")
    assert b.join_price(False) == D("0.485")
    assert b.opposite_ask(False) == D("0.5133")
    assert b.spread == D("0.0283")


def test_gemini_adapter_normalizes_tape_row_with_ticker_expiry():
    g = va.GeminiAdapter()
    exp = tb.ticker_expiry_epoch("BTC05M2608302330")   # 2026-08-30 23:30 UTC
    row = {"symbol": "GEMI-BTC05M2608302330-UP", "t": exp - 250,
           "bid": "0.4800", "bid_sz": "500", "ask": "0.5000", "ask_sz": "500"}
    b = g.normalize(row)
    assert b.venue == "GEMINI" and b.tick == D("0.01")
    assert abs(b.tte_s - 250) < 1e-6
    assert b.yes_bid == D("0.48") and b.no_bid == D("0.50")
    assert b.extra["up_is_yes"] is True
    assert g.normalize({"symbol": "GEMI-BADTICKER-UP", "t": 1.0,
                        "bid": "0.5", "ask": "0.6"}) is None


def test_gemini_complement_price_counts_ceil_cent_fees_both_legs():
    g = va.GeminiAdapter()
    px, econ = g.complement_price(D("0.48"), D("0.55"), D("0.01"))
    # 0.48 + 0.50 pair leaves 0 after 1c+1c fees; 0.49 leaves exactly 1c
    assert px == D("0.49") and econ["regime"] == "pair_target"
    assert econ["hard_bound_px"] == "0.49"
    # opposite ask binds: must rest strictly below it
    px2, _ = g.complement_price(D("0.48"), D("0.49"), D("0.01"))
    assert px2 == D("0.48")
    # no fee-safe complement when the surplus cost is too high
    px3, econ3 = g.complement_price(D("0.79"), D("0.30"), D("0.01"))
    assert px3 is None and econ3["regime"].startswith("no_")


def test_pmus_adapter_complement_uses_q1_bound_and_never_counts_rebate():
    p = va.PMUSAdapter()
    px, econ = p.complement_price(D("0.48"), D("0.55"), D("0.005"))
    assert px == D("0.500")            # min(0.98-0.48, 0.51 hard, 0.545)
    assert p.maker_fee(D("0.5"), D("1")) == D("0")
    # 0.0238 at this account's Taker Tier 1 coefficient of 0.095. The
    # schedule has moved twice now -- 0.06, then 0.0695 effective
    # 2026-09-17, then re-tiered exchange-wide on 2026-11-14 -- and each
    # time a pinned literal here was quoting a coefficient the venue no
    # longer charges us. See tests/test_pmus_fees.py.
    assert p.taker_fee(D("0.5"), D("1")) == D("0.0238")


def test_state_key_is_venue_free_and_inventory_units_are_behavioral():
    inv = va.L5Inventory()
    g = va.GeminiAdapter().normalize(
        {"symbol": "GEMI-BTC05M2608302330-UP", "t": tb.ticker_expiry_epoch("BTC05M2608302330") - 200,
         "bid": "0.48", "ask": "0.50"})
    p = va.PMUSAdapter().normalize({"slug": "s", "bid": "0.48", "ask": "0.50"},
                                   now=0.0, settlement_t=200.0)
    assert va.l5_state(g, inv) == va.l5_state(p, inv) == "p45-55c|tte2-5m|flat"
    inv.record(True, D("0.48"), D("0.06"), "open")
    assert inv.behavioral_residual() == 1.0
    inv.record(True, D("0.47"), D("0.03"), "extend")
    assert inv.behavioral_residual() == 1.5
    assert va.l5_state(g, inv).endswith("imb1-10")
    assert inv.last_px == D("0.47") and inv.last_rel == "extend"


def test_open_side_convention_cheaper_touch_ties_yes():
    mk = lambda b, a: va.NormalizedBook("X", "m", 0, D(b), D(a), None, None,
                                        D("0.01"), D("0.2"), D("0.8"), 100)
    assert va.open_side_yes(mk("0.48", "0.50")) is True     # 0.48 vs 0.50
    assert va.open_side_yes(mk("0.52", "0.54")) is False    # 0.52 vs 0.46
    assert va.open_side_yes(mk("0.50", "0.50")) is True     # tie -> YES


def test_decision_record_carries_live_field_set_and_no_orders():
    inv = va.L5Inventory()
    prop = {"recommended_relation": "open", "relation_ranked": [
        {"relation": "open", "p": 1.0}], "state_n": 5, "off_distribution": False,
        "persist_extend_p": 0.0, "switch_complete_p": 0.0}
    rec = va.decision_record(venue="GEMINI", market_id="m", t=1.0,
                             state="p45-55c|tte2-5m|flat", inv=inv, prop=prop,
                             acted="open", refusal=None,
                             planned_size={"contracts": "1", "usd": "0.48"},
                             shadow={"stochastic": {"sampled": "extend"}},
                             book=None)
    for k in ("state", "last_rel", "v2_recommended", "relation_ranked",
              "state_n", "off_distribution", "persist_extend_p",
              "switch_complete_p", "acted", "refusal", "inventory_before",
              "planned_safe_size", "shadow", "shadow_differs_from_executed"):
        assert k in rec
    assert rec["shadow_differs_from_executed"] is True
    assert rec["orders_placed"] == 0 and rec["venue"] == "GEMINI"


def _gbook(bid, ask, tte=200):
    return va.GeminiAdapter().normalize(
        {"symbol": "GEMI-BTC05M2608302330-UP",
         "t": tb.ticker_expiry_epoch("BTC05M2608302330") - tte,
         "bid": bid, "ask": ask})


def test_canonical_state_keys_by_outcome_price_never_mirrored():
    """OWNER 5558477213 B: a NO fill at 0.08 is p<10c in the canonical
    history (the frozen model is not mirror-symmetric); the v1 key mirrors
    it to the YES scale (p>90c). Both are kept."""
    inv = va.L5Inventory()
    inv.record(False, D("0.08"), D("0.06"), "open")
    book = _gbook("0.91", "0.93")
    assert va.l5_state(book, inv).startswith("p>90c")          # v1 (mirrored)
    assert va.l5_state_v2(book, inv).startswith("p<10c")       # canonical
    assert inv.last_px == D("0.92") and inv.last_px_outcome == D("0.08")
    # YES fills agree under both conventions
    inv2 = va.L5Inventory()
    inv2.record(True, D("0.47"), D("0.06"), "open")
    assert va.l5_state(book, inv2) == va.l5_state_v2(book, inv2)


def test_pre_fill_canonical_state_uses_the_cheaper_touch():
    """Pre-fill the shared open convention buys the cheaper touch; the
    canonical key prices that outcome (v1 priced the YES bid)."""
    inv = va.L5Inventory()
    hi = _gbook("0.92", "0.93")          # YES 0.92 / NO 0.07 -> buys NO at 0.07
    assert va.l5_state(hi, inv).startswith("p>90c")
    assert va.l5_state_v2(hi, inv).startswith("p<10c")
    lo = _gbook("0.05", "0.06")          # YES 0.05 / NO 0.94 -> buys YES at 0.05
    assert va.l5_state_v2(lo, inv).startswith("p<10c") and va.l5_state(lo, inv).startswith("p<10c")
