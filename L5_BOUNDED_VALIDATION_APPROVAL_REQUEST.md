# Bounded $1 PMUS BTC15m validation — prepared, STOPPED on two blockers

**I did not submit anything.** Four of six prerequisites are satisfied. Two
cannot be satisfied by me, and one of them cannot be satisfied by anyone
without a decision you have not yet made.

Per your instruction — *"If a prerequisite cannot be satisfied, stop and
report the exact blocker and required approval"* — this is that report.

## Controls verified FUNCTIONING

**363 control tests pass** across all six families you named:

| control | evidence |
|---|---|
| exact-order cancellation | `test_l5_emergency_cancellation_proof`, `_deadman_exact_cancel`, `_disarmed_cancellation`, `_orphan_cancellation` — cancel by **venue id** with proof by re-read |
| independent watchdog | **live now**: sweeping every 20s, `open_orders: 0`, `read_error: null`, `cancel_enabled: false`, report age fresh against a 120s bound |
| settlement accounting | `test_l5_settlement_retirement`, `_binary_settlement` |
| account-wide exposure | `test_l5_account_wide_exposure`, `_potential_exposure`; live: **$2.0400 of $5**, capacity **$2.9600** — $1 fits |
| four-bucket accounting | `test_l5_four_bucket_accounting`, `_unresolved_unknown_backstop` |
| HALT gate | `test_l5_halt_gate`, `_halt_reset`, `_watchdog_halt_end_to_end`, `_watchdog_credential_free` |

The watchdog is refusing to overwrite the latch on every sweep, with the
reason it should: *"clearing is an owner action, not an automated one."*

Venue eligibility: `MARKET_STATE_OPEN`, two-sided book, `stream_age_s` 0.002–0.022,
source-to-receipt lag median 0.039s, `gap_suspected: false`.

## BLOCKER 1 — the loss gate refuses, and the correction does not clear it

This is the one that needs your decision. `l5_live_validation_gates.refuse()`
computes:

```
loss = max(0, −cumulative_realized) + max(0, cumulative_unrealized_loss)
refuse if loss >= max_cumulative_loss_usd
```

With `cumulative_realized = −233.98540000`:

| ceiling | now | after the 47-leg correction (−$24.3216) |
|---|---|---|
| **$1** (the defensible ceiling for a $1-loss validation) | REFUSE | **REFUSE** |
| $5 | REFUSE | **REFUSE** |
| $20 (`L5_MAX_CUMULATIVE_LOSS_USD` default) | REFUSE | **REFUSE** |
| $25 | REFUSE | permit |

**`CUMULATIVE_LOSS_LIMIT:233.98540000>=1`** today, and
**`CUMULATIVE_LOSS_LIMIT:24.32160000>=1`** even with the cost-basis correction
applied. I want to be unambiguous about this, because it was implicit in
earlier reports and should not have been: **applying the 47-leg correction
does not unblock this validation.** It reduces the figure 9.6×; the gate still
refuses by 24×.

There are exactly three ways past it and **all three are yours, not mine**:

1. **Raise `L5_MAX_CUMULATIVE_LOSS_USD` above $24.33** (after the correction)
   or **$233.99** (without it). This is the honest version of "trade while
   historical losses are unresolved," and the gate exists to stop precisely
   that. I am not proposing it.
2. **Rescope the ceiling to the verified-L5 population** (`verified_l5_realized`
   = $0). I already answered the factual question on this and the answer has
   not changed: it **would** permit trading with $24.32 of unresolved
   account-wide loss. The `L5_MAX_UNRESOLVED_UNKNOWN_USD` backstop was built
   as its precondition and is still **unset**, so the rescoping is not
   defensible today even with the backstop available.
3. **Resolve the attribution** so the loss stops being unknown. All 49 legs are
   `UNKNOWN_OWNER` with `l5_owned_fills: 0`, and `our_client_order_ids` is 1.
   The venue does not record who placed a historical fill. I do not believe
   this is resolvable from available data.

