# Proposed next live validation — for authorization, not authorized

**Nothing here is armed and nothing here is a request to arm.** The service is
disarmed (`armed false`, `owner_arm_token false`, approval expired) and stays
that way until a fresh, explicitly bounded authorization says otherwise.

## What the repair changed, and what that leaves unknown

Three defects are fixed and parity-verified:

1. the completion gate refused a **non-negative** pair, because the 0.9532
   settled target was used as a ceiling;
2. completions were priced at the **opening** depth, so 19 of the campaign's
   21 orders rested two ticks behind the touch;
3. a cancelled-unfilled completion could not be **re-placed**, because the
   granted attempt never reached the client order id.

What the repair cannot establish from recorded data: **whether a complement
resting at the touch actually fills.** PMUS publishes no print endpoint —
`/trades`, `/history`, `/ohlc`, `/candles`, `/price-history` all 404, leaving
only a cumulative `sharesTraded` lower bound — so joint maker-fill probability
is not measurable from replay. That is the only reason to go live at all.

## Stage A — mechanism validation (the cheap, decisive step)

**Question: does the repaired engine behave correctly when inventory is
unmatched?** This does NOT require a fill, which is why it is cheap and why it
can pass or fail on a handful of windows.

| | |
|---|---|
| venue / market | PMUS, BTC 15-minute UP/DOWN only |
| windows | **4** |
| concurrency | one market, one resting order |
| exposure | **$20** total, unchanged |
| per-market worst-case deficit | **$3.00**, unchanged |
| daily settled-loss halt | **$5**, unchanged |
| execution | maker-only, post-only |
| taker + defensive crossing | **disabled**, unchanged |
| opening pair cap | **0.96**, unchanged |
| completion hard ceiling | **1.00** (the repair) |
| completion depth | **0 ticks** — at the touch |
| two-sided resting | **NOT enabled** (`L5_TS_TWO_ORDER_PAIR` false) |

Four windows because only **1 of 3** produced an unmatched leg last time, so
four gives a reasonable chance of one or two — and one is enough for a
mechanism test.

### Pass criteria, all mechanical and all observable without a fill

When an unmatched leg exists, for every 5 s decision tick until the window
closes:

1. a completion is **submitted at or inside the touch**, not behind it
   (compare `own_px` against the book in the same presubmit record);
2. after a cancellation with inventory still unmatched, a **re-placement goes
   out** under a new id in the same round, bounded by the 4-placement budget —
   `order_resting` must not read `false` for minutes on end as it did on
   1445z;
3. every tick that does **not** submit records an **auditable reason**
   (`PAIR_ABOVE_REDEMPTION`, `ALREADY_RESTING_AT_OR_BETTER`,
   `ECONOMICS:*`, `STALE_BOOK`, `PAST_TERMINAL`, …);
4. **no** submission at a pair above 1.00; **no** taker order; **never** two
   resting orders; attribution reads `CONTROLLER` for controller-opened
   inventory.

### Fail / halt conditions — stop openings and investigate

* a completion rests >120 s with neither a fill nor a reprice;
* any submission at an effective pair above 1.00;
* two orders resting simultaneously;
* a coid outside the expected deterministic namespace;
* attribution reads `MIXED` unexpectedly;
* the watchdog reports `unverified` or `not_ours` non-empty.

### What Stage A costs

Worst case is one stranded leg per window at the observed clip: 4 × ~$0.71 ≈
**$2.84**, inside the $5 daily halt and well inside $20. The realistic case
is better, because the repair exists to stop exactly that.

## Stage B — economics, and only on a separate authorization

**Question: does improved completion produce positive settled net P&L?**
Metric: **matched pairs per filled first leg**, which is 0/1 today.

This needs a rate, and a rate needs events. At the observed 1-in-3 incidence
of unmatched legs, distinguishing (say) 0.3 from 0.7 with any confidence needs
on the order of **8-10 unmatched-leg events, i.e. roughly 24-30 windows.**

**I am not proposing that now.** It is several times the exposure of anything
authorized so far, and it is only worth spending after Stage A proves the
mechanism works. Stated here so the cost of the real question is visible
rather than discovered later.

## The separate question: two-sided resting

Measured on the recorded books, pre-positioning both legs from one book gives
a pair of **0.95** on a 1-tick book — inside the 0.96 cap and inside the
0.9532 target — against **1.0000** and **0.9700** achieved sequentially,
because the complement got dearer while we waited for leg one (+5 ticks over
47 s on 1430z, +2 ticks over 12 s on 1445z). Conditional on both filling that
is +$0.0955 per pair against +$0.0000 and +$0.0573.

It costs capital (~$1.81 committed when both fill, against at most one leg
today) and raises the worst single-leg residual from −$0.7067 to −$1.1078,
both still inside the $3 deficit. The mechanism already exists, disarmed, in
`second_leg_slot_refusal`, bounded to one opposite-side leg of the same round
with every existing gate still pricing both as filled.

**It requires two resting orders, which the approved scope caps at one, so it
needs its own authorization and is not part of this proposal.** The price case
is measured; the fill case is not. If it is ever tested, the metric must again
be matched pairs per filled first leg.

## What this proposal deliberately does not ask for

No capital increase. No crossing. No cap or threshold change. No concurrency.
No new service, strategy or research program. No two-sided resting.

## The honest ceiling

Incentives were **82%** of L5's measured economics ($559/day against
$122.71/day of trading cash) and we are credited **$0**. At L5's own trading
margin, $480/day implies **$2.33M of daily turnover, 39x his**; at our own
measured pair margin it implies **~161 matched contracts per window against
4.6 achieved**. So this validation is about stopping a **−$0.7067 leak** and
making matched pairs reachable at non-negative cost. It is not a path to
$480/day, and the question that is — whether any PMUS maker incentive is
creditable to this account — is read-only and should be answered before any
scaling work.
