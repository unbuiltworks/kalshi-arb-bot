# The $2.51 transfer — provenance, reconciliation, and what it corrects

Source: `kind: incentive_audit`, emitted by the probe on serving `3ec9e1cd996c`
at **2026-09-14T22:59:21.565847915Z**, from the authenticated
`/v1/portfolio/activities` walk (1,269 rows, 64 pages, ended `eof`,
2026-08-14T03:26:06Z → 2026-09-14T22:54:32Z) and `/v1/incentives/earnings`.

## Identification

| field | value |
|---|---|
| timestamp | **2026-09-14T22:39:20.206118633Z** (`createTime` == `updateTime`) |
| source identifier | **`CG78P5C0000Q`** — a Polymarket US internal transaction id |
| transaction hash | **none published.** `externalId` is `""`; no chain hash appears in the record |
| sender / source account | **not publicly identified.** `paymentMethod: null`, `metadata: {}`, `externalId: ""` — the venue names no counterparty contract or account |
| asset | **USD** (`currency: "USD"`), the PMUS fiat-denominated balance, not USDC |
| activity type | **`ACTIVITY_TYPE_TRANSFER`** |
| status | `ACCOUNT_BALANCE_CHANGE_STATUS_COMPLETED` |
| venue label | `description: "liquidity reward payout"` → **REWARD (liquidity program)** |

Not REDEEM (those are `ACTIVITY_TYPE_POSITION_RESOLUTION`, 90 rows), not
MAKER_REBATE and not TAKER_REBATE — on this venue those are credited per fill
as `commissionNotionalCollected`, never as a transfer.

The transport type is TRANSFER and the *label* is a reward. The label is what
classifies it; the type only says how it moved.

## One credit or many

**One transaction, many underlying credits.**

The `transactions[]` array inside the balance change holds exactly **one**
entry, the same id and the same `2.51`. So it is a single balance movement.

But the earnings endpoint shows **44 PAID reward rows totalling $7.04**, and
the account has exactly **three** `"liquidity reward payout"` transfers:

| transaction id | when | amount |
|---|---|---|
| `CCRG00G5T00Q` | 2026-09-09T12:28:25.392786335Z | $2.88 |
| `CD6Q1RAG000Q` | 2026-09-10T13:14:05.538549390Z | $1.65 |
| **`CG78P5C0000Q`** | **2026-09-14T22:39:20.206118633Z** | **$2.51** |
| | | **$7.04** |

$2.88 + $1.65 + $2.51 = **$7.04**, which equals `credited_usd` and the PAID
total exactly. So the three payouts are the settlement of the 44 PAID
per-market reward rows, and the $2.51 aggregates a subset of them.

## Attribution — partially blocked, and the blocker is ours

The payout row itself carries **no** attribution: `metadata` is empty and there
is no market, order or fill reference on it. Attribution can therefore only be
made by matching it to the per-market earnings rows.

**That match cannot be completed from this snapshot**, because our own audit
truncates: `rows_detail` was `rows[:50]` against **79** rows, so only $5.42 of
the $7.04 PAID is visible and every visible PAID row is dated 09-08…09-10 —
the rows behind a 09-14 payout are among the 29 that were cut. A truncation
that does not announce itself is indistinguishable from an absence, which is
the same failure as a fabricated zero.

Fixed and **staged, not deployed** (`src/pmus_incentive_audit.py` is a watched
file): the cap is raised to 2,000 and any remaining cut is reported as
`rows_detail_truncated` / `rows_detail_omitted`. The next audit run will carry
all 79 rows and the per-market attribution of the $2.51 closes then.

So: **attributable to the liquidity program and to this account, with certainty;
to specific markets, not yet; to a specific order or fill, no — the venue does
not publish that linkage on a payout.**

## Reconciliation

### Ledger C — incentives, venue-credited only

| stream | now | previously recorded | change |
|---|---|---|---|
| maker rebates credited on fills | **$9.14** over 737 credited fills of 1,174 | $8.63 over 686 of 997 | +$0.51, +51 fills |
| liquidity rewards PAID | **$7.04**, 44 rows, 3 transfers | $4.53, 2 transfers | **+$2.51, +1 transfer** |
| **confirmed incentives** | **$16.18** | $13.16 | +$3.02 |

Excluded, deliberately: rewards PENDING $0.70 (11 rows) and SKIPPED $1.37
(24 rows) — accrued, not credited. Also excluded: the formula estimate
`estimated_rebate_on_our_fills` $9.31, which is close to the observed $9.14 and
corroborates it but is never counted. And the **$10.00 ISV goodwill credit**
(`CAVGFQ5ZA00M`, 2026-09-06, outage remediation for the Sep 5 incident) is
neither trading P&L nor an incentive and is netted into nothing.

### Ledger D — venue-confirmed realized trading/settlement

`analysis/l5/cross_shadow_live/ledger.jsonl`: **−$21.8092** over 23 settled
markets, `settled_by: position_resolution_activity`, spanning
2026-09-13T23:31Z → 2026-09-14T20:47Z.

