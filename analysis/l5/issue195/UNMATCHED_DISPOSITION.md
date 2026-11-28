# The unmatched leg now gets a priced decision

## The silence this closes

0100z carried a residual 1 DOWN to a **−$0.4900** settlement. In the 14
minutes after the last cancel the tape held **152 decision records**, of
which the final **83 read `NOT_AN_OPPORTUNITY` with verdict `N/A`**. Not one
named the position, its worst case, or the fact that nothing was available.
The engine stopped **asking** rather than deciding.

## What the engine does now

`src/l5_unmatched_disposition.decide` is a pure function. On a confirmed
unmatched leg it enumerates **six** dispositions, prices each, ranks them,
and returns a record naming the selection and why every alternative was
rejected.

| action | crosses | priced from |
|---|---|---|
| `PRESERVE` | no | the resting quote's pair |
| `REPRICE` | no | **deferred to `completion_requote`** |
| `COMPLETE_MAKER` | no | `quote_own_price`, 2 ticks behind the touch |
| `COMPLETE_TAKER` | **yes** | the far side (`ask` for UP, `1−bid` for DOWN) + taker fee |
| `REDUCE` | **yes** | the held leg's own bid + taker fee |
| `HOLD` | no | carry to settlement |

Each carries `pair_own_cost`, `edge_per_contract`, `worst_case_usd`,
`best_case_usd`, `ev_at_market_usd`, `fees_usd` and
`queue_ahead_at_price`.

### The ranking rule, and two drafts of it that were wrong

**1. HOLD is the fallback, never a competitor.** An unfilled maker quote
leaves us in exactly the HOLD state, so a maker action whose pair is inside
redemption weakly **dominates** holding. An earlier draft ranked HOLD on the
same scale and it won — because holding's best case is the whole $1.00
windfall, which we keep anyway if the quote does not fill.

**2. Among executable non-HOLD actions: best worst case, then best *best*
case, then EV, then the shallower queue.** Ranking the queue before the pair
chose a **0.9800** pair over a resting **0.9500** one — precisely the defect
PR #200 fixed. Best case is where the pair edge lives, so it must rank
above queue depth.

**Why not EV.** If the complement's executable own price is `p`, then
`EV(hold) = h(1−p) − c` and `EV(complete) = h − c − hp = h(1−p) − c`. They
are **equal, exactly** — `test_declining_a_zero_edge_completion_is_ev_neutral`
already pins it. Ranking on EV would be choosing by rounding error. What
differs is the worst case, and the 15-window measurement is why that
matters: carrying realizes −$0.19 to −$0.25 a leg while completing costs
$0.04 to $0.07.

### It defers rather than duplicating

Pair ceilings come from `max_pair_cost()` and `completion_pair_ceiling()`.
Whether a resting completion may be repriced is answered by
`completion_requote` **itself**, so PR #200's ceiling and PR #202's recovery
test cannot be contradicted here by a second opinion. `COMPLETE_MAKER` is
marked **infeasible**, not merely ranked lower, when a resting completion
holds an equal-or-better pair — there is one order per side, so placing it
would mean withdrawing the cheaper quote, which is the #200 defect exactly.

And the converse case is handled: a resting quote whose pair is **above**
redemption does *not* dominate holding, because a fill there locks a certain
loss. It is refused `RESTING_PAIR_ABOVE_REDEMPTION` so HOLD and REPRICE can
compete.

## The crossing route stays unreachable

`selected` is only ever an action whose `executable_now` is true, and both
owner switches (`L5_TS_COMPLETION_CROSS` **and** the taker policy) must read
YES. An accessor that cannot be read at all reports the route **off**, never
on. The crossing actions are still **priced**, because the whole point of
the record is to state what declining them costs — and the module holds no
transport, no credentials and no executor, verified on its parsed AST.

## Decision census over all 15 recorded windows

| sample | decisions | selected | mean counterfactual worst-case gap |
|---|---|---|---|
| in sample | 1,612 | `PRESERVE` 84.6%, `HOLD` 15.4% | **$0.6725** |
| out of sample | 2,540 | `PRESERVE` 65.4%, `HOLD` 34.6% | **$0.5139** |
| in sample, route *hypothetically* on | 1,612 | `COMPLETE_TAKER` 81.7%, `REDUCE` 18.3% | $0.0000 |

**No crossing action is ever selected with the route off**, across 4,152
decisions. The gap is the worst-case exposure that declining it leaves
standing — not an expected loss, and not a realized one.

On the venue-confirmed 0100z state the engine selects **`PRESERVE`** (the
0.9500 pair), refuses `COMPLETE_MAKER` as
`RESTING_COMPLETION_HAS_AN_EQUAL_OR_BETTER_PAIR`, refuses `REPRICE` as
`REQUOTE_REFUSED:IMPROVEMENT_BELOW_MIN_TICKS`, prices `COMPLETE_TAKER` at a
**1.0200** pair and refuses it `PAIR_ABOVE_COMPLETION_CEILING`, and prices
`REDUCE` at a certain **−$0.0373** against holding's **−$0.4900** risk —
reporting a counterfactual worst-case improvement of **$0.4527** that it
does not act on.

## A correction to my own earlier measurement

The hold-versus-complete comparison I reported previously priced taker
completions at `touch_own_price`, which is the **maker** touch. A taker
**lifts the far side**: buying UP pays the ask, and buying DOWN sells YES
into the bid. I understated the taker's cost by a whole spread — one tick in
217 of 220 recorded samples. Corrected, with the crossing price:

| d | OOS taker, before | OOS taker, **corrected** |
|---|---|---|
| 2 ⬅ deployed | +0.0034 | **−0.0008** |
| 3 | +0.0078 | **+0.0029** |
| 4 | +0.0103 | **+0.0048** |

**My claim that taker completion "flips the sign" was too strong out of
sample.** It removes the loss; it does not establish an edge. Best
out-of-sample figure is now **+$0.0048 per contract-attempt** at `d=4`, so
$480/day implies roughly **1,000 contracts per leg in every window, all
day** — about double my earlier estimate. In sample it remains clearly
positive (+$0.016 to +$0.037).

## Does this resolve the silent unmatched-inventory problem?

**The silence: yes.** Every tick with unmatched inventory now emits
`L5_UNMATCHED_DISPOSITION` naming the position, the basis, the book, the
deadline, every feasible action with its economics, the selection, the
reason, and the rejected alternatives. Fail-closed paths are *named* —
`ORDERS_UNCERTAIN`, `BOOK_STALE`, `GAP_SUSPECTED`, `TICK_UNKNOWN`,
`INVENTORY_UNREADABLE`, `PAST_DEADLINE:…` — never `N/A`. Repeats are
throttled to 15s while nothing changes and emit **immediately** on a change
of selected action, so the 83-identical-record pattern cannot recur.

**The economics: no, and only authorization can.** With the crossing route
off, the best available action remains `PRESERVE` or `HOLD`, whose worst case
is the full held cost. The engine now *states* that it is accepting a
−$0.49-class worst case and that a disabled route would have capped it — but
it cannot act on that, and it must not.

## Still open

- The crossing route remains **disabled**; nothing here enables it.
- The **$0.0521** completion-budget discrepancy remains open; the durable
  budget is not reset.
- No fill probability exists (see `l5_queue_depletion`): maker upsides are
  conditional on fills this engine does not predict.
- 15 windows across 3 dates. Thin, overlapping entries, settlement proxied
  by the final mid.
