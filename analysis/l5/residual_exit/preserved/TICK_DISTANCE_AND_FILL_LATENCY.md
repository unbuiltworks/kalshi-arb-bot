# Tick distance and fill latency: what our own live orders show

**Two findings. (1) We have never quoted beyond 2 ticks, so the owner's
2–5 tick observation is untested by us, not contradicted. (2) The recovery gate
cancels INSIDE our own fill-latency distribution, which is a quantified
patience problem independent of whether the two cancelled orders would have
filled.**

Live orders only (`l5f-` coids; dotted shadow coids excluded), from the
preserved tapes. Tick distance is measured from the **own-side best bid** —
`book bid` for an UP order, `1 − book ask` for a DOWN order — at the instant of
submission, which is the price convention the planner itself uses.

| window | side | role | own | own touch | ticks | queue ahead | outcome | submit→terminal |
|---|---|---|---|---|---|---|---|---|
| 0115z | DOWN | first leg | 0.24 | 0.25 | 1 | 760.00 | FILLED | 17.1 s |
| 0115z | UP | **complement** | 0.70 | 0.72 | 2 | 6357.09 | **CANCELED** | 21.0 s |
| 0130z | UP | first leg | 0.29 | 0.31 | 2 | 5934.32 | FILLED | 18.8 s |
| 0130z | DOWN | complement | 0.56 | 0.58 | 2 | 4422.01 | FILLED | 14.5 s |
| 0145z | DOWN | first leg | 0.35 | 0.37 | 2 | 1468.79 | FILLED | 5.5 s |
| 0145z | UP | **complement** | 0.61 | 0.63 | 2 | 5477.04 | **CANCELED** | 13.6 s |
| 0200z | UP | first leg | 0.35 | 0.37 | 2 | 4236.16 | FILLED | 23.5 s |
| 0200z | DOWN | complement | 0.59 | 0.61 | 2 | 7778.94 | FILLED | 10.8 s |

**Fill latencies: 5.5, 10.8, 14.5, 17.1, 18.8, 23.5 s** (6 of 8).
**Cancellations: 13.6 and 21.0 s** (2 of 8, both complements).

## 1. The 2–5 tick region is unsampled

`L5_TS_DEPTH_TICKS` is 2, so the planner quotes exactly two ticks behind the
own-side touch on every order. Every observed distance is 1 or 2 (the single
1-tick case is quantisation). **There are zero live observations at 3, 4 or 5
ticks**, so nothing in our data speaks to the owner's manual experience either
way. Confirming or refuting it requires quoting there, which is a live change
and is not proposed here.

What our data does say about 2 ticks: **6 of 8 orders filled, median latency
about 16 s, and both failures were complements cancelled by the recovery gate
rather than orders that sat unfilled to expiry.** At 2 ticks, fills are the
normal case on this venue — which is consistent with the owner's report rather
than against it.

## 2. The recovery gate fires inside the fill-latency distribution

The gate needs `rest_s >= 15.0` and drift `> 4 ticks`. Verified on the tape:
0115z fired at `rest_s 16.62`, 0145z at `rest_s 16.79` — both compliant, and an
earlier reading of mine that suggested a 9 s firing was wrong, because the rest
clock runs from the stored submit time rather than the log line.

But compliant is not the same as patient:

* **2 of 6 fills (33%) arrived after 17 s** — at 18.8 s and 23.5 s.
* The longest fill, **23.5 s**, is longer than *both* cancellations.
* So an order cancelled at ~17 s of rest is being cancelled at a rest time
  where a third of our observed fills had not yet arrived.

**This is a demonstrated risk, not a realized cost in these two cases.** Both
cancelled complements are separately proven unfillable by the post-cancel bid
path (`COMPLEMENT_CANCELLATION_CHECK.md`): 0115z's UP at 0.70 never saw a bid
below 0.73, 0145z's UP at 0.61 never below 0.65. Keeping them resting would
have changed nothing. The overlap says the gate *could* cancel a fillable
order, not that it has.

## Pre-specified candidates for the frozen-holdout evaluation

Registered here before any run, so there is no threshold search to argue about:

1. **`min_rest_s` raised above the observed fill-latency distribution** — e.g.
   to 30 s, beyond our longest observed fill of 23.5 s. Smallest possible
   change: one configurable constant, already `max()`-guarded to move in the
   safe direction only.
2. **Complement recovery disabled entirely**, first-leg recovery unchanged.
   Justified by mechanism rather than by these outcomes: after a complement is
   cancelled, the replacement must still satisfy the pair cap against a moved
   book, which in 2 of 2 live cases was impossible — so the cancel spends the
   per-market budget for nothing while discarding whatever option value the
   resting order held.
3. **Persistent resting** (no drift cancel while still economically eligible),
   scored against current cancel/reprice.
4. A **2–5 tick ladder**, which cannot be evaluated on our live data at all and
   would need the frozen replay plus an explicit queue model.

Each must be scored on realized-equivalent net P&L, pair completion, residual
losses and drawdown — with queue-ahead respected, traded volume required to
clear it, no touch scored as a fill, maker fees charged, and the forfeited pair
profit charged on windows that later completed.

**More fills are not more profit.** 0145z's first leg filled in 5.5 s, the
fastest fill of the campaign, and that window produced its largest loss
(−$0.3605).

Nothing changed. `MAKER_ONLY`, `max_live_orders: 1`, `depth_ticks: 2`, cap
0.96, both halts live.
