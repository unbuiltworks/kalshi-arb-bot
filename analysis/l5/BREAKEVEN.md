# Break-even completion rate

Basis: **per opened leg, measured WITHIN markets, reconciling only** — 15 settled markets, 179.73 opened legs (149.93 matched pairs, 29.80 residual units)

| quantity | value |
|---|---|
| pair profit per matched pair | $0.057230 |
| residual loss per residual unit | $-0.331449 |
| confirmed maker rebate per opened leg | $0.002953 *(approximate mapping)* |
| confirmed liquidity rewards | $0.0000 |
| confirmed fees | $0.0000 |

| leg completion rate | value |
|---|---|
| break-even, excluding credits | **85.28%** |
| break-even, including confirmed credits | **84.52%** |
| **observed** | **83.42%** |
| gap vs the credited end | **-1.10 pp** |

**BELOW BREAK-EVEN on both ends of the range.** the two figures BRACKET the answer: the credit mapping is approximate, so the truth sits between them.

## What a completion point is worth

- **$0.003887 per opened leg** for each percentage point of completion rate
- **$0.6986** per point across this cohort
- **+1.10 pp** still needed to reach break-even
- a residual unit costs 5.8x what a matched pair earns, which is why completion rate dominates every other lever measured so far

## Excluded

- `aec-setkameua-vervas-kohboh-2026-09-08` — qty_reconciliation.reconciles is false (qty_delta 4.3400)

a market whose venue and ledger quantities disagree is refused every per-unit rate: its residual units may be a missed fill rather than a flat book, which would put loss in the numerator against no units in the denominator

## Credits

rebates accrue on maker NOTIONAL; we hold a per-fill figure and scale it by this cohort's fills-per-leg. The break-even is therefore a RANGE, not a point.

every liquidity-reward row is SKIPPED or PENDING; accrued face value is never revenue

## Per-market cross-check (weaker cut, kept on purpose)

- markets paired to zero **11** / stranded **5** → observed **68.75%**
- break-even on that cut: **73.63%** including credits

CONTAMINATED AND KEPT ANYWAY. Every venue-confirmed market is negative and every derived market positive partly BY CONSTRUCTION: a market that pairs to zero leaves no residual to settle, so the venue never publishes a realizedPnl for it and it can only appear on the derived side. Read the per-leg basis above; this is here so the weaker cut can be argued with rather than hidden.
