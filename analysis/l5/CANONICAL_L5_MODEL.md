# CANONICAL L5 MODEL — rebuilt from the full corpus

Answers A–I. Assembled from five independently-run sections:
`CANON_CORPUS_VALIDATION`, `CANON_REGIMES`, `CANON_ECONOMICS`,
`CANON_CONSTANTS`, and `analysis/kalshi/KALSHI_ECONOMICS_GATE`.

**Everything here is historical reconstruction of another trader's public
activity. No figure is realized, filled, submitted or settled profit for
our account. L5's rebates are not evidence that equivalent incentives
accrue to us.** No live authorization sought or taken. Replay → shadow →
live-probe discipline intact; nothing advanced past replay.

## Corpus, validated (Section 1)

Identity = **provenance**: admitted iff the envelope's `params.user` is
L5's address and no *contradicting* name. The `name` field is never a
positive filter — the `/activity` pages omit the profile block, so that
filter discards **706,500 of 784,500 records**. All 1,570 envelopes carry
L5's address; **0 records disqualified on identity.**

The prescribed dedupe key `(txh, asset, timestamp)` is **defective**: the
`/activity` pages carry no `asset` field, so it degenerates and collapses
**33,057 records that differ in price/size/side/outcome/type**.
Redemptions are worst hit — one `transactionHash` carries many distinct
`REDEEM` sizes — so redemption cashflow would have been materially
understated, which is the quantity the incentive question turns on. The
conflict-free key is `(txh, conditionId, outcomeIndex, timestamp, price,
size, type)`.

| quantity | value |
|---|---|
| unique records after dedupe | **776,749** |
| **TRADE fills** | **760,817** |
| distinct TRADE event slugs | **17,003** (17,046 keyed by slug+conditionId) |
| REDEEM / MAKER_REBATE / TAKER_REBATE / REWARD | 15,729 / 110 / 73 / 20 |
| **span** | **2026-03-19 10:09Z → 2026-08-31 23:17Z** (128 active days) |
| side | **BUY 757,613 / SELL 2** |

Three independent sections reproduced 760,817 exactly. Corroboration of
identity beyond provenance is **thin**: only 3,175 of 676,750 nameless
transactions also appear in the named file. The span does **not** reach
September — that was my error, now corrected; September is a separate,
thinner harvest.

**Duration, both readings** (they look contradictory and are not):
15m dominates **fills** 694,799 to 62,816; 5m dominates **events** 10,920
to 6,083. 15m events carry far more fills each.

## A. How many distinct L5 regimes exist? — FOUR, three change points

Daily UTC grid, 17 observables, multivariate binary segmentation with
permutation testing (400 draws, α=0.01), minimum 7 days and 300
independent events per side. **No month boundary imposed, and none of the
three boundaries falls on a month edge.**

| | R1 | R2 | R3 | R4 |
|---|---|---|---|---|
| span | 03-19→04-24 | 04-25→06-22 | **06-23**→08-18 | 08-19→08-31 |
| independent events | **386** | 1,684 | 8,235 | 6,741 |
| fills/event | 40.6 | **96.3** | 42.4 | 34.7 |
| completion rate | 0.933 | **0.994** | **0.729** | 0.733 |
| pair cost median | 0.959 | 0.967 | 0.984 | 0.971 |
| first buy = favourite | 0.41 | 0.45 | **0.617** | 0.630 |
| mean fill size | 14.6 | 18.6 | 30.3 | 34.1 |
| terminal residual frac | 0.220 | 0.132 | **0.445** | 0.489 |
| TAKER_REBATE / REWARD records | 0 / 0 | **0 / 0** | 53 / 7 | 11 / 11 |

R1's 386 events are the weakest evidence in the table and are flagged as
such.

## B. What caused the transitions?

**2026-06-23 is the real break** — its change-point gain is ~5× either
other (6,806 vs 1,413 and 929). At it, simultaneously: entry flips to
systematically favouring the favourite (0.45→0.62), fill size roughly
doubles, completion collapses 0.994→0.729, terminal residual triples, pair
cost moves toward $1 — and **the first TAKER_REBATE and REWARD records in
the entire corpus appear.**

R1→R2 (04-25) is a **volume ramp, not a policy change**: the same policy
scaled to 96 fills/event at 99.4% completion. R3→R4 (08-19) is larger size
with a partial recovery of pair-cost discipline, not of completion.

## C. Which regimes made money before incentives? — THE EARLY ONES, and decisively

Seven components kept strictly separate; settlement coverage ~100% after a
second source-backed resolver was added (a `REDEEM` record's
`outcomeIndex` is the leg actually paid, applied only where exactly one
index was redeemed — **1,999 cross-source agreements, 0 disagreements**).

