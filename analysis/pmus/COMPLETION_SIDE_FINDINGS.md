# Completion-side intervention — FINDINGS

`L5_NATIVE` frozen (`030d8bf`). B' unchanged as the acquisition/risk baseline.
No deployment. $5 research-loss budget untouched (gating `realized_today`
−4.50680000 after 1645z; $0.49320 remaining).

## The question

> After a maker fill creates residual, can active complement execution convert
> enough stranded inventory into matched pairs to produce positive net
> expectancy while retaining substantial maker volume?

## **NO — not on this evidence.**

Bounded taker completion is **significantly negative** at the strongest
trigger, out of sample. Even a physically unachievable optimistic bound —
maker completion that pays no fee, *earns* the half-spread, and is assumed to
fill every time — fails to show a robust benefit in 11 of 12 cells.

## Why: the theory, then the measurement

Hold `q` of side S at basis `c`; let `a` be the executable complement price.

- Carry EV = `q·(P_true(S) − c)`
- Complete EV = `q·(1 − c − a)`, certain
- **Complete − Carry = `q·(1 − a − P_true(S))`**

For a binary pair `1 − a_~S = bid_S`, so under efficient pricing
(`P_true = mid`) completion requires `mid_S < bid_S` — impossible. **Taker
completion always pays the spread unless the residual is adversely selected.**
Writing `P_true(S) = mid_S − δ`, completion pays **iff `δ > half-spread + fee`**.

Measured cost on PMUS BTC15m, from 315 authenticated-WS book snapshots
(1615z+1630z, median `stream_age_s` 0.032):

| component | value |
|---|---|
| spread | **0.0100** at median, p75 AND p90 — a rock-solid 1 cent |
| half-spread | 0.0050 |
| taker fee `θ·p(1−p)`, θ=0.0695 | 0.0146 at p=0.3, **0.0174 at p=0.5** |
| **total taker cost/contract** | **≈ 0.022** |
| min-side depth | median 499 shares, p10 90.5 — **vs our 0.9 clip** |

Depth is nowhere near binding at our size. **The taker fee alone is ~1.7
points and is the dominant obstacle** — it must be overcome before any edge
accrues.

## The decisive test: forward simulation, no lookahead

Walk each window under B'. The first time TTE falls below the trigger with
non-zero residual, complete **exactly the residual held at that moment** at the
contemporaneous traded price ± half-spread (+ taker fee). Then **continue under
B'**, so residual rebuilt afterwards is carried to settlement as B' would.
Cluster bootstrap by window, 3,000 resamples.

### Bounded taker completion, Δ net $/window vs B'

| trigger | AUG_DEV | AUG_OOS | SPRING |
|---|---|---|---|
| TTE ≤ 420s | **−0.2503** [−0.4427, −0.0780] | **−0.2562** [−0.4405, −0.0741] | +0.0180 [−0.3560, +0.4244] |
| TTE ≤ 300s | −0.1203 [−0.3042, +0.0448] | −0.0901 [−0.2495, +0.0680] | **−0.3859** [−0.7733, −0.0018] |
| TTE ≤ 180s | −0.3517 [−1.0159, +0.2731] | −0.1530 [−0.7434, +0.4531] | +0.2015 [−0.1635, +0.5892] |
| TTE ≤ 90s | −0.3331 [−0.9179, +0.2712] | +0.1659 [−0.4327, +0.8387] | +0.0243 [−0.2593, +0.3149] |

Bold = CI excludes zero. **Not one positive cell is significant. Three
significantly negative cells.**

### Optimistic maker bound (no fee, earns the half-spread, always fills)

| trigger | AUG_DEV | AUG_OOS | SPRING |
|---|---|---|---|
| TTE ≤ 420s | −0.0821 | −0.0801 | +0.2393 |
| TTE ≤ 300s | +0.0458 | +0.0707 | −0.2010 |
| TTE ≤ 180s | +0.1688 | +0.3811 | +0.3600 [−0.0103, +0.7548] |
| TTE ≤ 90s | +0.1352 | **+0.5997** [+0.0083, +1.2741] | +0.1224 |

