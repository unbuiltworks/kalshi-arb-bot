from src import gemini_kalshi_rollover_optimizer as opt
from src import gemini_kalshi_predictive_prewarmed as base
from src import gemini_kalshi_predictive_prewarmed_safe as safe
from src import gemini_kalshi_micro_live_readiness_20bp as readiness


def test_shared_subscription_preserves_legacy_price_scale():
    payload = opt._subscribe_payload(7, "KXXRP15M-26AUG191330-30")
    assert payload == {
        "id": 7,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_ticker": "KXXRP15M-26AUG191330-30",
            "use_yes_price": False,
        },
    }


def test_snapshot_repair_uses_supported_update_subscription_shape():
    payload = opt._snapshot_payload(9, 42, "KXBTC15M-26AUG191330-30")
    assert payload["cmd"] == "update_subscription"
    assert payload["params"]["sids"] == [42]
    assert payload["params"]["market_tickers"] == ["KXBTC15M-26AUG191330-30"]
    assert payload["params"]["action"] == "get_snapshot"


def test_optimizer_install_changes_transport_only():
    original_min_profit = readiness.MIN_LOCKED_PROFIT
    original_boundary = readiness.MIN_DOUBLE_BOUNDARY_BPS
    original_probe = readiness.exact._probe
    opt.install()
    assert base._start_feed is opt._start_feed_shared_kalshi
    assert base._start_next_at is opt._start_next_early
    assert readiness._kalshi_target_loop is opt._kalshi_target_loop_prefetched
    assert readiness.MIN_LOCKED_PROFIT == original_min_profit
    assert readiness.MIN_DOUBLE_BOUNDARY_BPS == original_boundary
    assert readiness.exact._probe is original_probe
    # Activation/event-order safety remains installed on the pump path.
    assert base._pump_feed is safe._pump_feed_activation_safe


def test_prewarm_lead_is_within_existing_validated_cli_envelope():
    assert 0.1 <= opt.PREWARM_LEAD_S <= 5.0
