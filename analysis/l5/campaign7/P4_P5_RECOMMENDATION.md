# P4/P5: both candidates already exist, and neither is activatable on evidence

## Both A and B are implemented and flag-gated

**B (autonomous inventory-aware decisions) already runs 24/7.** The shadow
controllers decide from `(prop, inv, book, tte)` -- weighted-average
inventory cost, matched/unmatched quantity, book, time remaining --
with no L5 print. Proof: during 2045z, the window where the follower made
ZERO decisions, the controller logged
`provenance: CONTROLLER_GENERATED, market: ...-2045z, inventory
{yes:0,no:3,matched:0,residual:-3}, decision: no_action,
reason: residual_cap:3>=3`.

It is also already measured out of sample, and it loses:

| policy | windows | net/window |
|---|---|---|
| ARGMAX | 759 | -0.2213 |
| PAIR_AVAILABLE | 759 | -0.2237 |
| RESIDUAL_URGENCY | 759 | -0.2318 |

with the defect diagnosed in its own source: the argmax extends 90% of the
time against L5's 52%, "walks inventory up to the cap and then parks" --
exactly what the 2045z record shows. Both attempted corrections were tried
and neither helped. **B would not have traded 2045z either.**

**A (two-order concurrency) is implemented behind `L5_TS_TWO_ORDER_PAIR`,
default off.** `second_leg_slot_refusal` grants the slot only when the flag
is on, exactly one order is live, in THIS market, on the OPPOSITE side, and
claimed in the SAME round; every other condition is a named refusal and
fails closed. The slot "raises MAX_LIVE_ORDERS by exactly one for this
order... it buys a slot and never headroom".

## P4 checklist: already covered by tests, 195 passing

| requirement | tests |
|---|---|
| combined obligations and exposure reserved | obligation basis, envelope fit, reservation exceeds both fills |
| either order fills first / partial / cancel / unmatched | partial reduces only its own row, either fill order handled, cancel proves terminal |
| inventory and residual limits hold | account ceiling unchanged, residual ceiling unchanged at three |
| cancellation targets exact IDs | each order keyed by its own client and venue id |
| no second unrelated market or plan | another market refused, another round refused, third order refused |
| watchdog / halts unaffected | the slot does not bypass any other gate |

## Why A is still withheld, now with Campaign 7 added

The suite already encodes the economics: locking both legs at plan forfeited
**$0.5335** on an 8-window settled campaign -- four times that campaign's
whole net of $0.1336 -- because the complement is priced from the REALISED
first leg against the CURRENT book and beat the 0.95 plan in four of five.
It also creates a new failure mode: today the CHEAPER leg is always placed
first, so a stranded window can only strand the cheap side; with both legs
resting the DEAR side can strand, 1.1x to 6.9x worse.

Campaign 7 does NOT reproduce the forfeit -- locking at plan would have been
**+$0.0251 better** over the campaign, with only 3 of 7 pairs beating plan.
So the effect is regime-dependent and close to zero on average.

## The decisive number

    deployed follower policy, 12 settled windows, losses included
      total +$2.2637   mean +$0.1886/window   sd 0.7128   t = 0.92
      -> NOT distinguishable from zero; 57 windows needed

    two-order serialisation delta (C7: +$0.0251/campaign = $0.0036/window)
      -> 158,052 windows to resolve

**An eight-window A/B cannot isolate A's economic effect by four orders of
magnitude.** Running one would be theatre. And no strategy change can be
credited until the baseline itself is known to be positive, which needs 57
windows.

## The volume arithmetic that reframes the target

$480/day needs 54.4 matched contracts/window at the blended $0.0919 edge.
We achieve 2.0. The funnel is 366 observed intents -> 36 submitted -> 18
filled -> 14.03 matched. Even PERFECT conversion of 52 observed intents per
window at the realised 50% fill rate gives ~26 fills; at our ~2-contract
clip that is ~26 matched contracts, still half the target. At L5's median
clip of 10.8 it would be ~140.

**Frequency alone cannot reach the target. Clip size must rise.** That
inverts the "frequency before size" heuristic for this specific gap, and
size is bounded by the cash envelope -- i.e. capital, which is correctly
gated behind validated profitability.