## BLOCKER 2 — HALT and SHADOW_ONLY are owner actions

Both are working as designed and neither is mine to change.

**HALT** is latched (`halt_why: halt_asserted`, stored reason
`UNMANAGED_RESTING_ORDER` — which is the latch's own text, not an order;
there are zero open orders at the venue). `HaltAsserter` has no clear verb.
The only route is `deploy/l5_halt_reset`, the owner-gated one-shot admin image,
which requires: temporary venue credentials on the watchdog service, pointing
the watchdog at the admin image, a single-use reset id, and passing four
fail-closed gates. **While that image is the running configuration the
watchdog is not protecting anything.** That is your action and your window.

**`L5_SHADOW_ONLY`** must be unset on `l5-pmus-live`, and the arm token must
match this session's id. Both are Railway variable changes on the money-capable
service.

## The blocker you did not ask about, which I think matters most

**At $1, this validation cannot measure the thing it is for.**

| | |
|---|---|
| $1 at the observed median pair cost 0.99 | **1.01 contracts per side** |
| true maker rebate at 1 contract, p=0.50 | $0.003125 |
| what the venue pays (rounds to **nearest** cent) | **$0.00 or $0.01** — 0× or 3.2× |
| matched edge at pair cost 0.99 | **$0.0100** per pair |
| fee/rebate rounding granularity | **$0.0100** per order, two legs |

**The rounding is at least as large as the entire signal.** And my own
retrospective found the whole positive result *is* the rebate — so a $1 run
measures the one input it cannot resolve. One pair is also n=1 against a fill
rate the requirement needs to ±3pp.

A $1 run would prove **mechanics** — submit → fill → complete → settle →
reconcile, with real fees and a real rebate line. That has genuine value. It
would not produce a profitability verdict, and you told me earlier: *"Do not
perform another 0.01-contract experiment solely to prove order submission."*
This is close to that instruction's spirit, so I am flagging it rather than
spending your $1 and reporting an uninterpretable number.

## The exact approval request

**Option A — mechanics validation (what $1 can actually deliver).** Say:
> *"Clear HALT via the l5_halt_reset procedure, unset L5_SHADOW_ONLY, set the
> arm token, raise L5_MAX_CUMULATIVE_LOSS_USD to $X, and run the $1 mechanics
> validation. I accept that it measures mechanics, not profitability."*

I need `$X` from you (> $233.99, or > $24.33 with the correction applied
first). I will not pick that number. On your word I execute inside $1 deployed
/ $1 loss, one market, two resting orders, maker-first, completion only on
contemporaneous executable economics, no retries, no scaling, and record every
submitted / filled / cancelled / settled / realized line with fees and rebates
separated.

**Option B — the read-only measurement, which I recommend first.** Set
`PMUS_WS_LADDER_MARKETS` to the BTC15m slug on the recorder that is *already
running*. No HALT reset, no credential window, no loss-gate decision, no
capital, no risk. It yields the actual PMUS both-fill rate against the
69.26–76.93% requirement — the number that decides this, which $1 of live
capital cannot produce.

**Option C — correction first, decide after.** Approve the
`correct_cost_basis` verb (design already submitted), apply the 47-leg
correction, and revisit with realized at −$24.3216. This does not unblock the
validation but it makes the remaining decision an order of magnitude smaller.

## State as I leave it

Unchanged and verified: `submission_gate.blocked: true` /
`SHADOW_ONLY_LOCK`, HALT latched, `armed: false`, `live_orders: 0`,
`max_live_orders: 1`, `max_cash_deployed_usd: 1`, `L5_TS_COMPLETION_CROSS`
unset, `L5_MAX_UNRESOLVED_UNKNOWN_USD` unset, account exposure $2.0400 of $5.
No order submitted. No gate, limit, threshold, variable or mode changed. No
service created. The 47-leg correction remains prepared and unapplied.
