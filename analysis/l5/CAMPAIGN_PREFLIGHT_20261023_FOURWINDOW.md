# Four-window $20 campaign: preflight FAILED, not armed

**The authorization is accepted. The account state does not permit the
authorized size, and making it fit would require raising a limit the
authorization forbids. No approval document was created, no submissions were
enabled, the shadow lock was never touched.**

Owner authorization: four eligible BTC15m windows, max $20 aggregate capital
exposure, existing $5 daily realized loss limit unchanged, existing cumulative
loss / residual / concurrent-order / watchdog / halt controls unchanged, no
automatic renewal. Instruction: *"Do not reset ledgers or raise limits"* and
*"Activate live submissions only if every preflight and authorization check
passes."*

## Reconciliation — clean

| | value | source |
|---|---|---|
| open orders | **0** | independent watchdog `L5_WATCHDOG_SWEEP`, 13:41:31Z, sweeping every 20s |
| `overdue` / `not_ours` / `unverified` | `[]` / `[]` / `[]` | same |
| `read_error` | `null` | same |
| `cancel_enabled` | `true` | same — exact-order cancellation intact |
| watchdog `halt` | `false` | same |
| all-time realized | **−$9.30360000** | executor heartbeat 13:39:49Z |
| today realized | **−$0.404800** | same |
| windows settled | 278, `undated_settlements: []` | same |
| `halt_latch` | `CLEAR` | same |
| shadow lock | `true`, `armed: false`, `submission_gate.blocked: true` | same |
| campaign approval | `approval_state: approval_stale` | same |
| submissions spent | 6 (the Oct 23 orders) | same |
| deployment | `d1cead39` SUCCESS, 1 replica, no staged changes | owner-verified, re-confirmed |

Nothing is outstanding and nothing is unsettled. The prior campaign ledger is
closed. On every gate except one, this campaign could arm.

## The blocker: the $20 envelope is not enforceable

`L5_CAMPAIGN_BUDGET_USD` is a **cumulative worst-case loss ceiling** of
**$11.10**, and `worst_case_usd` charges the loss side of realized P&L against
it. With −$9.3036 realized:

```
  ceiling                               11.10
  worst_case_usd(realized -9.3036)       9.3036
  AVAILABLE HEADROOM                     1.7964      <-- the binding control
  authorized aggregate exposure         20.00
```

**The authorized envelope is 11.1x what the existing ceiling permits.** The
$20 cap and the $11.10 cumulative ceiling are different controls and both
apply; the smaller binds, so the gates would refuse at $1.7964 regardless of
what the approval document said. The envelope is therefore not enforceable *as
authorized* — not because a control is missing, but because a tighter one the
authorization preserves gets there first.

To make $20 reachable the ceiling would have to rise to
**$9.3036 + $20 = $29.3036** — a 2.64x raise. That is the one thing the
authorization explicitly forbids, so I did not do it and did not arm around it.

## And the four-window shape does not fit either

On the cash-gate basis the resting leg is a `BUY_SHORT` charged $1/share, so
`oblig_per_contract = 1 + up_px`:

| entry `up_px` | obligation/contract | max contracts, ONE window |
|---|---|---|
| 0.20 | 1.20 | 1.50 |
| 0.50 | 1.50 | 1.20 |
| 0.88 | 1.88 | 0.96 |

`worst_case_usd` charges an **unsettled** leg its full cost until its market
retires. So window 1 consumes essentially all $1.7964 and windows 2–4 are
refused in sequence until it settles — and BTC15m settlement lags the window
close. The existing ceiling permits roughly **one window of about one
contract**, not four windows inside $20.

## What I did not do

* did not create an approval document — a fresh four-window approval whose
  envelope the gates cannot honour would be a document asserting a bound that
  does not exist;
* did not enable submissions;
* did not touch `L5_SHADOW_ONLY`, any limit, or any ledger;
* did not re-point the $20 at a different control to make it "fit".

## The two ways forward, both the owner's call

1. **Run it at the size the existing ceiling permits** — one window, ~1
   contract, ~$1.80 worst case. Within the $20 cap (a maximum, not a target)
   and needs no limit change. I can arm this on one word.
2. **Raise the cumulative ceiling** from $11.10 to ~$29.30 so $20 is
   enforceable. That is a limit raise and needs explicit authorization naming
   the new ceiling.

## One thing worth weighing first

The policy's expectancy is **negative under every defensible fill assumption**
(`PROFITABILITY_DIAGNOSIS_20261023.md`): breakeven needs 84.52% leg completion,
our measured completion is 83.42%, and the realized one-complement-per-window
case is 14.4% → −0.27/leg. Four windows at ~1.5 units has an expected outcome
near **−$0.10 to −$0.40**, and one window at ~1 contract near −$0.05 to −$0.27.

It also will not produce the measurement that is actually missing — the
per-attempt complement fill rate at depth 0/1 — because the 0.96 pair ceiling
forces depth 2 in 99.40% of ticks. That number is obtainable in the read-only
shadow at no capital risk.

## Provenance of the ceiling figure

The $11.10 is from the owner's standing instruction (*"the $11.10 cumulative
campaign budget ceiling ... Available headroom must remain $2.2012 or less"*,
which reconciles exactly: 11.10 − 8.8988 = 2.2012 at that time) and matches
`l5_campaign_budget.DEFAULT_BUDGET_USD`. Variable **values** are redacted to
this session (OAuth returns names only), so if `L5_CAMPAIGN_BUDGET_USD` has
since been changed the arithmetic moves with it. `python3 -m
src.l5_runtime_config_verify` in-service reports the effective value.
