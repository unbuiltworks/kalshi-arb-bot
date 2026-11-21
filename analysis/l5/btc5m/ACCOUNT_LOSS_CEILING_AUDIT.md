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

**Explained, 2026-11-19 (code and commit history, deployed lineage `c9f9f87`).**
The difference is exactly **$78.6836**, and it is the owner-approved
re-anchor of 2026-11-15:

* Commit `e8ea99bb2` added the rebase `upper-basis-contamination-20261115`:
  the total then read −78.6836 and was replaced, at the owner's instruction
  ("--from-now"), so that measurement restarted from the balance of $338.3456.
  A second entry, `residual-zeroing-20261116`, set both lines to zero at
  2026-11-16 00:11Z. The heartbeat's `cash_anchor` is that entry's.
* A rebase replaces `realized_attributed` / `realized_unattributed`, which
  the gate reads. It deliberately keeps the old scalar `realized_today`, and
  it does not touch the per-market rows. Every later settlement and
  correction moves all three together.
* So: old scalar −215.4681, minus the superseded −78.6836, equals the gate's
  −136.7845 to the last digit. The row sum (−215.49) is the old scalar less a
  $0.02 settlement adjustment that is added to the totals but not to its row.

No arithmetic is wrong. The two totals count different periods.

**What that changes in this audit:**

1. **−$136.78 is what the ledger has posted since 2026-11-16 00:11Z**, not a
   lifetime figure. Over the same period the venue balance went from $338.35
   to $321.48: **−$16.87 in cash against −$136.78 in the ledger**, a gap of
   about $120, assuming no deposit or withdrawal in those four days (not
   verified). That gap is the $1.00 bound on positions settled since the
   re-anchor.
