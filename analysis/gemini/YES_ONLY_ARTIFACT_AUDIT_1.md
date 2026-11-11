# Artifact audit of the YES-only result

**The claim under attack:** YES first fills 48 → 24 completions; NO first fills
140 → 0. Ten audit items, a side-swap inversion test through the fill model,
and the denominator reported from the rung rather than the fill.

**Headline: the fill model is side-symmetric and the result is not a simulator
artifact of the kind we have been bitten by before — but two of my own
reporting errors are corrected below, one of which changes the break-even by
4x, and two interpretation caveats survive the audit and are stated as open.**

---

## 0. Two corrections to what I reported last turn

**C1 — the pair net was a TOTAL, not a per-pair figure, and it was at
q = 0.01.** `ECON_FUNNEL`'s `net=` prints `matched_net_total_excl_subsidy`.
So `net=0.0011337` is the sum across **24** pairs of **0.01** contracts each:

```
per contract-pair = 0.0011337 / (24 x 0.01) = $0.0047237
```

F1 agrees independently: `0.000472375 / (10 x 0.01) = $0.0047237`. The
per-contract pair net is **$0.0047237**, not $0.0011337.

**C2 — that error was also live in the expectancy code.** `net_pair_economics`
is an absolute dollar figure at the struck quantity; the residual term is per
contract. Averaging them raw understates the pair term a hundredfold. Both are
now normalised to **one contract**, a pair whose economics carry no quantity is
skipped rather than assumed, and a test pins it.

**Consequence for the break-even** (§6): the bar is **four times more
forgiving** than I stated. That is a correction against my own conclusion, not
in favour of it.

---

## 1. Source rows and time ranges

The fill-sequence ledger is cumulative **in memory** and was wiped by each
redeploy. The counters quoted last turn were read at 18:04–18:24Z from a
process that started at the `c42c9fe` deploy, **17:17:55Z**. Contracts named in
those lines run `GEMI-BTC05M26092018{05,10,15,20,25}-UP` and
`GEMI-BTC15M2609201815-UP`.

`ECON_SPAN` now prints `first_utc`, `last_utc`, the distinct symbol count,
the cohorts and the `seq_id` range on every window, so this stops being an
inference from deploy timestamps. The ledger is now also persisted, so from
`89d4d28` onward the span is continuous across restarts and the reset
confound disappears.

## 2. Disjointness from the 22-fill toxic-NO cohort — CONFIRMED

The earlier cohort was `GEMI-BTC05M2609201555-UP` at **15:54:38Z**
(`GEMINI_FLOW_LADDER_1.md`). The ledger holding it was destroyed by the
`be3c9eb` (16:16Z) and `c42c9fe` (17:17Z) deploys. Every row behind the 48/140
counters was created after 17:17:55Z. **Disjoint by construction**, and the
independent-confirmation claim stands.

It is worth being precise about what that buys: the two cohorts are disjoint in
*rows*, not in *market regime*. They are ninety minutes apart on the same
venue and the same instrument family. That is out-of-sample in time, not in
conditions.

## 3. Contract orientation / resolutionSide mapping — CLEAN

`metadata.resolution_for` reads `status`, `resolutionSide`, `resolvedAt` and
`settlementValue` from the contract object, takes N reads because the endpoint
flaps (one `settled` reading beats any number of `active` ones), and runs
`_strike_check`: *UP resolves `yes` when `settlementValue >= strike.value`*,
verified to AGREE on four settled BTC5m contracts. The settlement labeller
**refuses to label** when `strike_check.agrees` is false, and an UNCONFIRMED
reading leaves rows unlabelled rather than assigning an outcome. Tests pin
both refusals.

## 4. YES/NO complement identity — CLEAN, and READ not derived

`metadata.outcome_book` takes both outcomes' bids and asks from the contract's
own `prices.buy` / `prices.sell`. It does **not** derive the NO side. It then
*checks* `no_bid == 1 - yes_ask` and `no_ask == 1 - yes_bid`, reports the
residuals numerically and sets `identity_consistent`. A book that violates the
identity is reported, not smoothed.

## 5 & 9. Fill-model symmetry and side-dependent queue bugs — THE BIG ONE

This codebase has already been bitten here. Until **2026-09-20 00:04:28Z**
(`1e3159e`) `size_ahead` returned `None` for every NO leg, and
`Quote.__post_init__` turns `None` into **zero size ahead** — so a NO rung
filled from the front of a queue it never had to clear while a YES rung had to
clear the whole displayed ladder. Nine consecutive BTC15m windows ended long
NO and never once long YES. That defect produces *precisely* the pattern we are
now excited about.

Two things had to be established, and both are:

**(a) The fix is in the code that produced these counters.** `1e3159e` is an
ancestor of the deployed HEAD; the arm constructs `HypotheticalBook` with the
default `QUEUE_SYMMETRIC`; rows are stamped `CORRECTED_QUEUE_V1`.

**(b) The model is symmetric as a property, not as an anecdote.**
`tests/test_gemini_fill_model_side_swap.py` builds a book that is its own
mirror under the complement identity and applies the exact inversion

```
leg    YES <-> NO
book   yes <-> no
depth  bid level at px  <->  ask level at (1 - px)
print  (sell, tp)       <->  (buy, 1 - tp)
```

then requires identical behaviour at every level — `reaches`, `size_ahead`,
`Quote.take`, `_reconcile` placement, and the whole rung-to-fill path — across
a grid of prices, tapes and **lopsided** books. **83 assertions pass.**

