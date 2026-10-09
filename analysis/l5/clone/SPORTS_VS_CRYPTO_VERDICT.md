# PMUS sports vs crypto for the same L5 policy — measured

**One engine, one service** (`bc7a1f5b`, commit `3f0e769e2`), both regime
policies on identical market states. Sports arm live from 13:07Z; readings
below at 13:48Z. Everything is SHADOW; nothing is realized. E1 disarmed.

**Clock:** sports markets are admitted only on the venue's own game
position (`pmus_event_progress`: period tokens, `ended` flag). Contract
expiry is never used. 205 markets passed; a market with an unrecognised
period token is refused, not guessed.

## Answer: no, sports is not a materially better environment for this policy

Not because sports is worse at any one thing, but because the single cohort
that produces fills produces *adverse* ones, and every cohort that would be
safe to quote in produces **no fills at all**.

### By market type (early arm — the primary candidate)

| cohort | cands | queue-cleared | spread (median) | own-side depth | flow $/min | adverse sel. t+30 | complement ≤0.96 | pair edge |
|---|---|---|---|---|---|---|---|---|
| **soccer `three_way`** | 9 | **55.6%** | 0.01 (mean .041, max .13) | 73 | 5.53 | **−0.02** (6/6 adverse) | **0%** | **−0.01** |
| `cpc` crypto up/down | 2 | 100% | 0.01 | 4,937 | 129.22 | −0.04 | 0% | −0.03 |
| `moneyline` | 5 | **0%** | 0.01 (max .03) | **6,542** | 5.38 | — | — | — |
| `astatc` player props | 12 | **0%** | 0.04 (max .26) | 15 | 0.54 | — | — | — |
| `spread` | 3 | **0%** | 0.03 (max .23) | 1.1 | 0.00 | — | — | — |
| `total` | 2 | **0%** | **0.56** (max .98) | 55 | 0.00 | — | — | — |

By sport: soccer 55.6% cleared (n=9), crypto 100% (n=2), **tennis 0%**
(n=5, depth median 1.1 shares, flow $0.006/min), **table-tennis 0%**
(n=16 decided, spread median 0.04 but flow median $0.71/min).

### The spread hypothesis fails exactly where you said not to assume it

- **Wide spread, no flow → no fills.** Table-tennis quotes 4¢ median (up to
  98¢ on totals) and cleared 0 of 16. Totals quote 56¢ median and cleared
  0 of 2. A quote nobody trades through is not an opportunity.
- **Tight spread, deep book → no fills either.** Moneyline is 1¢ wide with
  **6,542 shares** of own-side depth and cleared 0 of 5: our unit sits
  behind thousands of shares and the pessimistic queue test — correctly —
  never clears it.
- **Fills require flow large relative to the queue ahead**, which is true
  only in crypto (flow $129/min) and soccer three-way (flow $5.5/min
  against a 73-share queue). Those two cohorts are the entire fill sample,
  and both show **negative** post-fill markouts and **0%** complement
  completability at the 0.96 ceiling.

### What is better in sports, and what it is not enough for

| | crypto | sports |
|---|---|---|
| watchable markets with an authoritative clock | ~5 live books | **205** |
| spread (median, best cohort) | 0.01 | 0.01–0.04 |
| own-side depth | 3,000–7,600 | 1–8,500 by cohort |
| pair cost available when holding | 1.08–1.12 | **1.06–1.21** (median 1.095–1.14) |
| fraction at or below 0.96 ceiling | 0% | **0%** |

Sports gives **40× the watchable universe** and a genuinely authoritative
clock. It does not change the thing that killed the crypto arm: once a
first leg is actually held, the complement costs **1.06–1.21** combined, so
the pair cannot be assembled below $1.00 in either habitat.

### Two measurements that moved in the right direction

1. **The first completed pairs in shadow**: matched pair cost **0.92**
   (early) and **0.945** (late), n=1 each, both at or below the ceiling. So
   sub-ceiling completion is not impossible — it is rare.
2. **Aggregate pair-edge opportunity at entry** is positive
   (+0.83¢ early / +0.74¢ late per share over 23/35 queue-cleared
   candidates), while **adverse selection on those same fills is negative**
   (−0.44¢ early / −0.43¢ late). The gap is the cost of being filled.

## Opportunity frequency and realistic capacity

- **Frequency**: 83 early-arm candidates and 26 defensible would-fills in
  ~11 h across both habitats; the sports arm contributed 9–11 soccer
  candidates in its first 40 minutes. Thin, but soccer three-way is the
  first cohort with both fills and non-trivial frequency.
- **Capacity** is *not* the binding constraint where fills happen:
  own-side depth is 73–6,500 shares against our 1-unit quote. The binding
  constraint is queue position, then pair cost.
- At 1 share/leg with soccer's observed clear rate, a live probe would
  assemble on the order of **5 pairs/hour of game time** — and at the
  measured pair cost, each would lose money.

## Economic conclusion

**NO-GO for a bounded live test on PMUS sports with this policy, for the
same arithmetic reason as crypto**, and the NO-GO on BTC up/down stands
unchanged and un-relaxed. The ceiling was not loosened to manufacture a
GO; `L5_CLONE_PAIR_CEILING` is still 0.96.

## Evidence required before any bounded live test

1. **A cohort where `pair_cost_available` when holding is at or below 0.96
   in a material fraction of observations.** Currently 0% in every cohort
   measured, n=4 per cycle. This is the single gating number.
2. **A positive post-fill markout on queue-cleared candidates in that
   cohort**, over n ≥ 100, not n ≤ 6.
3. **≥ 30 completed shadow pairs** with their cost distribution, versus the
   n=1 we now have. Soccer three-way is the only cohort likely to produce
   them on this slate.
4. **A settlement path.** Still unavailable: the closed-market sweep finds
   **0 of 16** held markets (crypto *and* sports), and
   `/v1/markets/{slug}` is 404. Without it no shadow sequence can be booked
   to a settled bottom line, so the final economics stay an estimate. The
   authenticated position-resolution feed is the only known source and it
   covers our own fills only.
5. **Rebate accounting as a separate line.** The event feed serves
   `feeCoefficient` and a `rebate_eligible` flag per market, now carried on
   every cohort stamp. A maker rebate could plausibly cover a 1¢ gap — but
   it is an accrual, never netted into cost here, and must be confirmed
   credited before it counts.

## Which categories to keep watching

1. **Soccer three-way (CSL and similar)** — the only cohort with fills and
   frequency. Needs items 1–3 above.
2. **Moneyline** — tight and deep, zero fills at 1 unit. Worth re-testing
   only with a quote that is not behind 6,500 shares, i.e. a different
   price rule, not a different market.
3. **Tennis / table-tennis / player props / totals — drop.** Depth of
   1 share and flow of $0.00–0.71/min cannot support a maker strategy at
   any ceiling; their wide spreads are an artifact of nobody quoting, not
   an opportunity.