2. **The 62 rows before 2026-11-13 are not in the gate's figure at all.**
   They were wiped by the re-anchor. Correcting them now would add a credit
   to a total that no longer holds their loss. Step 3 below ("move the floor
   back") is withdrawn.
3. **Possible credit already in the figure, size unknown.** The attestation
   corrects rows from 2026-11-13 on and does not know about the re-anchor.
   Any row that settled between 2026-11-13 and the re-anchor and was
   corrected afterwards added its improvement to a total its loss had already
   left. That would make −136.78 too favourable by that amount. The state
   needed to size it is on the service's volume; the diagnostic reports it.

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

## Preparation for correcting the remaining rows (owner-approved to PREPARE, 2026-11-19)

Nothing below has been applied. The ledger is unchanged.

The 159 rows still at the assumed $1.00 are three different problems:

| rows | why the existing attestation left them | what correcting them needs |
|---|---|---|
| **62** | settled before the attestation's start day (`retro_floor_utc_day: 2026-11-13`), so never attempted. Mostly 2026-10-15 and 10-16 | moving that floor back. A configuration decision |
| **94** | `VENUE_QTY_NE_LEDGER_QTY`: the venue's fills do not add up to the quantity this ledger holds for the position, and the rule corrects only on an exact match (`src/l5_live_execution.py`, about line 3465) | first, the two quantities side by side. They are computed and then discarded: the heartbeat carries only the count |
| **3** | `COST_CROSSCHECK_FAILED` | the same per-row figures |

By market family, the uncorrected keys in the heartbeat are spread across the
account: 25 BTC 15m, 25 BTC 5m, 22 college football, 17 CS2, 12 ATP, 31 other
crypto 15m, and smaller groups.

**Proposed order, each step its own approval:**

1. *Read-only diagnostic.* Have `l5-pmus-live` log, for each of the 97 rows it
   already walks, the venue quantity, ledger quantity, venue-derived cost and
   fees. It changes no figure and no gate. It is a code change and a deploy of
   a money-capable service, so it needs approval.
2. *Decide the mismatch rule on that evidence.* If the venue's fills are
   simply the true position and the ledger's quantity is the stale one, the
   correction is to take both quantity and cost from the venue where the
   settlement cashflow also reconciles. If the mismatch is something else,
   the bound should stay. This cannot be decided before step 1.
3. ~~Move the floor back to cover the 62 older rows.~~ Withdrawn: those rows
   predate the re-anchor and are not in the gate's figure (Finding 3).
4. ~~Explain the $78.70.~~ Done, Finding 3.

Until then the comparison with $75 remains a comparison with a bound, and
BTC5m remains blocked at arming.

## The read-only diagnostic — built and tested, NOT deployed (2026-11-19)

Owner approved building and testing only. Nothing is deployed, no ledger
figure is changed, no limit is touched.

**Where the code is.** `l5-pmus-live` runs a different line of history from
this branch, so the change is kept here as a patch that applies cleanly to
the deployed commit:
`analysis/l5/btc5m/patches/pmus-live-venue-cost-diagnostic.on-c9f9f879f.patch`.
Applied to `c9f9f879f1f752d2f4ca2b50cf158b7942631b00` it gives tree
`ecf74aaf8dcdfbefa125e56a5791da916cac07bb` (verified by applying it to a clean
checkout). No release branch has been pushed; that needs permission.

**What it logs** (`L5_VENUE_COST_DIAGNOSTIC`), for each row the attestation
gave up on:

* ledger: UP and DOWN quantity, cost, booked P&L, settlement value, time,
  origin, and whether it settled before the last re-anchor;
* venue: UP and DOWN quantity, fill-derived cost, fees, fill count, average
  price, or why the fills could not be read;
* the difference per side, and the form: `GROSS_MATCH`, `VENUE_NET_MATCH`,
  `QUANTITY_MISMATCH`, `VENUE_FILLS_UNREADABLE`;
* the attestation's own last refusal reason;
* a hypothetical P&L if the venue's fills were the position, marked
  `applied: false`;
* once per batch, the count and total of corrections posted after the
  re-anchor for rows that settled before it (item 3 of Finding 3).

**Why it cannot change anything.** It calls no method of the ledger, never
saves it, keeps its progress in memory, makes only the activity feed's GET,
and nothing reads its output. Off unless the variable is exactly
`READ_ONLY`; the hook swallows every exception.

**Tests** (`tests/test_l5_venue_cost_diagnostic.py`, 31): ledger identical in
memory and on disk after a full run; runs to completion against a ledger
that raises on any write and a transport that raises on anything but GET;
attempt counters, statuses and corrections unmoved; a row that *would*
reconcile is still not corrected; the attestation behaves identically with
the diagnostic on or off; gate off by default and for every near-miss value;
source checks that the module names no mutating call. Eleven deliberate
breakages each fail at least one test. Service regression subset on the
deployed lineage: 12 failed / 2315 passed before, 12 failed / 2346 passed
after, the same twelve (pre-existing).

**Not shown:** anything against the real venue feed or the real ledger.

### Deployment proposal — for separate approval

| | |
|---|---|
| service | `l5-pmus-live` only |
| now | deployment `46c928f1-b3bd-428b-893c-0c46d7755d21`, commit `c9f9f87`, branch `release/l5-pmus-live-registry-claim-20261119`, `armed: false`, `halt: true` |
| to deploy | `c9f9f87` plus the one patch above. The commit SHA is fixed when the release branch is created; the tree must be `ecf74aaf…` |
| needs | permission to push one release branch; permission to deploy it; permission to set one variable |
| variable | `L5_VENUE_COST_DIAGNOSTIC=READ_ONLY`. Not money-capable. Setting it restarts the service |
| arming | unchanged: stays disarmed and halted. No other variable changes |
| venue load | the activity feed only, paced by the attestation's own constants (one step per interval, a few pages a step), then idle |
| done when | the log reads `NOTHING_LEFT`; about 97 rows in batches of 8 |
| rollback | Railway rollback to deployment `46c928f1…`; or unset the variable, which disables it on the new build |
| restart risk | startup reconciliation runs as on any restart. Venue shows 0 open orders and the lane is disarmed |

One thing a deploy does besides the diagnostic: the attestation itself runs
again from boot as it does on every restart. Rows it has already exhausted
stay exhausted (that record is durable), so no new correction is expected
from the restart alone.

## Update 2026-11-19 23:55Z — release candidates, restart check, and what blocks a BTC5m window

**Two candidate commits for the diagnostic, both local, neither pushed as a
release branch.** Both are in the one patch file (two commits on `c9f9f87`).

| commit | tree | contents |
|---|---|---|
| `0ecf46d0a2cc7ea21a956a35d5ef7219614112c2` | `ecf74aaf8dcdfbefa125e56a5791da916cac07bb` | the diagnostic as first reviewed |
| `3390246e03203e7b13ec33ba587fdee399ea2ba1` | `b770ed2bac551929dd0e0181cbc9763a10cf1568` | plus: deposits/transfers seen by the same walk, listed since the re-anchor; a restart test |

The second is the one worth deploying: without the deposit and transfer rows
the ledger total cannot be checked against the venue balance. Its tree is
**not** the one named in the review, so it needs its own look.

**Restart check.** A new test starts a fresh process on the saved ledger with
a feed that now reconciles exactly: the attestation makes no request for
exhausted rows and corrects nothing; the gate's total is unchanged. The
heartbeat of 23:42Z shows `pending: 0`, so on restart the attestation has
nothing to do. The boot-time repairs and re-anchors are idempotent by id and
have already run on this build; the patch adds none.

**Load.** At most 2 feed pages per 5 seconds, through the service's own
rate-limited read, in the same loop as everything else. The service has 0
live orders and is disarmed; the watchdog is a separate process.

**Tests on `3390246e`:** 36 diagnostic + 41 attestation pass; applied to a
clean checkout of `c9f9f87` the same 77 pass; regression subset 12 failed /
2350 passed, the same twelve as the base. Sixteen deliberate breakages, each
fails at least one test except one that is equivalent code.

### State read 23:40-23:43Z

| | |
|---|---|
| `l5-btc5m-live` | `35224ec`, deployment `c6f44539`, `can_submit: false` (`ENVELOPE_EXPIRED`) |
| `l5-pmus-live` | `c9f9f87`, `armed: false`, `halt: true`, shadow lock on, 0 live orders |
| watchdog | `faebeca`, sweeping every 20s, not halted, cancel enabled, 0 open orders, 3 registries OK, managed lane OK |
| sports lane | combo maker `shadow`, 0 quotes; two-sided maker refusing on `submission_lock` (22:59Z, 23:35Z) |
| daily loss | -0.60 of 50.00 |
| cumulative loss | -136.7845 against 75: **blocks BTC5m at arming** |
| portfolio drawdown | 15.2018 against 15 (cash 321.4782, anchor 336.68): tripped. Read only by `l5-pmus-live`; `l5-btc5m-live` reads the daily and cumulative figures and not this one |

### BTC5m window packet (nothing applied)

Window `cpc-btc-updown-5m-2026-11-20-1500z`, 15:00:00Z-15:05:00Z,
`L5_BTC5M_NOT_AFTER=2026-11-20T15:05:00Z`. Any later single window can be
substituted. Build: `35224ec55fcda5a8bf568bb3616b3e1f64f398e4`, already
deployed; the release branch has no later commit, so a variable change
redeploys the same SHA.

Configuration diff: as in `BEHAVIOUR_PROBE_READINESS.md` section 2, with the
market and not-after above, plus:

* `L5_LIVE_AUTH_MAX_SUBMISSIONS=60`. Its current value is not logged anywhere
  and was not read (reading variables exposes the account's API secrets).
  Setting it makes the unknown irrelevant. A simulated window used at most 52.
* Class budgets are 90 / 90 / 90 (boot log, `open_budget: 90`). A new campaign
  approval carries a new nonce and so starts every counter at zero
  (`l5_live_authorization.experiment_id`): nothing to reset.

Unchanged: clip 0.05, per-side 0.30, unmatched 0.10, two live orders.