One significant cell in twelve, barely (lower bound +0.0083), **not confirmed
on DEV** (+0.1352, CI covers zero) and not on SPRING. Under the standing
discipline — select on DEV, decide on OOS — TTE≤90s is not even a DEV-justified
candidate, so it never reaches the OOS gate. One marginal cell in twelve is
what multiple comparisons produce.

## The mechanism: completion is undone by re-accumulation

At TTE≤420s on AUG_DEV, residual P&L moves −1866.99 → **−1827.67**: a 2.1%
improvement, for 8,006.8 contracts converted at a cost of $4,880.44.

**The spread and fee are paid immediately; the residual then rebuilds over the
remainder of the window under B' and is carried to settlement anyway.** Maker
volume is untouched (retention 1.003–1.016 throughout), so nothing is being
destroyed — the intervention simply does not pay for itself.

## Two artifacts I found and corrected

Both initially produced large positive results. Both were lookahead.

1. **Completion sized with hindsight.** The first harness completed the
   *end-of-window* residual at a *mid-window* price. The final residual is not
   known at the trigger. That version reported **+$12.65/window** at TTE≈420s
   on AUG_DEV. Corrected to complete only what is actually held at the trigger,
   the same cell reads **−0.2503/window**. The entire apparent gain was the
   artifact.

2. **Adverse-selection edge measured on a hindsight-selected side.** I measured
   "P(residual side wins) − contemporaneous price" and found −0.1771 to −0.2167
   at TTE≈420s with CIs excluding zero. But *which side ends up as residual* is
   known only at settlement, so the side selection used future information. The
   clean forward simulation is the valid test and it shows no benefit.

A third, smaller correction: measuring the edge against our **cost basis**
(δ = −0.0357 to −0.1000, CIs excluding zero) conflates "the market moved since
we bought" with "mispriced now". Only the latter is relevant to a completion
decision. Against contemporaneous prices the effect does not survive a clean
test.

## Evidence limits — stated because they bound the conclusion

- **The L5 corpus has no book.** Contemporaneous price comes from L5's own
  real traded prices, requiring a trade within **±60 s** of the trigger, else
  the window is **UNKNOWN** and excluded — never assumed to complete. No OHLC
  and no settlement price was used to price any fill, per instruction. This is
  a price-aware, **not depth-aware**, counterfactual.
- **PMUS book evidence is thin and perishable.** Full authenticated book with
  depth exists only for our own windows, and Railway log retention is actively
  destroying it: deployment `a5738478` has been reclaimed, so **1515z and 1530z
  decision-level book data is already gone**. 1615z/1630z (315 snapshots) were
  extracted and are the basis of the spread/depth figures above.
- **`gemini_bbo_btc15m` was NOT used.** It covers 2026-08-30→09-02 and does
  overlap the corpus, but it is a different venue from where L5 traded.
  Cross-venue substitution is not contemporaneous evidence.
- Maker rebates counted at **$0** throughout — confirmed zero, never credited.
  A zero commission is not a rebate.

## What this does and does not close

**Closed:** completion *triggered by elapsed time / TTE*, executed against the
prevailing price, does not create positive net expectancy. Combined with the
suppression result, **neither refusing acquisitions nor time-triggered
completion improves on B'.**

**Not closed:** completion triggered by a *price condition* rather than a
clock — completing only when the complement is executable at a level that makes
the pair economics favourable at that instant, rather than whenever the clock
says so. That is a different rule and this harness cannot evaluate it without
depth-aware book data, because its whole value would lie in choosing *which*
moments to act on.

Testing it needs contemporaneous PMUS BTC15m book+tape, which we do not have
at useful scale and are currently losing to log retention.

## North-star answer

**Completion-side intervention does not create positive executable net/window
on available evidence.** The throughput/capital calculation toward ~$5/window
is therefore not triggered, and no bounded live probe is proposed.
