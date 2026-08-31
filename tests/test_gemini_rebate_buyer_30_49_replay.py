import json
from pathlib import Path

from src.gemini_rebate_buyer_30_49_replay import replay


def row(*, bid, ask, actionable=True, side="YES", quote="0.40"):
    decision = {
        "actionable": actionable, "side": side if actionable else None,
        "quote_price": quote if actionable else None, "maker_fee_usd": "0.04",
        "projected_rebate_usd_not_realized": "0.05",
    }
    return {
        "type":"GEMINI_REBATE_BUYER_30_49_SHADOW", "ticker":"ETH05MTEST", "asset":"ETH",
        "yes_bid":str(bid), "yes_ask":str(ask),
        "basis_reserve_sensitivity_bps":{
            "10":{"probability_up":"0.50","policies":{"band_only":decision,"fair_1c":decision}}
        },
    }


def test_requires_later_trade_through(tmp_path: Path):
    p = tmp_path / "capture.jsonl"
    rows = [row(bid="0.39", ask="0.45"), row(bid="0.38", ask="0.39")]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = replay(p)
    assert result["policies"]["band_only"]["strict_fill_count"] == 1
    assert result["policies"]["fair_1c"]["strict_fill_count"] == 1
    assert result["policies"]["fair_1c"]["strict_fill_model_positive_at_confirmation"] == 1


def test_touch_without_trade_through_is_not_fill(tmp_path: Path):
    p = tmp_path / "capture.jsonl"
    rows = [row(bid="0.39", ask="0.45"), row(bid="0.40", ask="0.40")]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = replay(p)
    assert result["policies"]["band_only"]["strict_fill_count"] == 0
