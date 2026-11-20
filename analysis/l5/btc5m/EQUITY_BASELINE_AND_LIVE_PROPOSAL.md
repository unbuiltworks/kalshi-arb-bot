# Venue-equity accounting and one BTC5m live window — proposal for the owner

2026-11-20. Nothing here is applied. No limit, ledger figure or arming state
has changed. BTC5m is disarmed. No new fills, no new settled result.

## 1. Diagnostic result (deployed read-only, owner-approved)

`l5-pmus-live` ran `3390246e0` (deployment `4efa8e28`). Ledger identical
before and after: total -136.7845, 116 corrections, 97 rows at the bound,
468 rows. All 97 rows compared; raw output in
`evidence/venue_cost_diagnostic_rows_20261120.json`.

| finding | |
|---|---|
| money in or out since the re-anchor (2026-11-16 00:58Z) | **none.** Feed read to its end; 4 deposits, 8 transfers, 1 bonus, all earlier |
| corrections posted after the re-anchor for earlier rows | none. The suspected unearned credit does not exist |
| cause of the mismatch | 94 of 97: the venue holds fills the ledger does not. In 89 the venue shows the same quantity on both sides (a bought pair) where the ledger holds a one-sided leftover costed at $1.00 |
| rows inside the gate's figure | 42 of 97 (the other 55 settled before the re-anchor) |
| those 42, as booked | -108.20 |
| the 40 of them the venue could be read for | booked -107.20, on the venue's fills **-9.88** |
| effect on the gate's figure if those 40 were corrected | -136.7845 to **about -39.47**. Not applied |
| venue balance over the same period | 338.3456 to 321.4782: **-16.87** |

So the ledger overstates the period's loss by about $97 on evidence in hand.
About $22 between the corrected figure and cash is still unexplained.

## 2. What is built (code only, not deployed)

**`l5-pmus-live`** — see section 5 for the pinned release (it supersedes
the `b06342976e` first named here); kept here as
`patches/pmus-live-equity-baseline.on-3390246e0.patch`.

* Baseline record `L5_EQUITY_BASELINE`: id, timestamp, buying power,
  positions, open orders (must be 0), equity (must equal buying power plus
  matched pairs), the allowed drawdown, the historical figures (preserved),
  and a verbatim statement that it resets and replaces no limit.
* Deposits and withdrawals: detected from the venue feed. Because none has
  occurred since the re-anchor, the venue's amount field is unverified, so
  amounts are not interpreted: any such row after the baseline blocks until
  a new baseline is written.
* Period result: only rows with a measured cost count. Uncertain rows are
  listed beside it and added to nothing.
* The account risk record now also publishes the existing portfolio drawdown
  limit and the baseline, each with its own verdict.

**`l5-btc5m-live`** — on this branch. Its account-risk read now requires both
blocks in the record and refuses on either one's refusal, on a state it does
not know, or on no remaining room. The daily and cumulative checks run first
and are unchanged. Remaining room is the tightest of all four.

**What blocks trading, each tested end to end across both services:**

| condition | result |
|---|---|
| buying power never read, or older than 180s | blocked |
| order state uncertain; positions unreadable | blocked |
| transfers not yet read, read stale (>15 min), feed error, feed not read back to the baseline | blocked |
| any deposit, transfer or bonus after the baseline; an undated one | blocked |
| baseline malformed, equity overstated, read with orders resting | blocked |
| drawdown at or past either limit; room unreadable or zero | blocked |
| the older service not publishing the blocks | blocked |
| daily or cumulative ledger limit breached | blocked, as before |

Tests: 94 new on the service side (185 with the adjacent suites; regression
subset 12 failed / 2445 passed, the same twelve as the base); 15 new on the
BTC5m side (BTC5m subset: the same 11 pre-existing failures before and
after). 26 deliberate breakages, each fails at least one test.

One existing test fixture was changed: the BTC5m account-risk fixture now
includes the two blocks, because the reader requires them.

Not shown: any of this against the real venue. The transfer check's date
margin was removed on review; see section 5.

## 3. What still blocks a window, and the decision each needs

