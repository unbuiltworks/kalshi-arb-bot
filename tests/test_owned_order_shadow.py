"""The shadow's safety invariants and fill standard, pinned.

These exist because the shadow's whole validity rests on (a) it being
incapable of submitting or signing anything, and (b) never counting a
touch as a fill.
"""
import ast

from src.polymarket_l5.owned_order_shadow import (CANDIDATE, FINGERPRINT,
                                                  Order, classify_fill)

MOD = "src/polymarket_l5/owned_order_shadow.py"


def mk(px=0.50, qty=10.0, qa=100.0):
    return Order("o1", "tok", "up", px, qty, 0.0, qa, qa)


def test_no_write_verb_and_no_credential_identifier():
    tree = ast.parse(open(MOD, encoding="utf-8").read())
    attrs = {n.attr.lower() for n in ast.walk(tree)
             if isinstance(n, ast.Attribute)}
    names = {n.id.lower() for n in ast.walk(tree)
             if isinstance(n, ast.Name)}
    for bad in ("post", "put", "delete", "patch", "sign",
                "send_transaction", "place_order", "create_order"):
        assert bad not in attrs, bad
    for bad in ("private_key", "privatekey", "api_secret", "signer",
                "mnemonic", "seed_phrase"):
        assert bad not in (attrs | names), bad


def test_touch_is_never_a_fill():
    """The single most important property."""
    assert classify_fill(mk(), [(0.50, 100.0)], []) is None


def test_queue_cleared_is_not_a_fill():
    """Our hypothetical order is not in the observed book, so the queue
    ahead draining proves the queue cleared, not that we traded."""
    assert classify_fill(mk(), [(0.50, 0.0)], []) == \
        "QUEUE_CLEARED_FRONT_OF_BOOK"


def test_level_consumed_is_evidenced():
    assert classify_fill(mk(), [(0.49, 80.0)], []) == \
        "EVIDENCED_LEVEL_CONSUMED"


def test_trade_through_needs_queue_plus_our_size():
    o = mk(qty=10.0, qa=100.0)
    assert classify_fill(o, [(0.50, 100.0)], [(0.50, 90.0)]) is None
    assert classify_fill(o, [(0.50, 100.0)],
                         [(0.50, 60.0), (0.49, 60.0)]) == \
        "EVIDENCED_TRADE_THROUGH"


def test_prints_above_our_price_do_not_fill_a_bid():
    assert classify_fill(mk(), [(0.50, 100.0)], [(0.55, 500.0)]) is None


def test_candidate_has_no_5c_extend_rule_and_suppression_only():
    assert CANDIDATE["no_5c_extend_rule"] is True
    assert CANDIDATE["residual_control"] == "QUOTE_SUPPRESSION_ONLY"
    assert CANDIDATE["no_aggressive_opposite_side"] is True
    assert len(FINGERPRINT) == 64


def test_stale_market_is_refused_not_reported_as_zero_fills():
    """The first dispatch attempted 27 markets dated 2025-12-19 and logged
    them as INCOMPLETE with polls=0, which reads like a fill-free market.
    A past end date must be refused before polling."""
    import time

    from src.polymarket_l5.owned_order_shadow import collect_market

    rows = []
    status = collect_market(
        {"slug": "btc-updown-5m-1766161800",
         "end_date": "2025-12-19T16:30:00Z",
         "tokens": ["a", "b"], "outcomes": '["Up","Down"]'},
        rows.append, 1.0, 60.0)
    assert status == "STALE_MARKET_SKIPPED"
    assert rows and rows[0]["status"] == "STALE_MARKET_SKIPPED"
    assert rows[0]["polls"] == 0


def test_discovery_requires_a_future_end_date():
    import inspect

    from src.polymarket_l5 import owned_order_shadow as M
    src = inspect.getsource(M.discover)
    assert "te <= now" in src, "discovery must exclude past end dates"
    assert "ascending\": \"false\"" in src or "'ascending': 'false'" in src
