# Consolidated economic verdict — Tracks A and B

Scripts: `../scripts/A1_maker_taker.py`, `../scripts/B1_cross_time_replay.py`,
`../scripts/92_passive_vs_aggressive.py`, `../scripts/91_across_time_gate_v2.py`.
Results: `A1_MAKER_TAKER.json`, `A1_role_by_class.json`,
`B1_CROSS_TIME_REPLAY.json`, `PASSIVE_VS_AGGRESSIVE_RESULT.json`,
`ACROSS_TIME_GATE_v2_polymarket.json`.

Nothing here is realized profit for our account. Live trading was not
armed and no service or dataset was modified.

---

## 1. How much of L5's execution is identifiable as maker versus taker?

**81% maker, 19% taker — over 61,739 classifiable fills.**

The `takerOnly` endpoint returns only trades where L5 was the taker, so
**presence proves taker; absence proves maker only where the feed is
complete.** Verified before use:

| property | value |
|---|---|
| envelopes / HTTP status | 27, all 200 |
| raw rows → unique | 13,500 → **11,810** |
| distinct offsets | {0, 500, … 4000} — **pagination truncated** |
| span | 2026-08-28 00:07:57Z → 2026-08-31 23:11:19Z (3.96 d) |
| hourly coverage | **95.8%** (hours with ≥1 taker trade) |
| median gap between taker trades | 11 s |
| largest gap | 15,145 s (one 4.2-hour hole) |
| dense contiguous window | **74 hours** |

Because pagination stopped at offset 4000, each run saw only the newest
~4,500 taker trades. Claims are therefore restricted to the dense window:

| class | fills | status |
|---|---|---|
| **TAKER_OBSERVED** | 11,810 | **directly evidenced** — row is in the feed |
| **MAKER_INFERRED** | 49,929 | **inference** from absence inside dense coverage |
| COVERAGE_UNKNOWN | 10,811 | **no claim** — outside dense coverage |

Independently corroborated: the book join (below) finds 65.0% of fills at
or below the prevailing bid on a different window by a different method.

## 2. What predicts L5's next side, price, size and timing?

**Side: nothing we can find.** The switch hazard is constant at 0.27–0.29
per fill across five months, flat within ±0.05 across every residual-VWAP
decile in every month, and the pair-cost change on a switch fill is
indistinguishable from a same-leg fill. Regenerating only the leg sequence
from a memoryless hazard reproduces mean run length to four decimals
(3.4041 vs 3.4046). Side is not predictable because it is not a decision.

**Price, size and timing: strongly predicted by execution mode**, and the
mode is predicted by time to settlement. This is the main new finding.

| | MAKER (81%) | TAKER (19%) |
|---|---|---|
| mean fill price | **0.422** | **0.534** |
| median size | **10.6** | **38.0** |
| median seconds to settlement | **441** | **144** |

Taker share by time to settlement — monotone and steep:

| ≥600s | 300–600 | 120–300 | 60–120 | 30–60 | 0–30s |
|---|---|---|---|---|---|
| **0.036** | 0.044 | 0.322 | 0.455 | 0.502 | **0.665** |

Taker share by size — monotone: 0.040 (<5) → 0.124 (5–10) → 0.170 (10–20)
→ 0.226 (20–50) → **0.362 (50–100)**.

Taker share by price — **U-shaped**: 0.082 at 0.2–0.3 rising to 0.387
above 0.90 and 0.224 below 0.10. Passive in the middle, aggressive at the
extremes.

**So L5 rests small and early and cheap, and aggresses large and late and
dear.** That single mode variable explains the price, size and timing
structure that four rounds of sequence modelling could not.

**One hypothesis this kills:** the aggressive mode is *not* "pay up to
complete the pair". Taker fills complete a pair on 0.458 of occasions
versus 0.486 for maker fills — slightly *less* often. Late aggression is
not pair completion.

## 3. Can cross-time compression produce positive net expectancy on an accessible venue?

**On Kalshi: no. Decisively, in both modes, at every threshold.**

192 markets, chronological replay, no lookahead, Kalshi fee schedule
applied, every market settled including one-sided ones.

| θ | both legs quoted | AGG both-filled | AGG mean P&L | AGG CI95 | PAS both-filled | PAS never filled | PAS mean P&L | PAS CI95 |
|---|---|---|---|---|---|---|---|---|
| 0.20 | 0.031 | 0.031 | −0.0769 | [−0.105, −0.044] | 0.047 | 0.240 | −0.1018 | [−0.132, −0.070] |
| 0.25 | 0.057 | 0.057 | **−0.0714** | [−0.108, −0.034] | 0.073 | 0.182 | −0.1170 | [−0.155, −0.077] |
| 0.35 | 0.167 | 0.167 | −0.1023 | [−0.141, −0.060] | 0.156 | 0.104 | −0.1808 | [−0.223, −0.137] |
| 0.45 | 0.333 | 0.333 | −0.0823 | [−0.121, −0.043] | 0.354 | 0.042 | −0.2340 | [−0.274, −0.195] |
| 0.50 | 0.438 | 0.438 | −0.0762 | [−0.108, −0.044] | 0.438 | 0.042 | **−0.2692** | [−0.305, −0.233] |

**Every single confidence interval lies entirely below zero.** Best case
−0.071 per market.

