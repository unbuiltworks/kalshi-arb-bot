"""Production combo supervisor v41: minimal scalar-safe MVE NO-cover shadow scoring.

For a Kalshi MVE conjunction with selected leg settlement values x_i in [0,1],
the combo YES value is product(x_i), so combo NO is 1-product(x_i).  For every
individual selected leg i:

    (1 - product(x_i)) + x_i >= 1

because product(x_i) <= x_i.  Therefore a guaranteed NO-side cover needs the
combo NO plus only ONE selected component, not the sum of every selected
component used by v8's conservative inverse shadow.

This layer changes shadow scoring only.  It does not enable inverse live execution,
accept RFQ quotes, place orders, change the >=3c live threshold, or alter the
existing live-capable YES-cover executor.  Any candidate surfaced here still needs
a separately validated q=1 executor with fresh RFQ, component depth, capital,
position, rule, recovery and settlement gates before live promotion.
"""
from __future__ import annotations

from decimal import Decimal
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v40 as v40
from src import sports_kp_live_combo_supervisor_v8 as v8

D = Decimal
generator = v8.generator


def _selected_component(leg_row: dict[str, Any]) -> dict[str, Any] | None:
    market_ticker = str(leg_row.get("market_ticker") or "")
    side = str(leg_row.get("side") or "").lower()
    market = v8._MARKETS.get(market_ticker) or {}
    if side == "yes":
        price = generator._d(market.get("yes_ask_dollars"))
        depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    elif side == "no":
        price = v8.v7.v6._derived_no_ask(market)
        depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        return None
    if price is None or depth is None or depth < generator.ONE or not (generator.ZERO < price < generator.ONE):
        return None
    fee = generator._fee(price)
    return {
        "market_ticker": market_ticker,
        "selected_side": side,
        "selected_ask_dollars": str(price),
        "depth_fp": str(depth),
        "fee_conservative": str(fee),
        "component_all_in": str(price + fee),
    }


def _score_minimal_inverse(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "")
    quotes = v8._QUOTES.get(ticker) or []
    best_combo = v8._best_buy_no_quote(quotes)
    if best_combo is None:
        return None

    best_cover: dict[str, Any] | None = None
    for leg_row in row.get("legs") or []:
        if not isinstance(leg_row, dict):
            continue
        component = _selected_component(leg_row)
        if component is None:
            continue
        component_all_in = D(component["component_all_in"])
        all_in = component_all_in + best_combo["combo_no_cost"] + best_combo["combo_fee"]
        locked = generator.ONE - all_in
        candidate = {
            "direction": "COMBO_NO_PLUS_ONE_SELECTED_LEG",
            "settlement_identity": "1-product(x_i)+x_k>=1_for_any_selected_k",
            "best_quote": {
                "quote_id": best_combo["quote_id"],
                "combo_no_equivalent": str(best_combo["combo_no_cost"]),
                "combo_fee_conservative": str(best_combo["combo_fee"]),
                "updated_ts": best_combo["updated_ts"],
            },
            "components": [component],
            "component_count": 1,
            "all_in_cost_conservative": str(all_in),
            "locked_profit_conservative": str(locked),
        }
        if best_cover is None or locked > D(best_cover["locked_profit_conservative"]):
            best_cover = candidate

    if best_cover is None:
        return None
    live_floor = max(
        D(os.getenv("MVE_GENERATOR_MIN_LOCKED", "0.02")),
        D(os.getenv("MVE_LIVE_MIN_LOCKED", "0.03")),
        D("0.03"),
    )
    best_cover["target_locked"] = str(live_floor)
    best_cover["classification"] = (
        "SHADOW_EXECUTABLE_MINIMAL_NO_COVER"
        if D(best_cover["locked_profit_conservative"]) >= live_floor
        else "SHADOW_NO_TRADE"
    )
    best_cover["live_execution_enabled"] = False
    best_cover["order_entry"] = False
    return best_cover


# v8's _generate_both_directions resolves this name dynamically for every scored
# generated market. Replacing only the scorer leaves candidate creation and all
# live execution semantics unchanged while exposing the tighter guaranteed cover.
v8._score_inverse = _score_minimal_inverse

run = v40.run
main = v40.main

if __name__ == "__main__":
    main()
