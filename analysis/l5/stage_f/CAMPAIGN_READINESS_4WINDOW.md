# 4-window campaign readiness: DISARMED, three gates fail

**Date:** 2026-11-01T23:20Z
**Verdict: remain disarmed.** Three of the four checks you named
(authorization, deployment, reconciliation, watchdog) fail. Per your own
instruction I have armed nothing and name the precise blockers.

## State, read live (not from memory)

| | |
|---|---|
| executor service | `l5-pmus-live`, deployment `6e861476` SUCCESS |
| **running SHA** | **`4d52345d`** (2026-11-01 12:57Z) |
| my branch head | `c4f49611` — **10 commits ahead, none deployed** |
| `armed` | **false** |
| open orders at venue | **0** |
| pending-terminal / unacked / adopted / quarantined | **all empty** |
| `reconciled` / `uncertainty_cleared` | true / true |
| `realized_today` | **−8.90460000** |
| watchdog | sweeping 20s, `cancel_enabled: true`, `open_orders: 0`, `read_error: null` |
| venue health | 1,890 requests, **0× HTTP 429**, no cooloff |

## The four gates

### 1. Authorization — **FAIL**
`approval_state: approval_stale`. The prior campaign document has expired and
you instructed that it must not be reused. `EnvApprovalStore.put` **raises by
design** — "the executor may not write its own approval" — so a fresh document
must be installed into `L5_CAMPAIGN_APPROVAL` as an owner act.

### 2. Deployment — **PASS as deployed, but $50 IS NOT ENFORCEABLE**
This is the finding that matters most for your scope.

`src/l5_campaign_approval.py:49` at the running SHA:

```python
MAX_TOTAL_EXPOSURE_USD = D("20")
```

An approval is the **intersection** of the document and the code ceiling
(`eff_exp = min(want_exp, MAX_TOTAL_EXPOSURE_USD)`). A document asking for
**$50 is clamped to $20** and the scope reports
`clamped: {"max_total_exposure_usd": true}`.

So I can state plainly what requirement 1 asked me to confirm: **the $50
configuration is not enforced and cannot be, at this SHA.** Reaching $50 needs
a money-path code change plus a deploy.

**I recommend running at the existing $20 instead**, and not raising that
constant. Your own framing supports it — "hard limits, not spending targets",
and "keep individual opening sizes conservative until we have evidence that
the new entry logic improves net results". That evidence does not exist (§3),
so raising a capital ceiling now would increase expected loss, not optionality.
$20 is inside your authorization and needs no code change.

### 3. Reconciliation — **FAIL**
`reconciled: true` and `uncertainty_cleared: true`, but open items remain and
you asked for them resolved *before* live submissions:

- **drift, 2 markets**, unchanged since mid-October:
  `2026-10-14-2230z|UP ours 2.9000 / venue 0`,
  `2026-10-12-0030z|DOWN ours 2 / venue 0`;
- both listed `stranded_without_stamp` and `gross_detail_lost`
  (`gross_net_agrees: false`);
- **`leg_cost_basis_unknown`: 50 legs**, spanning BTC, ETH, SOL and XRP
  markets from 2026-10-14 to 2026-10-30.

The venue reports 0 for both drifted markets, so these are economically closed
there — our book still carries them. They are not blocking any *gate*, but
they are exactly the "unresolved inventory" you asked to clear first, and
clearing them is a ledger repair I have not been authorized to perform.

### 4. Watchdog — **PASS**
Independent service, 20-second sweeps, `cancel_enabled: true`,
`emergency_cancel: false` (correct — no latch), heartbeat fresh at 23:20:13Z,
`open_orders: 0`, every list empty, `read_error: null`.

