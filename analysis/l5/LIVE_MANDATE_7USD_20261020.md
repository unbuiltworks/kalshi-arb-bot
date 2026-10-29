# The $7 bounded live window: gates, crossing policy, remaining obstacles

Written against commit `b7eaedce` (running) plus this batch. **No order has
been submitted and none can be: the shadow lock is engaged and the
authorization is expired.**

## 1. Gates that require an explicit scoped authorization change

The deployed envelope is **$5**. The proposed mandate is **$7**. Every line
below is a RISK INCREASE and I have changed none of them.

| gate | deployed | needed for $7 | where |
|---|---|---|---|
| `max_usd_per_market` | `5` | `7` | SafetyLimits (env) |
| `max_total_exposure_usd` | `5` | `7` | SafetyLimits (env) |
| `daily_realized_loss_halt_usd` | `5` | `7` | SafetyLimits (env) |
| `max_cash_deployed_usd` | `4.65` | `6.50` | ValidationGates (env) |
| `max_unmatched_contracts` | `5` | `6` | ValidationGates (env) |

`max_cash_deployed_usd` must stay BELOW the $7 worst case, not equal it: the
gate charges a resting DOWN leg a dollar a share, so `6.50` sizes a pair whose
obligation is `$6.496` and whose worst single leg is under $7 at every book
observed. Setting it to `7.00` would size a pair whose stranded leg can exceed
the loss cap.

`max_unmatched_contracts` has to rise because the quantity rises: at the
0515z-style book a $6.50 envelope sizes **5.60 contracts** a leg, and 5 would
refuse the pair it just planned.

Unchanged and NOT to be touched: `max_live_orders=1`,
`max_concurrent_markets=1`, `max_abs_residual=10`, `NO_RECYCLING`, the
independent watchdog, the HALT latch, the submission claim, and the
preservation of `-$84.62` of unattributable history as UNKNOWN.

## 2. What $7 actually buys, measured

| book | envelope | qty/leg | pair | net if completed | worst single leg |
|---|---|---|---|---|---|
| mid (0330z) | $4.65 | 3.32 | 0.94 | +$0.2192 | **-$1.8528** |
| mid (0330z) | $6.50 | 4.64 | 0.94 | +$0.2984 | **-$2.5956** |
| extreme (0515z) | $4.65 | 4.00 | 0.95 | +$0.2200 | **-$3.2100** |
| extreme (0515z) | $6.50 | 5.60 | 0.95 | +$0.3000 | **-$4.4940** |
| even | $4.65 | 3.14 | 0.95 | +$0.1770 | -$1.5672 |
| even | $6.50 | 4.39 | 0.95 | +$0.2395 | -$2.1872 |

**Both sides scale together, so the ratio does not improve.** It stays about
**1 : 14** against -- one completed pair pays about a fourteenth of what one
stranded leg costs, at $5 and at $7 alike. Raising the envelope increases the
stake, not the edge, and it does not improve queue position: we are behind the
same thousands of contracts either way.

That is an argument for authorizing $7 only if the purpose is a larger
*measurement* sample, not a better expectancy. It is your call; I am not
making it.

## 3. B5: the crossing policy, presented for authorization

`max_completion_price` computes the dearest permissible completion from the
ACTUAL first-leg cost, the ACTUAL taker fee, the remaining inventory and an
approved residual budget. Four ceilings, four different questions:

For the worst stranded leg actually observed -- **DOWN 4.00 at avg 0.8025, a
-$3.21 worst case if carried** -- with a $7 residual budget:

| ceiling | price | meaning |
|---|---|---|
| `positive_both` | **0.18** | the completed pair pays on BOTH outcomes |
| `improves` | 0.95 | completing beats carrying the stranded leg |
| `within_budget` | 0.95 | the worst case stays inside $7 |
| `pair_cap` | **0.14** | effective cost per matched share stays <= 0.96 |
| **min of all four** | **0.14** | binding: `pair_cap` |

The market was offering **0.19**. So under the full set, **crossing would have
been refused** -- and under `positive_both` alone it is refused by ONE TICK.
That is the honest answer: at the one stranded case we have modelled, the
rescue was not available at a price that makes the pair pay.

**The policy I recommend you authorize**, rather than unrestricted crossing:

> Cross to complete only when the completed pair pays on BOTH settlement
> outcomes (`positive_both`) AND the worst case stays within the approved
> residual budget (`within_budget`), at the price actually executable with
> the actual taker fee, sized to the displayed depth. Do NOT apply the 0.96
> `pair_cap` to a crossing rescue -- that target is the discipline for
> OPENING a maker pair, and applying it here forbids rescues that strictly
> reduce loss. Never cross because the deadline is near.

Under that policy the observed case still refuses (0.18 < 0.19), which is the
right outcome and shows the policy is not a rubber stamp. `L5_TS_COMPLETION_CROSS`
stays **unset** until you authorize it; I have not enabled it.

## 4. Exact remaining obstacles to a bounded live order

| # | obstacle | whose | status |
|---|---|---|---|
| 1 | `SHADOW_ONLY_LOCK` engaged | mine | lifted by me only AFTER 2-4 are satisfied and preflight passes |
| 2 | owner arm handshake: `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` (equal), `L5_LIVE_AUTH_NOT_AFTER` | **yours** | **BLOCKING.** I do not read, generate or overwrite the token |
| 3 | the five limit raises in section 1 | **yours** | **BLOCKING** for $7. At $5 nothing needs changing |
| 4 | the crossing policy in section 3 | **yours** | optional; unset means a stranded leg is carried |
| 5 | fill probability at `depth_ticks=2` | mine | **not an obstacle to submitting, but the reason a live window may measure nothing.** Both legs rest behind 1,481-4,690 contracts at the same rung with `requires_price_to_reach_us: true` |

Not obstacles, each checked: HALT unlatched; preflight PASSED every window;
`-$84.62` unattributable history (`blocks_live_authorization: false`);
L5 realized `+$0.0276` against a $5/$7 halt; `$344.44` available cash;
watchdog sweeping.

## 5. The prepared window

Everything but 2 and 3 is ready. On your authorization I will, in this order:
confirm the running commit; reconcile every order and position BEFORE the
window; verify the watchdog is sweeping and the loss gates read the right
inputs; lift the shadow lock; let the engine place ONE maker leg; let the
complement reprice from the live book against the confirmed fill; cancel by
exact id at the TTL or the terminal; reconcile AFTER; and report first-leg
fills, second-leg fills, time between them, unhedged exposure, cancellations,
settlement and settlement-only realized net.

**I will report a submitted complement as submitted, never as a hedge.** Only
exchange-confirmed fills count, which the end-to-end suite now pins.
