# Why the engine fills once and then goes inert — at print resolution

**Data: the authenticated WS slice, `1330z`, 5.0 minutes, 2,951 book states and
1,282 individual venue prints de-duplicated on trade `id`. Fills are OBSERVED,
never inferred.** This is a partial window: it measures mechanics, not settled
economics. No net/window figure is claimed.

## The result does not move with more data

| data | events | prints | fills | pairs | places | requotes |
|---|---|---|---|---|---|---|
| 2.3 min | 2,048 | 700 | **1** | 0 | 56 | 54 |
| 5.0 min | 4,233 | 1,282 | **1** | 0 | **56** | **54** |

Doubling the prints changed nothing — not even the place and requote counts. The
engine does all of its acting in the first two minutes and is then **completely
inert**, which rules out data scarcity as the explanation.

## Two causes, both measured

### 1. We sit behind a queue ~84 prints deep

| | |
|---|---|
| displayed size at the touch | **median 1,219** contracts (p90 2,820, max 5,970) |
| median print size | **14.5** contracts |
| prints needed at our exact level before one contract reaches us | **~84** |
| total volume printed in 5.0 min | 55,469 contracts |

The market is extremely active — 55,469 contracts in five minutes against our
10-lot clip. **Activity was never the constraint.** We are a 10-lot order behind
a 1,219-lot queue, and volume has to chew through that queue *at our exact price*
before a single contract is ours.

### 2. We requote ourselves to the back of that queue, 54 times

54 requotes in 5.0 minutes is one every ~5.5 seconds. Every requote is a
cancel/replace, and a replace **joins the back of the new level's queue**. So the
engine never accumulates queue priority: it repeatedly starts a ~84-print climb
and abandons it seconds later when the touch moves.

**Persistence in PRICE is not persistence in QUEUE POSITION, and only the second
one earns fills.** Variant C persists the price it derives from basis, then
throws away the position that price bought every time the touch moves.

## And when it does fill, it prices its complement out of the market

```
10 DOWN bought at basis 0.49  ->  UP complement acceptable at <= 0.47
prints BEFORE that fill: 95,   of which 6 were <= 0.47  (low 0.46)
prints AFTER  that fill: 1,187, of which 0 were <= 0.47 (low 0.49, high 0.90)
```

The acceptable complement existed only *before* we took the first leg. After it,
the market left and never returned. That is adverse selection at print
resolution, and it is the same thing the sparse tape reported as "51% of acquired
contracts never pair".

## What this changes

The continuous-book hypothesis was that participating more would fix pairing.
At print resolution the answer is more specific than yes or no:

* **Activity is not the constraint.** 55,469 contracts traded past us in five minutes.
* **Queue position is the constraint**, and we destroy our own by requoting.
* **Adverse selection is the second constraint**, and it is downstream of the first: the one fill we do get arrives precisely when the market is about to leave.

Mechanism A ("persistent maker") therefore survives, but its content changes.
The thing to persist is the **queue position**, not the price. A rule that holds
a level while the touch moves away — accepting that it is briefly off-touch —
is the only way to climb 1,219 contracts of queue. Requoting to follow the touch
guarantees we never do.

**Not yet established:** whether holding position actually converts into pairs, and
at what rate, over a full window. That needs the remaining 10 minutes of prints.
