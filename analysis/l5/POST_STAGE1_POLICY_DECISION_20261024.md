# Post-Stage-1 profitability decision: A / B / C / D

**2026-10-24.** Stage 1 closed and reconciled at **−$3.7194** realized net.
No live authorization exists and none is requested by this document. Nothing
here changes a trading policy; the only thing deployed alongside it is
shadow-mode instrumentation.

---

## 1. The defect is arithmetic, not statistical

The engine rests both legs `d` ticks behind the touch (`L5_TS_DEPTH_TICKS`,
deployed value 2) on a book whose spread is one tick `t = 0.01`. Write the
bid `b`, so the ask is `b + t`.

**What the plan prices.** Both legs are quoted off ONE snapshot:

```
UP leg own      = b − d·t
DOWN leg own    = 1 − (ask + d·t) = 1 − b − t − d·t
planned pair    = 1 − t − 2·d·t          = 0.95 at d=2
```

**What the fill can deliver.** An UP bid at `b − d·t` only fills when the bid
*reaches* `b − d·t`. At that instant the ask is `b − d·t + t`, so:

```
DOWN complement own = 1 − (ask_new + d·t) = 1 − b − t
pair AT THE FILL    = (b − d·t) + (1 − b − t) = 1 − t − d·t   = 0.97 at d=2
```

**Both expressions are independent of the price level — `b` cancels.** And
the pair ceiling is `0.96 = 1 − 4t`, which sits **exactly one tick between
the two**:

| depth_ticks | planned pair | pair at the fill | ≤ 0.96 cap at the fill? |
|---|---|---|---|
| 1 | 0.97 | 0.98 | no |
| **2 (deployed)** | **0.95** | **0.97** | **NO — refused** |
| 3 | 0.93 | 0.96 | exactly at the cap |
| 4 | 0.91 | 0.95 | yes, one tick of slack |

So the entry rule is permitted by a pair price that the act of filling
destroys. The strategy quotes a 5-tick pair, gets filled on a 3-tick pair,
and refuses anything above 4 ticks.

### Confirmed on production records, four times

Every stranded window logged a frozen complement that sums with its first
leg to **exactly 0.9500**:

| Window | `first_leg_cost` | `frozen_price_superseded` | planned pair |
|---|---|---|---|
| 1500z | 0.4400 | 0.5100 | **0.9500** |
| 1530z | 0.4000 | 0.5500 | **0.9500** |
| 1545z | 0.1900 | 0.7600 | **0.9500** |
| 1515z | 0.3000 | — (completed at 0.92) | **0.9500** |

And the 1500z at-fill pair computes to 0.9700 against the real book
(0.44/0.45), which is what the ring replay produced independently.

---

## 2. Option B — faster event-driven complement evaluation: **REJECTED on data**

B was examined first, as instructed. The complement is currently re-priced
about every 33 seconds (nine evaluations in 1500z, gaps 24.4–36.5s), and the
first look after the 1500z fill came 30.07s late. That sounds like the
problem. It is not.

**The minimum pair cost ever achieved after a fill, per window:**

| Window | min pair observed | cap | shortfall |
|---|---|---|---|
| 1500z | 1.0400 | 0.96 | 8 ticks |
| 1530z | 1.0400 | 0.96 | 8 ticks |
| 1545z | **0.9800** | 0.96 | 2 ticks |

**No stranded window ever came within two ticks of the cap, at any observed
instant.** A faster evaluation cadence can only capture an opportunity that
exists; there was none to capture. The best the pair is ever going to be is
`1 − t − d·t = 0.97` at the moment of the fill, and that is already outside
the cap before any latency is counted — the first post-fill observation in
every window was *worse* than 0.97, never better.

B therefore addresses latency when the binding constraint is tick geometry.
Its residual value is capturing retraces shorter than the cadence, which is
a second-order effect on a distribution nobody has measured. **Not the
answer, and cheap to say so definitively.**

---

## 3. Option C — two concurrent maker orders: **REJECTED on worst-case modelling**

C is the only option that can realize the planned 0.95, because that price
requires both legs resting simultaneously, which `max_live_orders = 1`
forbids. The cash cost is already paid: the N=2 audit (`1b5ccb45`) found the
cash obligation does not rise at N=2, because the gate already reserves both
legs whether they rest together or in sequence.

**But the worst case is not inventory, it is exposure breadth.** Today one
resting leg can only be hit by a move in one direction. Under C we rest a bid
below the market *and* an offer above it, so:

- market falls → the bid fills → long the falling side → strand
- market rises → the offer fills → short the rising side → strand
- market oscillates across the 5-tick band → both fill → pair locked at 0.95,
  **+$0.05 per pair**

C converts *"no fill, no loss"* into *"one fill, probable loss"* in the
direction we currently decline to quote. The measured unit economics make
that trade hostile:

```
strand        −$0.3062 per contract
locked pair   +$0.0500 per pair  (+$0.0250 per contract-leg)
ratio         ~12 : 1 against
```

So C needs `P(both legs fill)` to exceed roughly **12×** `P(newly-exposed
one-sided fill)` merely to break even. Stage 1 had **3 of 8 windows with no
fill at all**; under C a share of those become one-sided strands. On this
sample that plausibly costs more than the oscillation case earns — and note
the shadow controller's own prior production measurement of touch placement:
**70% adverse at −$0.0467 a unit against +$0.0050 of captured spread**. The
adverse term dominates there too.

