# First windows on the execution lifecycle release

Deployment `11133fc0`, commit `b7eaedce`, SUCCESS 2026-10-20T05:19:07Z.
Shadow lock ENGAGED, authorization EXPIRED, $5 envelope unchanged, watchdog
untouched. **No order was submitted and none could be.**

## The release's expected improvements, checked against production

| expected | observed |
|---|---|
| `TWO_SIDED_ROUND` with `retired: true` | **YES**, within 3 minutes: `{"round":2,"plan_unexecutable":"LEG_WOULD_CROSS:UP","retired":true,"retirement_blocked_by":null}` |
| more than one proposed pair price per window | **YES** -- `accumulation_round: 3`. Under the shadow lock no claim is ever taken, so a round can ONLY advance by retirement: rounds 0, 1 and 2 were retired and re-planned |
| no `pair_cost` above 0.96 | **YES** -- every observed pair 0.9500 |
| complements only where one side is ahead | **YES** -- `two_sided_complement: null` throughout, correctly: nothing fills under the lock, so nothing is ever one-sided |
| `BOOK_STALE` gone | **YES** -- zero occurrences since `bd8ef135` |

## 0530z, complete

```
ORDER:NOT_ARMED                      18    eligible pairs, stopped at the lock
arm_refused_shadow_only_lock        228
TWO_SIDED_PROBE:PRICE_OUT_OF_BAND    14
TWO_SIDED_PROBE:TOO_LATE_TO_WITHDRAW  3    (T+540)
TWO_SIDED_PROBE:PAST_ENTRY_CUTOFF     8    (T+600)
preflight PASSED, armed false, book_updates 7754
```

`PRICE_OUT_OF_BAND` is new and correct: this market opened 0.03 / 0.98 and
spent the window near settlement. Two ticks behind a 0.03 bid is 0.01, and
the DOWN leg at `(1 - 0.98) - 0.02` is 0.00 -- not quotable. The engine
refuses rather than inventing a price.

## The pair it proposed, and its economics

From the 0515z/0530z records, at a 0.18 / 0.19 book:

```
UP    0.1600 x 4.00    touch 0.1800    alone_cost $0.6800
DOWN  0.7900 x 4.00    touch 0.8100    alone_cost $3.2100
pair_cost 0.9500   edge 0.0500   gross cash $3.7800
cash_gate_obligation $4.6400  (of $4.65)
```

| | per contract | pair of 4.00 |
|---|---|---|
| post-only, **no rebate assumed** | +0.053754 | **+$0.2200** |
| if both legs paid taker | +0.029129 | +$0.1100 |
| rebate upside, never assumed | +0.003754 | +$0.02 |

`both_positive_if_taker: true`. **Worst single leg: -$3.21** -- far worse than
the -$1.85 seen at a mid-book, because at 0.18/0.19 the DOWN leg costs 0.79 a
share. The pair is cheap in aggregate and its ONE-SIDED risk is much larger.

## Modelled pair completion: still ~zero, and now we know why

Both legs, every tick, from `two_sided_queue`:

| leg | wire | rests | same_rung_fifo | cross_price_depth | price must reach us |
|---|---|---|---|---|---|
| UP | 0.1600 | BID | 2,205 - 3,549 | 6,261 - 10,423 | **yes** |
| DOWN | 0.2100 | ASK | 1,481 - 4,690 | 910 - 6,178 | **yes** |

* **CONSERVATIVE** -- no fill. `conservative_required_volume` is **2,209 to
  4,694 contracts** of direction-correct volume at our own rung.
* **BASE** -- no fill, same queue.
* **OPTIMISTIC** -- no fill: needs a print strictly THROUGH our price.

**Residual exposure: zero actual.** Modelled residual: zero, because nothing
fills. **Net settlement economics: $0.00.**

## What this window actually teaches, and it is not plumbing

The lifecycle now works: rounds retire, plans re-derive at the current touch,
the pair cap holds, the transformation is reconciled, and the complement path
is wired and tested. **None of that changes the fill probability.**

Two ticks behind the touch on a book carrying thousands of contracts per rung
puts us behind a queue of 2,000-4,700 contracts AND requires the price to
travel to us. That is not a fillable quote, and no amount of lifecycle
correctness makes it one. The single real fill we have
(`D6XA5J4G10JH`, +$0.0341) came from resting at 0.89 while the book was
0.93/0.94 -- a 4-cent gap that the market then crossed.

So the binding question for profitability is now explicitly: **at what
placement does a BTC15m maker quote actually fill, and does the pair still
clear the fee floor there?** That is a measurement, not a redesign:
`depth_ticks` is one variable, the queue data is already recorded on every
decision, and the frozen AT_TOUCH vs ONE_TICK_BEHIND comparison (task #29)
was pre-registered for exactly this. It needs no new service and no new
strategy -- and it should be answered BEFORE spending a live authorization,
because a live window at `depth_ticks=2` would most likely reproduce what
every shadow window has: an eligible, correctly-priced pair that never fills.
