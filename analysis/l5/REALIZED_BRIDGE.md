# Realized bridge: settlement -> actual realized net

| line | usd |
|---|---|
| A. Settlement / trading P&L (venue realizedPnl) | -12.5082 |
| B. + confirmed maker rebates (credited at fill) | 2.1300 |
| C. + confirmed liquidity rewards posted | 0 |
| D. - fees charged to us | 0 |
| **= actual realized net** | **-10.3782** |

## Memo — in the balance, NOT performance

- Non-trading credits (goodwill / remediation): **10**
- Liquidity rewards accrued but not credited: **1.48**

## Double-count test

- Markets tested: 3; reproducing exactly: 3
- Safe to add credits: **True**
- The venue's realizedPnl is pure settlement cashflow (payout - cost_basis), carrying no rebate and no fee, so credits are additive to it rather than already inside it. A failure here means the venue changed that and the bridge would double-count; it refuses instead.

## Liquidity rewards by status

| status | rows | usd | counted as income |
|---|---|---|---|
| PENDING | 1 | 0.40 | NO |
| SKIPPED | 1 | 1.08 | NO |

Only a status the venue uses for money paid counts. An unrecognised status is an accrual until it is understood, never income.