| gate | reads now | to clear it |
|---|---|---|
| cumulative loss, $75 | -136.78 | **A.** correct the 40 rows from the venue evidence (to about -39.47); needs the correction rule built and approved. **B.** an explicit policy transition replacing it with the equity baseline. Not both needed |
| portfolio drawdown, $15 from $336.68 | $15.20 | once BTC5m honours it, the owner must set a new anchor and allowance. This is the risk authorization |
| equity baseline | not set | the owner's document |

Recommendation on the first: **A**. The evidence exists, it keeps the
ceiling in force instead of replacing it, and it is the smaller change in
policy. It costs one more build and one approved ledger write.

## 4. The concrete proposal

| | |
|---|---|
| starting equity | **$321.4782** as read 2026-11-20 00:42Z; to be re-read when the baseline is written. 0 open orders. Ledger positions: two stale unmatched legs from October, valued at 0 |
| maximum additional loss | **$1.00**, proposed. Set as both the baseline's `max_drawdown_usd` and the drawdown limit (anchor moved to the starting equity, allowance $1.00). The dollar loss allowed from here falls from "none" to $1.00; the old anchor's $15.20 stays on the record |
| why $1.00 | the window's own ceiling is $0.20; the campaign's approved exposure ($0.60) must fit inside the remaining room or the lane refuses to start |
| window | `cpc-btc-updown-5m-2026-11-20-1500z`, 15:00-15:05Z, or any later single window |
| BTC5m build | the commit `release/l5-btc5m-equity-gate-20261120` points to (section 5); supersedes `35224ec` |
| service build | `30c639da67e0` (section 5) |
| orders | maker only, UP and DOWN together, requote at 2 ticks, 240s order age, clip $0.05, per side $0.30, unmatched $0.10, two live orders, window loss ceiling $0.20, 60 submissions |
| watchdog | independent, 300s order age, exact-id cancel, healthy at 00:11Z with 0 open orders |
| unchanged | $50 daily halt; $75 cumulative ceiling (subject to section 3) |

**Order of approvals:** release branches for both builds; deploy
`l5-pmus-live` (disarmed); deploy `l5-btc5m-live` (disarmed); the cumulative
gate decision; the baseline document and drawdown setting; the BTC5m
configuration; arming for the one window. After the window: actual fills and
settled net P&L from venue cashflows.

## 5. Release preparation, 2026-11-20 (owner-approved: preparation and testing only)

Nothing deployed. No ledger write, limit change or arming.

| service | branch | commit | base (deployed now) | code files changed |
|---|---|---|---|---|
| `l5-pmus-live` | `release/l5-pmus-live-equity-baseline-20261120` | `30c639da67e036b5712382f3175fbb75381fff0e` | `3390246e0` | new `l5_equity_baseline.py`, `l5_equity_feed.py`; `l5_pmus_live_runner.py` (+122); Dockerfile (+2); three test files |
| `l5-btc5m-live` | `release/l5-btc5m-equity-gate-20261120` | the commit that branch points to (the one containing this section) | `35224ec` | `l5_btc5m_runner.py` (+36); its test file |

**A mistake in the first push of the service release, caught by the
regression run on the pushed commit.** `ccc479e50` carried the earlier
transfer check: a stray file revert during my mutation testing had dropped
the rewrite, and I had not read the local result that showed it. Three tests
failed on the pushed commit. `56dfa0b71` restores the rewrite; `30c639da6`
adds one assertion. The branch was not force-pushed; all three commits are
on it. Regression on a fresh checkout of `56dfa0b71`: 12 failed / 2446
passed, the same twelve as the base. `30c639da6` changes one test line; the
186 adjacent tests pass on it.

