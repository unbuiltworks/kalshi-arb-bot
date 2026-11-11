# Rebate reconciliation — what legitimately adjusts our realized net

Source: `analysis/l5/REALIZED_BRIDGE.md`, `analysis/l5/realized_bridge.json`,
`src/l5_realized_bridge.py`, and the live heartbeat at
2026-11-07T19:11:58Z. No figure below is an estimate.

## 1. The bridge

| line | usd | provenance |
|---|---|---|
| A. settlement / trading P&L | **−12.5082** | the venue's own `realizedPnl` |
| B. + confirmed maker rebates | **+2.1300** | **credited at fill**, 293 fills |
| C. + confirmed liquidity rewards | 0 | none posted |
| D. − fees charged to us | 0 | maker-only; the venue charged none |
| **= actual realized net** | **−10.3782** | |

**The credits are additive, and that was tested rather than assumed.** The
double-count test reproduced 3 of 3 markets exactly: the venue's
`realizedPnl` is pure settlement cashflow (`payout − cost_basis`) carrying
no rebate and no fee, so a rebate is not already inside it. Had that failed
the bridge refuses rather than double-counting.

So **$2.13 of maker rebates is confirmed credited** — $0.00727 a fill over
293 fills. My earlier statements that "no rebate has ever been credited"
were **wrong about this $2.13**; they were correct only about the Stage 1
windows, where no rebate was credited. The distinction matters and I had
been stating the stronger claim.

## 2. Two memo lines that are NOT performance

| line | usd | why it is excluded |
|---|---|---|
| non-trading credits | **10.00** | one `ACTIVITY_TYPE_TRANSFER`, kind **`remediation`**, dated 2026-09-06. *"Held OUT of the bridge on purpose: it is in the balance and it is not trading income."* |
| rewards accrued, not credited | **1.48** | PENDING $0.40 + SKIPPED $1.08. *"Only a status the venue uses for money paid counts. An unrecognised status is an accrual until it is understood, never income."* |

**This is where a −$5.25-style figure comes from, and it would be wrong.**
Adding the $10 remediation transfer to the realized net turns a goodwill
credit into trading performance. The bridge excludes it deliberately and so
must any P&L we report. The $1.48 is not income until the venue pays it.

## 3. Applying the bridge to the CURRENT ledger

| | usd |
|---|---|
| account-wide realized now (`realized.realized_total`) | **−15.61090000** |
| + confirmed maker rebates | +2.1300 |
| **= rebate-adjusted realized net** | **−13.4809** |

**And one line of this is NOT reconciled.** The bridge's A line is
**−12.5082**; the ledger's account-wide figure is **−15.6109** — a
**−$3.1027** difference. Settlements recorded since the bridge snapshot
account for roughly **−$1.83** (the six settled windows at −$1.4214 plus
1230z at −$0.4100), leaving about **−$1.27 unexplained**, before any scope
difference between "the venue's `realizedPnl`" and "our attributed
settlement rows".

**So the defensible statement today is a range, not a number:**
rebate-adjusted realized net is **−$13.48**, with **−$1.27 of the A line
unreconciled** and a separate attribution question behind it —
`accounting.verified_l5_status` reads `UNPROVEN_LEGACY_ATTRIBUTION` and
`realized.unknown_basis_keys` lists eight 2026-10-14/15 markets whose cost
basis was never attested. The verified L5-strategy figure is
**−$3.4103**.

## 4. The three measures, kept apart

| measure | field | value |
|---|---|---|
| account-wide cumulative | `realized.realized_total` | **−15.61090000** |
| day-scoped | `day_realized.utc_day_realized_usd` | **−0.9000** (2026-11-07) |
| verified L5-strategy | `accounting.verified_l5_realized` | **−3.410300** |

The heartbeat labels them itself: *"the DAILY ceiling reads
`utc_day_realized_usd`; the separate cumulative ceilings read
`all_time_realized_usd`."* None substitutes for another.

## 5. What would close it

1. **Re-run the bridge against the current ledger**, so line A is today's
   venue `realizedPnl` rather than the snapshot's. The module exists
   (`src/l5_realized_bridge.py`); it needs an authenticated venue read,
   which is read-only and needs no trading authorization.
2. **Attest the eight unknown-basis markets** from 2026-10-14/15, which is
   what `UNPROVEN_LEGACY_ATTRIBUTION` is waiting on. Until then the
   account-wide and L5-strategy figures cannot be bridged to each other.
3. **Re-read reward status.** $1.48 sits in PENDING/SKIPPED; only a status
   the venue uses for money paid may become income.

**Until 1 and 2 are done, the reported figure stays −$15.6109 account-wide**
— the conservative, unadjusted ledger value — with **−$13.4809** as the
rebate-adjusted figure stated alongside it, and **neither** including the
$10 remediation credit.
