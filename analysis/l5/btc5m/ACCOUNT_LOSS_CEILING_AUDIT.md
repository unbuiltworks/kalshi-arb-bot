# Audit: the $75 cumulative loss ceiling and the −$136.78 it is compared to

Read-only, 2026-11-19. Sources: the `l5-pmus-live` heartbeat of 17:47Z, the
`pmus-spring-l5-probe` risk read of 21:08Z, and the code. Nothing was reset,
raised or changed. This document recommends no number.

## What the gate is

| | |
|---|---|
| ceiling | **$75.00**, `L5_MAX_CUMULATIVE_LOSS_USD` on `l5-pmus-live` (code default is $20) |
| compared against | `lifetime_realized_usd`, the all-time settled total in that service's ledger |
| scope | the whole venue account, every lane, every market the ledger holds a row for |
| who reads it | `l5-pmus-live` itself; the sports maker; `l5-btc5m-live` before arming (`ACCOUNT_CUMULATIVE_LOSS_CEILING`) |
| verdict now | −136.78 against 75.00: **BLOCKED**, room −$61.78 |

No record of who set 75, or when, was found in the repository.

## Finding 1 — −$136.78 is not a venue-confirmed cashflow figure

It is a ledger total in which a third of the rows are costed at an assumed
price.

| cost basis of the settled rows | rows |
|---|---|
| authenticated fills (a measured cost) | 287 |
| own fills, attribution unverified | 22 |
| **assumed $1.00 per contract — the ledger never saw the price** | **159** |
| total | 468 |

The ledger's own label on the assumed rows: *"their figure is a bound and
not a result."* It is the conservative reading of an unknown cost, and it is
the right default for a loss gate. But it overstates every loss on a
position bought below $1.00, which is all of them.

How large the overstatement is, from the same heartbeat:

* Venue cost attestation has re-read 116 rows so far and moved the total by
  **+$315.17**. Example: BTC5m window 2026-11-18 1540z, booked −$28.62,
  attested **−$1.84**.
* 97 rows were given up on after the maximum number of attempts and are
  **still at the $1.00 bound**.

## Finding 2 — the venue's cash says the account is about $15 down

| | |
|---|---|
| buying power, read from the venue 17:46Z | **$321.48** |
| owner-set portfolio anchor (`L5_PORTFOLIO_ANCHOR_USD`) | $336.68 |
| drawdown from anchor | **$15.20** |
| venue balance on 2026-11-16 00:11Z, account flat | $338.35 |

An account holding $321 against a $337 anchor has not lost $137 since that
anchor was set. **Not established:** the deposit and withdrawal history, and
when the anchor was last moved. Without those, lifetime cash P&L cannot be
stated; only that cash is $15.20 below the anchor and $16.87 below the
2026-11-16 balance.

A separate gate already reflects this: `l5-pmus-live` is refusing on
`PORTFOLIO_DRAWDOWN_LIMIT:15.2018>=15`. That limit is $15 and it is
legitimately tripped by twenty cents.

## Finding 3 — the ledger's own totals disagree by $78.70

| | |
|---|---|
| account-wide `realized_attributed` (what the gate reads) | −$136.78 |
| sum of the per-market rows by origin | −$215.49 |
| of which origin BTC15M (the service's own strategy) | −$3.62 over 193 markets |
| of which origin UNKNOWN (positions other services opened) | −$211.87 over 275 markets |

The gate's figure and the rows it is supposed to summarise differ by $78.70
and this audit did not find why. It is consistent with corrections applied
to one total and not the other, but that is a guess.

## Why the ceiling was exceeded

On this evidence, mostly not by losing $137. The `l5-pmus-live` strategy's
own measured result is −$3.62. The rest is positions opened by other
services on the same account (the sports maker, BTC5m windows, manual
orders) that this ledger can see but cannot price, booked at $1.00 a
contract.

**That does not make the account profitable, and it does not mean the gate
should be ignored.** Cash is down. The honest statement is that the gate's
input is a bound that is known to be loose by an unknown amount, with 97 rows
still uncorrected.

## What would settle it, without touching the ceiling

1. Cost the remaining 159 assumed rows from the venue's own fill history, by
   the existing attestation path. `fix/l5-venue-attested-cost-basis-20261113`
   and `config/cost_basis_batch_manifest_20261112_manual1915z.json` are the
   prior work. It changes a ledger that gates money, so it needs the owner.
2. Explain the $78.70 difference between the two totals.
3. State the deposit and withdrawal history so cash P&L can be computed
   against it.

After that, the comparison against $75 is a real one, and whether trading
resumes is the owner's decision on a true number.

## What this means for a BTC5m window

`l5-btc5m-live` reads the same published record. As it stands it would
refuse at arming with `ACCOUNT_CUMULATIVE_LOSS_CEILING`. That has not been
observed on the lane itself, because the disarmed loop stops at the envelope
gate first.
