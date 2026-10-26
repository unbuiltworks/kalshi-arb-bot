# `max_live_orders=1`: does it prevent reproducing L5's concurrency?

**Yes, in 93.9% of windows — and the $5 cap is not what binds. The order count is, and it leaves
84% of the authorized envelope permanently idle.**

Nothing was changed. Recommendation only.

## 1. The measurement, and why it is a proof rather than an estimate

Fill records do not state how many orders rested. One signature does: **two fills on the same side,
in the same second, at two different prices cannot come from one order.** That is a provable lower
bound on concurrent resting orders. If the opposite side also fills in that second, add one more.

Full corpus, 5,502 windows, **424,141 fill-seconds**:

| | |
|---|---|
| seconds with 2+ prices on **one** side | **51,005 (12.0%)** |
| seconds with **both** sides filling | 23,175 (5.5%) |
| distinct prices in one second, one side | p50 **2**, p90 **3**, p99 **4**, max **10** |

**Per-window minimum capacity required** (worst second in the window, including the opposite side):
p10 **2**, median **3**, p90 **4**, p99 **7**, max **10**.

| capacity | windows served |
|---|---|
| **1 (deployed)** | **6.1%** |
| 2 | 35.9% |
| 3 | 75.4% |
| **4** | **91.6%** |
| 6 | 99.0% |

So `max_live_orders=1` is **structurally incapable** of reproducing L5's resting pattern in
**93.9%** of windows. This is not a fill-rate argument or a shadow result — it is arithmetic on his
executed trades.

## 2. How the three commitments are counted together

`l5_live_validation_gates.refuse()`:

```python
this_order = (q if intent.endswith("BUY_SHORT") else q * px)
committed = (cash_committed(filled_legs)        # filled, unsettled legs
             + pending_obligation(outstanding)  # EVERY resting order, full worst case
             + this_order)
if committed > self.max_cash_deployed_usd:      # $5
    return "CASH_COMMITMENT:..."
```

`pending_obligation` charges each resting order `qty * price` (or `qty` for a resting SHORT, which
owes up to $1/share). So **held inventory, partial fills and open-order commitments all compete for
the same $5**, each priced as though it fills in full. `cash_policy` is `NO_RECYCLING`, so settled
cash is not reused within the run.

**The limit is already 1+1 in the pair case**: `ceiling = max_live_orders + (1 if pair_second_leg_ok
else 0)`. And the code's own author states why raising it is safe:

> *"the cash envelope, the residual ceiling, the daily and cumulative halts — already prices an
> outstanding order as though it fills in full, so two resting legs are charged as two filled legs
> without any change here. **That is what makes this safe to grant: it buys a slot, never
> headroom.**"*

## 3. What the $5 envelope actually affords, at the deployed $0.80 clip

| concurrent orders | resting cost | with one filled leg | fits $5? |
|---|---|---|---|
| 1 (deployed) | $0.80 | $1.60 | yes |
| 3 | $2.40 | $3.20 | yes |
| **4** | **$3.20** | **$4.00** | **yes** |
| 5 | $4.00 | $4.80 | yes |
| 6 | $4.80 | $5.60 | **NO** |

**At `max_live_orders=1`, $4.20 of the authorized $5 — 84% — can never be deployed.** The cash gate
would refuse at 6; the order gate refuses at 2. The order gate is strictly tighter and is the one
doing the work.

## 4. Recommended bounded value: **4**

| why 4 | |
|---|---|
| coverage | serves **91.6%** of windows against 6.1% today — the knee of the curve (1→6.1, 2→35.9, 3→75.4, 4→91.6, 6→99.0) |
| fits the envelope | $0.80x4 = **$3.20** resting, **$4.00** with a filled leg — inside $5 with $1.00 spare |
| survives the pair allowance | the existing `+1` makes it behave as 5 during a completion: $4.00, **still inside $5** |
| why not 6 | 6 resting plus a filled leg is $5.60 — the cash gate would refuse it, so the slot could never be used and the limit would be decorative |
| why not 2 or 3 | 2 leaves 64% of windows unreachable; 3 leaves 25%. Both leave envelope idle for no risk benefit |

**It buys no dollars.** Worst-case loss stays bounded by the unchanged $5 cash cap, $5 daily halt,
$20 cumulative, `max_unmatched_contracts` 5, `max_worst_case_deficit_usd` $2.00 and
`max_abs_residual` 10. The only thing that changes is how many slots the same $5 may occupy at once.

## 5. Honest limits of this evidence

- The measurement is a **lower** bound on L5's concurrency. A second with one fill says nothing, so
  the true requirement is at least these figures, never less.
- It says nothing about whether *our* orders would fill. It establishes that the current limit makes
  the pattern **impossible to attempt**, which is a different and weaker claim than profitability —
  and I am not claiming the latter.
- Our clip is ~1.6 contracts against L5's median 13.1, so we would be reproducing the **shape** of
  his concurrency at 1/8 the size, not his book.

## 6. Preserved

$5 exposure cap, $5 daily halt, $20 cumulative, reconciliation, exact-order cancellation
(`cancel_exact` by venue order id, market-scoped deadman), and the independent watchdog (20s sweeps,
`cancel_enabled: true`, `read_error: null`). No new strategy, service, branch or research project.
**`max_live_orders` is unchanged at 1 and I am not changing it without your authorization.**