Two structural reasons, both visible in the table:

* **One leg only, 71–80% of markets.** The strategy almost never completes
  the pair on Kalshi; it ends up holding a directional bet. That is where
  the loss is.
* **Passive is far WORSE than aggressive** — −0.269 against −0.076 at
  θ=0.50. A resting Kalshi bid gets filled precisely when the market is
  moving against it. That is adverse selection, measured, and it is large.

That second row is the most important number in Track B: **on Polymarket
L5's maker fills earn 1.2 cents below mid; on Kalshi our simulated maker
fills lose roughly 19 cents per market more than taking.** The venues are
not comparable, and the difference is in the flow, not the strategy.

**On Gemini: NOT TESTABLE, and this is a data limitation, not a
profitability finding.** Of 1,012 Gemini leg-markets in the tape, **0
quote both legs** — only `…-UP` markets appear, no `…-DOWN`. And there are
833 prints against 250,815 book snapshots. A cross-time pair cannot be
assembled where the complement is never quoted, and a passive strategy
cannot be evaluated without aggressive flow to fill against.

## 4. Which venue and market has the strongest defensible evidence?

**Polymarket BTC/ETH up-down, and it is not close** — it is the only venue
with an actual profitable track record in evidence, 760,817 real fills, a
measured 1.2-cent-below-mid maker edge, and an independent book to check
it against.

Of the accessible venues, **Kalshi KXBTC15M/KXETH15M has the strongest
evidence — and that evidence is negative.** 192 markets, 744,525 events,
both modes, eight thresholds, all CIs below zero. Gemini has no usable
evidence either way.

Market-category ranking by defensibility of *any* conclusion:

| rank | venue / market | evidence | direction |
|---|---|---|---|
| 1 | Polymarket BTC/ETH up-down | 760,817 fills + 369,164 book snapshots | **positive**, but not ours |
| 2 | Kalshi KXBTC15M / KXETH15M | 192 markets, 744,525 events | **negative**, CIs clear |
| 3 | Kalshi KXSOL/KXXRP/KXDOGE 15M | present in tape, thin | insufficient |
| 4 | Gemini BTC/ETH 05M | 0 markets with both legs | **untestable** |

## 5. What realistic daily capacity and profit could that support?

**On Kalshi, on this evidence: none.** Expectancy is negative before any
capacity question arises. Sizing up a negative edge increases losses.

**For reference, what L5 actually achieved** (reconstruction of a third
party's public activity, not our realized profit): $70,917 over 165 days
= **~$430/day**, on $7,551,797 of gross buy notional — **0.94% on
turnover** — from 6,815,896 matched pairs at a mean locked **+1.43 cents
per pair**. Its trading-only peak was $240/day (July); the $480/day figure
was only ever reached in August, the one month where trading alone was
negative and the total was incentives.

**Capacity for us is not estimable from any data held.** It is set by how
often our own resting quotes get filled, which no observed book can tell
us — see Q6.

## 6. What remains unidentifiable without actual resting-order data?

1. **Our own fill rate.** Whether a resting quote fills is a function of
   flow arriving against *our* order, not of any book we can observe. The
   queue model here assumes we join behind all depth at or better than our
   price and never re-price or cancel. Real queue position, cancel/replace
   behaviour and partial fills are all unmodelled.
2. **Our own adverse selection.** Track B measures it for a *simulated*
   Kalshi quote, and it is large and negative. The real figure depends on
   our latency and quoting policy.
3. **Whether L5's 1.2-cent maker edge is skill or flow.** The passive
   classification is now established; *why* passive fills were cheap on
   Polymarket is not. Passive patience and active price selection predict
   the same fill record.
4. **Pre-incentive era mechanics.** Both evidence windows (takerOnly
   Aug 28–31, Zenodo Aug 21–24) are late-regime. The profitable Mar–Jul
   era has no book and no maker/taker flag at all. Applying these findings
   there assumes continuity — defensible, since mean accepted price is
   stable at 0.457–0.486 across all six months, but still an assumption.
5. **Settlement truth on Kalshi.** Track B uses a last-print proxy; the
   tapes carry no settlement record.
6. **Depth and scaling everywhere.** All book figures are touch prices, so
   every result is an upper bound on an implementable strategy.

---

## The verdict

**The L5 programme is not closed by a failed simultaneous-pair test** —
that test was invalid, and it fails equally on Polymarket (0.01% of
169,254 snapshots), the venue where the strategy demonstrably worked.

**But it is not reopened by the cross-time correction either.** The
cross-time mechanism was tested properly on the one accessible venue with
adequate data, in both execution modes, across eight thresholds, with
fees and a queue model, and it is **negative with every confidence
interval clear of zero.** On Kalshi the strategy ends up one-sided 71–80%
of the time, and passive quoting is worse than aggressing by about 19
cents per market.

What L5 had was a venue where resting below mid got filled by benign
flow. What Kalshi offers, on 192 markets of evidence, is a venue where
resting below mid gets filled by flow that is moving against you.

**The single remaining question is empirical and cannot be answered by
analysis:** what our own resting quotes actually do on a candidate venue.
That requires a bounded, instrumented live maker probe — recording our
quotes, their fills, and the mark shortly after each fill — behind the
existing replay → shadow → live-probe discipline. It is an execution
decision, not a research one.