| month | events | trading net **ex** incentives | $/day | **$/event** | confirmed incentives |
|---|---|---|---|---|---|
| 2026-03 | 88 | −$0.35 | −$0.01 | −0.0040 | $0 |
| 2026-04 | 548 | **+$4,646** | $154.87 | **+8.48** | $918 |
| 2026-05 | 919 | **+$3,921** | $126.48 | +4.27 | $2,004 |
| 2026-06 | 1,031 | **+$4,403** | $146.77 | +4.27 | $1,834 |
| 2026-07 | 3,666 | **+$7,454** | **$240.45** | +2.03 | $5,774 |
| 2026-08 | 10,794 | **−$1,888** | −$60.90 | **−0.17** | **$61,022** |

**Pre-REWARD (Mar–Jul): +$20,424 ex incentives over 6,252 events / 110
days = $186/day, $3.27/event, on $2.99M gross buy notional.** The
pre-incentive strategy stood on its own.

**August: trading alone is −$1,888** and the month is +$59,134 **only**
because of $61,022 of confirmed incentives.

**The mechanism:** matched-pair economics grew ~10× into August but the
**directional residual settlement loss grew faster and cancelled it**.
That is the same failure mode our own Gemini replay found independently —
the matched book is roughly neutral and the residual is where money dies.

**And $/event decays monotonically: 8.48 → 4.27 → 4.27 → 2.03 → −0.17.**
Frequency scaled ~120× while per-event edge collapsed to negative.
"Frequency before size" has sharply diminishing returns in L5's own tape.

## D. How much confirmed value came from rebates/rewards?

**$71,552 — 79.4% of whole-corpus reconstructed net** ($18,536 trading
ex-incentives). Independently reproduced by two sections to the dollar:

| type | records | total | first appears |
|---|---|---|---|
| MAKER_REBATE | **110** | $35,228 | 2026-04-21 |
| TAKER_REBATE | **73** | $7,526 | 2026-06-20 |
| REWARD | **20** | $28,798 | **2026-08-08** |

Nothing modelled, nothing estimated — there is no ESTIMATED column
because nothing was estimated. **203 records in 760,817 fills is a tiny
population and supports no per-contract rebate rate**; absence of a rebate
record is not proof of absence of a rebate.

### The number that reframes the program objective

**L5's trading alone NEVER reached $480/day in this corpus — peak
$240.45/day (July).** The $480/day figure is cleared only in August, at
$1,907/day, in the one month where **trading alone was negative** and the
entire sum is incentives.

**So the $480/day target is itself an incentive-era artifact.** It was
never produced by L5's tradeable behaviour.

## E. What sequential decision process characterizes each regime?

Alphabet: OPEN / EXTEND / COMPLEMENT / SWITCH / STOP, buy-only (2 SELLs
total, both in R1), conditioned only on state after the current fill.

Early (R1+R2) vs late (R3+R4), event-weighted:

| | early | late |
|---|---|---|
| completion rate | 0.983 | 0.731 |
| repeated-compression motif (≥2 extends **and** ≥2 complements) | **0.962** | **0.444** |
| terminal residual fraction | 0.148 | **0.465** (3.1×) |
| pair cost median | 0.965 | 0.978 |
| **P(EXTEND)** | **0.699** | **0.716 — unchanged** |

**Verdict: OPEN → EXTEND → COMPLETE → repeated inventory compression
belongs primarily to the early, pre-incentive regimes.** The OPEN/EXTEND
half is regime-invariant; the **COMPLETE/compression half is what dies at
2026-06-23**, exactly where the incentive records begin. Late activity is
persistent two-sided accumulation that is *not* reliably compressed —
consistent with compensated liquidity provision.

Structure worth carrying into any clone: **P(COMPLEMENT) rises
monotonically with imbalance in every regime**, far more steeply late
(R4 0.130→0.278 vs R2 0.139→0.215); **P(STOP) at one-sided imbalance is
0.020 early but 0.082–0.104 late** — late L5 *abandons* imbalanced
inventory rather than compressing it; and P(STOP) spikes to 0.297 in the
final 30s.

## F. Which old L5 conclusions survive? — MOST DO NOT

Every L5-derived constant traces to **one function**:
`scripts/gemini_l5_inventory_replay.py:derive()`, reading
`data/l5/raw/trades.jsonl` with the name filter at line ~158. Re-running
it reproduces the frozen band `[0.20, 0.82]` and median first-fill `0.583`
**exactly** — so the frozen numbers are faithful to **13 hours of
2026-08-30 and only to that**. (The quoted "9,901 fills" is itself a
coarse-key artifact; the conflict-free key gives 10,457 on that file.)

