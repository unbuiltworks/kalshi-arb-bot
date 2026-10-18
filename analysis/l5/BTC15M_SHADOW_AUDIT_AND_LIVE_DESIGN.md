# BTC15m shadow assumption audit, and the smallest live validation

READ-ONLY. Nothing activated, deployed or ordered. System shadow-locked.
This is an audit plus a PROPOSAL requiring explicit approval.

## 0. The correction I owe first

I have been reporting modelled non-fills as if they were evidence that
L5-style BTC15m trading cannot work. That was wrong. A conservative
execution model returning zero fills is a statement about the model, not
about the venue, and the two have been conflated — including by me, in
`FEE_RECONCILIATION.md` and the §90 waterfall language.

The audit below finds the shadow is **pessimistic on fills and optimistic
on fees at the same time**, so its net is not conservative in a single
direction and cannot be read as a lower bound at all.

## 1. Every venue-confirmed BTC15m fill we have

16 established legs, 21 fills, qty 64.59, notional $14.2075, fees $0.3500
(2.4635%). One leg (`2026-10-15-1930z|UP`) remains UNESTABLISHED and its
cost stays UNKNOWN.

| class | legs | basis |
|---|---|---|
| **TAKER** | **11** | fee equals `round_half_up(0.0695 q p (1-p))` exactly |
| ZERO | 4 | charged nothing, raw taker fee would have been $0.01–0.03 |
| ZERO (1 leg) | 1 | `2230z\|UP`, raw taker $0.03, charged $0 |
| **CREDIT** | **1** | `1230z\|UP`, charged **−$0.0100** |

**69% of our venue-confirmed BTC15m fills were TAKER-priced.** We have
**6 maker fills in total**, and no record of how many passive orders were
placed to obtain them — a numerator with no denominator. Our own history
therefore contains *no* measurement of a passive fill rate in this lane.

## 2. Assumption audit

