"""Safety wrapper for the bounded $10 paired-maker live canary.

V1 contains the trading loop. V2 closes two terminal-accounting gaps without
loosening any entry rule:
- if a known one-sided fill remains after an unambiguous missing-side order
  rejection, flatten that q1 outcome immediately instead of hard-stopping naked;
- treat a confirmed full emergency flatten as the terminal path before the generic
  hard-halt branch so its actual realized rescue P&L is preserved in reporting.

Ambiguous cancels/fractional fills are deliberately NOT auto-flattened because doing
so could double the position; those remain hard halts/manual-review cases.
"""
from __future__ import annotations

from typing import Any

from src.gemini_maker import live_10_canary as v1


async def _complete_missing_safe(*args: Any, **kwargs: Any) -> dict[str, Any]:
    original = _ORIGINAL_COMPLETE
    out = await original(*args, **kwargs)
    if out.get("reason") != "COMPLEMENT_POST_REJECTED":
        return out

    filled_outcome = str(kwargs["filled_outcome"])
    filled_price = v1.D(str(kwargs["filled_price"]))
    filled_client_id = str(kwargs["filled_client_id"])
    event = kwargs["event"]
    flatten = await v1._flatten_filled(
        outcome=filled_outcome,
        symbol=event.symbol,
        filled_price=filled_price,
        original_client_id=filled_client_id,
    )
    qty = v1.D(str(flatten.get("filled_quantity") or "0"))
    if qty != v1.ONE:
        return {
            "hard_halt": True,
            "reason": "COMPLEMENT_POST_REJECTED_FLATTEN_NOT_FULL",
            "flatten": flatten,
        }
    pnl = v1.D(str(flatten.get("rescue_realized_pnl") or "0"))
    return {
        "hard_halt": False,
        "flattened": True,
        "reason": "COMPLEMENT_POST_REJECTED_FLATTENED",
        "flatten": flatten,
        "rescue_realized_pnl": pnl,
        "within_rescue_loss_cap": pnl >= -v1.MAX_WORST_RESCUE_LOSS,
    }


async def _execute_one_pair_safe(*args: Any, **kwargs: Any) -> dict[str, Any]:
    out = await _ORIGINAL_EXECUTE(*args, **kwargs)
    # A full emergency flatten is terminal and flat. Preserve its realized result;
    # the outer loop will stop immediately on `flattened`.
    if out.get("flattened") and out.get("hard_halt"):
        flatten = out.get("flatten") or {}
        qty = v1.D(str(flatten.get("filled_quantity") or "0"))
        if qty == v1.ONE:
            out = dict(out)
            out["hard_halt"] = False
            out["rescue_realized_pnl"] = v1.D(str(flatten.get("rescue_realized_pnl") or "0"))
    return out


_ORIGINAL_COMPLETE = v1._complete_missing
_ORIGINAL_EXECUTE = v1._execute_one_pair


async def run() -> None:
    original_complete = v1._complete_missing
    original_execute = v1._execute_one_pair
    try:
        v1._complete_missing = _complete_missing_safe
        v1._execute_one_pair = _execute_one_pair_safe
        await v1.run()
    finally:
        v1._complete_missing = original_complete
        v1._execute_one_pair = original_execute


def main() -> None:
    v1.asyncio.run(run())


if __name__ == "__main__":
    main()
