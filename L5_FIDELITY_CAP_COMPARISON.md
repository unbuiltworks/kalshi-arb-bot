# L5 vs our engine, decision by decision — **0.99 improves fidelity by ZERO**

PMUS both sides. L5's fills from the committed `data/l5/raw/activity.jsonl` through the
existing pipeline (`analysis/l5/inventory_trajectory/trajectory.py`). No new collection, no
new service, no deployment. Gate values are the **deployed** ones read from
`L5_EFFECTIVE_RUNTIME_CONFIG`, not from a replay harness.

**Scope: 527 windows, 62,020 decisions, chronological, 1773917919 -> 1788217875.**
527 openings, 33,240 extensions, 28,253 completions.

Script: `analysis/l5/fidelity_cap/compare.py`; per-decision ledger:
`analysis/l5/fidelity_cap/decisions.jsonl`.

## VERDICT: raising the cap to 0.99 does NOT materially improve behavioural fidelity

**Fidelity 0.96 -> 0.99: 0.04% -> 0.04%. +0.00 points. Zero decisions flip.**

| | cap 0.96 (production) | cap 0.99 (candidate) |
|---|---|---|
| **reproduced** | **22 / 62,020 = 0.04%** | **22 / 62,020 = 0.04%** |
| refused by the cap specifically | 11,912 = **19.21%** | 10,020 = 16.16% |
| refused for other reasons | 50,086 = **80.76%** | 51,978 = 83.81% |

## Why zero: the cap was masking a tighter gate

| gate | at 0.96 | at 0.99 | change |
|---|---|---|---|
| `PAIR_CEILING` | 11,912 | 10,020 | **−1,892** |
| `MAX_UNMATCHED_CONTRACTS` | 44,202 | 46,094 | **+1,892** |

**The same 1,892 decisions move from cap-refusal to unmatched-refusal.** Loosening the ceiling
hands them straight to the next gate. Net fidelity change: **nil**.

Full refusal attribution at the production cap:

| gate | count | share of all decisions |
|---|---|---|
| **`MAX_UNMATCHED_CONTRACTS`** | **44,202** | **71.27%** |
| `PAIR_CEILING` | 11,912 | 19.21% |
| `COMPLETE_CUTOFF` | 5,231 | 8.43% |
| `OPENING_LEG_MAX_CONTRACTS` | 497 | 0.80% |
| `MAX_CASH_DEPLOYED` | 140 | 0.23% |
| `ENTRY_WINDOW_CLOSED` | 8 | 0.01% |
| `OPENING_NOTIONAL_BUDGET` | 6 | 0.01% |
| `MIN_FIRST_LEG_PRICE` | 2 | 0.00% |

By action type at 0.96 — note the cap touches **only** completions, and never an opening or
an extension:

| kind | n | reproduced | cap-refused | other |
|---|---|---|---|---|
| OPENING | 527 | 3.23% | 0.00% | 96.77% |
| EXTENSION | 33,240 | 0.02% | 0.00% | 99.98% |
| COMPLETION | 28,253 | 0.00% | **42.16%** | 57.84% |

## The actual binding constraint is inventory scale, by a factor of ~23

L5's unmatched residual at the moment of each decision, against our `MAX_UNMATCHED_CONTRACTS`
of **5**:

| p10 | p25 | **p50** | p75 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| 17.2 | 47.7 | **117.1** | 218.8 | 342.5 | 670.8 | 1154.0 |

**96.8% of L5's decisions occur with a residual already larger than our entire limit.** He
medians 117 contracts of unmatched inventory; we permit 5.

Fidelity is essentially a function of that limit, not of the ceiling:

| `max_unmatched` | 5 | 10 | 25 | 50 | 100 | 250 | 1000 |
|---|---|---|---|---|---|---|---|
| reproduced | 0.14% | 1.09% | 4.75% | 11.47% | 22.23% | 43.37% | 66.21% |

## And this is a reason NOT to chase fidelity that way

Raising `MAX_UNMATCHED_CONTRACTS` is a **risk-limit increase**, not a fidelity tweak, and the
measured economics argue against it directly:

- our own residual carries a **negative drift**: `residual_net` −$85.2296 over 355 settled
  windows, **−10.3 sd** below zero, and **−$0.6932/window** once `|resid|` reaches 3+
- L5 carries one-sided inventory as a matter of course and is profitable; **we are not**

So scaling inventory toward L5's 117-contract median would scale **our measured loss**, not
his profit. The fidelity gap and the profitability gap have the same cause — we are selected
into the wrong side — and inventory capacity is the multiplier on it, not the fix.

## What this retires

- **"The 0.96 cap is why we never complete"** — true of the PMUS *book* (pair cost is 0.99
  and the cap requires 0.96) but **false as an explanation of behavioural divergence**. On
  L5's own 62,020 decisions the cap accounts for 19.21% of refusals and `MAX_UNMATCHED` for
  71.27%, and removing the cap moves decisions between gates without reproducing one more.
