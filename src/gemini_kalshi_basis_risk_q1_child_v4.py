"""Dedicated dynamic-policy q=1 basis child, retaining v3 gate telemetry and v2 durable controls."""
from __future__ import annotations
import logging
from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_child_v3 as child_v3


def main() -> None:
    dynamic_policy.install()
    child_v3.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
