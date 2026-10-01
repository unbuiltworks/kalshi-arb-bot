# Accounting trust report

Built from raw venue records with `src/l5_cash_ledger.py` (Decimal only,
constructed from `str()` of the source value). 21 mutation tests in
`tests/test_l5_cash_ledger.py`.

## The reconciliation closes exactly

September capture, 161,376 events, 0 duplicates, 0 rejected rows:

| level | value |
|---|---:|
| market rollup (trading only) | −5,508.952001 |
| + incentives (account-level) | +23,047.404400 |
| = market + incentive | **+17,538.452399** |
| day rollup (all kinds) | **+17,538.452399** |
| account cash | **+17,538.452399** |
| residual market → account | **0.000000** (0 bps) |
| residual day → account | **0.000000** (0 bps) |

There is **no** unexplained residual at any level, so none is labelled
"rounding". Every nonzero difference below is a **population or field
difference**, named as such.

## Four findings that change numbers

**1. `usdcSize` is the cash; `price × size` is not.** They disagree on 11,076 of
71,984 March–August trades, by up to $1.43 on one row and **$3,789.35 = 52.0 bps**
in aggregate; on the September capture the drift is $6,224.36 = **34.0 bps**. For
scale, 52 bps of the September turnover is ~75% of the magnitude of the entire
reported trading net. A `price × size` ledger is wrong by that much. The ledger
uses `usdcSize` and keeps the product only as a reported cross-check.

**2. The feed paginates with overlap.** 3,980 of 78,000 payload rows (**5.1%**) in
`activity.jsonl` are duplicates. An append-only reader overstates cashflow by that
much. Dedupe is by content, not cursor. (The September gz files contained 0
duplicates.)

**3. Day and regime buckets of TRADING cash are not boundary-free.** 30 markets'
redemptions fall on a different UTC day from every buy in the same market,
carrying **$23,783.33 = 1.30%** of redemption cash. A day or regime split assigns
that spend and redemption to different buckets. **Only account-level and
market-level trading totals are boundary-free.** Incentive rows are same-day
credits and are unaffected — which is why the Sep 07–10 maker figure
(**+$4,851.72**) reconciles exactly while that regime's trading figure does not
match a market-resolution-based number.

**4. All source numerics arrive as JSON floats**, not strings, so the venue's own
decimal value is lost at the boundary before we see it. `Decimal(str(x))` recovers
the shortest round-tripping decimal — the best available, and **not** the same as
source fidelity. This is a ceiling on every figure here, not a defect in the
ledger.

## THE MATERIAL GAP: resolution is unverifiable for 99.7% of the population

The published September figures rest on classifying markets as resolved. From the
repository's raw records:

* **16 of 4,785** September conditionIds (**0.3%**) have a resolution record in
  `markets.jsonl`.
* **837 markets were bought and never redeemed**, carrying **$15,335.60** of
  spend — **711 of them ($12,511.77) in the Sep 01–10 block**, against the
  published count of **708**.

A bought-but-never-redeemed market is either a **resolved total loss** (−spend) or
**unresolved open inventory** (excluded from realized P&L). **In the cash feed the
two are identical** — neither has a redemption row — and they have opposite
accounting treatment. A mutation test now pins exactly this
(`test_unresolved_and_resolved_loser_are_NOT_the_same_cash_treatment`).

**This is the whole distance between the two published headlines.** +$7,125.74 and
−$5,047.88 differ by ~$12,174; the zero-redemption spend in that block is
$12,511.77. The two figures are the two treatments of one population. **The
correction was right** — 5- and 15-minute markets harvested days later have
certainly resolved — but its correctness rests on an assumption that is
independently verifiable here for 0.3% of markets.

## Ledger reconciliation vs the published September figures

| | ledger (all capture) | published (resolved ≥10 min pre-harvest) | delta |
|---|---:|---:|---:|
| spend | 1,831,577.38 | 1,563,497.12 | **+268,080.26** |
| redeem | 1,826,068.43 | 1,558,449.24 | +267,619.19 |
| trading net | −5,508.95 | −5,047.88 | −461.07 |

The $268k is a **population difference** — the published set excludes markets not
resolved at harvest — not a discrepancy. It is **not reproducible** from this repo
because the resolution timestamps are not here.

## Classification

| number | status |
|---|---|
| Account cash, September capture, **+$17,538.4524** | **CASH-RECONCILED** — 0.000000 residual at market, day and account level |
| Spend −$1,831,577.38 / redeem +$1,826,068.43 | **CASH-RECONCILED** |
| Trading net **−$5,508.95** (this capture) | **CASH-RECONCILED** as cashflow; **UNRESOLVED** as *realized* P&L, pending resolution status for the 837 |
| MAKER_REBATE +$13,565.72, REWARD +$8,229.66, TAKER_REBATE +$1,252.02 | **CASH-RECONCILED** at account level; **never allocated to markets** — every row has an empty `conditionId`, asserted in code |
| Sep 07–10 maker **+$4,851.72**, taker **+$268.03** | **CASH-RECONCILED** |
| REWARD ends 2026-09-06 | **SOURCE-ATTRIBUTED** |
| Published trading net **−$5,047.88** | **SOURCE-ATTRIBUTED**, not reproducible here — the resolved-market population cannot be rebuilt from these files |
| 708 zero-redemption losers | **SOURCE-ATTRIBUTED**; ledger finds 711 in-block, difference unexplained but ≤3 markets |
| Regime trading splits (A/B/C) | **MODELED** — event-level date bucketing, exposed to the 1.30% cross-day effect |
| Per-market incentive economics | **UNRESOLVED** by construction — the venue publishes none |
| Resolution status of 837 markets, $15,335.60 | **UNRESOLVED** — 0.3% coverage |
| Our own live PMUS scoreboard ($56.06 turnover, $0.42 credited) | **CASH-RECONCILED** (venue-credited), separately audited |
| Replay/shadow P&L | **MODELED** — no cash moved |

## What would close the gap

Resolution records (or `outcomePrices`) for the September conditionIds. That single
join converts the trading net from CASH-RECONCILED-as-cashflow to
CASH-RECONCILED-as-realized, and is the only thing standing between the two.
Nothing else in this ledger is unexplained.

---

> **SUPERSEDED IN PART by `analysis/l5/economics/CANONICAL_L5_BENCHMARK.md` (frozen at 1f43db3b).**
> Authoritative trading P&L for the captured population is **−$6,338.70** at 100%
> settlement coverage. Figures in this document that predate that join remain as
> the record of how it was reached; they are not the benchmark.
