# L5 POST-AUGUST BEHAVIOR & ECONOMICS REPORT

Generated `2026-10-07T04:41:43.424624+00:00` by `scripts/l5_post_august_report.py`.
Subject: Polymarket trader `l5Zn1bWoM8eTsK`, proxy wallet `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`.

## Profit attribution

**NONE. Another trader's public activity. No figure here is submitted, filled, settled or realized profit for our account.** Labels in force: this document is
**historical replay / observation** only. Nothing here is *shadow*, *live probe*,
*submitted*, *filled*, *settled* or *realized* for us. L5's `REWARD`,
`MAKER_REBATE` and `TAKER_REBATE` rows are **his** credited incentive cashflows;
they are reported separately from trading economics, are never netted into it,
and are **not** evidence that any equivalent program applies to our account.
L5's historical ~$3.2k-$3.7k/day incentive observations are **not transferable**
to us and are not treated as our opportunity anywhere below.

Fee schedule fingerprint asserted: `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`
(taker theta **0.0695** from 2026-09-17T04:00Z, primary captured source; the
stale 6% scalar is not used).

## 1. Where the canonical corpus ends, and what this adds

The canonical corpus (`data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz`)
ends **2026-08-31** -- `analysis/l5/CANON_REGIMES.md` states this in its own coverage
caveat. This report covers only days **after** that date, drawn from four
harvests normalized through the canonical pipeline.

| metric | value |
|---|---|
| post-August rows loaded | **449340** |
| of which TRADE fills | **437827** |
| independent window-events | **13988** |
| days with observed activity | **37** |
| observed span | **2026-09-01 -> 2026-10-07** |
| calendar days in that span | 37 |
| **calendar days NOT observed** | **0** |

### Dedupe

Key used -- the Section-1-validated key, **not** the originally prescribed one:

```
(transactionHash, conditionId, outcomeIndex, timestamp, str(price), str(size), type)
```

`(transactionHash, asset, timestamp)` is **defective** on this feed: the
`/activity` pages carry no `asset` field, so it degenerates to `(txh, None, ts)`
and silently collapses records differing in price/size/side/outcome/type.

| | count |
|---|---|
| duplicates dropped | **10468** |
| **collisions with DIFFERING fields** | **0** |
| records contradicting identity | 0 |
| records with no `name`/`proxyWallet` (kept on provenance) | 0 |
| records with `name`/`proxyWallet` | 459808 |
| TRADE rows with unresolved slug | 0 |

A collision count of **0** means the key lost nothing: every dropped row was
byte-identical in every carried field. Identity is by **provenance** --
the envelope's own `params.user` is L5's wallet -- and only a *contradicting*
`name`/`proxyWallet` disqualifies a record. A name-only filter would discard
the ~90% of pages on which the feed omits the profile block.

### Identity corroborated, not merely asserted

The canonical corpus could corroborate identity on only **~0.5%** of its
nameless transactions (`CANON_CORPUS_VALIDATION.md`), leaving provenance as
the sole evidence for the rest. The post-August window does better.

The 24/7 ops collector (`data/l5/ops/activity_*.jsonl`) writes **bare**
records that carry `proxyWallet` on every row -- an identity signal
*independent* of the fetch envelope. Overlapped against the provenance-
admitted nameless harvest it produced **4366 exact duplicates, 0
collisions, and only 5 genuinely new row(s)**.

So on the overlap an independent `proxyWallet` signal agrees with
provenance **byte-for-byte in every carried field**. That is a real
corroboration of the identity rule on this window, not an assumption --
though it still covers only the overlapping day, and the remaining days
rest on provenance alone.

Rows kept per source:

| source | rows kept |
|---|---|
| `ops:activity_20260901.jsonl` | 5 |
| `post_august:l5_activity_post_august_20261007T043152Z.jsonl.gz` | 270885 |
| `sep2026:l5_activity_sep2026_raw.json.gz` | 144394 |
| `sep2026:l5_activity_sep2223_raw.json.gz` | 16982 |
| `sep2325:l5_sep23_25_daily.json` | 17074 |

