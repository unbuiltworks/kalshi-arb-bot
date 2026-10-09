# Research brief — GROK

## Your task

You are given the complete public execution history of a single
prediction-market trading account over 5½ months: **760,817 fills across
17,046 markets**, with the account's inventory reconstructed after every
single fill.

**Infer the sequential decision process that generated this behaviour.**

Treat it as reverse-engineering an unknown algorithm from its output
trace. I want the decision rules, not a description of the data.

I have deliberately given you **no hypothesis of my own.** Do not ask me
what I think it is, and do not try to agree with me — there is nothing to
agree with yet. Form your own account of the mechanism from the trace.

## Background — only what you need, no more

* The venue is Polymarket. The markets are **binary, short-dated BTC and
  ETH "up or down" windows**: each market opens, runs for 5 or 15
  minutes, and resolves to `Up` or `Down`. Market slug
  `btc-updown-15m-1787903100` = BTC, 15-minute window, starting at unix
  epoch 1787903100.
* The two outcomes are complements. One contract of `Up` plus one
  contract of `Down` pays exactly **$1.00** at resolution, whichever way
  the window goes. Prices are in dollars, 0 to 1. A contract that wins
  pays $1.00; a contract that loses pays $0.00.
* The account is a third party. It is not mine. I have no privileged
  information about it of any kind.
* **You have executions only.** No orders, no cancellations, no order
  book, no bid/ask, no maker/taker flag, no underlying price path.
  Timestamps are whole seconds. Read `LIMITS_OF_PUBLIC_FILLS.md` before
  concluding anything — several obvious-looking inferences are
  unavailable, and I would rather you tell me a question is unanswerable
  than answer it unsupportably.

## Questions, in priority order

### 1. The decision rule

Given the state of a window — elapsed time, time remaining, current
inventory on each leg, current effective pair cost, the prices filled so
far — **what determines the next action?** Specifically:

* What triggers entry into a window at all, and what determines *when*
  within the window entry happens?
* What determines **which leg** is bought next?
* What determines **size**?
* What determines **price** — is there a reservation price, a band, a
  rule relative to inventory?
* What terminates activity in a window?

Write the rule as **pseudocode or a state machine** that someone could
implement. Where a parameter appears, give its value and the evidence for
that value. Where the rule is conditional on regime, say so.

### 2. Timing

* Is there structure in the inter-fill gaps (`seconds_since_prev_fill`)?
  Characterise it. Is it a clock, a reaction, a queue artefact, or an
  execution-pacing choice?
* Does behaviour depend on position within the window
  (`seconds_from_window_start`, `seconds_to_settlement`)? Some fills land
  **after** nominal expiry — what is happening there?
* Does the rule differ between 5-minute and 15-minute windows beyond
  simple time-scaling?

### 3. Inventory management

* How is the relationship between the two legs managed over a window?
  Is there a target, a tolerance band, a ratchet?
* `effective_pair_cost` is the FIFO cost of the matched pairs held. How
  does it evolve within a window, and is its evolution controlled or
  incidental?
* `residual_qty` / `residual_side` is the unpaired holding. Is the
  residual a deliberate position, a byproduct, or a risk being managed
  down? What is the evidence either way?
* Is there evidence of a position limit, a capital limit, or a stop?

### 4. Side switching

The account alternates which outcome it buys. 12,977 of 17,046 windows
contain at least one switch.

* What triggers a switch? Price? Inventory imbalance? Elapsed time? A
  fixed cadence? Something else?
* Are there distinguishable switching *modes*, or one rule with varying
  inputs?
* `S1`, `S2`, `S3` in the case studies are the extremes — highest switch
  rate, longest single-leg run, and a long window with no switch at all.
  Can one rule produce all three?

### 5. Sizing

* Characterise the size distribution and, more importantly, the size
  *sequence*. Is size state-dependent?
* **Careful here:** `size` is filled size. A sequence of small fills is
  equally consistent with one large resting order being filled
  incrementally. Say which of your sizing conclusions survive that
  ambiguity and which do not.

### 6. Regime change

The corpus spans 2026-03-19 to 2026-08-31, and is heavily weighted to
August (10,794 of 17,046 windows). Parts are chronological.

* Did the decision process **change** over the span? Where are the
  breakpoints? What changed — parameters, or structure?
* Is early behaviour a cruder version of late behaviour, or a different
  strategy?

### 7. What would falsify you

For each substantive claim, state **what you would expect to see in this
data if you were wrong.** I will run those tests.

## How to answer

* **Quantify.** Every claim cites the fills, windows, or case studies it
  rests on, with counts.
* **Rank by confidence**, in three tiers: (a) strongly supported by the
  data, (b) plausible but under-determined, (c) speculative.
* **Separate mechanism from motive.** "It buys the cheaper leg when the
  pair cost would fall below X" is a mechanism. "It is market-making" is
  a motive. Both are welcome, labelled.
* **Say what you cannot tell.** A clear "this data cannot answer that" is
  worth more to me than a confident guess.
* **Give me testable predictions.** End with a numbered list of concrete,
  falsifiable statements with a direction and, where you can, a
  threshold — the package includes a harness that will test them against
  all 760,817 fills, and I will send you the results.

## Files, and the order to read them

1. `README_UPLOAD.md` — this package's contents.
2. `DATA_DICTIONARY.md` — every field, how it was produced, where it
   misleads. **Note the warning about `outcome_index`.**
3. `LIMITS_OF_PUBLIC_FILLS.md` — what this data cannot show.
4. `CORPUS_PROFILE.json` — descriptive distributions, for orientation.
5. `case_studies/CASE_STUDIES_ALL.md` — 29 complete windows, every fill
   in order with inventory after each. **Start your reasoning here.** The
   strata are chosen to span the behaviour, including its extremes.
6. `dataset/l5_window_summary.csv.gz` — all 17,046 windows, one row each.
7. `dataset/l5_fills_enriched_SAMPLE.csv.gz` — 56,154 fills, whole
   windows sampled across the full span.
8. `dataset/fills/l5_fills_enriched_part*.csv.gz` — all 760,817 fills,
   chronological, 14 parts. Load as many as you can.
