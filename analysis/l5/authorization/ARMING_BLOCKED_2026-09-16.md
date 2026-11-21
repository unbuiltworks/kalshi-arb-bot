# Arming sequence HALTED at step 5 — the rung clamps the authorization

**HALT is still in place. No `live_mandate.json` was created. Nothing is armed.
No order has been or can be submitted.**

Owner authorized 2026-09-16: `max_residual` $3.00 · `max_spend_per_market` $5.00
· `max_total_spend` $15.00 · `max_markets` 1 · `max_total_markets` 3 ·
`max_daily_realized_loss` $6.00 · expiry 24 h · k 0.00619 unchanged.

## Steps completed

| # | Step | Result |
|---|---|---|
| 1 | Package matches the limits | **PASS**, one correction needed: the package names `service: l5-continuous-probe`; the real service is **`l5-r1-probe`** (`6111a5d4`), which already runs `src.pmus_l5_continuous_session` as its CMD. A mandate naming the wrong service is refused (`mandate_service_mismatch`) — fail-closed, but it would never arm. |
| 2 | Tested GuardedTransport / immutable mandate in the deployed artifact | **PASS** — commit `9fc7f32d6c66` serving, `analysis/l5/authorization/` COPYed into the image, `live_mandate.json` absent. |
| 3 | Adversarial + ledger-prefix invariant tests | **PASS — 39/39** (`adversarial` 21, `ledger_invariant` 5, `deployment` 13). |
| 4 | Account / open-order / residual state safe | **PASS** — 18:15Z: `SHUTDOWN_VERIFIED`, `held_preserved []`, zero open orders, account flat, `refusals 0`, settle cursor 89→90 advancing, realized today −$0.0916. |
| 5–9 | mandate creation, deploy, SHA verify, runtime verify, remove HALT | **NOT DONE — BLOCKED** |

## The blocker, measured

`session_notional_cap()` deliberately returns **`min(rung, mandate)`**. That rule
exists because on 2026-09-16 a stale approval *raised* the default $0.05 → $7.50.
It is a safety property and it is working exactly as designed here — but it also
means a tight stale rung **clamps a roomier new authorization**.

The deployed artifact's active rung is **R1**:

```
active rung R1: cap $0.05 | residual_units 3 | markets/campaign 3
                max_actions_per_session 6 | concurrency 1

session_notional_cap() = min(rung $0.05, mandate $5.00) = $0.05
max_residual_units()   = 3 CONTRACTS  (rung-governed; the $3.00 mandate figure
                                       is enforced at the wire, not here)

EFFECTIVE per-market spend $0.05 vs authorized $5.00  ->  100x BELOW
~0.11 contracts/market at a $0.45 touch; ~$0.15 across all three markets
```

Three authorized limits already match R1 exactly (3 markets, 1 concurrent, and
`max_actions_per_session` 6 as an extra bound). **Two do not**: per-market spend
($0.05 vs $5.00) and residual (3 contracts ≈ $1.35 vs $3.00).

## Why this stops the sequence rather than being worked around

Arming now would run a **$0.05/market** probe — the canary size that preceded the
breach, not the probe that was authorized. At ~0.11 contracts per market it
cannot meaningfully exercise **partial fills, queueing, or inventory evolution**,
which is the probe's stated objective. Reporting it as "the authorized probe"
would be false.

Every route to $5.00 changes the artifact, and the owner's own fail-closed
condition is "runtime differs from the tested artifact".

## Options — owner's call, none taken

| | Route | Assessment |
|---|---|---|
| **A** | Add a new rung matching the authorization ($5.00 cap, `residual_units` sized to $3.00), test it, deploy | **Recommended.** Keeps `min()` intact; small reviewable commit; the new artifact becomes the tested one. But it IS a risk-ladder change, and the authorization says it "does not authorize any … risk-limit increase" — so it needs an explicit word. |
| **B** | Point `PMUS_SPRING_RUNG` at an existing $7.50 rung | **Not recommended.** That is the exact lever of the breach; those rungs carry other changed levers (`residual_units` 6, different pair tolerance); and selecting it needs either a boot-time Railway variable (the banned anti-pattern) or a Dockerfile edit. |
| **C** | Arm at the effective $0.05 tonight | Safe, everything above already verified — but it is a different experiment from the one authorized and will not meet the objective. |
| **D** | Make the mandate override the rung (drop `min()`) | **Actively recommend against.** It inverts a safety property built specifically in response to 2026-09-16. |

Nothing proceeds until the owner chooses. The frozen 100K shadow run continues
untouched and is unaffected by any of this.
