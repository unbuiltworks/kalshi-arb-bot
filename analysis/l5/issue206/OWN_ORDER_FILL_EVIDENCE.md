# What our own orders say about maker fills

**34 venue-confirmed orders. Not a replay, not a shadow, not a bracket.**
No profitability is claimed anywhere below; a fill rate is not a P&L.

## 1. The inventory

Every `l5f-` order we have ever rested, with a venue-confirmed terminal
state. Sources, all venue tapes:

| source | window(s) | orders | filled | cancelled |
|---|---|---|---|---|
| `issue195/FILL_RECONCILIATION.md` | 2015z 11-06 | 18 | 9 | 9 |
| `residual_exit/preserved/TICK_DISTANCE_AND_FILL_LATENCY.md` | 0100z–0200z 11-02 | 11 | 8 | 3 |
| `issue195/WINDOW_1230Z_RESULT.md` | 1230z 11-07 | 5 | 3 | 2 |
| | **total** | **34** | **20** | **14** |

Each carries side, own price, quantity, queue-ahead where the tape recorded
it, distance behind our own touch, submit→terminal duration and terminal
state. The 2015z fills sum **penny-exact** to that window's settled
`up_cost 1.541400` / `dn_cost 2.4200`, so the rows are the same objects the
settlement was computed from.

**Excluded, deliberately.** `ow|ARGMAX|…` and dotted coids are shadow
quotes. The 110 distinct `l5f-` ids in `data/pmus_decision_tape/` are
`armed: false` / `ORDER:NOT_ARMED` **intents** — `transport.submit` raised
`NotArmed` at the wire, so no order existed and a fill rate over them would
be meaningless.

**What we do not have.** Per-order queue-ahead for the 0100z–0200z campaign
(the tape did not record it); any order quoted deeper than 4 ticks (we have
never done it); any order outside BTC15m; and the fill-calibration
recorder's `queue_ahead` captures, which live on the Railway volume and were
taken while unarmed.

## 2. Censoring is the whole statistical content

14 orders did not fill, and almost none of them *failed* to. They were
**cancelled by us** — by the requote, the distance recovery or the order
TTL — several inside two seconds:

```
cancel times (s)  1.1  1.2  1.7  5.4  6.1  6.8  8.2  13.6  19.5  21.0  44.8  120.9  123.0  124.0
fill times   (s)  0.0  1.0  2.9  5.5  5.9  6.0  6.1   7.3  10.8  11.4  11.6   14.0   14.5  17.1
                 18.8 23.5 35.0 35.0 63.0 73.4
```

An order cancelled at 1.0s says essentially nothing about whether it would
have filled at 20s. Scoring it as a non-fill understates the rate; dropping
it overstates it. **Both are wrong.** These are right-censored durations and
the Kaplan-Meier product-limit estimator is the standard treatment: each
cancellation leaves the risk set at its own time rather than scoring against
us.

| estimator | P(fill) |
|---|---|
| naive ratio 20/34 | 58.8% |
| **Kaplan-Meier, by 73.4s** | **81.8%** |
| median fill time | **17.1s** |

By role: openings **78.7%** (n=21), completions **83.8%** (n=12).

**Holdout.** Fitting on 2015z + 0100z–0200z (29 orders) and checking 1230z
(5): the fit curve's median is 17.1s and its last fill 35.0s, while 1230z
filled at 6.0s, 35.0s and **63.0s** — *outside* the fitted range. The curve
is conservative out of sample on duration, not optimistic.

## 3. Where this sits against the recorded-book bracket

PR #208 ranked strategies on a bracket: **PROVEN** (the opposite touch
reached our price — sufficient, a lower bound that over-samples the adverse
case) and **POSSIBLE** (enough size left the queue — necessary only, an
upper bound). At round level, from our own orders:

| | P(pair) | P(single) |
|---|---|---|
| PROVEN, d=2 OOS | 52.5% | 42.3% |
| **our own orders** | **72.7%** (8 of 11) | 27.3% |
| POSSIBLE, d=2 OOS | pairs nearly everything | — |

**Our own orders sit above PROVEN and below POSSIBLE**, which is where a
bracket is supposed to put them. PROVEN was understating pairing, exactly as
its own docstring warned.

## 4. The finding that matters

**Every single we have ever carried into settlement was created by our own
cancellation of the complement. Not one was caused by the market refusing
us.**

| window | complement | cancelled at | P(would have filled) |
|---|---|---|---|
| 0145z | UP 0.61 | 13.6s | **70.7%** |
| 2015z | UP 0.3700 | 19.5s | **60.0%** |
| 0115z | UP 0.70 | 21.0s | **60.0%** |