**Transfer completeness (the owner's condition).** The date-margin stop is
gone. A read counts only when the venue ends the feed with an explicit
`eof`; a page with no cursor and no `eof`, or the page limit, is incomplete
and blocks. The cost: a full read is a few hundred pages, so it repeats
every 15 minutes and goes stale after 35. Between reads a new deposit is
unseen and looks like a gain, so a gain now never adds room beyond the
allowance; an unseen deposit can mask losses only up to its own size until
the next read. A new withdrawal looks like a loss, the safe direction.

**The correction, checked against the evidence, not built.** For the 40
readable rows inside the gate's figure:

| check | result |
|---|---|
| ledger quantity is contained in the venue's fills, both sides | 40 of 40 |
| venue shows less than the ledger on any side | 0 |
| every side the ledger holds is one it never had a cost for | 40 of 40 |
| venue quantity equal on both sides (a full pair) | 39; one unbalanced |
| fees on the venue's fills | $0.00 on every leg, none unreadable |
| settlement value readable (0 or 1) | 40 of 40 |
| concentration | largest single row +$5.60; top 8 are +$40.40 of the +$97.32 |

The existing correction refuses these because it requires the ledger's
quantity to equal the venue's (gross, or net). Here the venue holds more:
the ledger recorded one leg and never the other. A rule for this case would
take quantity and cost from the venue's fills, value redemption on those
fills (a pair pays $1 either way), consume each trade id once so no fill is
used twice, and be limited to an owner-approved list naming each market
with the exact venue quantity and cost in the evidence file. The two
unreadable rows stay at the bound. Building that rule and applying it are
each a separate approval.

**The $1.00 allowance is a new capital-risk decision**, as the owner noted:
it authorizes loss after the earlier $15 limit was reached. It is not an
accounting change and nothing in these releases sets it.

## 6. The narrow ledger correction — built, not deployed, not applied (2026-11-20)

Local commit `cfac6707cb82caaf9b4d0cdbe7aa46f6fb20ac1f`, one commit on the
pinned service release `30c639da6`; kept here as
`patches/pmus-live-superset-correction.on-30c639da6.patch`. No release
branch has been pushed for it.

**The list.** `config/venue_superset_manifest_20261120.json`: the 40
markets, each with nine figures taken from the diagnostic evidence. SHA-256
`2d7a87d9968931e87ff353887f8b8bd3fe2e3b4aaf8ce20107ef7f28aa61c368`. Booked
-107.20, corrected -9.8835, total change +97.3165, which would take the
gate's figure from -136.7845 to **-39.4680**. The two unreadable markets are
named in the file as excluded and stay at the bound.

**How it is switched.**

| `L5_VENUE_SUPERSET_CORRECTION` | effect |
|---|---|
| unset | nothing is read or written |
| `INSPECT` | reads the feed, runs the real correction on a copy of the ledger that cannot be saved, reports what would change. The ledger is untouched |
| `APPLY` | writes, only if `L5_VENUE_SUPERSET_CORRECTION_APPROVED` equals the manifest's SHA-256 above |

**What makes a market refuse** (each tested; the bound then stays):

* any of the nine listed figures differs from what is found now, in any
  digit: a fill added, removed or repriced since the list was written;
* the venue holds less than the ledger on either side;
* the ledger already had a cost for a side it holds;
* any fee on any leg is non-zero or unreadable (fees are not in the booked
  result; all 40 are zero);
* the feed was not read to its end;
* the row settled before the last re-anchor (its loss is no longer in the
  total, so its improvement would be a credit against nothing);
* a trade already used by an earlier correction (no fill counted twice);
* the market is not in the list.

A second pass, or a restart, corrects nothing more. Without a listing the
existing correction behaves exactly as before, and the existing automatic
attestation never supplies one.

**Tests:** 45 new; 13 deliberate breakages each fail at least one.
Regression subset on the service lineage: 12 failed / 2518 passed, the same
twelve as the base. One existing allowlist changed: the image data-file
guard now names the manifest, as it already names the earlier cost-basis
manifests.

**What a corrected figure is and is not.** -39.47 would be the ledger's
total since the re-anchor with those 40 rows costed from venue fills. It is
not a statement that the account recovered anything: cash over the period
is -16.87, about $22 is still unexplained, and the portfolio drawdown limit
is separate and still reached.

**Suggested order when the time comes:** deploy with `INSPECT`, read the
report against the manifest, then a separate approval for `APPLY`.

## 7. Baseline document, to be filled from a fresh venue read

Not set anywhere. `L5_EQUITY_BASELINE` on `l5-pmus-live`, one line of JSON:

```json
{
  "id": "baseline-YYYYMMDD-HHMMz",
  "at_utc": "<time of the venue read>",
  "buying_power_usd": "<venue buying power at that time>",
  "matched_redemption_usd": "0",
  "equity_usd": "<buying power + matched redemption>",
  "positions": {},
  "open_orders": 0,
  "max_drawdown_usd": "<the owner's allowance>",
  "historical": {
    "ledger_realized_usd": "<the ledger total at that time>",
    "prior_anchor_usd": "336.68",
    "prior_drawdown_usd": "<the drawdown at that time>"
  },
  "acknowledgement": "THIS SETS A STARTING POINT FOR MEASUREMENT. HISTORICAL LOSSES ARE PRESERVED IN historical AND ARE NOT ERASED. IT DOES NOT RESET, RAISE OR REPLACE THE CUMULATIVE LOSS CEILING OR ANY OTHER LIMIT."
}
```

`positions` must list any market where the ledger holds both sides, and
`matched_redemption_usd` must equal the pairs in it. The two October legs
the ledger still shows are one-sided and count 0. The document is refused
if read with orders resting or if the equity does not add up.

The existing drawdown limit is two other variables on the same service,
`L5_PORTFOLIO_ANCHOR_USD` and `L5_PORTFOLIO_MAX_DRAWDOWN_USD`. Changing them
is the capital decision and is the owner's alone.

## 8. Combined service release, pinned 2026-11-20 (preparation only)

| | |
|---|---|
| branch | `release/l5-pmus-live-equity-and-correction-20261120` |
| commit | `cfac6707cb82caaf9b4d0cdbe7aa46f6fb20ac1f` (tree `a1ad8ce6d640…`) |
| contains | the accounting release `30c639da6` plus the correction, one commit |
| base | `3390246e0`, running on `l5-pmus-live` now |
| manifest SHA-256 in that commit | `2d7a87d9968931e87ff353887f8b8bd3fe2e3b4aaf8ce20107ef7f28aa61c368` |
| regression, fresh checkout of the pushed commit | 12 failed / 2518 passed, the same twelve as the base; 253 adjacent tests pass |

It supersedes `30c639da6` as the service release. The BTC5m release is
unchanged: `a178c20f4348376cac6ddf3d9b2310eaf1a0f49a`.

Deploy order: `l5-pmus-live` first with `L5_VENUE_SUPERSET_CORRECTION`
unset, then `l5-btc5m-live`, then `INSPECT`. Rollback for the service is
Railway deployment `4efa8e28-8f76-475a-a406-b0fdfcc8574b` (`3390246e0`); for
BTC5m, `c6f44539-c668-4418-80c3-28cf8f2935fc` (`35224ec`).

Not deployed. No ledger write, limit change or arming.

## 9. Deployed disarmed, and INSPECT (owner-approved, 2026-11-20 02:00-02:31Z)

| service | commit | deployment | state |
|---|---|---|---|
| `l5-pmus-live` | `cfac6707cb82` | `e4336027` | disarmed, halted, 0 live orders |
| `l5-btc5m-live` | `a178c20f4348` | `cd13ec23` | `can_submit: false` (`ENVELOPE_EXPIRED`) |

Railway skipped the first BTC5m deployment: that service builds only when
`deploy/l5_btc5m_live/DEPLOY_TRIGGER` changes. The skipped deployment was
rebuilt as itself; same commit. A future BTC5m release should touch that file.

**INSPECT, 02:29:29Z** (`evidence/venue_superset_inspect_20261120.json`):
40 listed, 40 would apply, 0 refused, change +97.3165, total -136.7845 to
-39.4680, `ledger_written: false`, manifest SHA-256 `2d7a87d9…61c368`, feed
read to `eof` in 234 pages. Seven markets move down (-0.0064 to -0.55), one
is unchanged, 32 move up.

**Ledger after the run (heartbeat 02:30:46Z):** -136.7845, legacy -215.4681,
116 corrections (+315.16535), 97 rows at the bound, 468 rows, row sum
-215.4881. Every figure identical to before the deployments. Watchdog
02:30:52Z: not halted, 0 open orders, three registries OK.

Not exercised: BTC5m's equity gate. The disarmed loop stops at the expired
envelope before reading the account record; it first runs at arming.

Not applied. No limit, baseline or arming change.

## 10. Correction applied (owner-approved, 2026-11-20 03:05Z)

`l5-pmus-live` on `cfac6707cb82`, deployment `734c134d`. APPLY with the
approved manifest hash `2d7a87d9…61c368`: 40 listed, 40 applied, 0 refused,
feed read to `eof` (`evidence/venue_superset_apply_20261120.json`).

| | before | after |
|---|---|---|
| cumulative total the gate reads | -136.7845 | **-39.4680** |
| corrections applied | 116 | 156 |
| rows at the assumed cost | 97 | 57 |
| sum of rows | -215.4881 | -118.1716 |
| rows | 468 | 468 |

Identical across four heartbeats (03:05-03:08Z). Unchanged: $50 daily halt,
$3.00 exposure, $2.70 cash, drawdown limit $15 from $336.68 (still reached,
15.2018), equity baseline OFF. Disarmed, halted, 0 orders; watchdog clean.

Both correction variables were then set to empty (off), restarting the
service once more. Post-restart check pending at the time of writing.

-39.47 is the ledger with those rows costed from venue fills. It is not
evidence of profitability: cash over the period is -16.87 and about $22 is
unexplained. 57 rows remain at the bound (2 unreadable inside the figure,
55 settled before the re-anchor and outside it).

## 11. New venue-equity baseline set (owner-approved, 2026-11-20 03:41Z)

Preflight 03:23-03:38Z: buying power $321.4782 (venue, 03:38:22Z); venue
positions none (two reconciles); ledger holds two one-sided October legs the
venue does not, valued 0; open and pending orders 0; no transfers since the
re-anchor on a feed read to `eof`; both lanes disarmed; watchdog healthy.

Set on `l5-pmus-live` (commit `cfac6707cb82`, deployment `270f602d`), and
nothing else:

| variable | value | was |
|---|---|---|
| `L5_EQUITY_BASELINE` | `evidence/equity_baseline_document_20261120.json` | unset |
| `L5_PORTFOLIO_ANCHOR_USD` | 321.4782 | 336.68 |
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | 1.00 | 15 |

Baseline `baseline-20261120T0338Z-prior-limit-15usd`, at 2026-11-20
03:38:22Z, equity $321.4782, allowance $1.00. It carries prior anchor
$336.68, prior drawdown $15.2018 and ledger total -$39.4680. The record has
no field for the prior $15 limit; it is in the id and recorded here.

Published 03:56-03:58Z (`evidence/equity_baseline_published_20261120.json`):
baseline ON, equity now $321.4782, change $0, room $1.00, no refusal;
transfers none since baseline, read `END_OF_FEED`, 234 pages, first complete
03:54Z, about 12 minutes after the container started; drawdown limit ON,
anchor $321.4782, limit $1.00, room $1.00. Unchanged: $50 daily halt, $3.00
exposure, $2.70 cash, ledger -39.4680, 156 corrections, 57 rows at the bound.
Disarmed, halted, 0 orders, no alarms. Watchdog 03:59Z clean. BTC5m on
`a178c20f4348`, `can_submit: false`.

BTC5m gate (`evidence/btc5m_gate_check.py`, the deployed file, run on the
published record): allowed as published with room $1.00; blocked when the
record is 31s old, either block is missing, the balance is stale, transfers
are not established, either limit is breached, room is zero or unreadable,
the baseline is malformed, or the daily or cumulative limit is reached. Run
here, not in the service: the disarmed lane stops at the expired envelope
before reading the record, so its first in-service read is at arming.

Open: the count of pre-baseline non-trading rows differs by reader and run
(13, then 5 from the report-only diagnostic; 17 from the gating read). Not
explained. All agree nothing follows the re-anchor or the baseline.

Operational notes for a window: after any `l5-pmus-live` restart the
baseline refuses for about 12 minutes until the transfer read completes; the
read repeats every 15 minutes and expires after 35. `l5-pmus-live`'s own
drawdown gate no longer refuses; it remains disarmed, halted and shadow-locked.
