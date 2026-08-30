from decimal import Decimal as D

from src.btc_spot_toxicity_guard import BtcSpotGuard


def ns(ms: int) -> int:
    return ms * 1_000_000


def test_guard_is_fail_closed_without_data():
    g = BtcSpotGuard(window_ms=2000, stale_ms=1500, pull_move_bps=D('5'))
    d = g.decision(now_ns=ns(1000))
    assert not d.ready
    assert d.stale
    assert d.dangerous
    assert d.reason == 'NO_SPOT_SAMPLE'


def test_guard_allows_fresh_calm_market():
    g = BtcSpotGuard(window_ms=2000, stale_ms=1500, pull_move_bps=D('5'))
    g.record('100000', now_ns=ns(0))
    g.record('100010', now_ns=ns(500))  # +1 bp
    d = g.decision(now_ns=ns(600))
    assert d.ready
    assert not d.stale
    assert not d.dangerous
    assert d.reason == 'CALM'
    assert abs(d.move_bps) < D('5')


def test_guard_pulls_on_fast_move():
    g = BtcSpotGuard(window_ms=2000, stale_ms=1500, pull_move_bps=D('5'))
    g.record('100000', now_ns=ns(0))
    g.record('100060', now_ns=ns(500))  # +6 bp
    d = g.decision(now_ns=ns(600))
    assert d.ready
    assert d.dangerous
    assert d.reason == 'FAST_SPOT_MOVE'
    assert d.move_bps >= D('5')


def test_guard_pulls_on_stale_data():
    g = BtcSpotGuard(window_ms=2000, stale_ms=1500, pull_move_bps=D('5'))
    g.record('100000', now_ns=ns(0))
    d = g.decision(now_ns=ns(1601))
    assert not d.ready
    assert d.stale
    assert d.dangerous
    assert d.reason == 'STALE_SPOT'


def test_coinbase_ticker_parser_ignores_other_channels_and_products():
    payload = {
        'channel': 'ticker',
        'events': [{'tickers': [
            {'product_id': 'ETH-USD', 'price': '4000'},
            {'product_id': 'BTC-USD', 'price': '101234.56'},
        ]}],
    }
    assert BtcSpotGuard._ticker_prices(payload) == [D('101234.56')]
    assert BtcSpotGuard._ticker_prices({'channel': 'heartbeats'}) == []