### Unverifiable from here
`realized_today: -8.9046` is reported under a **daily** name but matches the
**cumulative** post-baseline ledger (−8.8291 → −8.9046 after the last
campaign). The gate reads two separate figures — `l5_day_loss` (UTC-day) and
`l5_cum_loss` (post-baseline) — against two separate ceilings. Today's actual
realized loss is ≈−$0.0755, so the $5 **daily** ceiling is not breached, but
**I cannot confirm the arithmetic end-to-end**: variable *values* are redacted
to this OAuth client (names only), so I cannot read
`L5_MAX_L5_CUMULATIVE_LOSS_USD` to check −8.9046 against it. Requirement 1
asked me to confirm the daily loss calculation is correct; I can confirm the
code reads two distinct figures, and I cannot confirm the configured ceilings.
That needs an owner read of the two variables.

## 3. Requirement 2 has no deliverable, and that is the real blocker

You asked for improved entry decisions before running. I have **no validated
improvement to deploy.** Every candidate tested this session failed on
held-out data:

| candidate | result |
|---|---|
| simultaneous two-sided maker policy | **NO-GO** — break-even joint-fill 93.0% vs observed 53.3% |
| entry-condition selection | **NO EDGE** — 15 entries, Wilson CI [0.301, 0.752] |
| inventory-imbalance gate | **REJECT** — would have blocked nothing |
| late risk-reduction to T+720 | **FAILS OOS** — t = −0.71, CI [−0.043, +0.020] |
| completion ceiling 0.96 → 0.99 | **+$0.0001/window**, past break-even at ~0.980 |
| our actual fill range (fills 1–3) | **−53.5 bp/$ out-of-sample** |

And the inputs requirement 2 specifies — executable depth, queue position,
realistic maker-fill likelihood, joint-fill probability — are precisely the
observations we do not have. Requirement 2 also says not to invent a
fill-probability model from inadequate data. I agree, and I won't.

**The measurement is currently blocked.** The observer is collecting L5
actions again (288/288 polls, 76 new actions in one window), but its
order-book WebSocket is rejected with **HTTP 401** — credentials present,
venue refusing. So `raw_frames: 0`, `book_states: 0`,
`excluded_for: FEED_NEVER_CONNECTED`. No depth or queue evidence is being
recorded, which is the input the entry policy needs.

## What I can run, and what it is worth

Within scope and with no code change: **4 windows, $20 exposure, $5 daily
halt, 3-contract residual cap, $1.50 worst-case deficit, 2 live orders,
maker-only, 0.96 ceiling** — i.e. the engine exactly as it is, with the entry
policy exactly as it is.

Honest expected value: the last campaign of this configuration realized
**−$0.0755 over 7 windows**, and the fill range we operate in measures
**−53.5 bp/$** out of sample. A 4-window run should be expected to lose a
small amount. You have already framed it correctly — "four windows are a
diagnostic campaign, not proof of a sustainable edge" — so if the purpose is
diagnostic, it is defensible. It is not a step toward $480/day on its own.

## What I need from you

1. **Install a fresh `L5_CAMPAIGN_APPROVAL`** for 4 windows. I cannot mint it.
   State the exposure as **$20** unless you want the code ceiling raised, which
   I do not recommend (§2).
2. **Confirm or clear the exposure question**: run at $20, or authorize the
   `MAX_TOTAL_EXPOSURE_USD` code change explicitly.
3. **Authorize the ledger repair** for the 2 drifted markets and the 50
   unknown-cost-basis legs, or waive it in writing for a 4-window run.
4. **Read back `L5_MAX_L5_DAILY_LOSS_USD` and
   `L5_MAX_L5_CUMULATIVE_LOSS_USD`** so the loss arithmetic can be confirmed —
   values are hidden from me.
5. **Rotate the PMUS API credentials** so the observer's book feed stops
   returning 401. This does not block the campaign but it blocks every
   entry-policy improvement after it.

On your go for (1)–(3) I will run the four windows at $20 and report per-window
realized economics in the format you specified. I will not arm before then.

Nothing armed, no order submitted, no limit changed, no variable written to the
executor, no new service, no infrastructure cost.
