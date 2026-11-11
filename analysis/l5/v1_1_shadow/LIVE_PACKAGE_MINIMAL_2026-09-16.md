# Smallest continuous-live authorization package — FOR OWNER REVIEW

> **NOT ARMED. NOT AUTHORIZED.** `analysis/l5/authorization/live_mandate.json`
> does **not** exist and this file does not create it. Its absence is the safe
> state. `analysis/l5/authorization/HALT` remains in place. Authorizing means
> the owner committing the mandate file; revoking means deleting it.
>
> The 100K preregistered maturity run continues untouched. Its result stays
> official. **This package does not claim a maturity PASS** — M3/M4/M5 failed at
> both the M1 floor and the 50K checkpoint and remain failed.

## Why a bounded probe is defensible despite the failed stability gates

The gates measure whether the **participation rate** is estimable. It is not.
But the participation rate is not what bounds this probe — the **mandate** is,
enforced at the transport, immediately before submission, by economic effect
rather than action name (`src/pmus_live_mandate.py`).

**Rate instability therefore changes how long the probe takes and how much
evidence it yields. It does not change how much money is at risk.** Every cap
below binds at the wire whether participation is 0.155 or 0.660 — the measured
extremes of the sample.

Concurrency is **chosen, not derived from the unstable rate.** That was the
defect in the earlier draft of D.

## The mandate — all twelve required fields

| Field | Proposed | Grounding |
|---|---|---|
| `authorization_id` | ⟨owner assigns, new⟩ | never reused; an old id cannot authorize this |
| `authorized_at` | ⟨owner assigns, UTC⟩ | approvals must POSTDATE it; undated are refused |
| `service` | `l5-continuous-probe` | one mandate, one service |
| `strategy` | `l5_continuous_inventory` | |
| `strategy_version` | `v1.1` | Stage 1 → 2 → 3, artifact `c176813c250bacd0` |
| `max_markets` | **1** | concurrent. Spring discipline's one-market rule — a choice, not an estimate |
| `max_total_markets` | **3** | lifetime of this authorization |
| `max_spend_per_market` | **$5.00** | 1.5× the measured shadow maximum ($3.30) at this exact residual ceiling |
| `max_total_spend` | **$15.00** | 3 × $5.00. **This is the bankroll** — the wire refuses the order that crosses it |
| `max_residual` | **$3.00** | Envelope A's deployed `residual_ceiling_usd`, **unchanged**. Measured to be the binding constraint |
| `max_daily_realized_loss` | **$6.00** | Envelope A's deployed stop, **unchanged**. Settlement-confirmed realized only. Tightening is always safe |
| `expiry` | **authorized_at + 24 h** | shorter than the 72 h draft because this is the smallest |

`k` stays at the deployed **0.00619**. No threshold, model, envelope or
collector value is changed anywhere in this package.

### Why $30 exposure is absent

`exposure_cap_rate` was **0.0000 in all 26 windows** and `spent_usd` never
exceeded **$3.30** against Envelope A's $30 ceiling. The exposure ceiling is
~10× larger than anything the controller reaches; it is not load-bearing and is
omitted rather than carried as decoration. `max_total_spend` does that job.

## Selection and gating

- Source: the recorder's existing read-only `/status` handoff. No new discovery,
  no new venue permission, no new collector.
- **Stage 1 decides participation** at the preregistered H = 10 s, threshold
  0.35, train-only. A WAIT is logged with its reason.
- **Stage 2 decides the action** with real `last_rel`/`last_size` context.
- Risk and venue vetoes stay attributed to risk and venue, never merged into the
  model's reason.
- **No new first leg while unrelated residual exposure exists.**

## Maker / post-only

Post-only on every order. **Taker is disabled, not discouraged.** No automatic
late-taker behaviour from mature August L5. Pair completion at the
spring-discipline target (`≤ 0.96`); near-$1 tolerance stays locked.
Cancel/replace on book movement rather than crossing.

## Settlement and accounting

`submitted → filled → settled → realized`, strict. Nothing realized before
venue-confirmed settlement. A settlement whose trades lack the venue's
`realizedPnl` is **SETTLED_REALIZED_UNCONFIRMED** and stays out of every net.
Matched-pair arithmetic is derived, never realized profit. Rebates, rewards and
incentives accrue separately and never enter trading P&L. The probe's cohort is
stamped per service and **never merges** with legacy or R1.

## Exact stop conditions

Halts — cancel-only, positions preserved — on **any** of:

1. `analysis/l5/authorization/HALT` present (read **before** `armed()`);
2. mandate absent, malformed, expired, or bound to another service;
3. any envelope consumed: `max_total_spend`, `max_spend_per_market`,
   `max_residual`, `max_markets`, `max_total_markets`;
4. `max_daily_realized_loss` reached on **settlement-confirmed** realized P&L;
5. any transport refusal (`MandateRefusal`) — it stops, it does not retry smaller;
6. unhandled exception, venue auth failure, or ledger/venue quantity mismatch.

And the two rules that are the lesson of 2026-09-16:

- **No automatic relaunch** after a completed or crashed probe.
- **Continuation, settlement, restart, resume and `advance_gate` can never
  enlarge or replace the active mandate.** A market beyond `max_total_markets`
  needs a new authorization — a human and a commit.

**No R2/R3/R4 ladder. No automatic progression.**

## What this probe would actually establish

The dominant driver of participation variance is endogenous inventory dynamics,
and shadow fills are **THEORETICAL** — marked filled when the observed touch
trades through a hypothetical maker quote. Real fills are slower, partial and
uncertain.

So the one thing shadow structurally cannot measure is the one thing this probe
would: **the real fill process, and the real inventory trajectory it produces.**
That is the entire case for running it, and it is why more shadow time does not
substitute.

## What authorization does NOT include

Raising any cap; adding markets; enabling taker; near-$1 pair tolerance;
assuming any rebate or reward before it is credited and source-accounted;
tuning k, the thresholds, the model or the envelope from live results.
