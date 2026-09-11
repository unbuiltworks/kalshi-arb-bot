# Entry quality: pre-registration

**Frozen before any outcome was examined.** Written and committed before the
study code existed, for the same reason as the residual pre-registration: a
threshold chosen after seeing which entries would have paid is not
out-of-sample however it is later presented.

Owner, 2026-09-11: *"Entry quality. This is now P0. If 75% of post-imbalance
COMPLETE opportunities are already ceiling-blocked, then many bad outcomes are
effectively created on the first fill... The question becomes: what first legs
should we never have taken?"*

---

## 0. What is already known, so this does not redo it

Two prior studies bear on this and are **not** repeated here.

**`COMPLETE_FILL_PREDICTORS.md`** (716 accepted completes) already measured the
dominant fill predictor, and it is not aggressiveness:

```
pair cost at touch  < 1.00      147 attempts   45.6% fill
pair cost at touch >= 1.00      353 attempts   10.8% fill
share of our completes placed at or above par:  70.6%
```

This reframes the owner's second lever before it is opened. A 10.5% completion
fill rate is **mostly a regime, not a queue position**: most of our completes
are placed where the pair already costs more than a dollar, and no counterparty
trades the other side of that. Buying queue position inside the above-par
regime cannot recover a 45.6% fill rate, because the 45.6% belongs to the
below-par regime. So lever 2 is substantially *downstream of* lever 1, and
lever 1 goes first — which is the order the owner set independently.

**`L5_COMPLETION_FORENSICS.md`** (31 markets, 09-08) already measured what
happened *after* the first fill: abandonment in 7 markets (2.14 completes
each, one deadline and done) against genuine difficulty in 3 (47 completes
each, 5.7% fill). It did **not** measure entry state. Its one entry-side
number is a null worth carrying forward: median entry spread was **0.010 in
all three cohorts**, so spread at entry did not separate them.

## 1. The question, stated as a measurement

> Of the episodes where our first leg filled, does the state at that entry
> separate the ones that paired from the ones that stranded — and is any
> separation strong enough to refuse entries on, without refusing so many that
> nothing is left?

## 2. Population, unit and label

- **Population:** every episode whose first leg **filled**. Not the settled
  cohort: an episode that filled one leg and never paired is the failure being
  studied, and settlement-keyed tables cannot see it.
- **Unit:** one row per filled episode. Features are read from the OPEN
  decision that produced the first filled order, and from nothing later.
- **Label:** `STRANDED` iff residual != 0 at episode close, else `PAIRED`.

**The label needs no settlement hindsight.** Whether a residual remained is
known when the episode closes; the resolution side and realized number are not
used, are not read, and are banned as inputs exactly as in the residual
pre-registration.

## 3. What counts as knowable at entry

| knowable | source |
|---|---|
| our entry price | `complement_preflight.chosen_px` |
| the complement's ask at that moment | `complement_preflight.opp_ask` |
| **implied pair cost at entry** | `chosen_px + opp_ask` — **the primary feature** |
| distance from the wire touch | `chosen_px` vs `complement_preflight.wire_px` |
| which side we opened | `open_side`, `complement_preflight.side` |
| planned size | `planned_safe_size.contracts` / `.usd` |
| pricing regime | `complement_preflight.regime` |
| L5 state key and support | `state`, `state_n`, `persist_extend_p`, `switch_complete_p` |
| market category | slug prefix (`aec-atp`, `aec-cs2`, `tsc-mlb`, `asc-cfb`, …) |
| time of day | `t_utc` |

Coverage, measured before any outcome was joined: `complement_preflight` is
present on **2382 of 2597 OPEN decisions (91.7%)**, and where present,
`chosen_px` and `opp_ask` are both present **100%** of the time. This is
deliberately checked first because the last fill study had to report
`opp_ask_authenticated` as UNAVAILABLE at 1.1% coverage, and a feature that
cannot be read is not a feature.

**Banned as inputs:** settlement outcome, resolution side, `realized_usd`,
final inventory, anything downstream of the entry decision, and anything
derived from them.

## 4. The primary hypothesis, named before running

