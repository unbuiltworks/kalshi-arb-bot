# Complement reachability after the first fill

**The P0 question.** Before optimising first-fill capture, can Gemini support
the **second half** of the L5 inventory cycle at all? Every actual and
hypothetical first fill is reconstructed forward to settlement: inventory →
every COMPLETE quote → its resting duration → the opposite-side touch and
depth → external prints capable of reaching it → queue ahead → reprices →
EXTEND opportunities and fills → matched inventory → terminal residual.

**Sample: 18 seeds — 4 ACTUAL venue fills, 14 HYPOTHETICAL first fills** taken
from the large-print forensic, across 13 BTC5m contracts. Seven arms.

**Model A is unchanged.** Every action below comes from the frozen
`continuous_probe.plan`. The arms change **only** the price a COMPLETE is
quoted at, or how long a quote is left alone. Neither is wired into the live
path. Nothing was sent. **Every fill here is SHADOW/THEORETICAL** — no order
existed, and our own size would have changed the book it is measured against.

Code: `complement.py` (37 tests). Reproduce:
`python3 -m src.gemini_execution.complement_report`.
Output: `analysis/gemini/complement_reachability.json`.

---

## The headline

> **Longer rest does not materially change BROAD_LEAKAGE. Neither does a more
> competitive COMPLETE. The verdict is `BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM`
> in all seven arms.**

And the reason is not the one we went looking for. **Flow toward the
complement is not missing and it is not one-sided.** It arrives when we are
no longer there.

---

## 1. The arm table — the primary metric is matched inventory, not fills

| arm | initial fills | **→ matched** | pair basis | complement reach | complement fills | EXTEND fills | residual units | capital |
|---|---|---|---|---|---|---|---|---|
| NATIVE | 18 | **0** | — | **0 / 77** | 0 | 7 | 0.25 | $0.1315 |
| IMPROVE_1 | 18 | 1 | **0.84** | 1 / 74 | 1 | 7 | 0.24 | $0.1372 |
| +REST 15 | 18 | 1 | 0.87 | 1 / 74 | 1 | 7 | 0.24 | $0.1434 |
| +REST 30 | 18 | 1 | **1.02** | 1 / 74 | 1 | **8** | 0.25 | $0.1530 |
| +REST 45 | 18 | 1 | **1.02** | 1 / 74 | 1 | **8** | 0.25 | $0.1514 |
| +REST 60 | 18 | 1 | **1.02** | 1 / 74 | 1 | **8** | 0.25 | $0.1526 |
| **Model B**: native OPEN/EXTEND + competitive COMPLETE | 18 | 1 | **0.84** | 1 / 74 | 1 | 7 | 0.24 | $0.1372 |

Every improved arm converts **the same single seed** — the 11:45 contract.
Not one of them converts a second.

**The materiality bar.** One matched pair out of eighteen against zero out of
eighteen is **p = 0.50** on the exact test. That is a coin flip, not a
finding. `leakage_verdict()` now requires three gates together, and no arm
clears them:

| gate | why | who clears it |
|---|---|---|
| separable from NATIVE (α = 0.05) | one extra seed is one seed | **nobody** (p = 0.50 everywhere) |
| pair basis at or below par | a pair bought above $1.00 is a matched **loss** | IMPROVE_1, REST_15, Model B |
| residual no worse than NATIVE | buying pairs with more naked exposure is the failure mode | all |

## 2. The nine quantities asked for

**a. Probability an external print reaches a COMPLETE.** NATIVE: **0 of 77
complement quotes — 0.0%.** Every improved arm: **1 of 74 — 1.35%.**

**b. Time to the first reachable complement.** **33.244 s** — the median and
the only value, because there is exactly one across the whole study. NATIVE
has none at any horizon.

**c. Time to a complement fill.** **33.244 s.** The same event: when a
complement was finally reachable it filled in the same cycle.

**d. COMPLETE price and effective pair cost.** The one fill, on the 11:45
contract holding NO, complement leg YES at TTE 93.6, reached by a 263-contract
seller with **zero size ahead**:

| arm | native price | quoted | fill | effective pair cost |
|---|---|---|---|---|
| IMPROVE_1 / Model B | 0.56 | **0.57** | 0.01 | **0.84** |
| REST 30 / 45 / 60 | 0.56 | **0.75** | 0.01 | **1.02** |

**e. Does minimum rest preserve a complement that native repricing abandoned?
No — zero times.** Rest reused a held quote on 34 / 48 / 53 / 56 of 74
complement quotes (15/30/45/60 s), and 32–53 of those reuses sat at a price
different from the native one. Exactly **one** reused quote was ever reached —
and it is the same fill `IMPROVE_1` already gets **without** any rest.

Worse, look at what rest *did* to it. The market moved 19 cents in the rest
window; our stale bid was still sitting at **0.75** when native would have
quoted **0.56**. The same 263-contract seller reached both. Rest paid **18
cents more for the identical fill** and pushed the pair basis from 0.84 to
**1.02 — above par, a matched loss.** On a 5-minute contract, minimum rest is
not patience. It is standing still to be picked off.

**f. Does improve-one-tick change reachability while staying maker? Yes,
marginally, and it is always available.** 74 of 74 improvements existed and
were maker-only; **zero were refused** for crossing or for an unprovable ask.
It moves reach from 0.0% to 1.35% and is the only arm change that produces
matched inventory at or below par. It is worth 1 fill in 74 quotes.