There is also a second-order cost: on the one window that did complete,
1515z, C would have locked 0.95 (+$0.15 on 3 contracts) where the sequential
policy actually captured a 0.92 pair (**+$0.2400**) by waiting for a deeper
retrace. C is not strictly-better upside; it trades a rare good price for a
more reachable mediocre one while widening adverse exposure.

---

## 4. Option D — and the ONE recommended change

### RECOMMENDATION: align resting depth with the completion cap — `L5_TS_DEPTH_TICKS` 2 → 4

This is the single change with the greatest credible improvement in net
expectancy per dollar of risk, for five reasons:

1. **It repairs a proven inconsistency rather than betting on a
   distribution.** At d=4 the pair at the fill is `1 − 5t = 0.95`, one tick
   inside the cap. The complement becomes *submittable at the moment of the
   fill* instead of refused. In four of five fill-bearing windows **no
   complement order was ever submitted at all** — this is the change that
   makes one submittable.
2. **It improves strand economics unconditionally.** The first leg is two
   ticks cheaper, so every stranded contract loses $0.02 less. On Stage 1's
   12.93 stranded contracts that is **+$0.26** against the −$3.9594 residual
   line, independent of whether any completion rate improves.
3. **It is one existing environment variable.** No code, no new inventory
   state, no concurrency, no cancellation race, no new failure mode. It can
   be reverted by changing one value.
4. **Risk scales down with it.** Resting deeper fills less often, so dollars
   at risk fall with opportunity count. Expectancy *per dollar of risk* is the
   right frame and frequency largely cancels — which matters because current
   expectancy per fill is negative, so fewer, more completable fills is the
   direction of travel.
5. **The depth dimension is already pre-registered in the shadow harness.**
   `ShadowController` carries `placement`/`offset_ticks` with an explicit
   discipline that the offset "is not searched, not tuned, and not re-chosen
   after results". d=4 is *derived* from the identity `1 − t − d·t ≤ 0.96 ⇒
   d ≥ 3` plus one tick of slack. It is not fitted to any result.

### The honest risk, which the validation plan must measure

Resting deeper selects harder for large adverse moves, because only a bigger
move reaches us. The retrace needed to fill the complement also grows to `d`
ticks. So d=4 could worsen the very adverse selection it is meant to survive.
**That is the one thing that would invalidate this recommendation**, and it
is measurable before any money moves.

### Why not D-as-smaller-legs

Smaller opening legs scale the pair term and the strand term together and
leave the shortfall ratio untouched. Break-even currently needs **7.66 paired
contract-legs per stranded contract**; Stage 1 achieved **0.464**, a 16.5×
shortfall. Size does not move that ratio; completability does.

---

## 5. Bounded validation plan — shadow first, no capital, no new infrastructure

**Stage 0 — shadow only, $0 at risk. No authorization required beyond this.**

Already deployed with this document: the book-evidence recorders are wired to
the shadow fill and modelled-inventory streams
(`L5_SHADOW_FILL_BOOK_EVIDENCE`, `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`), on their
own sampler instance so shadow traffic cannot move the money path's counters.
A modelled fill passes `venue_event_t=None` deliberately, so its event-time
book reads `NO_VENUE_EVENT_TIME_SUPPLIED` rather than borrowing our clock.
This exercises the two paths the owner correctly flagged as unvalidated.

**To run (needs no new service and no new branch):** add one pre-registered
placement arm at four ticks behind the touch, beside the existing `AT_TOUCH`
and `ONE_TICK_BEHIND` arms, and let the existing 3 fill scenarios score it.
Justification is the tick identity above, recorded before the run.

**Sample size:** ≥ 200 BTC15m windows (~2 days) before reading anything. Five
windows is what produced the current ambiguity and will not be repeated.

**Per arm, report:** modelled fills; completion rate; adverse-fill fraction
and $/unit; stranded count and cost; queue-ahead at placement and the
unknown-queue fraction; and net per window. Scored queue-aware, **no
touch=fill**, fees included, **confirmed rebates only — currently exactly
$0.00 credited** — and residual settlement priced at the venue's own
resolution.

**Pre-registered promotion rule.** Promote d=4 to a bounded live probe only if
all three hold:

- (a) completion rate materially above the d=2 arm, with non-overlapping
  intervals;
- (b) net per window better than the d=2 arm;
- (c) adverse-fill fraction **not worse** than the d=2 arm.

Any one failing → do not promote, and report which.

**Stage 1 (only on a pass, and only with separate written authorization):** a
bounded live probe at the **same** envelope Stage 1 used — $5 cash, $5
account-wide exposure, 3-contract opening cap, maker-only, one market, one
live order, 8 windows, $5 daily and $20 cumulative halts — changing **exactly
one variable** and nothing else. No capital increase.

**What would make me withdraw this recommendation:** the d=4 arm showing a
higher adverse-fill fraction, or a completion rate indistinguishable from
d=2. Both are visible in Stage 0 without risking a dollar.

---

## 6. What is still not known, stated plainly

- Whether the venue serves an order-level timestamp at all. Until a real fill
  occurs under instrumentation, `book_at_event` will read
  `NO_VENUE_EVENT_TIME_SUPPLIED`, and no claim of fill-instant synchronization
  is made. Ring frame boundaries are our receipt clock
  (`FRAME_IN_EFFECT_BY_LOCAL_RECEIPT_CLOCK`, `offset_measured: false`).
- The retrace-duration distribution, which is the only thing that could
  rehabilitate option B.
- `P(both legs fill)` versus `P(newly-exposed one-sided fill)`, which is the
  only thing that could rehabilitate option C. Stage 0 measures both, so
  rejecting them now costs nothing permanent.
