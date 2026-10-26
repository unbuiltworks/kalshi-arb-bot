# The strand exit that the entry cap refuses, and what must be fixed before
# `max_live_orders=4`

**Read-only. Venue-confirmed fills and real venue books from production
`l5-pmus-live` logs. Nothing armed, submitted, cancelled or deployed; no limit
changed.** `src/l5_strand_completion_counterfactual.py` (11 tests),
`src/l5_residual_reachability_census.py` (6 tests).

---

## PART 1 — 100% of realized losses involve a strand

Seven venue-confirmed windows, 14 fills each with a `venue_order_id`. Totals
re-derived independently here and reproducing `L5_ECONOMIC_DECOMPOSITION.md`:

| character | windows | realized |
|---|---|---|
| full strand (loss = **exactly** acquisition) | 1300z, 1945z | **−$1.4802** |
| partial strand | 1245z, 1345z | **−$1.0146** |
| completed pair | 1315z, 1330z, 1400z | **+$0.3500** |
| | | **−$2.1448** |

**Every loss involves a strand; every completed pair made money.** The
separator is completion versus strand, not price — losses are not monotone in
entry price, and a full strand costs ~$0.80 whatever the entry price, because
clip = 0.80/p.

| | |
|---|---|
| mean completed-pair margin | **+$0.1167** |
| mean strand loss | **−$0.6237** |
| **break-even strand rate** | **15.8%** |
| observed strand rate | **4 of 7 = 57.1%** |
| per-window realized | −$0.3064 |

A completed pair earns about a ninth of what a strand costs, so the strategy
needs a strand rate under ~16% and is running at 57%.

## PART 2 — the exit was available and the entry cap refused it

`cpc-btc-updown-15m-2026-10-25-1945z` is the one full strand with book data:
**121 `L5_RESIDUAL_BOOK_SAMPLE` rows** carrying the ACTUAL filled inventory
(1.56 UP at own cost 0.44, acquisition $0.6864) and the real venue book every
5 s from the fill to window close.

| | |
|---|---|
| taker completion executable | **109 of 121 instants (90.1%)** |
| residual sellable into displayed bids | **109 of 121 instants** |
| instants with no bid at all | 12 (all at window close) |
| taker pair own cost | min **0.99**, max 1.43 |
| **ever at or below the deployed 0.96 cap** | **never** |

Selling the held UP at bid `b` and completing by buying DOWN at own cost
`1 − b` are the **same trade** up to fees — the module asserts the identity —
so this is one action, not two.

### Causal rules, no hindsight, no assumed maker fill

Each rule fires at the first instant its own condition is true, using only
information available then. Completion is priced by **taker walk against
displayed depth** for the exact quantity needed; a maker touch is reported and
never counted as available.

| causal rule | fires | settled P&L | vs actual |
|---|---|---|---|
| pair <= 0.96 — **the deployed cap** | **never** | −$0.6864 | $0.0000 |
| pair <= 0.99 | 204 s into the window | **−$0.0144** | **+$0.6720** |
| pair <= 1.00 | 120 s into the window | −$0.0300 | +$0.6564 |
| exit at the last executable instant | 819 s | −$0.6808 | +$0.0056 |

**The defect is a category error, not a missing feature.** `0.96` is an ENTRY
profitability criterion. Applied to inventory already held it asks "can I lock
a profit", and the honest question for a residual is "is exiting better than
holding something that may settle worthless". The engine evaluated the
complement **twelve times** and logged
`TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP: 12` — it was working exactly as
configured, and the configuration had no exit.

Note what the recovery is and is not: it converts −$0.6864 into −$0.0144. It is
a **near-breakeven exit, not a profit** — the best bid reached 0.45 against our
0.44 basis and the fee ate the cent. And the value is entirely in acting early:
waiting to the last executable instant recovers $0.0056 of $0.6864.

### What is measured and what is not

- **Measured, one window:** +$0.6720 on 1945z, from real inventory and real
  books, taker-only.
