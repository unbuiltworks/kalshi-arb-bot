# Polymarket EV wallet + temporal crypto shadow

Status: **READ_ONLY / NO ORDER ENTRY / ZERO REALIZED P&L CREDIT**

This isolated research lane now runs two distinct Polymarket studies without trading credentials:

- `swisstony_sports` — public wallet `0x204f72f35326db932158cba6adff0b9a1da95e14`
- `l5_crypto` — public wallet `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`
- `polymarket_temporal` — independent q=1 temporal-pair shadow for BTC, ETH, SOL, and XRP 5m/15m Up/Down markets

## Wallet-forensics questions

1. What market families, sides, price bands, DCA patterns, and two-sided inventory behavior are actually present in recent public fills?
2. When a public fill becomes visible to us, what executable CLOB price and top-level depth are still available?
3. Is direct replication still positive after measured public-feed detection delay and slippage?

## Temporal-shadow questions

The temporal engine is venue/asset agnostic, but Polymarket is the first live-shadow adapter. It discovers current short-duration crypto markets through public Gamma data and reads executable CLOB top-of-book prices.

For each market it records:

- a simultaneous two-sided pair only if the q=1 pair clears the configured net floor after modeled crypto taker fees;
- otherwise one hypothetical first leg only when it is below the configured first-leg ceiling;
- temporal completion only when the opposite side later produces at least the configured net locked edge;
- incomplete first legs as stranded exposure, never as profit.

Current research defaults are `q=1`, `>=3c` net pair edge, and a `45c` first-leg ceiling. These are conservative baseline parameters, not a claim that they reproduce `l5_crypto`.

## Expansion contract

The shared temporal engine defines the first-tier matrix as BTC/ETH/SOL/XRP across Polymarket, Kalshi, and Gemini Predictions (12 independent venue x asset streams). Only Polymarket is live-shadow wired in this branch today. Kalshi and Gemini adapters remain separate follow-on implementations so venue fees, rules, and P&L cannot be mixed.

## Accounting boundary

This lane is not guaranteed arbitrage. Public wallet P&L, modeled copy P&L, temporal locked-pair shadow P&L, favorable markouts, and hypothetical fills are never `REALIZED` P&L. Promotion requires a separately authorized bounded live probe and authoritative settlement/reconciliation.

## Infrastructure boundary

The service is deployed in the separate Railway project `arb-ev-wallet-shadow`. It must not modify or consume service slots in `sports-arb-autonomous`, and it must not interfere with the AWS maker migration or the Aug. 28 edge-census clock.
