# Production deployment gate for Polymarket-triggered exact-Kalshi routing.
import asyncio
from decimal import Decimal

from src.mlb_kp_rule_risk_event_live_q1 import ExactKalshiLimiter, MAX_TRIGGER_AGE_MS, _chunks, _top_book
from src.mlb_kp_rule_risk_event_rollover import HARD_HALT_RC, safe_pre_mutation_block


class Quote:
    yes_ask = Decimal("0.49")
    yes_depth = Decimal("2")
    no_ask = Decimal("0.52")
    no_depth = Decimal("3")


def test_trigger_freshness_never_exceeds_original_100ms_gate():
    assert MAX_TRIGGER_AGE_MS <= 100


def test_ws_quote_converts_without_midprice_math():
    book = _top_book(Quote())
    assert book.yes_ask == Decimal("0.49")
    assert book.yes_depth == Decimal("2")
    assert book.no_ask == Decimal("0.52")
    assert book.no_depth == Decimal("3")


def test_exact_kalshi_limiter_suppresses_immediate_repeat():
    async def check():
        limiter = ExactKalshiLimiter()
        assert await limiter.allow("KXMLBK-TEST") is True
        assert await limiter.allow("KXMLBK-TEST") is False
        assert limiter.allowed == 1
        assert limiter.global_suppressed + limiter.ticker_suppressed == 1
    asyncio.run(check())


def test_polymarket_universe_is_sharded_below_connection_cap():
    slugs = [f"m-{i}" for i in range(1034)]
    shards = _chunks(slugs, 900)
    assert len(shards) == 2
    assert sum(len(shard) for shard in shards) == 1034
    assert max(map(len, shards)) <= 900


def test_rollover_only_retries_known_pre_mutation_ledger_blocks():
    assert safe_pre_mutation_block(RuntimeError("unresolved sports live trade blocks execution: sports-gk:test"))
    assert safe_pre_mutation_block(RuntimeError("shared sports budget exhausted: used=20 requested=1 ceiling=20"))
    assert not safe_pre_mutation_block(RuntimeError("insufficient venue cash polymarket=0 kalshi=10"))
    assert not safe_pre_mutation_block(RuntimeError("unexpected execution transport failure"))
    assert not safe_pre_mutation_block(ValueError("bad state"))


def test_rollover_preserves_hard_halt_code():
    assert HARD_HALT_RC == 70
