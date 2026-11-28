# The repaired dataset, and what it changes

Reproduce with `python3 -I analysis/l5/issue206/l5_repair_dataset.py`.

## 1. The collector defect and the repair

`data/l5/raw/activity.jsonl` was paged with `limit`/`offset` against a feed
ordered by timestamp. Offset paging over a server-ordered collection drops
and repeats rows whenever the set shifts between requests, and the busiest
windows shift fastest. The damage is provable without reference to the file:
**a redemption pays $1 per winning contract and only one outcome pays, so
the payout cannot exceed the larger side's holding** — and it did in 93 of
315 redeem-mismatch windows.

**The collector cannot be re-run.** `data-api.polymarket.com/activity` and
`/trades` both return **HTTP 410 Gone** from this environment. No collector
can fetch what the host no longer serves.

So the repair is a **verified union of every capture already in the
repository**, none of which is modified:

| source | rows | new | note |
|---|---|---|---|
| `activity.jsonl` | 77,990 | 72,446 | the offset-paged file the analysis used |
| **`activity_gap.jsonl.gz`** | **706,306** | **691,077** | collected by **time window**, not offset |
| `sep2026/…sep2026_raw.json.gz` | 144,368 | 141,488 | |
| `sep2026/…sep2223_raw.json.gz` | 16,978 | 16,699 | |
| | | **921,710** | deduped TRADE+REDEEM; **902,016 trades** |

**12.8× more trades than the file I had been analysing.** The gap capture
used `start`/`end` rather than `offset`, which is precisely why it did not
suffer the same loss — and that is the empirical case for the collector in
`l5_activity_collector.py`, which pages by time window, **splits any page
that returns exactly `limit` rows** rather than accepting it, checkpoints
after every verified page, retries with bounded backoff, dedupes across
runs, names every unverified slice as `SATURATED` or `FAILED`, and refuses
to overwrite an existing capture.

### Window classes, btc-updown-15m

| class | windows | meaning |
|---|---|---|
| **COMPLETE** | **4,720** | redemption identity holds; settleable |
| INCOMPLETE | 1,789 | payout exceeds the larger side: rows provably missing |
| INDETERMINATE | 46 | no redemption, one-sided, or no trades |

Against 408 capture-consistent windows before the repair.

### Independent validation

- **`/trades` endpoint capture** (a different endpoint): 10,183 deduped
  rows, **100% present in the union, none missing.**
- **`/positions` capture**: 17 btc15m positions, total `cashPnl`
  **−$493.57**. A snapshot of *open* positions, so it validates nothing
  historical — reported because it is negative and that is worth knowing.
- **Maker/taker composition is INDETERMINATE.** `trades_takeronly.jsonl`
  holds 11,810 rows against `trades.jsonl`'s 10,183 — 116%, so the
  taker-only set is not a subset and the two captures cover different
  periods. **No maker share is claimed.** The pair basis is the only
  evidence on this: a median of 0.9650 is below 1.00, which a taker lifting
  both sides cannot achieve.

## 2. Corrected L5 economics — COMPLETE windows only

| | value |
|---|---|
| windows | 4,720 |
| trades | 580,602 |
| turnover | $5,308,467 |
| **net** | **+$148,870.37** |
| **net per window** | **+$31.54** |
| **capital efficiency** | **+2.80% of turnover** |
| matched | 5,089,010 ct, basis 0.9650, **+$0.0350/ct** |
| residual | 1,240,600 ct, basis 0.3205, **−$0.0236/ct** |

**The residual result reverses.** On 1.24M residual contracts a residual
**loses 2.4¢**. The +$0.026…+$0.037 I reported came from 186 windows of the
broken file, and those were the *quiet* ones — it was selection, exactly the
limitation I had flagged but not expected to flip the sign.

**The matched edge does not move:** +$0.0356 (501 windows), +$0.0359 (186
determinable), **+$0.0350 (4,720 COMPLETE)**. That stability across every
cut ever measured is why the repair is believed rather than the convenient
half of it.

**Excluded sample:** the 1,789 INCOMPLETE windows show a residual at
**+$1.1641/contract** — impossible for a binary contract, which is the
contamination made visible. Their net is inflated by the missing cost and
**no unexplained redemption proceeds are treated as profit**.

## 3. Our cash commitment — traced, and my own error corrected

**I reported `CASH_COMMITMENT` rising from $1.3766 to $2.1897 and called it
cumulative non-recycling. The $2.1897 was my own test double-filling orders
the fake transport had already filled.** `committed` is incremented in
`apply_fill` only — never on submit — so there is **no double counting.**

The true trace at a $1.00 cash cap:

```
tick 0   DOWN 1 @ 0.37 fills   committed 0.37
tick 1   UP   1 @ 0.58 fills   committed 0.95   <- a COMPLETED PAIR
tick 2   refused  CASH_COMMITMENT:1.3766>1.00   (0.95 held + 0.4266 new)
```

**It is reserved capital, correctly accounted, under the configured
`NO_RECYCLING` policy** — the $0.95 has genuinely left the account and does
not return until settlement. That is right for a **cash** bound.

**But the authorization was "$1 maximum aggregate worst-case exposure" — a
risk bound.** A matched pair redeems exactly $1.00 either way, so its worst
case is a **gain** of `1 − basis`; it cannot lose. Measured against the real
executor:

