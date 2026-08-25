"""Production entrypoint for the hardened G/K sports transport.

The underlying supervisor/discovery/risk budget remain unchanged. Only the live
submission primitive is replaced before reporting/budget/capital wrappers install.
"""
from __future__ import annotations

from src import sports_gk_autonomous_supervisor as supervisor
from src.gemini_kalshi_probe_transport_fractional_safe_v2 import (
    submit_dual_probe_fractional_safe_v2,
)

# The supervisor installs reporting, durable budget, and venue-capital wrappers
# around canary.submit_dual_probe at runtime. Install the hardened transport first
# so those wrappers remain authoritative and unchanged.
supervisor.canary.submit_dual_probe = submit_dual_probe_fractional_safe_v2


def main() -> None:
    supervisor.main()


if __name__ == "__main__":
    main()