No window hit the venue's offset cap: no pagination truncation. Every
window terminated on a short page, so the counts are counts, not floors.

## 2. Behavior and economics, per month and per regime

Canonical regime change points in this repo: **`2026-04-25`, `2026-06-23`, `2026-08-19`**.
`REGIME_2026-08-19_ON` is the **current** regime and is reported on its own.
The pooled cross-regime **+$174.56/day is NOT current expectancy** and is
deliberately not restated as one anywhere in this document.

### 2.1 Volume and sequence composition

| period | days | events | fills | notional USD | OPEN | EXTEND | COMPLEMENT | SWITCH |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `MONTH_2026-09` | 30 | 11347 | 367771 | 4,403,302 | 3.1% | 70.8% | 15.6% | 10.5% |
| `MONTH_2026-10` | 7 | 2641 | 70056 | 632,168 | 3.8% | 72.4% | 14.7% | 9.1% |
| `REGIME_2026-08-19_ON` | 37 | 13988 | 437827 | 5,035,470 | 3.2% | 71.1% | 15.5% | 10.2% |

Per-event action rates (actions per window-event):

| period | EXTEND/event | COMPLEMENT/event | SWITCH/event | completion rate |
|---|---:|---:|---:|---:|
| `MONTH_2026-09` | 22.95 | 5.07 | 3.39 | 50.3% |
| `MONTH_2026-10` | 19.20 | 3.91 | 2.42 | 49.6% |
| `REGIME_2026-08-19_ON` | 22.24 | 4.85 | 3.21 | 50.2% |

`EXTEND` dominates the tape. That is the **persistent incremental inventory**
motif the operating model describes: same-side `EXTEND` is a first-class
action here, not an error state.

### 2.2 Market mix / categories

**`REGIME_2026-08-19_ON`**

| family | events | share |
|---|---:|---:|
| `crypto_updown:BTC/5m` | 6409 | 45.8% |
| `crypto_updown:BTC/15m` | 3267 | 23.4% |
| `crypto_updown:ETH/5m` | 2929 | 20.9% |
| `crypto_updown:ETH/15m` | 1383 | 9.9% |

### 2.3 Pair-cost distribution and the $0.96 question

| period | pairs measured | median pair cost | p10 | p90 | <= $0.96 | <= $1.00 |
|---|---:|---:|---:|---:|---:|---:|
| `MONTH_2026-09` | 5710 | **0.9671** | 0.7953 | 1.2434 | **45.8%** | 66.1% |
| `MONTH_2026-10` | 1310 | **0.9649** | 0.7541 | 1.3112 | **47.8%** | 63.5% |
| `REGIME_2026-08-19_ON` | 7020 | **0.9668** | 0.7876 | 1.2609 | **46.2%** | 65.6% |

In the current regime the **median pair cost is 0.9668, ABOVE the $0.96
spring-discipline target**, and only **46.2%** of measured pairs complete at or
below $0.96. This is the *near-$1 pair tolerance* the operating model assigns
to **incentive-era** L5 behaviour. It stays **locked** for us: per CLAUDE.md,
near-$1 pair tolerance is unlocked only if equivalent economics are
source-confirmed for our account, and they are not.

### 2.4 Matched vs residual, and residual persistence

| period | matched shares | matched share of gross | residual share | median terminal resid frac | median peak resid | events ending with residual |
|---|---:|---:|---:|---:|---:|---:|
| `MONTH_2026-09` | 4,001,140 | 85.4% | 14.6% | 93.7% | 80.0 | 98.9% |
| `MONTH_2026-10` | 561,548 | 84.0% | 16.0% | 100.0% | 60.8 | 98.3% |
| `REGIME_2026-08-19_ON` | 4,562,687 | 85.2% | 14.8% | 95.5% | 80.0 | 98.8% |

