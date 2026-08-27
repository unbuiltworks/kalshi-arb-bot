# Polymarket EV wallet shadow

Status: **READ_ONLY / NO ORDER ENTRY / ZERO REALIZED P&L CREDIT**

This isolated research lane observes two public Polymarket wallets without using trading credentials:

- `swisstony_sports` — public wallet `0x204f72f35326db932158cba6adff0b9a1da95e14`
- `l5_crypto` — public wallet `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`

## Phase 1 questions

1. What market families, sides, price bands, DCA patterns, and two-sided inventory behavior are actually present in recent public fills?
2. When a public fill becomes visible to us, what executable CLOB price and top-level depth are still available?
3. Is direct replication still positive after measured public-feed detection delay and slippage?

## Accounting boundary

This lane is not guaranteed arbitrage. Public wallet P&L, modeled copy P&L, favorable markouts, and hypothetical fills are never `REALIZED` P&L. Promotion requires a separately authorized bounded live probe and authoritative settlement/reconciliation.

## Infrastructure boundary

The service is deployed in the separate Railway project `arb-ev-wallet-shadow`. It must not modify or consume service slots in `sports-arb-autonomous`, and it must not interfere with the AWS maker migration or the Aug. 28 edge-census clock.
