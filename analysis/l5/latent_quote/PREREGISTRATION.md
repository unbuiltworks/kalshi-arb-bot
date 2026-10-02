# L5 latent-quote realtime shadow — frozen preregistration

**Frozen before any prospective scored result exists.** READ-ONLY SIDECAR.
**No live order. No order path exists in the code. No credentials are read.
No production or executor file is modified. No Gemini. The active PMUS
experiment, its recorder and its completeness gate are not touched or read.**

## The language correction this experiment exists to resolve

The historical study must **not** be read as "L5 rests at the bid". Historical
Polymarket data has **no L2**; the historical quantity was a *trade-derived
same-outcome bid proxy* (the last same-outcome taker SELL print), not a quote.

**This experiment must discriminate between five candidate passive pricing
rules**, which a stale print proxy cannot separate:

| rule | definition |
|---|---|
| AT_BID | rest at the prevailing same-outcome bid |
| BEHIND_BID | one tick behind the bid |
| IMPROVE_BID | one tick inside, improving the bid, only while still passive |
| BASIS_RELATIVE | priced off L5's own running basis / pair-cost constraint |
| STATE_DEPENDENT | the rule itself varies with state |

No one of these is assumed. The price arms in §C are the test.

## Architectural correction, surfaced rather than papered over

The approval said "consume existing recorder output". The existing full-L2
recorder is **`pmus-btc15m-tape` on Polymarket US (PMUS)** — a *different venue*
from the one L5 trades on. PMUS has different participants, a single unified
book per market, and its own liquidity programme. Predicting **L5's** latent
quote from **PMUS** state would be a cross-venue inference, and the behavioural
label and the book would come from different order books.

Polymarket's **own** public CLOB serves a full L2 ladder live, read-only, with
no credentials:

```
GET https://clob.polymarket.com/book?token_id=<clobTokenId>
  -> 35 bid / 64 ask levels, ms timestamp, tick_size 0.01, min_order_size 5
     separate books per outcome (Up and Down are distinct token books)
```

**So this experiment reads Polymarket's own public book — the same venue, the
same order book, that L5 actually quotes into.** This is strictly safer than a
recorder sidecar: it has **zero coupling** to the PMUS recorder, so it cannot
interfere with the active experiment even in principle. It consumes no PMUS
recorder output at all.

PMUS recorder output is **not** read by this experiment. If a cross-venue
comparison is wanted later it is a separate study.

## A. Input state — captured BEFORE any future data

Decision cadence **5 s**; book polling **2 s**. At each decision instant, from
data already received:

**MARKET** (per outcome token, both Up and Down): timestamp, slug/window,
seconds since open, seconds to settlement, bid/ask and the **full depth
ladder**, spread, book-change counts since the previous decision, recent trades
by taker direction, short-window traded volume, and BTC reference where
synchronized.