And the property has teeth: a test reconstructs the old `size_ahead`, shows
the mirrored rungs then diverge (the NO rung fills on a 1-contract print the
YES rung sitting behind 100 displayed contracts never sees), and shows the
corrected function does not.

**Verdict: no simulator artifact. Do not stop.**

## 6. Arm / state leakage — CLEAN

`registry.state_for` keys `MarketState` by cohort (socket, census, hazard —
deliberately shared) and `CohortState` by `"cohort::arm"`. Arm-level files
carry the arm in the name (`btc5m.PP_F1.fills.json`); a test asserts PP_F0's
fills never appear in PP_F1's. `observation_id` is *deliberately* shared across
arms — it is how the ladder deduplicates one market state seen by four rungs —
and it is an identifier, never a fill.

## 7. Rollover / reset artifact — CLEAN in the model, WAS PRESENT in the record

`close_symbol` and `observe` both filter by symbol, so a sequence opened in one
window is never marked against the next window's book — the cross-contract
labelling bug was caught and fixed before it ran. What *was* an artifact is the
record itself: the ledger was memory-only, so every deploy silently restarted
the cohort. Fixed in `89d4d28`.

## 8. Stale quote asymmetry — CLEAN

Both legs' queue positions are computed from **one** `depth` object read at one
instant, so neither leg can be staler than the other. The book is served from
the maintained websocket (age p50 0.042 s) with a REST fallback, and a sequence
gap invalidates the book rather than being ignored.

## 10. Breakouts — NOW INSTRUMENTED

`ECON_CUT` prints first fills and completion **by leg** within each of
`by_cohort`, `by_tte_bucket_s`, `by_entry_price_bucket` and
`by_market_direction`. Market direction is the contract's published outcome and
shows `UNRESOLVED` until settlement has been read — an absence of evidence,
never a third outcome.

---

## The denominator, reported correctly

`ECON_BYLEG` now prints, per leg, **from the rung that was offered**:

```
candidate_rungs -> rungs_rested -> first_fills -> complement_price
                -> complement_fills
first_fill_rate_of_rested_rungs
completion_rate_of_first_fills      (the conditional rate, kept)
completion_rate_of_rested_rungs     (the one that matters for capacity)
fills_as_the_OTHER_legs_complement
```

**Why this matters, stated before the numbers arrive.** A candidate state in
this arm rests **both** legs, so the per-leg counts overlap and must never be
summed against the candidate total. More importantly: "24 of 48" and "0 of 140"
are conditional on *being filled*, and being filled is itself side-dependent.
If the ask ladder is systematically thinner than the bid ladder, a NO rung
genuinely queues behind less size and fills more often — a **market** fact,
correctly modelled, that would produce this asymmetry with no directional-flow
story at all. `completion_rate_of_rested_rungs` is immune to that and is the
number to read.

## Two caveats that SURVIVE the audit

1. **Selection on the fill, not on the rung.** Above. Open until
   `ECON_BYLEG` reports.
2. **NO completions are not actually zero in the system.** The 24 completions
   after YES first fills *are* NO fills — the complement of a YES leg is a NO
   leg. So the model fills NO rungs readily in the complement role. What has
   never happened is a **YES rung filling as a complement** after a NO first
   fill. The honest statement of the asymmetry is therefore *directional*:
   after aggressive YES buying (which is what fills our NO rung), YES selling
   does not come back; after aggressive YES selling, YES buying does, half the
   time, in a median 12 s.

---

## The break-even, from the authoritative cohort values

```
E_cycle = P(complete) x completed_pair_net + P(residual) x mean_residual_net
        = 0   =>   r* = -p x pair_net / (1 - p)
```

With the F1 YES cohort, **per contract**, and no rounding:

| quantity | value |
|---|---|
| p = completion | 10 / 14 = **0.714285714...** |
| completed pair net | 0.000472375 / (10 x 0.01) = **$0.00472375** |
| **r\*** | **-$0.01180938** |

**The residual may lose up to 1.18 cents per contract on average — a little
over one tick — before the loop turns negative.** On F0's larger YES cohort
(24 of 48, pair net $0.0047237) the bar is `r* = -$0.0047237`.

That bar is not obviously out of reach, and it is 4x more forgiving than I
said last turn. It is also not obviously reachable: a residual is a leg left
behind precisely because the market moved away from it, and the sports lane's
equivalent measured **-$0.331 per residual unit**. Which side of 1.18 cents
Gemini's residuals land on is the whole question, and it is now being measured.

## Checkpoints

`ECON_CHECKPOINT` reports at **n = 20, 50, 100** confirmed residual
settlements (fixed in advance, in code, so the read cannot be chosen after the
fact). Each prints the sign of E_cycle, whether the 95% bootstrap interval
excludes zero, and the mean the remaining settlements would have to carry to
reach break-even at that n.

Unresolved residuals are **excluded from every mean and from the denominator**
— never counted as zero. With no known outcome E_cycle reports UNCOMPUTABLE.
Completion intervals are Wilson (0 of 140 has an upper bound above zero, and
says so); the E_cycle interval is a 4,000-draw bootstrap that resamples whole
sequences, keeping each completion outcome attached to its own payoff.

## Kill condition, stated in advance

If the YES-leg residual mean settles **clearly below -$0.0118 per contract**
with an interval that excludes the break-even, the loop dies. It is not tuned
around.