And the opening legs, where the churn is worse — five of 2015z's nine
cancellations fired between **1.1s and 6.8s**, each with a **76–81%**
conditional chance of still filling. Mean over all 14 cancellations:
**55.8%**.

The two cancellations that cost nothing are the ones at 120.9s, 123.0s and
124.0s — past the last observed fill, where the conditional chance is
**0.0%**. The order TTL is doing its job; the discretionary cancels were not.

## 5. What was changed, and what was already fixed

**`TSP.REQUOTE_MIN_REST_S = 14.0`** — new. `recover_stale_leg` already
refused `TOO_YOUNG` below `recover_min_rest_s`; the requote path had **no
rest floor at all** and could cancel a quote one second after placing it.
`REQUOTE_MIN_RUNWAY_S` measures time left in the *window*, not the order's
own age.

The value is the **fit set's median fill time, 14.5s, rounded down to
14.0** — a distributional statistic, deliberately *not* a threshold fitted
to the individual cancels it would prevent. Choosing 25s to clear the three
complement cancels at 13.6/19.5/21.0s would be fitting to three points.

Those three are handled by **PRESERVE (PR #202)** instead, which needs no
timer: a resting quote has the same worst case as holding and strictly
better upside, so cancelling it is never an improvement. Verified against
all three historical prices —
`test_the_controller_now_preserves_the_complements_we_cancelled` — including
the 0.97 pair, since the opening ceiling gates *submitting* a completion,
not continuing to hold one.

The money path passes the order's own age from the row's submit stamp; an
unparseable stamp returns `RESTING_AGE_UNREADABLE` and **refuses**, which
leaves the quote resting — the safe direction.

**The floor cannot override a mandatory deadline.** The TTL and terminal
deadlines still fire, and the curve says that costs nothing.

## 6. Exposure sufficiency: simultaneous fills, cancellation races, restarts

Driven against the real executor and store. **No demonstrable gap remains**,
and each is pinned to the control that actually refuses:

- **Simultaneous fills** — both sides filling *pair*, so the reservation
  takes `max(up_live, dn_live)`, not the sum (which would double-charge a
  pair) and not the signed net (the 1415z error). An unequal pair of fills
  leaves a gap that is reduced, not opened into.
- **Cancellation races** — `ValidationGates.gate`'s own `book_uncertain`
  test refuses every submission while the ledger cannot say whether an order
  is live (`PAIR_UP:BOOK_UNCERTAIN:…`, 0 sent). A cancel that filled anyway
  appears as inventory and drives a reduction; the vanished order cannot net
  it away. This is **not** duplicated into the reopening gate — a second
  opinion there could drift from the one enforced.
- **Restarts** — a fresh executor re-reads inventory from the volume and
  reduces it. With the claim files unreadable *and* inventory held, both
  rules combine correctly: the reduction proceeds, and a restart with no
  inventory cannot open blind.

## 7. What remains unknown

1. **The fill curve away from our own choices.** These 34 orders were placed
   by a policy that chose when and where to quote — mostly 2 ticks behind a
   one-tick touch, in BTC15m, in three campaigns over six days. This is the
   fill curve *for those choices*. It extrapolates to no depth we have never
   quoted at, and `TICK_DISTANCE_AND_FILL_LATENCY.md` already records that
   we have never quoted beyond 2 ticks.
2. **Post-fill adverse selection, per fill.** Measuring it needs the book
   path after each individual fill, which the preserved tapes do not carry
   for most orders. What we have instead is the *realized* residual
   settlement: 4 of 5 residual legs settled worthless (n=5, far too small to
   carry a model). The recorded-book estimate of −$0.19 to −$0.25 per leg
   remains the only figure with any sample behind it, and it is derived from
   PROVEN fills, so it is biased toward the adverse case by an **unmeasured**
   amount.
3. **Whether the measured 72.7% pair rate survives more patience.** It is
   computed over 11 rounds, 3 of which we spoiled ourselves. Removing our
   own interference should raise it, and that is a prediction, not a result.
4. **Queue position within a price level.** `queue_ahead` is an aggregate
   depth bound, never a position; `l5_queue_depletion` exists to say so.

## 8. The smallest genuinely necessary next experiment

**One BTC15m window, maker-only, $1 maximum worst-case exposure, with the
patience floor active and no discretionary cancel permitted inside 14s.**

It is necessary because items 1 and 3 above cannot be closed from any
recording: both require resting *our own* orders and observing whether they
fill without the touch crossing us. It is sufficient because a single window
produces 2–4 orders, and the quantity being measured — did a quote left
alone for its full TTL fill — needs only that.

It is also the smallest thing that could *falsify* the change: if quotes
left alone for 14s do not fill more often than the ones we cancelled, the
floor is wrong and should be removed.

**Expected cost: $1.** Requires owner authorization. **Not requested here.**