- **The cap-to-0.99 proposal** — no economic support (the sweep reversed at n=14) and now **no
  fidelity support either**. Both legs of the case are gone.

## Smallest justified next action

**None on the cap.** It is not the binding constraint on fidelity and not shown to pay.

The single measured fact that governs everything above is the **negative residual drift**. It
makes inventory scale dangerous rather than desirable, and it is the common cause of both
gaps. The next question is therefore whether that drift is a property of *our entry selection*
— which L5's own profitability says it need not be — and that is an entry question on
PMUS data, not a cap or inventory question.

**Requirement honoured: production cap stays 0.96, `shadow_lock=true`, `armed=false`,
`campaign_id=null`, no live order, no deployment, no new service.** No claim of improved
profitability is made anywhere above.

---

# ADDENDUM — the exact figure, and what the cap actually is

## The direct count, replacing my estimate

I interpolated p10 -> median and estimated our cap refuses ~64% of L5's formations. **The
file carries the exact count and it is worse:** `within_0_96_at_formation = 141` of 506
formations = **27.9%**, so **our cap refuses 72.1% of what L5 himself did.** Estimate
withdrawn, direct count recorded.

## The file states the test, and answers it against us

`first_formation.reads`, written by whoever built the benchmark:

> *"if the median here is already ~0.96 the economics are available at acquisition; if it sits
> near par they are EMERGENT and a pair-forming gate rejects the entry"*

The median is **0.9900** — near par. **So by the benchmark's own stated criterion the pair
economics are EMERGENT, and a pair-forming gate rejects the entry.** That is our gate.

## L5 converges imbalance over ~100 fills; we permit 5 contracts and ~4.5 fills

Nested checkpoints (windows *reaching* fill N, so comparable across rows):

| checkpoint | imbalance_rel median | matched pair VWAP | matched qty median | windows |
|---|---|---|---|---|
| after 2 | **1.0000** (fully one-sided) | 0.9900 | 0.00 | 512 |
| after 5 | 0.4999 | 0.9911 | 19.2 | 501 |
| after 10 | 0.2777 | 0.9938 | 50.24 | 474 |
| after 40 | 0.1431 | 0.9912 | 311.19 | 349 |
| after 100 | **0.0761** | **0.9828** | **827.48** | 290 |

He **begins fully one-sided**, takes a median of 3 fills to his first pair (p90 nine), and
grinds matched VWAP from 0.9938 down to 0.9828 between fills 10 and 100 while carrying a
median 827 matched contracts, in 290 of 527 windows. We run ~4.5 fills a sequence with a
5-contract unmatched limit.

## THE SHARP STATEMENT — this is what the cap is

**Our 0.96 is applied as a PRECONDITION for every completion. L5's comparable number is an
OUTCOME of working inventory over ~100 fills. We require at the first trade what he achieves
at the hundredth.**

That reconciles the two halves of this document. The cap touches **only completions** (42.16%
of them) and never an opening or extension, and raising it to 0.99 buys **zero** fidelity
because `MAX_UNMATCHED_CONTRACTS` absorbs the same 1,892 decisions. The cap is a **symptom of
the structure, not its cause**: a precondition carved out of one of L5's outcomes.

Raising it to 0.99 would make the *entry gate* faithful. It would not make us L5, who also
takes ~20x our fill count and converges imbalance over a horizon we never reach.

## A caveat recorded so neither session quotes it

`final` reports matched pair VWAP median **0.9603** — almost exactly our cap — and it is
tempting to call 0.96 "L5's destination". **It must not be read that way.** Unlike `after_N`,
`final` takes `rows[-1]` of every window (n=527), mixing a 3-fill endpoint with a 400-fill
one, so it is not on the same axis as the nested series (after_100 is n=290). The clean trend
only reaches **0.9828** by fill 100. Direction supports the reading; that figure does not
establish it.

## Peer corrections resolved

The peer **withdrew** their residual-drift *mechanism* on both objections: the confound (a
large adverse move causes residual and loss alike, and the stratification controls for
nothing) and the self-contradiction (their own sweep reversed, when the mechanism predicts
improvement). The **drift number survives as evidence about SELECTION, not about carrying** —
L5 carries fully one-sided inventory routinely and is profitable. My strata reconciliation
(−82.19 over 326 windows vs −85.2296 over 355, leaving 29 windows and $3.04 of fractional
residuals) is accepted; the 3+ figure is a stratum mean, not a decomposition.

## Standing conclusion, unchanged

**Fidelity: 0.99 buys nothing (0.04% -> 0.04%). Economics: unresolved and negative so far.**
The binding constraint is inventory scale (~23x) against a residual whose drift is measured
negative for us. Chasing fidelity through the unmatched limit would scale a measured loss.

**Cap stays 0.96. `shadow_lock=true`, `armed=false`, `campaign_id=null`. No live order, no
deployment, no new service, no profitability claim.**