**L5-MODEL STATE** (hypothetical, from L5's observed public fills only):
hypothetical Up inventory, Down inventory, matched inventory, directional
residual, Up running basis, Down running basis, effective pair cost, run length,
previous action side, previous action price, time since previous action.

**Prohibited as inputs: settlement outcome, and any print or book state at or
after the decision instant.** Ties at the decision second are excluded.

## B. Side models — the main problem. Frozen before scoring

| id | model |
|---|---|
| **S0** | PRIOR — empirical side base rate only |
| **S1** | FLOW — the side opposite recent taker pressure |
| **S2** | CHEAP-SIDE (H1) — the outcome cheapened relative to its recent price / L5's basis |
| **S3** | PAIR-COST (H3) — the side whose passive acquisition most improves expected matched-pair basis |
| **S4** | H1 + H3 |
| **S5** | FULL SIMPLE — H1 + H3 + run persistence + recent flow + time variables |
| S6 | (incremental comparison only) S5 + H2 inventory-band control — **reported for comparison, never assumed core logic** |

Deliberately few features. For every model: side accuracy, balanced accuracy,
per-side precision/recall, log loss and Brier, calibration, performance by
confidence bucket, by market and by day, and incremental gain over S0/S1.

**ABSTAIN is allowed and is a first-class outcome.** A quote is not required at
every state.

**Confidence buckets, frozen now:** `0.50–0.55 · 0.55–0.60 · 0.60–0.65 ·
0.65–0.70 · ≥0.70`. Whether higher-confidence subsets are actually more accurate
is a prospective test. **No cutoff is chosen after seeing P&L.**

## C. Price arms — generated in parallel, never adaptively

For the predicted side, all arms are emitted simultaneously as independent
hypothetical quotes:

| arm | price |
|---|---|
| **P1** | current same-outcome bid |
| **P2** | one tick behind the bid |
| **P3** | improve the bid by one tick, **only if still strictly passive** (price < ask) |
| **P4** | basis-constrained: the highest passive price keeping projected matched-pair cost within the L5-derived economic rule (pair cost ≤ 0.96, the spring-discipline figure) |
| **P5** | the H1/H3 state-driven price from the inferred behavioural model |

These are **parallel experimental arms, not adaptive hindsight.** Exact queue
ahead and full depth at the hypothetical placement are recorded. **No arm ever
crosses**; an arm whose price would be marketable is recorded as `REFUSED_CROSS`
and not placed.

## D. Shadow order lifecycle

`PLACED_SHADOW → RESTING → PARTIAL_SHADOW_FILL → FULL_SHADOW_FILL`, and
`CANCELLED / EXPIRED / REPRICED`.

**No silent repricing.** A policy that wants a new price must emit an explicit
`CANCELLED` event for the old hypothetical order and a new `PLACED_SHADOW` for
the new one. Both events are preserved.

**No touch = fill.** A defensible fill requires one of:

1. **TRADE_THROUGH** — a print strictly through our level;
2. **QUEUE_CONSUMED** — executed volume at our level since placement exceeds the
   queue ahead observed at placement;
3. **DEPLETION_CORROBORATED** — L2 depletion at our level consistent with
   execution **and** corroborated by a contemporaneous print at that level.

**If queue cannot be established, the fill is `UNKNOWN` — never assumed.**

Captured per order: time to first fill, time to full fill, queue ahead at
placement, volume consumed ahead, subsequent price movement, markout after fill,
and whether the market moved adversely before and after the fill.

## E. Labels against L5 — anticipation, kept separate from execution

Where contemporaneous public L5 activity exists, compare the prediction to L5's
*subsequent* action. **Identical timestamps are not required.** Horizons:
**5 s, 10 s, 30 s, 60 s**. For each: did L5 subsequently trade the predicted
side, near the predicted price, in a similar size regime, and consistently with
EXTEND vs COMPLETE?

**BEHAVIORAL ANTICIPATION and EXECUTION SUCCESS are reported separately and
never combined.** A quote may anticipate L5 and not fill; it may fill and not
resemble L5. Those are different outcomes with different meanings.

## F. Economic accounting — defensible shadow fills only

Tracked separately, never netted: matched-pair economics; directional residual
P&L; spread/basis improvement; **modeled** maker rebate; **modeled** taker fee
for any comparison arm that crosses; liquidity rewards **zero unless verified**;
settlement cashflow; unresolved inventory.

Reported: net per eligible window, net per traded shadow window, pair-cost
distribution, completion rate, residual exposure, required capital, and capacity
from **actually observed depth and queue**.

**Status remains SHADOW throughout. No realized-profit language. Nothing here is
submitted, filled, settled or realized.**

## G. Core comparison, identical prospective states

| id | system |
|---|---|
| **M0** | PRINT MIRROR — observe the public print, then attempt the equivalent action |
| **M1** | PASSIVE FLOW BASELINE — rest opposite recent taker flow on a simple book-price rule |
| **M2** | L5 LATENT POLICY — H1/H3 + basis/run/flow state → side + passive price |

Compared on behaviour (L5-side anticipation, EXTEND/COMPLETE similarity, price
distance, size regime), execution (passive-postable rate, defensible fill rate,
queue time, adverse selection) and economics (pair cost, residual P&L,
fees/rebates, total shadow net).

**The hypothesis is NOT that M2 copies every L5 print.** It is that **M2
reproduces the upstream decision process better than reacting to downstream
fills.** Maker share is **not** an optimization target.

## H. Sample and freeze rules

- decision cadence **5 s**; book poll **2 s**; horizons **5/10/30/60 s**
- price arms **P1–P5**; side models **S0–S5** (+S6 comparison only)
- fill rules and cancel/reprice rules as §D
- confidence bins as §B

**Discovery vs untouched validation**, assigned by arrival rule before any
scoring: the **first 3 distinct UTC calendar days** of collection are
`DISCOVERY`; every subsequent day is `VALIDATION` and is **not inspected** until
discovery analysis is frozen. Window IDs are persisted with their class at
arrival.

**Serial dependence is binding.** Thousands of 5-second decisions are not
thousands of independent markets. Every result reports **both placement count
and independent window/day count**, and **no success is declared from
placement-level significance alone.** The ~2,000-placement target governs
*execution diagnostics only*; stage advancement requires independent
windows and days.

**No change to this experiment after seeing early results**, except genuine
instrumentation defects, which are documented separately in
`INSTRUMENTATION_DEFECTS.md` with the before/after and the reason.

## The question this must ultimately answer

> **Can a causal H1/H3 state model place passive orders before L5-like flow
> arrives, and do those orders fill often enough — without adverse selection
> erasing the pair-cost edge — to reproduce L5-like economics?**
