import pytest

from src import gemini_kalshi_rollover_optimizer as opt
from src import gemini_kalshi_predictive_prewarmed as base
from src import gemini_kalshi_predictive_prewarmed_safe as safe
from src import gemini_kalshi_micro_live_readiness_20bp as readiness


def test_shared_subscription_uses_one_sid_market_list_and_legacy_price_scale():
    payload = opt._subscribe_payload(
        7,
        ["KXXRP15M-26AUG191330-30", "KXBTC15M-26AUG191330-30"],
    )
    assert payload == {
        "id": 7,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta"],
            "market_tickers": [
                "KXBTC15M-26AUG191330-30",
                "KXXRP15M-26AUG191330-30",
            ],
            "use_yes_price": False,
        },
    }


def test_dynamic_market_updates_use_supported_one_sid_shape():
    add = opt._update_payload(8, 42, ["KXETH15M-X", "KXSOL15M-X"], "add_markets")
    assert add["cmd"] == "update_subscription"
    assert add["params"]["sids"] == [42]
    assert add["params"]["market_tickers"] == ["KXETH15M-X", "KXSOL15M-X"]
    assert add["params"]["action"] == "add_markets"

    snapshot = opt._snapshot_payload(9, 42, ["KXBTC15M-X", "KXXRP15M-X"])
    assert snapshot["params"]["sids"] == [42]
    assert snapshot["params"]["action"] == "get_snapshot"


def test_shared_sequence_is_global_across_markets_and_control_responses():
    hub = opt.SharedKalshiOrderbookHub()
    hub._sid = 42
    # First observed sequence may start above 1 because the subscribe ack itself is
    # unsequenced in the public schema.
    assert hub._observe_sequence({"sid": 42, "seq": 2, "type": "orderbook_snapshot"})
    # Different market on the same SID consumes the next sequence number.
    assert hub._observe_sequence({"sid": 42, "seq": 3, "type": "orderbook_snapshot"})
    # update_subscription OKs are sequenced too and must advance the same cursor.
    assert hub._observe_sequence({"sid": 42, "seq": 4, "type": "ok"})
    assert hub._observe_sequence({"sid": 42, "seq": 5, "type": "orderbook_delta"})
    assert hub.last_seq == 5
    assert hub.sequence_gaps == 0


def test_shared_sequence_gap_fails_closed():
    hub = opt.SharedKalshiOrderbookHub()
    hub._sid = 7
    assert hub._observe_sequence({"sid": 7, "seq": 10, "type": "orderbook_snapshot"})
    with pytest.raises(opt.SharedSequenceGap):
        hub._observe_sequence({"sid": 7, "seq": 12, "type": "orderbook_delta"})
    assert hub.sequence_gaps == 1


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
    assert base._pump_feed is safe._pump_feed_activation_safe


def test_prewarm_lead_is_within_existing_validated_cli_envelope():
    assert 0.1 <= opt.PREWARM_LEAD_S <= 5.0
