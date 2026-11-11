# Is stale recovery prematurely cancelling profitable complements?

**On the two live cases available: no. Neither cancelled complement could have
filled. But complement recovery also produced zero replacements in two of two
firings, which makes it useless rather than harmful — and that distinction
changes where the fix belongs.**

Live cases only, from the preserved tapes. Not a holdout result; the frozen
replay evaluation the owner asked for is still to come.

## The first-leg / complement split, which is real

| window | leg recovered | drift | cancel→terminal | replacement | fill | window realized |
|---|---|---|---|---|---|---|
| 0030z | **first leg** DOWN own 0.45 | 7 ticks | 110.1 s | none | — | $0.0000 |
| 0100z | **first leg** DOWN own 0.28 | 7 ticks | 3.81 s | DOWN own 0.37 | **filled in full** | **+$0.0416** |
| 0115z | **complement** UP own 0.70 | 7 ticks | 4.13 s | none | — | −$0.2304 |
| 0145z | **complement** UP own 0.61 | 5 ticks | 3.96 s | none | — | −$0.3605 |

First-leg recovery: 1 replacement in 2 firings, and it filled and produced the
campaign's only recovery-driven pair. Complement recovery: **0 replacements in
2 firings**, both windows ending in residual losses totalling **−$0.5909**.

## Could the cancelled complements have filled? No.

Our complement is a resting BID on the UP side at our own price. It fills only
if the market comes down to us — which requires the best bid to reach our
price. A best bid strictly above our price means both that the price never came
to us and that better-priced orders sat ahead of us in the queue. Using the
post-cancel bid path to **rule out** a fill is the conservative direction of
the no-touch-equals-fill rule, not a violation of it.

**0115z**, cancelled UP own **0.70** at 01:21:14.417. UP bid path after the
cancel: 0.73, 0.91, 0.94, 0.94, 0.94, 0.90, 0.82, 0.75, 0.81, 0.95, 0.96,
0.99. **Minimum 0.73 — never reached 0.70, closest 3 ticks away.**

**0145z**, cancelled UP own **0.61** at 01:47:12.499. UP bid path after the
cancel: 0.70, 0.65, 0.76, 0.74, 0.80, 0.84, 0.87, 0.94, 0.95, 0.96, 0.96.
**Minimum 0.65 — never reached 0.61, closest 4 ticks away.**

So in both cases **keeping the order resting would have realized exactly the
same settlement**: an unfilled complement and the full residual loss. The
cancellations cost nothing in forgone fills.

## What that means, and it is not what the hypothesis predicted

The suspicion was that recovery cancels complements that would have filled.
The evidence says it cancels complements that would **not** have filled — and
then cannot replace them, because once the book has moved, no complement price
exists inside the 0.96 pair cap. In 0145z the complement had been quoted at own
0.61 against a held DOWN at 0.35, i.e. pair exactly **0.96**, the cap's
boundary; one tick of adverse movement makes any replacement unpriceable by
construction.

**So complement-leg recovery is inert in these cases, not harmful.** Its
`recoveries_used: 1` of 1 budget is spent on a cancel that changes nothing,
which is a waste worth fixing but not the cause of the losses.

**The binding constraint is the pair cap against a moved book, not the
recovery gate.** That is where −$0.5909 of this campaign's losses were decided.

## What this does NOT establish

* Two cases. No holdout, no preregistration, no claim of generality.
* Bid path rules out a fill; it does not prove that a *differently priced*
  complement would have failed. A complement resting closer to the touch might
  have filled — and would also have breached the cap, which is the trade the
  cap exists to refuse.
* Nothing here evaluates whether the cap itself is correctly placed. Raising it
  is separately contraindicated by `L5_STRANDED_INVENTORY_BEHAVIOUR.md` §4(b).

## Carried into the post-campaign work

1. Score first-leg and complement recovery **separately** — their replacement
   economics differ by construction, and pooling them hid a 1-of-2 against
   0-of-2 split.
2. For every stale complement cancellation in the frozen replay, run this same
   bid-path test to classify the cancellation as costless or costly **before**
   scoring any rule.
3. Ask whether complement recovery should simply not fire: if the replacement
   is unpriceable inside the cap, cancelling spends the budget for nothing and
   forfeits whatever option value the resting order held.
4. Entry-time probability of profitable completion versus expected residual
   loss, on frozen holdout evidence, with no arbitrary first-leg price filter —
   the 0.20 floor stays disabled and stays unrecommended.
5. Controlled exit remains a separate risk-control analysis, not folded into
   this one.

Nothing changed. Execution remains `MAKER_ONLY`, `max_live_orders: 1`, cap
0.96, both halts live.
