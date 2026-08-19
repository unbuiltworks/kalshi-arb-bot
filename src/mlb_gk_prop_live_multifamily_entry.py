"""Entrypoint for the isolated HR/TB/K live canary."""
from __future__ import annotations

from src import mlb_gk_multifamily_discovery_patch as discovery_patch
from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary


def main() -> None:
    discovery_patch.install()
    canary.main()


if __name__ == "__main__":
    main()
