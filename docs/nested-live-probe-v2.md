# Nested live probe v2

Isolated one-shot Kalshi nested-strike probe harness.

- Default mode: dry run only.
- Quantity: exactly 1 contract.
- Same-event nested strikes only.
- Minimum modeled locked profit: $0.03.
- Event horizon: <= 6 hours.
- Boundary 0c/100c prices rejected.
- Live send remains disarmed until private fill -> IOC hedge orchestration is installed and explicitly armed.