| metric | old (13h) | full corpus | early | late | verdict |
|---|---|---|---|---|---|
| first-acq median price | 0.588 | **0.54** | 0.50 | 0.55 | SUPERSEDED |
| `L5_BAND` (p10,p90) | [0.20, 0.82] | **[0.25, 0.79]** | [0.36, 0.63] | [0.23, 0.80] | SUPERSEDED |
| % favored first | 69.4 | **60.18** | 44.41 | 62.11 | SUPERSEDED |
| median first-acq T+ | 44.0s | **42.0s** | 39 | 43 | SUPERSEDED |
| % ever complement | 71.5 | **76.07** | **98.06** | 73.38 | SUPERSEDED |
| median s to matched increase | 52.0 | **49.0** | 32 | 53 | SUPERSEDED |
| `L5_EXTEND_BUDGET` | 1 | **1.0** | 1.0 | 1.0 | **holds** |
| `MIN_GAP_S` | 2.0 | **2.0** | 2.0 | 2.0 | **holds** |
| fills/event median (p90) | 6 (136) | **7 (147)** | **81 (155)** | 6 (143) | SUPERSEDED |
| `L5_COMPRESS_TRIGGER` | 0.991 | **0.986** | 0.9758 | 0.9896 | SUPERSEDED |
| final achieved pair cost | 0.9624 quoted | **0.968** | 0.9583 | 0.970 | SUPERSEDED |
| EXTEND not price-gated | asserted | lower 270,049 / higher 272,445 | — | — | **CONFIRMED** |
| buy-only | asserted | BUY 757,613 / SELL 2 | — | — | **CONFIRMED** |
| duration scope | 15m assumed | 5m 10,920 ev / 15m 6,083 ev | — | — | SUPERSEDED |

Only `L5_EXTEND_BUDGET`, `MIN_GAP_S`, buy-only and "EXTEND is not
price-gated" survive.

### Where the deployed system still relies on superseded values

1. **`L5_WINDOW_S = 900.0` / `SCALE = 300/900`** — the most consequential.
   The corpus is **64% 5-minute events by count**. Every time constant is
   rescaled through this one divisor, so a wrong duration assumption
   mis-scales `ACQUIRE_FLOOR_S`, `PATIENCE_REF_S` and `MIN_GAP_S`
   simultaneously.
2. **`L5_BAND = (0.20, 0.82)`** gates OPEN and EXTEND. True band
   [0.25, 0.79] — and it is **period-unstable** (early [0.36,0.63] vs late
   [0.23,0.80]); one band cannot represent both halves.
3. **`L5_COMPRESS_TRIGGER = 0.991`**, `ACQUIRE_FLOOR_S = 44·SCALE`,
   `PATIENCE_REF_S = 52·SCALE` — all off the 13-hour fit.
4. **The "0.96 sits on L5's achieved median" claim** in
   `src/l5_shadow/inventory_policy.py:25`, `two_sided.py:64`,
   `cohorts.py:15`. Full-corpus median is **0.968**; the narrow file gives
   0.977. **0.9624 is not reproducible under either corpus.** The 0.96
   ceiling survives as **owner spring discipline** — what is removed is
   its empirical warrant, not the policy.
5. **The fidelity gate** (`analysis/l5/l5_action_model_v2.json`, scored by
   `src/l5_fidelity_gate.py`) was fitted on the 13-hour corpus, so **a
   PASS currently certifies fidelity to one afternoon.**
6. **CONTRADICTED OUTRIGHT, not merely superseded:**
   `src/gemini_execution/arming.py` requires "EXTEND needs ≥5¢ average
   improvement". L5 pays **lower on 270,049 same-side repeats and higher
   on 272,445** — a coin flip. **That gate is our invention and it
   suppresses roughly half of L5's EXTENDs.**

Per the brief, **nothing deployed was modified.** These are flagged, not
silently replaced.

## G. Venue characteristics required per profitable regime

**Inventory / pair-compression regime** (the profitable one):

| requirement | minimum | basis |
|---|---|---|
| own trades/minute | **≥30/min** | 60 / 2.0s median inter-fill gap |
| fills per event | ≥7 median, 147 p90 | n=17,003 |
| two-sided fill frequency | **≥76%** of events reach both legs | 12,934/17,003; early **98.06%** |
| complement latency | **≤49s median, ≤168s p90** | n=12,934; early 32s |
| duration | must support **5m and 15m**; open ≥198s median | parsed from slug |
| pair completion reachable | **≤0.968 median** | achieved matched unit cost |
| capacity floor | ≥$138 notional/event median, $1,333 p90 | own prints — a floor, not venue capacity |
| spread / depth | **UNAVAILABLE** | corpus is L5's prints only; no book |