| # | assumption | status | evidence |
|---|---|---|---|
| 1 | taker fee `round_half_up(0.0695 q p (1-p))` | **MEASURED** | 28/28 account-wide single-fill legs; 11/11 BTC15m |
| 2 | maker fee `MAKER_THETA = -0.0125` (a credit) | **MEASURED, supported** | predicts 5 of the 6 BTC15m maker legs EXACTLY, incl. the −$0.0100 credit; errs +$0.01 optimistic on one |
| 3 | matched pair pays exactly $1.00 | **MEASURED** | identity, and `settlement_net` implements it |
| 4 | fill when cumulative qualifying volume > displayed `queue_ahead` | **INFERRED** | displayed depth is an UPPER bound on the queue (the function's own docstring) and ignores cancellations ahead of us, which would fill us sooner. Never once checked against a venue-confirmed passive fill |
| 5 | a price not among the **4 displayed rungs** NEVER fills | **UNSUPPORTED, and biased the wrong way** | zero displayed depth means zero queue — the FASTEST fill, not the slowest. Beyond 4 rungs the ladder tape is silent, not negative. A `<= 0.96` pair late in a window requires quoting far outside 4 rungs, so this rule alone can force a structural non-fill |
| 6 | fills are all-or-nothing at the full clip | **CONTRADICTED** | 16 legs produced 21 fills; one leg filled in 5 pieces. Partial fills are real and unmodelled |
| 7 | our resting legs are makers | **CONTRADICTED** | 11 of 16 BTC15m legs were taker-priced |
| 8 | residual settles at the venue's `yes_value` | **CORRECT BUT NEVER INVOKED** | `settlement_net` prices it properly; `yes_value` is passed None, so `net_usd` is null on all 97 windows. My previous claim that "the pricing step was never run" was wrong — the step exists and is starved of one input |
| 9 | adverse selection on the residual | **UNMEASURED FOR US** | I borrowed L5's 2.2773%. We have exactly one settled BTC15m residual (`2230z\|UP`, −$0.59, settled worthless) |
| 10 | quote is held for the window; repricing never fires | **UNTESTED POLICY CHOICE** | `REPRICE:HOLD_PRIORITY` on 0 of 844 decisions. Queue loss after a reprice is not modelled at all |

**Net direction of the bias is unknown.** #4, #5 and #6 push the modelled
fill rate DOWN; #2 and #7 push modelled fees DOWN (more favourable). So the
97-window bounds are not a floor, and no amount of further shadow work
resolves #4, #5, #7 or #9 — they are only observable by resting real orders
and reading the venue's own `isAggressor`, `lastShares` and commission back.

## 3. Proposal: MPP-1, the smallest economically meaningful live test

**Purpose is measurement, not profit.** The deliverable is four numbers that
no shadow can produce: maker share, per-leg passive fill rate, partial-fill
distribution, and our own realized residual drag.

Uses the EXISTING executor, the existing durable `/data` ledger, the
existing watchdog and HALT. No new service, strategy, branch or volume.

**Mechanics, per window, one window at a time:**

1. One BTC15m market. Post **both** legs post-only at a combined pair cost
   `<= 0.96`, total cash `<= $4.65` → **Q = 4.84 shares** at 0.59/0.37.
2. Record per fill, from the venue's own rows: `isAggressor` (maker/taker),
   `lastShares` (partial fills), `lastPx`, `commissionNotionalCollected`,
   and place→terminal timestamps (order lifetime).
3. No repricing in MPP-1. One variable at a time; repricing is MPP-2.
4. At window close, **cancel the unfilled leg by exact venue id.**
5. **Hold any residual to settlement** — do not flatten. L5 never sells, and
   the residual's realized settlement is the drag we are trying to measure.
6. Reconcile: matched pays $1.00; residual at the venue's `yes_value`;
   realized net labelled only after settlement cashflow is confirmed.

**Unchanged safety:** `max_usd_per_market` $5, `max_total_exposure_usd` $5,
`max_concurrent_markets` 1, `max_abs_residual` 10 contracts (Q=4.84 is
inside it), `daily_realized_loss_halt_usd` $5, independent watchdog with its
own HALT authority and no ability to submit, durable authorization with a
venue-bounded deadline, one-shot submission claim per experiment id.

### Cost

| scenario per window | cost | net |
|---|---|---|
| both legs fill (matched) | $4.6464 | **+$0.2157** |
| UP only, settles right | $2.8556 | +$1.9844 |
| DOWN only, settles worthless | $1.7908 | −$1.7908 |
| **UP only, settles worthless (worst)** | $2.8556 | **−$2.8556** |

**The real cost bound is the existing $5 daily loss halt**: three
consecutive worst-case windows latch it and the day stops. So the downside
is **capped at ~$5/day by machinery already deployed**, not by my estimates.

| sample | orders | 95% half-width on a rate | elapsed |
|---|---|---|---|
| 20 windows | 40 | ±21pp | 5 h |
| **40 windows** | **80** | **±15pp** | **10 h** |
| 96 windows | 192 | ±10pp | 24 h |

**Recommended: 40 windows**, reached in ~10 hours of eligible windows or
across several days if halts intervene. Expected cost if the strategy is
break-even: ~$0 plus fees. Maximum cost: $5/day × days.

### What result would justify scaling

All four must hold, on settlement-confirmed data:

1. **Maker share ≥ 80%** of our filled passive orders carry
   `isAggressor: false`. Below that we are a taker and the 3.30% fee
   exceeds the 4.00% gross — scaling a negative. This single number can
   kill or clear the whole programme.
2. **Realized residual drag < 13.3%** of residual cost (the B25 tolerance).
   L5's own figure is 2.2773%; if ours is near his, there is room.
3. **Residual/matched ratio ≤ 0.78**, i.e. no worse than the shadow's B25.
4. **Realized net ≥ $0** over the 40 windows, settlement-confirmed, with no
   HALT latched for a cause other than a deliberate limit.

Fail any one → stop and report, do not re-size. Pass all four → the next
step is FREQUENCY, not size, per the standing promotion order
(PATIENCE → FREQUENCY → SIZE → CONCURRENCY).

### Gates before a single order — not bypassed

- **GATE 0: cancellation is still UNPROVEN.** Step 4 depends on cancelling
  an unfilled leg by exact venue id, and no order has ever been cancelled
  with a venue-confirmed terminal status. MPP-1 cannot run before that
  requirement is satisfied; I am not proposing to work around it.
- **GATE 1: explicit owner approval**, plus a fresh unused experiment id and
  a live authorization whose deadline covers the test window.
- **GATE 2:** shadow lock stays ON and the mechanism OFF until both above.

## 4. What this does NOT claim

MPP-1 cannot reach the $5/window objective and is not meant to. At $4.65 of
cash per window a fully matched window nets **$0.2157**. The objective needs
$364–$460 of committed capital per window under `NO_RECYCLING`. MPP-1's
whole purpose is to decide whether that capital would be deployed into a
positive or a negative margin — a question four measurements answer and no
further shadow iteration can.
