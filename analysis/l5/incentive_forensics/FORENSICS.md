# P0 — incentive forensics. Verdict: **UNATTRIBUTABLE**

## 1–2. Conditions traded, Aug 29/30/31

| day | BTC15m | BTC5m | ETH5m | ETH15m | total conditions |
|---|---|---|---|---|---|
| 08-29 | 87 | 277 | 203 | — | 567 |
| 08-30 | 89 | 278 | 192 | — | 559 |
| 08-31 | 74 | 214 | 169 | 17 | 474 |

58,260 TRADE rows over the three days.

## 3–5. Condition-level attribution: it does not exist

**The local record carries no incentive field at all.** Every one of the 21
keys on an activity row was enumerated: there is no fee, rebate, reward,
incentive or commission field on any of the 78,000 rows.

**The venue's own public interface confirms it.** Pulled L5's complete public
incentive history — **239 unique rows, 2026-04-21 → 2026-09-15**, far more than
the 10 in our local capture:

| | |
|---|---|
| incentive rows retrieved | **239** |
| rows carrying a non-empty `conditionId` | **0** |

Every REWARD, MAKER_REBATE and TAKER_REBATE row is **account-level**, with an
empty `conditionId`, `asset` and `eventSlug`. The authenticated
`/clob.polymarket.com/rewards/user` endpoint returns 401 without a key and —
with a key — serves only the *authenticated* account's rewards, never another
trader's.

**Verdict: `UNATTRIBUTABLE`.** Not partially. Zero dollars of L5's incentives
can be tied to any condition, market or cohort.

**No dollar is distributed by volume, and none is distributed at all.**

## The incentive regime changed, and the full series shows it

The three days we had locally were the tail of a long escalation:

| period | character | typical day |
|---|---|---|
| 2026-04-21 → 06-19 | maker rebate only | \$5–\$220 |
| 06-20 → 08-07 | taker rebate begins | \$25–\$560 |
| **08-08** | **REWARD stream starts** | \$1,206 |
| 08-18 → 09-15 | mature | **\$2,300–\$4,742** |

## BTC15m attributable economics

| | |
|---|---|
| trading P&L (527 windows) | **−\$150.87** |
| + attributable maker rebates | **\$0.00** |
| + attributable liquidity rewards | **\$0.00** |
| **= total attributable economics** | **−\$150.87** |

By day and by window: same answer. There is nothing to add.

## Requested ratios

Computable only at **account level**, across all cohorts:

| date | acct traded \$ | fills | incentive \$ | **inc/traded\$** | inc/fill | BTC15m share of traded \$ |
|---|---|---|---|---|---|---|
| 04-21 | 1,855 | 241 | 29.94 | 0.01614 | 0.124 | 100.0% |
| 08-28 | 135,679 | 12,335 | 3,777.76 | 0.02784 | 0.306 | 76.7% |
| 08-29 | 209,487 | 20,035 | 3,220.34 | 0.01537 | 0.161 | 77.7% |
| 08-30 | 242,214 | 23,314 | 3,744.81 | 0.01546 | 0.161 | 78.3% |
| 08-31 | 149,634 | 14,911 | 3,343.34 | 0.02234 | 0.224 | 78.9% |

The BTC15m column is its share of **traded dollars** — context only. It is
**not** an allocation key and nothing is split by it.

| period | traded \$ | incentive \$ | inc/traded\$ |
|---|---|---|---|
| **Spring** (BTC15m window, 03-19 → 04-21) | 29,981 | **29.94** | **0.00100** |
| **August** (BTC15m window, 08-28 → 08-31) | 737,014 | **14,086.24** | **0.01911** |

**19× more incentive per traded dollar in August than in spring.**

- `incentive / matched share` — **NOT_IDENTIFIABLE**, needs cohort attribution.
- `incentive / residual dollar risk` — **NOT_IDENTIFIABLE**, same reason.

## Critical hypothesis: is August rational once incentives are included?

**For BTC15m specifically: `NOT_IDENTIFIABLE`.** Attributable incentives are
\$0.00, so the attributable answer is unchanged — August remains −\$580.40.

What can be stated as a **feasibility bound, not an allocation**: over
08-28 → 08-31 the account received **\$14,086.24** while BTC15m trading lost
**\$580.40**. The loss is **4.1%** of the incentive pool, against BTC15m being
**78%** of traded dollars. So the hypothesis requires only that BTC15m earned
at least 4.1% of the pool.

**That makes the hypothesis comfortably feasible and does not establish it.**
The actual share is unknown and this record cannot produce it.

The correlation is now much stronger than the three-day snapshot suggested:
spring BTC15m trading was **profitable** (+\$429.53, ret/\$ +0.0153) in a
period with **\$29.94** of total incentives, and August trading was
**negative** (−\$580.40) in a period with **\$14,086** — 19× the incentive
intensity per traded dollar. Consistent. Still not causal.

## Do longer runs earn incentive that covers their residual loss?

**`NOT_IDENTIFIABLE`**, and the gap is not marginal.

Incentives resolve to **one account, one day**. A same-side run is a structure
inside a **15-minute window**, lasting seconds. The finest available incentive
granularity is coarser than the unit of analysis by three to four orders of
magnitude, and carries no market label at any level.

No amount of care closes that. It is not estimated here.

## What would resolve it

Nothing retrospectively, for L5. The venue does not publish per-condition
incentives for another trader's account, and the authenticated endpoint is
scoped to the caller.

**Prospectively, for our own account, it is measurable.** The authenticated
`/rewards/user` endpoint returns per-market rewards for the calling account, so
if we run a maker strategy we can attribute our own incentives per condition
and answer the run-length question directly on our own fills. That is the only
route to it, and it requires running the strategy rather than studying L5.
