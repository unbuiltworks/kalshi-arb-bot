# PMUS BTC15m $1 validation — executable configuration, and the four approvals

PMUS only. Every number below is from the live PMUS authenticated book, PMUS
venue minimums, and PMUS fee constants confirmed on our own account.

## First — I was wrong about the $1 envelope

I told you a $1 run "cannot measure what it is for" because the rebate rounds
away at that size. **The rebate rounding is real; the conclusion I drew from it
was wrong.**

At PMUS's actual 1¢ spread, a matched pair costs **$0.99 and redeems $1.00**.
That is **+$0.01 per pair with no rebate at all.** The rebate is upside, not
the edge. My "the margin is entirely the rebate" finding was about the full
strategy *including residual drag* — it does not apply to a matched pair, and I
carried it somewhere it does not belong.

**The $1 envelope supports a meaningful maker-first paired trade.**

## 2 — Can the existing engine execute within $1 / $1?

Yes. Current PMUS book, `cpc-btc-updown-15m-2026-10-16-1600z`,
`MARKET_STATE_OPEN`, **bid 0.3100 / ask 0.3200**, depth **7,595.72 / 481.01**,
`stream_age_s` 0.002, lag median 0.033s, `gap_suspected: false`.

| | |
|---|---|
| maker pair | UP at **0.31**, DOWN at **0.68** (= 1 − ask) |
| pair cost | **0.9900** → gross matched edge **$0.0100** per pair |
| venue minimum | qty **0.01** → cash $0.0099, worst-case loss $0.0068 |
| $1 cash binds at | q = **1.01** |
| $1 loss binds at | q = 1.47 — **cash is the binding constraint** |
| **use** | **q = 1.01 contracts per side** |
| cash deployed | **$0.9999** ✓ within $1 |
| worst-case loss | **$0.6868** ✓ within $1 (one leg fills at 0.68, settles worthless) |
| **both-fill net** | **+$0.0101**, with zero rebate |
| rebate at this size | UP $0.002700, DOWN $0.002747 → each rounds to **$0.00** |

Every existing control has headroom:

| control | limit | this run | |
|---|---|---|---|
| account-wide worst case | $5 | $2.0400 + $0.6868 = **$2.7268** | FITS |
| `max_usd_per_market` | $5 | $0.6868 | FITS |
| `max_unmatched_contracts` | 5 | 1.01 | FITS |
| `max_abs_residual` | 10 | 1.01 | FITS |
| `residual_cap_k` | 2, from flat | 1.01 | FITS |
| available cash | $344.41255 | $0.9999 | FITS |

## 3 — Minimum cash for a rebate-credited pair (asked, not required)

Our account pays a credited cent where the modelled rebate is ≈$0.005+ (paid
$0.0100 on modelled $0.0070, $0.0092 and $0.0123). So for a credited cent on
**both** legs at the current book:

| | current book (0.31/0.32) | earlier book (0.39/0.40) |
|---|---|---|
| minimum q | **1.87** | 1.68 |
| cash required | **$1.8513** | $1.6632 |
| worst-case loss | **$1.2716** | $1.0080 |

Both exceed the authorized $1/$1. **I am not requesting an increase** — the
validation is meaningful without it, and the zero-rebate line at q=1.01 is
itself a measurement worth recording.

## 4 — The blockers, and they are not mine to clear

Four, all verified live on deployment `22327f62`. None can be bypassed and I
have bypassed none.

### BLOCKER 1 — `CUMULATIVE_LOSS_LIMIT` (the hard one)

`refuse()` computes `loss = max(0, −realized) + max(0, unrealized)` and refuses
at `loss >= max_cumulative_loss_usd`. Live `realized_today` is
**−$233.98540000**, so:

- **ceiling must exceed $233.9854** to permit anything, today;
- **or exceed $24.3216** with the 47-leg cost-basis correction applied first.

