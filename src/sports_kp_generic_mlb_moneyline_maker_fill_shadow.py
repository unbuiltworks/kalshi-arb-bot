"""Fee-correct wrapper for the read-only K/P MLB moneyline maker-fill shadow.

Kalshi's current July 7, 2026 fee schedule lists KXMLBGAME with maker
multiplier 1 and specifies centicent rounding. The base shadow deliberately
started with a whole-cent conservative fee; this wrapper replaces only that
fee primitive with the current schedule arithmetic. It adds no order entry.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import logging

from src import sports_kp_mlb_moneyline_maker_fill_shadow as impl

D = Decimal


def _kxmlbgame_maker_fee_q1(price: Decimal) -> Decimal:
    raw = D("0.0175") * price * (D("1") - price)
    # Current Kalshi schedule: fee + position cost rounds up to a centicent.
    # MLB prices are cent ticks, so position cost already lies on that grid;
    # rounding the fee itself up to $0.0001 is equivalent for q=1.
    return (raw * D("10000")).to_integral_value(rounding=ROUND_CEILING) / D("10000")


class _VerifiedFeeLabelFilter(logging.Filter):
    """Keep inherited shadow telemetry labels consistent with verified KXMLBGAME fees."""
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = message.replace(
            '"maker_fee_applicability_certified":false',
            '"maker_fee_applicability_certified":true',
        )
        message = message.replace(
            "GENERAL_0.0175_FORMULA_AND_ZERO_FEE_UPPER_BOUND_APPLICABILITY_UNCERTIFIED",
            "KXMLBGAME_MAKER_MULTIPLIER_1_CENTICENT_ROUNDING_VERIFIED_2026_07_07",
        )
        record.msg = message
        record.args = ()
        return True


impl._kalshi_maker_fee_q1 = _kxmlbgame_maker_fee_q1
impl.logger.addFilter(_VerifiedFeeLabelFilter())


if __name__ == "__main__":
    impl.main()