**Incentive / liquidity regime:** maker compensation **≥$0.002242/contract**
(all-in $0.004554), from $35,228 / 15.71M contracts — early $0.001295 →
late $0.002451, ~1.9× richer late. Adverse-selection tolerance, fill rate
and spread/depth are all **UNAVAILABLE** from this corpus.

## H. Does any accessible venue meet those requirements? — NO, on current evidence

| requirement | Gemini BTC5m (measured) | Kalshi 15M (measured) |
|---|---|---|
| trades/minute | **~0.8/min** (4 prints per 5-min window) | median 48/min |
| two-sided ≥76% of events | complement reach **45.9%** | **UNAVAILABLE** (anonymous prints) |
| complement ≤49s median | **117s** median | UNAVAILABLE |
| pair ≤0.968 | **never below 1.00** (min 1.01, median 1.24) | **never below 1.00** (min 1.001, median 1.02) |
| maker compensation ≥$0.00224 | **$0** | **$0 confirmed credited; 0 of 383 BTC/ETH rows under any program** |

**Both candidate venues fail the single most important requirement: the
simultaneous pair never costs less than $1.00.** Gemini: 0 of 24,520
observations. Kalshi: 0 of 2,269 snapshots, in **all eight series**. The
pair-compression regime — the only regime that made money without
incentives — **cannot be run where the pair never pays.**

### The 52× problem

L5's all-in incentive is **$0.004554/contract**. Our measured Gemini maker
toxicity is **−$0.238/contract**. The hurdle is **52× L5's entire
incentive rate.** So L5's rebates could never have paid for Gemini-style
adverse selection — which means **L5's venue must have far lower maker
toxicity than Gemini's**, and that difference, not the rebate, is the
structural advantage. This corrects my earlier framing that "L5's edge
looks like a rebate": the rebate is 79.4% of its *net*, but it is
nowhere near large enough to buy its way out of a toxic book.

## I. The single highest-value next executable experiment

**Measure maker adverse selection on Polymarket crypto up/down directly.**

Reasons it dominates every alternative:
1. It is the **one unmeasured quantity** that explains the whole picture.
   L5 was profitable pre-incentive at $186/day with a $0.0046/contract
   rebate; that is only possible if its toxicity is a small fraction of
   Gemini's $0.238. If confirmed, **venue toxicity is the edge**, and the
   program should target a low-toxicity book rather than a rebate.
2. It is **zero-capital and zero-risk** — a measurement on public book and
   print data, no orders.
3. It **gates everything else**. Kalshi's gate is already blocked on the
   same missing number (its toxicity is unmeasured, which is why
   transferring Gemini's −$0.238 there is unsound).
4. Every other lane is either killed on arithmetic or blocked on terms we
   cannot source from this container.

**Blocking prerequisite, stated honestly:** the egress proxy returns 403
to CONNECT for `data-api`, `gamma-api` and `clob.polymarket.com`, and for
every Kalshi host. **This experiment cannot be run from this container**
and needs either network policy change or an out-of-container pull. Until
then the measurement is specified but not executable, and I will not
substitute a proxy for it.

Second-highest value, and cheap: **whether a Kalshi liquidity program ever
appears over a liquid series.** The existing collector already logs
`KALSHI_INCENTIVE_STATE`, so it needs no new code — and it is the one
observation that would reopen Kalshi.

## What NOT to do, on this evidence

* Do not clone late-regime L5. Its trading component is **negative** and
  its net is 79.4% incentives we do not receive.
* Do not unlock near-$1 pair tolerance. L5's own pair cost moving toward
  $1 **coincides with its trading economics going flat-to-negative** —
  direct evidence for keeping the cheaper target locked.
* Do not trust the fidelity gate as currently fitted; a PASS certifies
  fidelity to one afternoon.
* Do not treat $480/day as a behavioural target. It was only ever reached
  with incentives, in a month when trading lost money.

## Caveats that cut against the positive findings

1. Fees are CONFIRMED only where a position snapshot carries
   `entryFeesUsdc` (**1,537 legs, a minority**), so the fee line is a
   **lower bound** and `trading_net_ex_incentives` an **upper bound**.
   This cuts against the pre-REWARD positive and in favour of the August
   negative.
2. Identity for 99.5% of records rests on provenance alone.
3. R1 has only 386 events.
4. 203 incentive records support no per-contract rate; the
   $0.002242/contract figure is a corpus-wide average over a tiny
   population.
5. Maker/taker role is **UNAVAILABLE** corpus-wide — `/activity` carries
   no liquidity flag — so the fidelity gate's `maker_taker` dimension
   cannot be recomputed at all, and "compensated liquidity provision" is
   an inference from cashflow timing, not a measured maker share.