Raising it is the honest form of "trade while historical losses are
unresolved," which is what the gate exists to stop. **I will not pick that
number.** The three routes past it are unchanged: raise the ceiling, rescope to
`verified_l5` (not defensible — the `L5_MAX_UNRESOLVED_UNKNOWN_USD`
precondition is still unset), or resolve attribution (all 49 legs are
`UNKNOWN_OWNER`, `l5_owned_fills: 0`; the venue does not record who placed a
historical fill, so I do not believe this is resolvable).

### BLOCKER 2 — HALT latch

`halt_latch: HALT`, `halt_why: halt_asserted`. The watchdog refuses to clear it
on every 20s sweep, correctly: *"clearing is an owner action, not an automated
one."* The only route is `deploy/l5_halt_reset` — the owner-gated one-shot
admin image requiring temporary credentials, a single-use reset id, four
fail-closed gates, and **a window in which the watchdog protects nothing.**

### BLOCKER 3 — `L5_SHADOW_ONLY`

`submission_gate.blocked: true`, `reason: SHADOW_ONLY_LOCK`. A Railway
variable change on the money-capable service.

### BLOCKER 4 — `max_live_orders` is 1; a maker pair needs 2

Your envelope authorizes "maximum two resting orders." The live limit is **1**,
so the second leg would be refused. This needs `L5_MAX_LIVE_ORDERS = 2`.

Also required for the authorized selective completion:
**`L5_TS_COMPLETION_CROSS`** is currently unset, so completion is computed and
logged but never crosses.

## 5 — The executable configuration

On your approval of the four items below, this is what I run — unchanged
engine, no new strategy, no new service:

```
L5_TS_MARKET            cpc-btc-updown-15m-*       (BTC15m only)
L5_MAX_CASH_DEPLOYED_USD 1                          (unchanged)
L5_MAX_LIVE_ORDERS       2                          (1 -> 2, for the pair)
L5_ACC_MAX_ROUNDS        1                          (one pair, no accumulation)
L5_TS_COMPLETION_CROSS   YES                        (economics-gated only)
L5_MAX_WORST_CASE_DEFICIT_USD  1                    (the $1 loss cap)
L5_MAX_CUMULATIVE_LOSS_USD     <YOURS>              (must exceed $233.9854)
L5_SHADOW_ONLY           unset                      (owner)
HALT                     cleared via l5_halt_reset  (owner)
```

Behaviour: one market, maker-first both legs at the touch, q = 1.01 per side,
no repeat after completion or crash, no size increase, no martingale. Stop
immediately on reconciliation error, unexpected inventory, stale book, or any
risk-control refusal. Record every submitted / filled / cancelled / settled /
realized line with fees and rebates on separate lines, settlement-only realized
labelling.

What it measures: whether our maker orders actually rest and fill on PMUS; the
realized net of a completed pair (**+$0.0101** if both fill); the live residual
and completion economics if one fills; and the rebate line, which should read
**$0.00** at this size — a prediction worth testing.

## The exact approval I need

> *"Clear HALT via the l5_halt_reset procedure, unset L5_SHADOW_ONLY, set
> L5_MAX_LIVE_ORDERS=2 and L5_TS_COMPLETION_CROSS=YES, and set
> L5_MAX_CUMULATIVE_LOSS_USD to $____ (above $233.99, or above $24.33 if you
> apply the 47-leg correction first). Run the $1 validation."*

The blank is the only thing I cannot supply. Everything else is computed,
verified and ready.

## State as I leave it

`submission_gate.blocked: true` / `SHADOW_ONLY_LOCK`, HALT latched,
`armed: false`, `live_orders: 0`, `max_live_orders: 1`,
`max_cash_deployed_usd: 1`, `L5_TS_COMPLETION_CROSS` unset,
`L5_MAX_UNRESOLVED_UNKNOWN_USD` unset, account exposure $2.0400 of $5.
No order submitted, no variable changed, no gate weakened, no service created.
The 47-leg correction remains prepared and unapplied.
