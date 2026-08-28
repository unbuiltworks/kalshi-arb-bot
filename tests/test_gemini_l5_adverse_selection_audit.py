from src.gemini_l5_adverse_selection_audit import audit


def _row(ticker, pnl, *, yes="1", no="0", matched="0"):
    return {
        "ticker": ticker,
        "yes_qty": yes,
        "no_qty": no,
        "matched_qty": matched,
        "residual_side": "YES" if yes != "0" and matched == "0" else None,
        "residual_qty": yes if matched == "0" else "0",
        "resolution_side": "NO" if pnl < 0 else "YES",
        "settled_shadow_trading_pnl": str(pnl),
        "settled_shadow_modeled_pnl_with_maker_rebate": str(pnl),
    }


def test_audit_counts_one_sided_losses_and_groups():
    result = audit([
        {
            "venue": "gemini",
            "markets_in_replay": 4,
            "settled_detail": [
                _row("BTC05M1", -0.61),
                _row("ETH05M1", 0.36),
            ],
            "unresolved_detail": [],
        },
        {
            "venue": "gemini",
            "markets_in_replay": 3,
            "settled_detail": [_row("BTC15M1", -0.67)],
            "unresolved_detail": [],
        },
    ])
    assert result["input_artifacts"] == 2
    assert result["markets_in_replay"] == 7
    assert result["filled_settled_markets"] == 3
    assert result["one_sided_filled_settled_markets"] == 3
    assert result["both_sides_filled_settled_markets"] == 0
    assert result["fill_win_count"] == 1
    assert result["fill_loss_count"] == 2
    assert result["groups"]["BTC_5m"]["losses"] == 1
    assert result["groups"]["ETH_5m"]["wins"] == 1
    assert result["groups"]["BTC_15m"]["losses"] == 1
    assert result["classification"] == "SHADOW_SETTLED_NOT_REALIZED"


def test_audit_does_not_count_unfilled_settlements_as_fill_outcomes():
    result = audit([{
        "venue": "gemini",
        "markets_in_replay": 2,
        "settled_detail": [{
            "ticker": "BTC05M2",
            "yes_qty": "0",
            "no_qty": "0",
            "matched_qty": "0",
            "settled_shadow_trading_pnl": "0",
            "settled_shadow_modeled_pnl_with_maker_rebate": "0",
        }],
        "unresolved_detail": [],
    }])
    assert result["settled_markets"] == 1
    assert result["filled_settled_markets"] == 0
    assert result["fill_win_rate"] is None
    assert result["settled_shadow_trading_pnl"] == "0"
