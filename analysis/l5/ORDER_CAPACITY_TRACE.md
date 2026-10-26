# Is the three-fill pattern a hard order/fill limit? No.

Traced against the venue-confirmed Oct 26 records (2100z, 2115z, 2130z
preserved tapes). Nothing deployed, no limit changed.

## Answer

**The three-fill pattern is not caused by any cap on orders or fills per
window.** Every count budget had large headroom at campaign end:

| budget | spent | max | source |
|--------|------:|----:|--------|
| OPEN submissions | 3 | 12 | `AUTH.class_budget_report` |
| COMPLETE submissions | 3 | 18 | same |
| REPLACE submissions | 0 | 24 | same |
| flat backstop | 11 | 42 | `AUTH.max_submissions()` |
| windows | 8 | 8 | campaign approval |

`L5_MAX_LIVE_ORDERS = 1` is a **concurrency** cap, not a per-window total.
`src/l5_live_execution.py:524` computes
`ceiling = max_live_orders + (1 if pair_second_leg_ok else 0)`, so two orders
may rest at once, and only for the second leg of the *same* round
(`TS.second_leg_slot_refusal` requires the resting order to be this market,
the opposite side, claimed in this round, with this side unclaimed).

## The specific hypothesis, tested

> does the engine allow UP -> DOWN -> UP but block the fourth DOWN needed to
> complete existing inventory?

No. The observed sequences, with what happened to the fourth order:

**2100z** — DOWN fill, UP fill (pair), DOWN fill (new round), then
**two further UP orders were placed**: `DBBFS9YG11MM` (21:06:20.576) and
`DBBG9B8G51MM` (21:07:26.634). Both reached the venue, rested, and were
cancelled unfilled at 21:06:48.757 and 21:09:27.605. Orders four and five
existed; the book did not come to them.

**2115z** — UP fill, DOWN fill (pair), DOWN fill (new round), then **15
complement attempts**, every one refused `COMPLEMENT_PAIR_ABOVE_CAP`.

**2130z** — full timeline from the tape:

    21:31:45.391  ORDER:SUBMITTED  leg=UP   pair=0.9500 qty=2.09 claimed={UP:F, DOWN:F}
    21:31:46.358  ORDER:SUBMITTED  leg=DOWN pair=0.9500 qty=2.09 claimed={UP:T, DOWN:F}
    21:31:46.468  SAFETY_OVERLAY:MAX_LIVE_ORDERS      <- both legs now live
    21:32:12.145  DBBVENRG11KJ FILLED DOWN 2.09 @ 0.48
    21:33:34.498  DBBVEE8011N2 CANCELED UP (unfilled)
    21:35:07.511  TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP

All **11** `MAX_LIVE_ORDERS` refusals fall in a **183 ms burst**
(21:31:46.468 - 21:31:46.651), immediately after the second leg went on the
wire. **Zero** occur after the 21:33:34 cancel freed capacity. The gate
refused same-tick duplicates while two orders were legitimately live and
never blocked a completion.

**The gate that actually blocked completion is
`TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP`** — 28 occurrences across 2115z
and 2130z. It is economic: the complement would push the pair above the 0.96
cap.

## NEW_OPEN vs COMPLEMENT

Classifying by `two_sided.legs_claimed` is **unreliable** and should not be
used: when a round retires, `legs_claimed` resets to all-False, so a genuine
complement attempt reads as an opening. The 28 `COMPLEMENT_PAIR_ABOVE_CAP`
refusals all carry `claimed={UP:F, DOWN:F}` or `None` despite being, by the
refusal's own name, complement attempts. The reliable discriminator is the
submission's **class** in the durable ledger (OPEN / COMPLETE / REPLACE),
which is what the budgets already count — 3 OPEN and 3 COMPLETE this
campaign, matching the two pairs plus one orphan each in 2100z and 2115z.

This reporting ambiguity is worth fixing on its own: it makes an operator
reading decision rows unable to tell an opening from a completion.

## Can the opening budget starve completion? No - the guard exists

`src/l5_pmus_live_runner.py:3891` reports
`AUTH.completion_capacity_refusal(...)` with the contract:

> a risk-reducing order is NEVER refused by the flat backstop; a new
> exposure-increasing order is refused unless the capacity to close it is
> still reserved

with `COMPLETION_SUBMISSIONS_PER_OBLIGATION = 2`, and OPEN / COMPLETE /
REPLACE are **separate** budgets, so exhausting OPEN cannot consume COMPLETE.
The documented rationale is explicit that a reader of the total alone "cannot
tell whether the budget that is about to run out is the one that OPENS
exposure or the one that UNWINDS it." Completion priority is already
structural, and `may_open` was true throughout.

## Partial-fill accounting already exists

`L5_WINDOW_ACCOUNTING` emits, per window:

    orders: submitted, acknowledged, terminal, filled_orders, partial_fills,
            cancelled, rejected, fill_events
    contracts: filled_total, first_leg_filled, complement_filled
    execution: maker_orders, taker_orders, actual_fees_usd
    incentives: rebate_estimate / credited / pending, eligibility
    realized_net_usd  (venue-confirmed settlement only)

