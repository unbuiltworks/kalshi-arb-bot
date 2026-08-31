# Gemini L5 Crypto + Sports Shadow — Frozen Design

Frozen: 2026-08-31 ET
Branch: `experiment/gemini-l5-crypto-sports-shadow`
Base: `4ad8b98d33052f808786fe58a230615a03228a59`
Classification: **SHADOW / NO ORDER ENTRY**

## Objective

Test whether the structural behavior observed in the Polymarket L5 account can transfer to Gemini when the quote universe is widened beyond short crypto to include certified short-duration Sports markets.

This is a passive-liquidity/subsidy experiment, not a directional forecasting strategy and not locked arbitrage by default.

Economic question:

> Can broad, simultaneous, two-sided passive liquidity across short crypto and certified Sports generate enough genuine external maker fills while keeping one-sided residual losses below actually credited venue subsidy?

North-star metric remains:

`realized_net_cents_per_maker_turnover_dollar`

where realized net includes settlement-scored trading cash, actual fees, and only venue incentives that were actually credited. Modeled rebates or rewards are never realized P&L.

## Hard rules

1. Shadow only until separately promoted. This module must have no order-submit path.
2. No self fills, related-account crossing, wash-style turnover, or manufactured fills.
3. C-series is closed and `ladder_frontier.py` remains untouched until its sealed L-series read is complete.
4. Sports settlement semantics fail closed. A market that cannot be certified is excluded, not guessed.
5. Residual inventory is scored to authoritative settlement. No rescue/chasing/unwind logic in the first shadow.
6. Crypto and Sports are reported separately and combined; a strong category may not hide a weak one.
7. Subsidy is reported separately by program/category until actual account credits establish eligibility and effective rate.

## Initial universe

### Crypto

- BTC 5m / 15m
- ETH 5m / 15m
- SOL 5m / 15m
- XRP 5m / 15m

### Sports

Start with active events expected to settle within 24 hours. Expand to <=72 hours only after the same-day cohort is scored.

Initial admission is limited to simple binary Sports contracts whose Gemini event/market metadata and settlement rule can be certified unambiguously. Futures and long-dated events are excluded from churn scoring.

Every admitted Sports market must carry:

- event id / contract id
- category and subcategory
- sport/league where available
- effective time / expiry time
- market type
- outcome semantics
- settlement-rule certification status
- duration bucket: `<24h`, `24-72h`, `3-7d`, `>7d`

Any missing or ambiguous settlement field => `RULES_UNCERTIFIED`, no simulated exposure.

## Quote topology

The first unified shadow tests the simplest L5-like topology:

- stand on both YES and NO sides simultaneously;
- join the current best maker price;
- q1-equivalent simulated size per side for mechanical safety/comparability;
- maintain breadth across all eligible markets simultaneously;
- no completion chase after first fill;
- no taker rescue;
- no same-side unwind;
- hold residual inventory to settlement.

Deeper rungs are not part of the first Sports shadow unless the sealed L-series read independently shows incremental value from depth. The in-sample clue that deeper crypto rungs were rarely touched is not sufficient to decide this in advance.

## Fill convention

Use the same optimistic print-proxy maker-fill convention as the sealed Gemini frontier/L-series when comparable public data exists, and label it explicitly as shadow/proxy. Do not describe proxy fills as executable or realized.

For Sports, if the public stream cannot support the identical fill convention, record the mismatch and produce a separate comparability flag rather than silently changing assumptions.

## Accounting per market

Track at minimum:

- quote-seconds by side
- simulated maker fills by side
- maker turnover dollars
- matched quantity
- matched pair cost
- matched-pair trading margin
- one-sided residual quantity/dollars
- authoritative settlement outcome
- residual settlement P&L
- maker fees under current live fee config
- modeled rebate separately (never realized)
- modeled LR separately (never realized)
- 5s / 15s / 30s executable markout after each proxy fill where data permits

## Required aggregate metrics

By category, subcategory, duration bucket and combined portfolio:

1. `maker_fill_rate`
2. `maker_to_maker_completion_rate`
3. `time_to_opposite_fill_s`
4. `maker_turnover_dollars`
5. `matched_quantity_pct`
6. `one_sided_residual_dollars`
7. `residual_loss_per_turnover`
8. `pair_margin_per_turnover`
9. `fees_per_turnover`
10. `rebate_credited_per_turnover` — live-credit stage only
11. `lr_credited_per_quoted` — live-credit stage only
12. `net_subsidy_minus_losses`
13. `realized_net_cents_per_maker_turnover_dollar`
14. capital-minutes tied up and P&L/capital-minute
15. 5s/15s/30s markout and subsidy-earned-per-fill / inventory-loss-created-by-fill

## Structural comparisons

The shadow must let us answer:

- Crypto vs Sports completion and residual drag.
- Same-day Sports vs 24–72h Sports capital efficiency.
- Breadth benefit: total independent simultaneous markets vs residual concentration.
- Join-only topology vs any later depth arm, only after a new predeclared clock.
- Whether Sports adds enough genuine external flow to raise total executable capacity materially above the crypto-only ceiling.

## Promotion gates

No live Sports maker order from this workstream until all are true:

1. settlement-rule certification is fail-closed and tested;
2. fresh Sports shadow has decision-quality exact settlement sample;
3. residual drag is measured, not inferred from matched percentage;
4. Sep 2 verification establishes whether Sports receives Maker Rebate, Liquidity Rewards, both, or neither;
5. any subsidy relied upon has been observed as an actual account credit before scaling;
6. a separate bounded q1 AWS live canary is approved with explicit loss/session limits.

If Sports receives no subsidy, it remains eligible only if its own settlement-scored trading economics are independently positive; volume alone is not an edge.

## Relationship to other work

- Kalshi Policy D remains a separate pure trading-edge engine.
- Current Gemini L-series remains sealed and unchanged through its predeclared read.
- This work may reuse generic authentication, market discovery, inventory/accounting, L5 analysis, and AWS plumbing from existing branches, but must not merge stale strategy assumptions wholesale.
- Polymarket is research-only for this project until compliant US trading access exists.

## Target decision

After fresh mechanics plus Sep 2 subsidy verification, classify each universe as one of:

- `ECONOMICALLY_VIABLE_PASSIVE_LIQUIDITY`
- `MECHANICS_WORK_SUBSIDY_INSUFFICIENT`
- `SUBSIDY_EXISTS_RESIDUAL_TOO_TOXIC`
- `INSUFFICIENT_EXTERNAL_FLOW`
- `RULES_OR_DATA_NOT_CERTIFIABLE`

Only the first classification can proceed toward a bounded live canary.