**This is stale and its scope does not match Ledger C.** The probe's own
`realized_today_usd` at 23:01Z is **−$30.5813** for 2026-09-14 alone, so
settlements after 20:47Z have not reached the local ledger; and Ledger C above
spans the full account history back to 2026-08-14.

## All-in realized strategy P&L — stated, not fabricated

    trading/settlement P&L
  + confirmed attributable fees/rebates
  + confirmed rewards
  = all-in realized

The right-hand two terms are exact and account-lifetime:

    confirmed maker rebates      +$9.14
    confirmed liquidity rewards  +$7.04
    confirmed incentives         +$16.18

The first term is **not** available at the same scope. Adding a one-day
Ledger D to an account-lifetime Ledger C would produce a precise-looking number
that means nothing, so the equation is left open. Two things close it:

1. a settlement audit covering 2026-08-14 → now, so Ledger D matches Ledger C's
   window;
2. resolution of one semantic question below.

**The open question, and it is a $9.14 double-count risk.** Ledger D's
`realized_usd` is the venue's own `trade.realizedPnl`. Whether that field is
already net of `commissionNotionalCollected` is not settled by field names, and
if it is, adding the $9.14 of maker rebates counts them twice. The repo's design
intent is that the three outcomes stay separate
(`pmus_settlement_auditor.py`, "matched-pair P&L | directional residual P&L |
incentives/rebates"), but intent is not proof.

The test that settles it: reconstruct one settled market's P&L from its fills
(Σ payout − Σ price×qty) and compare to its `realizedPnl`; a gap equal to that
market's credited commission means the rebate is already inside. The liquidity
rewards ($7.04) carry no such risk — they arrive as separate transfers and
cannot be inside a trade row.

**Per the directive, the $2.51 is not counted as realized strategy P&L.** It is
recorded under confirmed incentives, where it is now venue-confirmed: a
COMPLETED balance change with an explicit venue label.

## Is the $8.63 / $4.53 accounting incomplete, delayed, or the same money seen twice?

**None of those. It was correct when measured, and has since been superseded by
new payments.**

* $4.53 was exactly $2.88 + $1.65 — the only two reward payouts that existed at
  the time. The $2.51 is a genuine **third** payout, with its own transaction
  id and its own timestamp four days later. Not a re-representation, not a
  delayed recognition of the same money.
* $8.63 → $9.14 of maker rebates likewise reflects **51 additional credited
  fills** (686 → 737), i.e. new trading, not a correction.

One thing *was* incomplete, and it is ours rather than the venue's: the audit's
own 50-row truncation, which hid $1.62 of PAID rewards and blocks per-market
attribution. That is fixed and staged.

No production risk change is proposed or made.

---

# Re-trace on request: is this Brett's $2.51, and is it already in Ledger C?

**Yes to both, with one venue caveat stated rather than assumed.**

| question | answer |
|---|---|
| already represented in Ledger C? | **Yes.** Ledger C's `LIQUIDITY REWARDS · PAID (credited) $7.04` is exactly $2.88 + $1.65 + **$2.51**, and the report's `latest audit` stamp is the same 2026-09-14T22:59:21.565847915Z capture the $2.51 came from. It is counted once, in the rewards stream only. |
| reward/rebate distribution? | **A liquidity REWARD.** The venue's own `description` is `"liquidity reward payout"`. It is **not** a maker or taker rebate: on this venue those are `commissionNotionalCollected` netted into each fill (Ledger C's separate $9.14 / 737 fills line) and never move as a transfer. |
| redemption? | **No.** Redemptions are `ACTIVITY_TYPE_POSITION_RESOLUTION` (90 rows in the same walk). This is `ACTIVITY_TYPE_TRANSFER`. |
| unrelated transfer? | **No.** The only unrelated transfer on the account is `CAVGFQ5ZA00M` $10.00, the ISV goodwill credit for the 2026-09-05 outage, which is neither trading P&L nor an incentive and is netted into nothing. |
| added to strategy P&L? | **No, and it will not be.** It sits under confirmed incentives. Ledger D is unchanged at −$25.0465 over 24 venue-confirmed settled markets. |

**The venue caveat.** The transfer identified here is on **Polymarket US**. The
request named "Polymarket", and those are different venues. This codebase holds
exactly one Polymarket-family address, `0xb945945d…db68`, and that is **L5's**
target wallet, not ours — there is no Polymarket (crypto) account of our own in
the repo to check. So: a $2.51 on the crypto side cannot be checked from here
and none is claimed to exist. If Brett's observation was on the crypto venue it
is a different event and would need its own trace; if it was the US account, it
is `CG78P5C0000Q` and it is fully accounted above.

Nothing about this changes the all-in equation's status: the incentive terms are
exact ($9.14 + $7.04 = $16.18) and the trading term still cannot be added at a
matching scope until a settlement audit spans the same window, with the
`trade.realizedPnl` double-count question resolved first.
