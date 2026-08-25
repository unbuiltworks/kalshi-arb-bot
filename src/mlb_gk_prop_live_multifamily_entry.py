"""Entrypoint for the isolated HR/TB/K live canary."""
from __future__ import annotations

from decimal import Decimal
import os

from src import mlb_gk_multifamily_discovery_patch as discovery_patch
from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary
from src.sports_trade_reporting import install as install_reporting


def _profit_floor() -> Decimal:
    value = Decimal(os.getenv("MLB_GK_MULTI_MIN_PROFIT", "0.03"))
    if value < Decimal("0.01"):
        raise ValueError("MLB_GK_MULTI_MIN_PROFIT may not be below 0.01")
    return value


def main() -> None:
    discovery_patch.install()
    canary.MIN_PROFIT = _profit_floor()
    print(f"MLB_GK_MULTI_THRESHOLD_OVERRIDE {canary.MIN_PROFIT}", flush=True)
    install_reporting(
        canary,
        strategy="sports_gk_multifamily_rule_risk",
        run_id_env="MLB_GK_MULTI_RUN_ID",
    )
    canary.main()


if __name__ == "__main__":
    main()