| configuration | orders | pairs | binding refusal |
|---|---|---|---|
| $1 cash + $1 risk | 2 | 1 | `CASH_COMMITMENT:1.3766>1.00` |
| **$20 cash + $1 risk** | **8** | **4** | **none — the risk gate never binds** |
| $20 + $20 | 6 | 2 | `MAX_LIVE_ORDERS` |

**Four matched pairs are already reachable inside a $1 worst-case-exposure
authorization**, because four matched pairs carry ~zero worst case. The
system already has the correct gate for this — `max_worst_case_deficit_usd`
— and it prices them at zero.

**No reservation is released and no limit is changed.** Capital recycling is
*not* implemented: the cash gate is correct as a cash gate, and whether the
$1 is expressed as cash or as risk is a capital-limit decision that is the
owner's. What is implemented is `l5_capital_block.matched_cash_split`, which
reports the two numbers side by side — committed cash, and how much of it is
actually at risk — so the choice is made against numbers. On 1230z it
reproduces the settlement exactly: committed $1.4100 = **$0.9550 risk-free
matched + $0.4550 at risk**.

## 4. The reopening prior, corrected again

`REOPEN_SINGLE_OUTCOME_USD`: **−0.2440 → 0 → −0.0236**. It has been wrong
twice, both times from incomplete data rather than bad algebra: first a
PROVEN sampling artefact, then a 186-window sample of a broken file. It is
now the measured value over 1.24M contracts.

**Throughput survives it.** At the deployed `d=2`:
`E = 0.727 × 0.0500 + 0.273 × (−0.0236) = +0.0299`, above the floor. The
gate still prices repeated rounds rather than forbidding them, and the worst
case is untouched — capacity still reserves the whole leg.

## 5. Three kinds of number

- **L5 historical realised:** +$148,870 over 4,720 COMPLETE windows,
  +$31.54/window at 2.80% of turnover. *Someone else's money, public data.*
- **Our simulated:** the A/B/C replay. *Not money.*
- **Our confirmed settled realised net: −$15.6109 all-time.** Unmoved.

## 6. The next economically justified action

At 2.80% of turnover, **$480/day needs ~$17,900/day of turnover — about
$178 per window.** That is the whole gap, and it is capital, not logic.

The smallest step that tests the logic rather than the capital: **one
BTC15m window at $1 worst-case exposure with the envelope expressed as risk
rather than cash**, which the measurement above says permits ~4 matched
pairs at ~zero added worst case. It would be the first test of repeated
completion in live conditions, and it needs an owner decision on how the $1
is expressed — **not** an increase in it.

## 7. How often is a residual negative, and do the wins cover the losses?

Measured on the 4,719 redemption-consistent windows that carried a residual,
1,240,600 contracts.

**The structure is exactly as expected.** A residual bought at basis `b`
pays `1−b` when it wins and loses `b` when it does not, so **one win covers
`(1−b)/b` losses**. At L5's measured basis of **0.3205** that is **2.12
losses per win** — so the whole question reduces to one comparison:

| | |
|---|---|
| break-even win rate (= the basis) | **0.3205** |
| **realised payout per contract** | **0.2995** |
| shortfall | **−2.1 points** |

**It misses by about two points.** Not a rout, not free money.

| per window | windows | share | total | mean |
|---|---|---|---|---|
| residual **negative** | 2,406 | **51.0%** | −$152,438 | −$63.36 |
| residual **positive** | 2,313 | **49.0%** | +$126,411 | +$54.65 |
| | | | **net −$26,027** | |

**The wins recover 82.9% of what the losses cost.** So a residual is close
to a fairly-priced coin flip with a ~2-point drag — which is why the
per-contract figure is −$0.021 rather than the −$0.244 my earlier model
claimed.

**Residuals are a cost of doing business, not a profit centre.** L5 still
nets +$31.54/window because matched pairs earn +$178,176 against the
residual's −$26,027 — the pairs pay for the drag nearly seven times over.

### Why this does not transfer to our Stage 1 size

The per-window swings are large relative to the mean:

| p01 | p10 | p50 | p90 | p99 | worst | best |
|---|---|---|---|---|---|---|
| −$244 | −$104 | −$0.80 | +$81 | +$241 | −$522 | +$773 |

That is on ~$1,065 of turnover per window. Scaled to a $10 envelope the
same distribution is roughly **±$2.30** at the 1st and 99th percentiles —
against a **$1.00** loss-at-risk budget.

**So the budget that protects us also removes the dynamic.** "One win makes
up for two losses" needs enough residual inventory for a win to be
material, and at $10 with a $1.00 deficit bound the bound cuts off the left
tail — and necessarily the right tail with it. The asymmetry only starts
paying for itself at a size where a single window's residual can swing tens
of dollars, which is Stage 3–4 territory, not Stage 1.

**The Stage 1 blocker is therefore narrowed, not removed.** The residual's
sign is now measured: **negative, by about 2 points of win rate, on 1.24M
contracts.** What remains unknown is whether OUR residuals behave like
L5's — ours are 5 settled legs, 4 of which lost — and that is still only
answerable from our own fills.