with `fill_events` (deduplicated positive deltas) kept distinct from
`filled_orders` (distinct coids), because "a partial fill is ONE order and
may be several events." Observed: 2100z submitted 5 / filled 3 / **partial 0**
/ cancelled 2; 2115z submitted 3 / filled 3 / partial 0. No partial fill has
occurred yet, so the accounting is untested in anger but present.

## Are late-window cutoffs blocking complements? No

143 `L5_RESIDUAL_BOOK_SAMPLE` observations across the three windows, each
carrying the complement's maker-join and taker-cross own price:

| window | strand | complement needs | cheapest inside cutoff | cheapest after | affordable moments |
|--------|--------|-----------------:|----------------------:|---------------:|-------------------:|
| 2100z | 1.77 DOWN @ own 0.26 | <= 0.70 | 0.94 | 0.99 | **0** |
| 2115z | 1.68 DOWN @ own 0.17 | <= 0.79 | **0.80** | n/a | **0** |
| 2130z | 2.09 DOWN @ own 0.52 | <= 0.44 | 0.49 | n/a | **0** |

**Not once was a complement affordable and blocked by time.** 2100z's samples
after the T+720 cutoff were *worse* (0.99 vs 0.94), so a later cutoff would
have hurt. 2115z and 2130z had every sample inside the cutoff. No cutoff or
runway gate needs changing, and none should be changed on this evidence.

## So what should change?

### Not order capacity

The evidence does not support increasing order capacity, and raising a cap
that never bound would add exposure for no gain. **Recommend leaving
`L5_MAX_LIVE_ORDERS`, the class budgets and the backstop exactly as they
are.**

### The one real constraint: the same 0.96 cap governs openings and completions

2115z missed its complement by **one cent** (needed <= 0.79, book offered
0.80). The cap is the binding constraint, not capacity, not time.

Openings and completions have different economics:

- For an **opening**, 0.96 leaves margin for the adverse move that a resting
  maker leg is selected for. That margin is the point.
- For a **completion**, the alternative is not "no trade" — it is holding an
  unmatched leg. Those two have *identical* expected value: holding yields
  `q[(1 - p_complement) - f]` and completing yields `q[1 - f - p_complement]`,
  the same number. Completion differs only in removing the variance.

So a completion-only cap above 0.96 does **not** raise expected profit. It
converts an EV-neutral gamble into a certain, smaller number. Its value is
that variance is what trips a $5 daily-loss halt and what makes an 8-window
result uninterpretable.

Applied to the record, a 0.99 completion-only cap rescues **1 of 3** strands:

| window | best pair available | under 0.99 cap? | realized | would have been |
|--------|--------------------:|:---------------:|---------:|----------------:|
| 2115z | 0.17 + 0.80 = **0.97** | yes | -0.2856 | **+0.0504** certain |
| 2130z | 0.52 + 0.49 = 1.01 | no (above par) | pending | unchanged |
| 2100z | 0.26 + 0.94 = 1.20 | no | -0.4166 | unchanged |

The machinery is already written and disabled: `src/l5_maker_pair_gate.py`
(`CEILING = 0.99`, `TAKER_ERA_CEILING = 0.96`, default OFF, refusing
`REFUSE_NOT_MAKER` for anything that could cross and `REFUSE_AT_OR_ABOVE_PAR`
at >= 1.00). It would need to be scoped to completions only, which
`effective_ceiling(maker_both_legs=...)` does not currently express.

**This conflicts with a durable rule and must not be enabled on my say-so.**
`CLAUDE.md` states that near-$1 pair tolerance "belongs to later
incentive-era L5 behavior and stays locked unless equivalent economics are
source-confirmed for our account." The honest case for unlocking it is
variance reduction at zero expected-value gain on a sample of three strands,
one of which it would have helped. That is a decision for the owner against
an explicit standing rule, not an engineering judgement.

## Proposed next bounded campaign (for review, not deployment)

1. **Keep** every count limit, cutoff, runway gate, the 0.20 floor, the 0.96
   opening cap, maker-only execution, `max_live_orders`, the completion
   reserve, exact-order cancellation, and all cash / exposure / loss limits.
2. **Fix** `src/l5_two_sided_probe.py:444` — key the first-leg floor's
   inventory exemption on the unmatched imbalance `abs(up - dn)` rather than
   `up != 0 or dn != 0`, so a post-pair opening faces the same 0.20 floor.
   One predicate, inputs already supplied at the call site
   (`l5_live_execution.py:5851`), no state, no reconciliation impact.
   Evidence: refuses 2115z's 0.17 leg (-0.2856), refuses zero legs that
   completed a pair.
3. **Fix the reporting ambiguity**: stamp each decision with its ledger
   submission class so an operator can separate NEW_OPEN from COMPLEMENT
   without inferring from `legs_claimed`, which resets on round retirement.
   Reporting only; touches no gate.
4. **Optional, owner's call only**: a completion-only pair cap above 0.96,
   bounded strictly below par, scoped so it can never apply to an opening and
   never permit a taker crossing. Rescues 1 of 3 observed strands, reduces
   variance, adds no expected profit, and contradicts the standing
   near-$1 lock in `CLAUDE.md`.
5. **Do not** increase order capacity, size, capital or loss limits, and do
   not enable taker completion. None of them is the binding constraint.