> **H1.** Episodes that stranded entered at a materially higher implied pair
> cost (`chosen_px + opp_ask`) than episodes that paired, with the separation
> concentrated at the $1.00 cliff already measured on completes.

The mechanism it claims: an entry whose complement already costs more than
`1.00 − our price` cannot be completed inside the pair ceiling without the
complement's price moving in our favour. Every later COMPLETE is then placed
in the 10.8% regime, and the market strands not because we managed it badly
but because it was born that way.

## 5. The rule form, fixed before fitting

> Refuse the entry when `chosen_px + opp_ask > θ`.

**One parameter.** The §6 budget of the residual pre-registration allows two;
this uses one, and adding a second requires amending this document before
seeing results.

## 6. The counterfactual is exact, and that is why this study is worth running

Refusing an entry **deletes the episode**. Both numerator and denominator of
residual incidence change deterministically — no fill has to be simulated, no
trajectory modelled. This is the property the repairing-EXTEND study lacked
and the reason it could not answer the incidence question at all.

**The one assumption, stated plainly:** freed concurrency is not modelled. In
reality a refused entry releases the single market slot and something else
would have been taken; that second-order effect is out of scope. It affects
throughput, and the owner has already established that throughput scales both
sides of the incidence ratio and leaves break-even untouched.

## 7. Split

- Chronological by first-fill time. **TRAIN = earliest two thirds** of filled
  episodes, **HOLDOUT = latest third**, read **once**.
- The threshold is chosen on TRAIN only.

**Prior exposure, declared.** The finding that motivates this study — 75% of
post-imbalance completes already ceiling-blocked — came from these same
markets, so TRAIN is not virgin ground and I am not going to pretend it is.
The holdout is the protection, and it is the number that will be reported as
the result.

**The sealed residual holdout is not being spent.** Those 8 markets were
sealed for evaluating a *post-imbalance* rule, and their post-imbalance
decision sequences stay unread. Their labels — which markets carried a
residual — were published in the R5F cohort table before the seal existed, so
using the label is not reading the seal. Their ENTRY rows are a different set
of decisions and are in scope here.

## 8. The bar, set before the result is known

A threshold may be proposed for **shadow evaluation only** if all hold:

1. On the **holdout**, residual incidence among retained episodes falls into
   or below the **17–19% break-even band**, from the ~31% baseline.
2. It **retains enough to be a strategy.** A filter that keeps four episodes
   has not solved incidence, it has stopped trading. The retained count and
   the retained *paired* count are reported beside every incidence figure, and
   a rule that discards more than **half the paired episodes** to get there is
   reported as a rejection of the strategy, not an improvement to it.
3. The paired episodes it discards are counted and priced at the measured
   paired mean (+$0.2851), against the stranded ones it avoids at −$1.9120, so
   the net is stated in dollars and not only in incidence.
4. It is one parameter.
5. It never licenses an entry the standing bounds refuse.

Clearing this earns shadow evaluation. It does not earn a live change.

## 9. What would falsify H1

- **Coverage collapse.** If implied pair cost at entry is computable on too
  few filled episodes, the study reports UNAVAILABLE rather than substituting
  a proxy. No proxy is pre-authorised.
- **Overlap.** If the entry distributions of PAIRED and STRANDED substantially
  overlap, entry state does not predict stranding, the loss is *not* created at
  entry, and §3 of the residual findings was over-read. That is a real
  possible outcome and will be reported as one.
- **Degenerate separation.** If the only thresholds that reach the break-even
  band retain almost nothing, the honest reading is that this engine's entries
  are not separable into good and bad by price alone, and the lever is
  selection *upstream* of entry — which markets we look at — not a filter on
  the entry itself.

## 10. Expected honest outcome, recorded now

The filled-episode population is roughly 96 episodes with roughly 37 stranded.
That supports a one-parameter threshold and a confidence interval, and nothing
more. **I expect the most likely result to be a real but partial separation —
a threshold that moves incidence materially without reaching 17% on its own.**
If that is what the data says, it will be reported as that, and the remaining
gap will be named rather than closed with a second parameter.

---

**Committed before running.** Deviations must be recorded as deviations, with
the reason, not silently applied.
