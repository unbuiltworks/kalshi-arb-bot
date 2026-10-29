# EXPLORATORY opportunity-frequency study — BBO / top-of-book only

**THIS IS NOT THE PREREGISTERED TEST.** It is exploratory, it uses top-of-book
only, and **no threshold or rule derived here may be used to select anything in
the preregistered full-ladder OOS evaluation** (`CLIP_CAPACITY_PREREG.md`).
Kept deliberately in a separate file so the two cannot be confused.

Source: `data/pmus_decision_tape/pmus_decision_states_20260927.jsonl` — 315
authenticated decision states from 1615z and 1630z, **all 315 two-sided**. No
size is inferred beyond displayed top-of-book quantity anywhere below.

## Headline: the taker fee is the whole story

Net per contract = gross pair edge − taker fee on the complement leg
(θ=0.0695, fee median **≈1.67–1.69¢** at the complement prices actually
observed).

| gross pair edge | median fee | **net/contract** | contracts/window for $5 | qualifying states displaying that size |
|---|---|---|---|---|
| ≥1¢ | 0.01668 | **−0.00668** | **impossible** | — |
| ≥2¢ | 0.01668 | +0.00332 | 1,506 | 138 / 197 |
| ≥3¢ | 0.01693 | +0.01307 | 383 | 95 / 133 |
| ≥4¢ | 0.01693 | +0.02307 | 217 | 99 / 130 |
| ≥5¢ | 0.01693 | **+0.03307** | **151** | **110 / 126 (87%)** |

**A 1¢ gross pair edge is worth less than nothing** — the fee exceeds it. Any
completion rule must demand **≥2¢, realistically ≥3¢**, gross. This is the
quantitative form of the result the clock-triggered study reached
structurally.

## 1. Opportunity frequency — do profitable complements appear?

**Yes, and often.**

| gross ≥ | reached (gross) | reached (net) | median time to first |
|---|---|---|---|
| 1¢ | 197 (62.5%) | 134 (42.5%) | 29.9 s |
| 2¢ | 197 (62.5%) | 133 (42.2%) | 29.9 s |
| 3¢ | 133 (42.2%) | 130 (41.3%) | 63.3 s |
| 4¢ | 130 (41.3%) | 122 (38.7%) | 63.3 s |
| 5¢ | 126 (40.0%) | 118 (37.5%) | 63.3 s |

Best pair cost reached per acquisition: min 0.7200, p10 0.8400, **median
0.9800**, p90 1.0000, max 1.0900. **62.9% reached below 1.00.**

Note the ceiling was not imposed: pair costs above 1.00 are recorded as
observed, and the median sits at 0.98 on its own.

## 2. Capture gap — what did B' do at these states?

| B' decision | states | had a ≥1¢ complement later |
|---|---|---|
| `SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` | 106 | **29** |
| `SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP` | 88 | **62** |
| `ORDER:NOT_ARMED` | 74 | 72 |
| `ORDER:SUBMITTED` | 36 | 28 |
| `SAFETY_OVERLAY:MAX_LIVE_ORDERS` | 10 | 5 |

Two structural gaps are visible:

- **maker-only blocked 29 states** that later carried a ≥1¢ complement. B' is
  maker-only by construction, so a *taker* completion is refused categorically
  — not on economics.
- **the residual cap blocked 62 states** that later carried a ≥1¢ complement.
  The cap is doing its job on exposure, but it declines to act at moments the
  pair economics later rewarded.

**Caveat that limits how far this goes:** these are states where B' declined
*some* action; this study does not establish that B' was holding residual at
each one, so "would have captured it" is not proven per state. It establishes
that the refusals and the opportunities coincide often.

## 5. Latency sensitivity — are we missing short-lived states?

**No, not at this granularity.** Qualifying windows persist for a long time:

| gross ≥ | n | median lifetime | p90 | max |
|---|---|---|---|---|
| 1¢ | 197 | **63.5 s** | 313.2 s | 439.1 s |
| 2¢ | 197 | 57.5 s | 313.2 s | 439.1 s |
| 3¢ | 133 | 57.5 s | 345.3 s | 439.1 s |

Median opportunity lifetime is **about a minute**, p90 over five minutes.
**Faster collection or execution infrastructure is therefore unlikely to be
material** — these are not sub-second states.

**Sub-2 s lifetime is UNKNOWN from this tape** and is not estimated. This tape
is decision-driven and bursty, not uniformly sampled; the 2 s-sampled lifetime
measurement belongs to the new full-ladder tape and is reported there.

## 3. Capacity — displayed size

At the best complement state: min 36, p10 193, **median 1,754**, p90 2,719
shares. At the ≥5¢ threshold, **110 of 126 qualifying states (87%) displayed
at least the 151 contracts** that $5/window would require.

**So depth is not the binding constraint at the sizes in question.** The
binding constraint is fill rate — whether we can actually acquire and complete
that many contracts — not whether the book shows them.

## What is causal here and what is not

- **Causal:** time-to-first-opportunity, opportunity lifetime, displayed size
  at the qualifying state, and B's recorded decision.
- **NOT causal / oracle:** "best pair cost reached" and the "% that ever
  reached a threshold" scan forward over the remainder of the window. They are
  a **feasibility ceiling** — the best that was ever available — not what any
  rule captures.

## Limits

- **n = 315 states from 2 windows in one hour of one day.** Nothing here has
  statistical weight. It is a feasibility scan, not evidence of expectancy.
- Top-of-book only. No ladder depth. No size inferred beyond displayed.
- Every acquisition is priced at the touch; actual fills may differ.
- Modeled maker rebate is excluded entirely. Credited rebate remains $0.