- **NOT measured:** whether the other three strand windows had comparable
  exits. Their `L5_RESIDUAL_BOOK_SAMPLE` rows do not exist — the sampler was
  not emitting then — and the date of the 1245z–1400z block is not established
  from the records read here. The 2026-10-20 occurrence of those slugs was
  `arm_refused_shadow_only_lock: 264`, `armed: false`, and did not trade, so it
  is not that block.
- **Arithmetic only, clearly labelled as an assumption:** if every one of the
  four strands had exited at 1945z's measured level, the seven windows would
  total **+$0.2924** instead of −$2.1448, a **+$2.4372** swing (+$0.3482 per
  window). That is an extrapolation from n=1, not a result.

## PART 3 — the slot-gate verification, required before `max_live_orders=4`

Audited against the **deployed** commit `497b1152`, not the branch head.

### PASS — resting-order commitments are counted correctly

`pending_obligation` charges **every** resting order at full worst case:
`qty * price` for a long, and `qty` (the whole $1 a share) for a resting short.
`outstanding_for_gates()` is account-wide over `store.live_orders()`, prices an
unreadable price at the worst case and an unreadable side as a short. The pair
path gates both legs **as if both were already resting**, and the campaign
budget shares the same two functions, so the gate's own comment holds:

> *"a CONCURRENT order is counted because `outstanding` is counted at its full
> worst case; a PENDING commitment is counted because `cash_committed` counts
> an unsettled leg in full and skips only SETTLED ones; and RESIDUAL exposure
> is counted for exactly the same reason."*

On the cash axis, raising the slot count genuinely **buys a slot, never
headroom.**

### BLOCKER — in the deployed code the "daily" loss ceiling is not daily

Both the daily and the cumulative ceiling read the **same post-baseline
cumulative figure**, so the ceiling named DAILY never resets. The separation
exists but is one of the **46 commits not deployed**. More slots consume a
one-way budget faster, and when it is gone the engine is halted until an owner
raises the limit — there is no daily recovery in production today. This is also
the gate that refused 2000z and 2015z outright
(`L5_DAILY_LOSS_LIMIT:5.245700>=5`).

### QUALIFIER — the fee model's unmodelled tail is bounded by the order count

`fee_worst` says so itself:

> *"if the venue rounds per FILL rather than per order, an order that fills in
> n pieces pays up to (n-1) extra cents... the exposure is bounded instead by
> the order count: with max_live_orders at 2 the worst unmodelled rounding is a
> few cents."*

Raising 1 → 4 widens precisely the thing that bounds it.
`fee_extra_per_order_usd` is $0.01 and exists to price it; at four slots that
allowance should be re-derived rather than inherited.

### SCOPE NOTE — the residual gate is per-market

`unmatched_after=self.store.residual(market)` is scoped to one market. Correct
under the current one-market discipline, where per-market equals account-wide.
It becomes a gap at the CONCURRENCY stage of PATIENCE → FREQUENCY → SIZE →
CONCURRENCY.

## The single change with the largest defensible improvement

**A residual-exit rule, separate from the entry pair cap.** It is the only
candidate with a measured net-dollar improvement on real money (+$0.6720 on the
one window with data), it needs no new capital, no new limit, no new service
and no maker-fill assumption, and it addresses the category that accounts for
100% of realized losses.

It should be built as the user's categories 3 and 4 — *complementary orders
reducing residual imbalance* and *emergency exposure reduction* — explicitly
**not** gated by the 0.96 entry criterion, and bounded by the existing hard
loss, exposure and watchdog controls.

`max_live_orders=4` remains the right structural change for reproducing L5's
resting pattern, but it is **second**, and it is blocked until the
daily-versus-cumulative separation is actually deployed.

**Unchanged: cap 0.96, `max_live_orders` 1, every risk limit, `armed=false`.**
No live order. Nothing here is realized profit; the only venue-confirmed
numbers are the seven settlements and the 1945z books.