**g. Additional EXTEND exposure while waiting.** NATIVE, IMPROVE_1 and Model B:
**7 EXTEND fills, 0.07 contracts**, TTE 136.9 → 117.9. Rest 30/45/60:
**8 fills, 0.08 contracts**, reaching down to TTE 99.6. Longer rest buys no
extra complement and one extra EXTEND — the wrong side of the trade.

**h. Residual at TTE 60 / 30 / 0 — and an honest caveat.** The residual is
identical at all three marks in all 18 seeds (YES seeds +0.01, NO seeds −0.01
to −0.03). **But that is not evidence of a stable residual.** TTE 30 and TTE 0
were **never observed in any of the 18 seeds**, and TTE 60 in only 3 of 18;
the rest are the final state carried forward, and the JSON labels each one
"no cycle reached this mark". Our recording stops between **TTE 202.8 and
TTE 36.2**. We do not know what the residual does below TTE 36.

**i. Settlement.** Recorded per seed in the JSON and **not used to justify any
behaviour here.** Which way a 5-minute contract resolved is a coin flip; it
cannot make a policy good or bad. It appears because the residual's terminal
value is part of the accounting, and for no other reason.

**Queue and placement, since they turned out to matter less than expected.**
All **77 of 77** native complement quotes sat **at the touch** on the leg we
were buying — never behind it. Queue ahead was readable on 54 of 77 (YES-leg
ladder only; the NO ladder is not published), median 10 contracts, max 3,657.
38 reprices across 11 seeds, median 3. **Price and queue were not the
obstacle: we were already best bid on the complement in every single case, and
0 of 77 were reached.**

## 3. Your one-sidedness hypothesis — refuted

> *"Are sellers repeatedly hitting our held side while almost nobody trades
> into the complement?"*

**No.** Pooled over 18 seeds, 28 prints ran toward EXTEND against 17 toward
the complement — 62.2% by count, but only **55.9% by notional** ($1,541.76 vs
$1,214.41). And the pooled figure double-counts: the 04:30 contract's tape is
shared by five seeds. **De-duplicated to 13 (symbol, leg) pairs the notional
is $598.06 toward EXTEND against $998.81 toward the complement — 37.5% extend.
There is *more* complement-direction notional than extend-direction
notional.**

Only 2 of 13 were genuinely one-sided toward EXTEND. 5 of 13 had **no flow at
all** after the fill, which is a different fault again.

Structurally one-sided flow would have explained everything. It is not what
the tape says.

## 4. What the tape says instead — a coverage failure

Of the complement-direction notional that existed after our seed fill:

| | prints | notional |
|---|---|---|
| **arrived while we were still quoting** | **1** | **$149.91** |
| **arrived after our last recorded cycle** | **16** | **$848.90** |

**85.0% of the complement-direction notional arrived after we had stopped
watching** (87.7% on the per-seed pooling, which double-counts). Sixteen of
seventeen complement-direction prints landed when we were not there.

And the causal link is as clean as this study can make it:

> **Exactly one seed had complement-direction flow arrive inside our cycle
> coverage — the 11:45 contract. That is the one and only seed that formed
> matched inventory, in every arm that formed any. Seeds with in-coverage
> complement flow: 1 of 1 matched. Seeds without: 0 of 17.**

Two further coverage facts:

- **7 of 18 seeds had zero cycles after the first fill.** For those, no
  COMPLETE was ever quoted, because our recording had already stopped. That is
  39% of the sample where the second half of the cycle was structurally
  impossible before price or rest could matter.
- **C8B alone contributed $572.26 of complement-direction notional, all of it
  after the window stopped on budget at TTE 203.**

This is the same finding the A/B/C/D attribution reached from the other
direction — **C (timing) at 46.0%, with 40.1% inside TTE < 60** — arrived at
here by an independent route.

## 5. What this does and does not license

**Answers the question you asked:** longer rest does **not** materially change
BROAD_LEAKAGE. A more competitive COMPLETE does **not** either. The single
matched pair in 18 seeds is 5.6% and statistically indistinguishable from
zero, and three of the six improved arms buy it above par.

**It does not license** a conclusion that Gemini cannot support the second
leg. The study cannot say that, because in 16 of 17 cases we were not present
when the complement-direction flow arrived. **What is established is that our
current coverage cannot support the second leg — not that the venue cannot.**
Those have opposite remedies and must not be conflated.

**It does not license anything live.** No bound is changed, no threshold is
introduced, no selector is built — the pre-entry persistence floor (≥20 pairs)
is not yet satisfied. Model A is untouched, the TTE bounds are untouched, and
nothing here is a request for authorization.

**Two caveats on the sample.** 14 of 18 seeds are HYPOTHETICAL, reconstructed
from large prints against a recorded book; our own size would have changed
that book. And the effective sample is 13 contracts, not 18 — five seeds share
the 04:30 tape. Both are stated wherever a number rests on them.

## 6. The next measurable thing

The evidence points at one variable, and it is not price, rest or aggression:
**whether we are still quoting when the complement-direction counterparty
arrives.** The 11:45 seed is the existence proof — in-coverage complement flow
produced a matched pair at a 0.84 basis on the first attempt.

That is a statement about window coverage, which is exactly what the TTE
bounds govern, and those are frozen pending your decision. I am not proposing
a change to them here.

---

**Accounting labels.** Everything above is **SHADOW / THEORETICAL**. No order
existed, nothing was submitted, filled, settled or realized. "Matched
inventory", "pair basis", "capital at risk" and "residual" are reconstructions
of a hypothetical inventory path, not positions and not a cost basis. Matched
and residual are never summed. No rebate is assumed anywhere in this document.