Residual is **tolerated, not hedged away**: a large majority of events end
carrying some directional residual. Consistent with the operating model, this
is **not** evidence that the residual has predictive skill -- no such claim is
made here, and none is supported by this tape.

| period | median 1st price | % first-buy favored | median s to first match |
|---|---:|---:|---:|
| `MONTH_2026-09` | 0.580 | 66.0% | 52 |
| `MONTH_2026-10` | 0.600 | 70.2% | 70 |
| `REGIME_2026-08-19_ON` | 0.590 | 66.8% | 55 |

### 2.5 Fee burden under the effective-dated schedule

**These are BOUNDS, not measurements.** `execution.isAggressor` **does not
exist** on L5's public tape; the liquidity-role field present is
`CHAIN_TOPIC_POSITION` (on-chain CTF topic position). No maker/taker role is
claimed, inferred or estimated. The two columns are opposite corners: every
buy as taker, and every buy as credited maker. L5's true cost lies between
them and this tape cannot narrow it.

| period | all-taker fee UPPER BOUND | all-maker credit UPPER BOUND | fills priced | fills in date-conflict window |
|---|---:|---:|---:|---:|
| `MONTH_2026-09` | $95,545.04 | $18,810.99 | 367771 | 139645 |
| `MONTH_2026-10` | $15,239.43 | $2,741.13 | 70056 | 1238 |
| `REGIME_2026-08-19_ON` | $110,784.47 | $21,552.12 | 437827 | 140883 |

Fills in the **2026-09-17 -> 2026-10-01** window are flagged because our primary
captured source and a secondary search summary disagree on the taker-theta
effective date. They are rated at the **higher** coefficient -- the conservative
direction -- and flagged rather than quietly rated.

### 2.6 Economics, decomposed as CLAUDE.md requires

The seven components are kept **separate**. Nothing is summed into a single
"profit" figure, because the components are not all observable and the
unobservable ones must not be filled in with a model.

