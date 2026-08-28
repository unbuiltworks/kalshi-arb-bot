import asyncio

import httpx

from src.l5_polymarket_lifecycle import fetch_complete_activity, summarize_lifecycle


def test_fetch_complete_activity_slices_and_dedupes():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        calls.append((int(params["start"]), int(params["end"]), int(params["offset"])))
        assert params["type"] == "TRADE,MERGE,SPLIT,REDEEM,MAKER_REBATE,TAKER_REBATE"
        start = int(params["start"])
        row = {
            "timestamp": start,
            "type": "TRADE",
            "conditionId": f"c{start}",
            "asset": "a",
            "side": "BUY",
            "outcome": "Up",
            "size": "1",
            "usdcSize": "0.6",
            "price": "0.6",
            "transactionHash": f"tx{start}",
            "slug": f"btc-updown-5m-{start}",
        }
        return httpx.Response(200, json=[row])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_complete_activity(
                "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68",
                start=1000000000,
                end=1000001799,
                slice_seconds=900,
                client=client,
            )

    rows = asyncio.run(run())
    assert len(rows) == 2
    assert calls == [(1000000000, 1000000899, 0), (1000000900, 1000001799, 0)]


def test_summary_separates_trade_and_lifecycle_activity():
    slug = "btc-updown-5m-1787876400"
    rows = [
        {
            "timestamp": 1787876410,
            "type": "TRADE",
            "conditionId": "c",
            "side": "BUY",
            "outcome": "Up",
            "size": "10",
            "price": "0.60",
            "usdcSize": "6",
            "slug": slug,
        },
        {
            "timestamp": 1787876420,
            "type": "MERGE",
            "conditionId": "c",
            "usdcSize": "4",
            "slug": slug,
        },
        {
            "timestamp": 1787876430,
            "type": "MAKER_REBATE",
            "conditionId": "c",
            "usdcSize": "0.05",
            "slug": slug,
        },
        {
            "timestamp": 1787876701,
            "type": "REDEEM",
            "conditionId": "c",
            "usdcSize": "5",
            "slug": slug,
        },
    ]
    result = summarize_lifecycle(rows)
    assert result["type_counts"] == {
        "TRADE": 1,
        "MERGE": 1,
        "MAKER_REBATE": 1,
        "REDEEM": 1,
    }
    assert result["markets_with_nontrade_lifecycle_activity"] == 1
    market = result["markets"][0]
    assert market["trade_buy_notional"] == "6"
    assert market["public_merge_usdc"] == "4"
    assert market["public_rebate_usdc"] == "0.05"
    assert market["public_redeem_usdc"] == "5"
