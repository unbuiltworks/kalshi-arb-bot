# #93 — why does the venue reject non-crossing complement orders?

**Status: UNRESOLVED, but four deterministic hypotheses are now REFUTED by
direct counterexample.** Evidence exhausted; the remainder needs
instrumentation, not more analysis.

Owner GO: *"identify every rejection reason — tick/price errors, size rules,
stale markets, balance/reservation, post-only protection, rate limits, venue
errors — and fix anything that's plainly our implementation defect."*
Every candidate below was tested. None is plainly our defect.

## The population

48 rejected complements after a real first fill (21.7% of the classified
population). Our own classifier splits them:

| class | n |
|---|---|
| `implementation bug candidate:rejected_at_non_crossing_price` | 23 |
| `venue/execution adaptation:post_only_cross_within_1_tick_of_lagging_ask` | 16 |
| `venue/execution adaptation:post_only_cross_after_book_move` | 2 |
| unclassified | 7 |

**The venue's own reason is uninformative on all 48.** Every one returns
`ORDER_REJECTED_ZERO_FILL / ORDER_STATE_REJECTED` with
`avgPx=0.0000; cashOrderQty=0.00` and no cause field. That is why this issue
stayed open: the venue does not tell us.

## Refuted hypothesis 1 — tick/price grid

Sub-cent prices are **accepted and filled**. Rejected orders are 125 whole-cent
vs 19 sub-cent; accepted are 506 whole-cent vs 35 sub-cent. Sub-cent pricing is
*more* common among accepted orders. A grid violation would reject all of them.

## Refuted hypothesis 2 — minimum size / minimum notional

Orders of **qty 0.01 contracts** and **$0.0018 notional** were accepted **and
filled**. 65 of 121 filled orders (53.7%) were sub-contract. Reject rate is
actually *lower* for small orders: 17.5% below one contract vs 25.4% at or
above. There is no minimum-size floor being hit.

## Refuted hypothesis 3 — our own book is stale/wrong

This one looked alive and died on inspection, which is worth recording.

Pooled across all readings our priced ask differs from the authenticated ask by
a mean of **−11.3 ticks**, which reads like a badly wrong feed. It is not. The
large errors are entirely **stale AUTHENTICATED reads**:

```
gap    auth_age_s   outcome
-27.0     261.8     rejected
-27.0     279.5     rejected
-27.0     279.5     rejected
-23.0     331.5     rejected
 -8.0     116.3     rejected
```

A 331-second book is not evidence about a submission made now. Restricted to a
**fresh** authenticated book (≤10 s), our ask matches the venue's **exactly**
in 14 of 14 comparable cases. **Our pricing feed is accurate.** The pooled
−11.3 figure was an artifact of averaging stale rows, and quoting it as "our
book is 11 ticks wrong" would have been the ninth silent failure of this
campaign.

## Refuted hypothesis 4 — a deterministic post-only distance guard

If the venue refused any order within N ticks of the opposite ask, placements at
distance 0 would always be rejected. They are not.

Placement distance from a **fresh** authenticated ask (≤10 s):

| margin (ticks) | rejected | accepted | reject rate |
|---|---|---|---|
| −7 | 0 | 2 | 0% |
| 0 | 0 | 3 | **0%** |
| 1 | 3 | 6 | 33% |
| 2 | 1 | 5 | 17% |
| 3 | 0 | 4 | 0% |
| 13 | 0 | 1 | 0% |
| **total** | **4** | **21** | 16% |

Orders at the **same distance** are sometimes accepted and sometimes rejected,
and distance 0 — the most aggressive placement in the sample — was accepted
3 of 3. No deterministic price-distance rule fits.

## What survives, and why it cannot be settled here

The only hypothesis left standing is a **sub-second race** between reading the
book and the venue processing our submit. It is consistent with everything
above: accurate feed, no grid or size rule, same-distance orders landing both
ways.

It cannot be proven with what we record. Deciding it needs venue-side
timestamps on accept/reject and full order lifecycle — **the same
instrumentation the category-A pricing problem needs**
(`PREREG_EXECUTION_QUEUE_FAILURE.md` §8.3).

**Sample honesty:** 25 orders with a fresh authenticated book, 4 rejections.
Far too thin to support any positive claim. It is sufficient to *refute* the
deterministic hypotheses, because each died on a direct counterexample rather
than on a rate comparison.

## Decision

**No live parameter changes.** No plainly-our-defect was found, so there is
nothing here to fix that would be justified by evidence. Per the owner's
direction — *"fix the rejections quickly, then immediately return to the much
larger pricing problem"* — this closes as evidence-exhausted and the work moves
to the 162 category-A cases.

The rejection instrumentation folds into the §8.3 spec rather than becoming its
own workstream.
