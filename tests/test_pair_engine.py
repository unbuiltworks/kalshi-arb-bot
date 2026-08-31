"""Pair-engine state machine tests, including the user's worked example:
maker fill YES 47c -> lean NO toward ceiling -> pair <= 98c; >100c forbidden."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.gemini_maker.pair_engine import (
    PairBook,
    PairInventory,
    decide,
)


def _book(yb=0.47, ya=0.50, nb=0.50, na=0.53):
    return PairBook(yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na)


def test_dead_no_pool_is_idle():
    d = decide(PairInventory(), _book(), flow_60s=0.0, seconds_to_expiry=200.0,
               in_reward_pool=False)
    assert d.state == "IDLE" and not d.quotes and d.cancel_all


def test_dead_with_pool_rests_two_sided_10x10():
    d = decide(PairInventory(), _book(), flow_60s=0.0, seconds_to_expiry=200.0,
               in_reward_pool=True)
    assert d.state == "SUBSIDY"
    assert sorted(q.side for q in d.quotes) == ["no", "yes"]
    assert all(q.size == 10 for q in d.quotes)
    # rests behind best bid, not at it
    assert all(q.price < 0.51 for q in d.quotes)


def test_hot_flat_joins_both_bids():
    d = decide(PairInventory(), _book(), flow_60s=250.0, seconds_to_expiry=200.0)
    assert d.state == "HOT"
    prices = {q.side: q.price for q in d.quotes}
    assert prices["yes"] == 0.47 and prices["no"] == 0.50


def test_lean_completes_pair_within_ceiling():
    # User's example: filled YES at 47c. Ceiling for NO = 53c. Book NO ask 52c,
    # so lean stops at 51c (one tick inside the ask, still maker), pair 98c.
    inv = PairInventory()
    inv.apply_fill("yes", 5, 0.47)
    d = decide(inv, _book(yb=0.47, ya=0.50, nb=0.48, na=0.52),
               flow_60s=250.0, seconds_to_expiry=200.0)
    no_quote = next(q for q in d.quotes if q.side == "no")
    assert no_quote.reason == "LEAN_COMPLETE_PAIR"
    assert no_quote.price == 0.51
    assert 0.47 + no_quote.price <= 1.00


def test_ceiling_never_exceeded():
    # Held YES at 60c -> NO ceiling 40c. NO bid already 45c: leaning would
    # break the pair ceiling, so the missing side must NOT be quoted above 40c.
    inv = PairInventory()
    inv.apply_fill("yes", 5, 0.60)
    d = decide(inv, _book(yb=0.55, ya=0.60, nb=0.45, na=0.48),
               flow_60s=250.0, seconds_to_expiry=200.0)
    for q in d.quotes:
        if q.side == "no":
            assert 0.60 + q.price <= 1.00 + 1e-9


def test_rescue_only_in_zone_and_only_cheap():
    inv = PairInventory()
    inv.apply_fill("yes", 5, 0.47)
    # outside rescue zone: no rescue
    d = decide(inv, _book(), flow_60s=0.0, seconds_to_expiry=200.0)
    assert d.rescue is None
    # in zone, ask within ceiling+premium: rescue fires
    d = decide(inv, _book(na=0.55), flow_60s=0.0, seconds_to_expiry=45.0)
    assert d.rescue is not None and d.rescue.side == "no" and d.rescue.price == 0.55
    # in zone but too expensive (ceiling 53c + 3c premium = 56c max): no rescue
    d = decide(inv, _book(na=0.60), flow_60s=0.0, seconds_to_expiry=45.0)
    assert d.rescue is None


def test_expiry_stop_cancels_quotes_but_allows_rescue():
    inv = PairInventory()
    inv.apply_fill("yes", 5, 0.47)
    d = decide(inv, _book(na=0.54), flow_60s=500.0, seconds_to_expiry=20.0)
    assert d.state == "EXPIRY_STOP" and not d.quotes and d.cancel_all
    assert d.rescue is not None


def test_worst_loss_cap_shrinks_size():
    inv = PairInventory()
    d = decide(inv, _book(), flow_60s=250.0, seconds_to_expiry=200.0,
               max_worst_loss=1.0)
    for q in d.quotes:
        probe = PairInventory()
        probe.apply_fill(q.side, q.size, q.price)
        assert probe.worst_settlement_loss() <= 1.0 + 1e-9


if __name__ == "__main__":
    import inspect
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)
