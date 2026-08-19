"""Entrypoint for the isolated HR/TB/K live canary."""
from __future__ import annotations

from src import mlb_gk_multifamily_discovery_patch as discovery_patch
from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary
from src.sports_trade_reporting import install as install_reporting


def main() -> None:
    discovery_patch.install()
    install_reporting(
        canary,
        strategy="sports_gk_multifamily_rule_risk",
        run_id_env="MLB_GK_MULTI_RUN_ID",
    )
    canary.main()


if __name__ == "__main__":
    main()
