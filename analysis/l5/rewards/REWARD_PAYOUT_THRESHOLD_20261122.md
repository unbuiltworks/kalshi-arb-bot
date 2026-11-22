# Sports liquidity rewards — the minimum-payout condition

22 Nov 2026. Read-only analysis of reward rows already recovered from the
venue (131 of about 185 rows; the rest scrolled out of the hourly log
window). Estimates are marked.

## What the rows show

| Day | Paid rows | Paid $ | Skipped rows | Skipped $ |
|---|---|---|---|---|
| 9 Sep | 2 | 1.51 | 2 | 0.09 |
| 10 Sep | 5 | 1.00 | 0 | 0 |
| 11 Sep | 0 | 0 | 6 | 0.35 |
| 12 Sep | 0 | 0 | 3 | 0.14 |
| 13 Sep | 0 | 0 | 12 | 0.89 |
| 14 Sep | 0 | 0 | 14 | 0.80 |
| 15 Sep | 0 | 0 | 5 | 0.27 |
| 14 Nov | 17 | 3.01 | 0 | 0 |
| 15 Nov | 22 | 2.95 | 0 | 0 |
| 16 Nov | 6 | 1.13 | 1 | 0.01 |
| 17 Nov | 6 | 0.40 | 4 | 0.48 |
| 18 Nov | 25 | 8.26 | 0 | 0 |

- Individual paid rows are as small as $0.01, so the venue's "rewards under
  $1.00 are not paid" rule is not per market.
- Every day whose rows total under $1 was skipped entirely (11 to 15 Sep).
  Days at or above $1 were paid. **Inferred:** the $1 minimum applies to a
  day's total, probably per programme. 17 Nov (some paid, some skipped,
  total $0.88 recovered) fits only if rows are missing or two programmes
  were involved.

## Why it matters for the controller

- A market having a live programme is not enough. A day of quoting that
  earns under $1 in total is paid nothing.
- **Estimate:** rewarded markets earned about $0.11 per dollar-hour resting
  on 14 to 18 Nov. At two contracts a side (about $2 resting per market),
  two markets for a 90-minute window is about $0.66: below the minimum.
  The same two markets for ten hours is about $4.40: paid.
- So reward credit in the controller should depend on the projected day
  total clearing $1, not on a programme existing. The code already has the
  venue's scoring rule (`quote_estimate` in `src/pmus_sports_rewards.py`);
  the controller does not yet use it for this.

Not yet verified: whether the minimum is per programme or per account, and
the current programme list and pool sizes (they change daily).
