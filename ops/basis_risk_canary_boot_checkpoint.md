# Basis-risk q1 canary boot checkpoint

Updated 2026-08-22.

- Research classification only; not guaranteed arbitrage.
- Approved maximum-loss research budget: $5.
- Initial live tranche remains q=1, one trade, <=$1 deployed capital.
- Guaranteed-arbitrage Gemini/Kalshi q1/q5 settlement-equivalence hard stop remains unchanged.
- Canonical crypto ledger reconciliation completed before any basis-risk arm: zero submitted crypto trades lacking TRADE_REALIZED.
- Hardened v2 entrypoints use schema-valid LIVE_PROBE mode and a Postgres-backed global one-probe reservation gate.
- Railway service must complete an unarmed boot/database validation before CRYPTO_BASIS_RISK_CANARY_ARM may be enabled.
- No basis-risk CAPITAL_RESERVED, order, fill, or realized event existed at this checkpoint.