| component | `REGIME_2026-08-19_ON` | status |
|---|---:|---|
| 1. matched-pair economics (locked, ex-fee, ex-incentive) | $160,792.82 | **observed** from fills |
| 2. directional residual P&L | **n/a** | **UNAVAILABLE** -- needs settlement outcome per residual leg; no cost basis beyond these fills |
| 3. spread capture | **n/a** | **UNAVAILABLE** -- needs the contemporaneous book; not modelled |
| 4. maker rebates (L5's, credited) | $34,822.79 over 37 credits | **observed**, L5's own; NOT ours |
| 4b. taker rebates (L5's, credited) | $2,799.98 over 37 credits | **observed**, L5's own; NOT ours |
| 5. liquidity rewards (L5's, credited) | $8,229.66 over 6 payouts | **observed**, L5's own; NOT ours |
| 6. fees | bound $0 .. $110,784.47 | **BOUND only** -- no aggressor flag |
| 7. settlement / redemption cashflow | $5,032,919.89 over 11433 REDEEM rows | **observed** gross credit, not profit |

#### Ex-incentive economics

Stripping **every** incentive stream, the matched-pair book in the current
regime locks **$160,792.82** ex-fee across **4,562,687** matched shares.
Against that sits an all-taker fee upper bound of **$110,784.47**.

So the ex-incentive matched book survives its own worst-case fee bound
with ratio **1.45x** (locked / all-taker bound). That ratio is *not* a
profit claim: components 2 and 3 are unobservable, the fee figure is a
bound and not a measurement, and the whole decomposition is another
trader's tape. It establishes only that the matched-pair leg is **not**
obviously fee-dead without incentives.

L5's credited incentives over the same regime total **$45,852.44**, i.e. **0.29x**
the ex-fee matched-pair lock.

**So incentives are NOT the larger term, and an earlier draft of this
section claimed they were. Corrected.** On the observed tape the
matched-pair leg (**$160,792.82** ex-fee) is roughly **3.5x** the entire credited
incentive stack (**$45,852.44**). L5's pricing is **cheap pair economics in its
own right** -- a median pair at 0.9668 locks about **3.32 cents per pair-share**
before any subsidy.

**The fee bound is what makes this undecidable, and it is the whole**
**uncertainty.** The all-taker upper bound is **$110,784.47** -- i.e. **69%** of
the ex-fee lock. So depending on L5's true (unobservable) maker/taker split,
the ex-incentive matched book is anywhere between
**comfortably profitable** (mostly maker: fee near zero, keeps ~$160,792.82) and
**nearly fee-dead** (mostly taker: keeps ~$50,008.34), at which point the
credited incentives would indeed become the dominant term. **This tape
cannot distinguish those two worlds**, and the difference is exactly what
decides whether the behaviour is copyable.

Per CLAUDE.md, the incentive figures stay additive and separate: they are
never netted into trading P&L, and they are recorded here precisely because
they are the component that does **not** transfer to us.

---

## 3. THE DECISIVE QUESTION -- did L5 abandon or change the
## subsidy-harvesting posture after the August reward program ended?

### 3.1 What the reward streams actually did

There are **three distinct programs** on three distinct payout clocks, not one
"reward program". Separating them is the whole answer.

| stream | paying days observed | last paying day | total USD (post-August, L5's own) |
|---|---:|---|---:|
| `REWARD` | 6 | **2026-09-06** | $8,229.66 |
| `MAKER_REBATE` | 37 | **2026-10-07** | $34,822.79 |
| `TAKER_REBATE` | 37 | **2026-10-07** | $2,799.98 |

Per-day detail (`observed=false` means WE did not fetch that day -- a zero
there is our gap, never L5's silence):

| day | REWARD | MAKER_REBATE | TAKER_REBATE | observed |
|---|---:|---:|---:|---|
| 2026-09-01 | 1,174.30 | 967.26 | 148.26 | yes |
| 2026-09-02 | 1,502.63 | 1,091.58 | 150.54 | yes |
| 2026-09-03 | 1,605.57 | 1,053.54 | 182.45 | yes |
| 2026-09-04 | 1,158.91 | 803.61 | 197.35 | yes |
| 2026-09-05 | 1,272.53 | 1,163.83 | 110.82 | yes |
| 2026-09-06 | 1,515.71 | 1,181.92 | 89.58 | yes |
| 2026-09-07 | 0.00 | 1,077.85 | 85.47 | yes |
| 2026-09-08 | 0.00 | 1,228.98 | 67.67 | yes |
| 2026-09-09 | 0.00 | 1,164.28 | 60.89 | yes |
| 2026-09-10 | 0.00 | 1,380.61 | 54.00 | yes |
| 2026-09-11 | 0.00 | 1,424.67 | 55.07 | yes |
| 2026-09-12 | 0.00 | 1,584.52 | 62.23 | yes |
| 2026-09-13 | 0.00 | 2,470.86 | 101.21 | yes |
| 2026-09-14 | 0.00 | 1,463.77 | 80.38 | yes |
| 2026-09-15 | 0.00 | 1,409.58 | 53.26 | yes |
| 2026-09-16 | 0.00 | 1,437.43 | 61.86 | yes |
| 2026-09-17 | 0.00 | 943.57 | 46.54 | yes |
| 2026-09-18 | 0.00 | 903.34 | 56.02 | yes |
| 2026-09-19 | 0.00 | 1,337.62 | 52.93 | yes |
| 2026-09-20 | 0.00 | 914.23 | 51.26 | yes |
| 2026-09-21 | 0.00 | 1,088.18 | 58.46 | yes |
| 2026-09-22 | 0.00 | 1,104.47 | 50.66 | yes |
| 2026-09-23 | 0.00 | 1,347.80 | 54.35 | yes |
| 2026-09-24 | 0.00 | 969.57 | 53.96 | yes |
| 2026-09-25 | 0.00 | 520.95 | 42.85 | yes |
| 2026-09-26 | 0.00 | 475.52 | 58.86 | yes |
| 2026-09-27 | 0.00 | 766.89 | 102.38 | yes |
| 2026-09-28 | 0.00 | 411.60 | 25.73 | yes |
| 2026-09-29 | 0.00 | 228.36 | 26.59 | yes |
| 2026-09-30 | 0.00 | 206.25 | 36.38 | yes |
| 2026-10-01 | 0.00 | 248.89 | 52.49 | yes |
| 2026-10-02 | 0.00 | 270.37 | 58.61 | yes |
| 2026-10-03 | 0.00 | 337.40 | 69.78 | yes |
| 2026-10-04 | 0.00 | 544.93 | 135.85 | yes |
| 2026-10-05 | 0.00 | 438.36 | 86.01 | yes |
| 2026-10-06 | 0.00 | 413.94 | 51.28 | yes |
| 2026-10-07 | 0.00 | 446.27 | 67.97 | yes |

### 3.2 Answer

**L5 did NOT abandon the subsidy-harvesting posture. One of three subsidy
streams ended; a larger one continued for 31 further observed day(s), and the
trading posture tracked the stream that kept paying rather than the one
that stopped.**

The evidence, in order:

1. **`REWARD` -- the August liquidity-reward program -- stopped.** Last observed
   payout **2026-09-06**. It paid a short trailing tail into September and ceased.
2. **`MAKER_REBATE` did not stop.** It is an *account-level* program on its own
   payout clock, last observed **2026-10-07**, and it never depended on the August
   liquidity pool. It kept paying on **31 observed day(s) after `REWARD`'s
   final payout**, and it is the **largest** incentive stream in the post-August
   window ($34,822.79 vs $8,229.66 of `REWARD`).
3. **The pricing posture did not retreat to a cheaper, subsidy-free line.**
   If L5 had lost his subsidy and had to live on pair economics alone, the
   expected response is *cheaper* pair completion. That is not what the tape
   shows: the current-regime median pair cost is **0.9668**, still slightly
   *above* the $0.96 fee-safe target, with only **46.2%** of pairs completing
   at or below $0.96. The posture held where it was.

   **Honest limit on this bullet.** It is consistent with continued subsidy
   but does not *prove* subsidy-dependence. A median pair at 0.9668 still locks
   ~3.32 cents per pair-share before any rebate, and across the regime the
   ex-fee matched-pair lock (**$160,792.82**) is about **3.5x** the entire credited
   incentive stack (**$45,852.44**) -- so incentives are **not** the larger
   observed term, and any claim that they are is wrong on this data.
   Whether the pair leg *survives fees* depends entirely on L5's
   unobservable maker/taker split (see 2.6).
4. **The mechanics did not change shape.** `EXTEND` still dominates the action
   mix, completion is still partial, residual is still carried. The
   OPEN -> EXTEND -> COMPLETE/SWITCH motif is intact after `REWARD` ended.

5. **The cleanest cut: October.** Every October day in this window is
   OBSERVED -- no gaps -- and across them `REWARD` pays **$0.00** while
   `MAKER_REBATE` pays **$2,700.15** and `TAKER_REBATE` **$521.99**. So the
   August program's absence is a *measured zero on fetched days*, not an
   inference from a hole in our coverage -- and L5 traded **70056 fills over
   2641 events** through it anyway.

   Month over month the behaviour barely moves: median pair cost
   0.9671 (Sep) -> 0.9649 (Oct), completion 50.3% -> 49.6%, EXTEND share 70.8% -> 72.4%.
   **The subsidy mix changed; the policy did not.**

**What changed** is therefore not the posture but its *subsidy mix*: a
market-level TWAP liquidity program fell away and an account-level maker-rebate
program carried the economics. The earlier repo claim that "the reward
environment is worth $0.00" was a `rates: null` read of the **market-level**
program only, and it missed the larger account-level stream; it is already
withdrawn in `analysis/l5/L5_SEPTEMBER_CONTINUATION.md` and is not revived here.

### 3.3 CONFIDENCE

**Moderate-to-high on the stream question. LOW on "is the latest behavior
worth copying".** Stated separately because they are different claims.

*High-confidence part.* That `REWARD` ended while `MAKER_REBATE` continued is
read directly off credited cashflow rows in L5's own public feed, on three
separate payout clocks, across 37 observed days. This is about as direct as
public evidence gets.

Coverage is **gapless**: every one of the 37 calendar days from 2026-09-01 to 2026-10-07
is observed, with **0 failed envelopes and 0 truncated windows**. So the
stream conclusion does not rest on an unfetched interval -- there is none.

*Low-confidence part, and the honest limits:*

- **The window ends 2026-10-07.** Nothing here says what happens after it; a
  program can end the day after a harvest.
- **No maker/taker role exists on this tape.** The claim "only a maker credit
  explains above-$0.96 pair completion" is an *inference from pricing*, not a
  measurement of L5's liquidity role. `execution.isAggressor` does not exist;
  `CHAIN_TOPIC_POSITION` is not a role flag.
- **Directional residual P&L and spread capture are UNAVAILABLE.** Two of the
  seven components are simply missing, so no net profitability for L5 is
  computed and none should be read in.
- **L5's incentives are not our incentives.** Whether `MAKER_REBATE` applies to
  *our* account at *our* size is a separate, unresolved question;
  `analysis/l5/incentives/PMUS_INCENTIVES_CURRENT.md` records that the venue
  doc host is egress-blocked and no credentialed read was available.

### 3.4 What evidence would change the answer

- **A credited-cashflow read over the unfetched days** (and forward past the
  observed end). A `MAKER_REBATE` series that goes to zero would flip the
  answer to "the posture lost its subsidy".
- **A true liquidity-role field, or our own fills at L5's prices.** That would
  convert the "maker credit must be behind this pricing" inference into a
  measurement, or refute it.
- **Settlement outcomes joined per residual leg**, which would populate
  component 2 and allow an actual net figure for L5 rather than a bound.
- **A source-confirmed statement of OUR OWN rebate eligibility and rate.**
  This is the one that decides "worth copying": if L5's pricing is
  rebate-dependent and we have no equivalent rebate, then **copying the latest
  behaviour copies the trade without the subsidy that paid for it** -- which is
  exactly the failure mode CLAUDE.md's rebate rule forbids.

### 3.5 Direct implication for the clone

The latest L5 behaviour is **not** safe to copy at face value. Its pair
completion sits *above* the fee-safe line, and the economics that make that
rational are a credited maker rebate we have not source-confirmed for
ourselves. Spring discipline's materially-cheaper completion target
(`<= 0.96`) should therefore **stay as it is**, and near-$1 pair tolerance
should **stay locked**. No threshold in this repo was fitted or tuned to
anything in this report.

---

## 4. UNAVAILABLE -- stated, not estimated

- **`maker_taker_role`**: The tape carries NO execution.isAggressor field. The liquidity-role field present is CHAIN_TOPIC_POSITION (on-chain CTF topic position). No maker/taker role is claimed. Fee figures are BOUNDS.
- **`spread_capture`**: Requires the contemporaneous book, which this feed does not carry. UNAVAILABLE -- not modelled.
- **`order_placements_and_cancels`**: Not public. Patience-in-book UNAVAILABLE.
- **`l5_pnl`**: No cost basis beyond these fills; settlement credit for events outside the fetch span UNAVAILABLE.

## 5. Reproduction

```
# fetch (CI only -- data-api.polymarket.com is egress-blocked locally)
#   touch .github/post_august_request && git push   ->  l5-post-august-harvest
python3 scripts/l5_post_august_report.py   # read-only, no network
```

Machine-readable output: `analysis/l5/post_august/post_august.json`.
Raw envelopes with provenance: `data/l5/raw/post_august/` (append-only).
Behavioral model reused, not reimplemented: `scripts/l5_regime_detect.py`
(`build_events`, `event_features`, `cash_ledger`, `parse_slug`).

