# D — bounded continuous-inventory live probe

> **STATUS: DRAFT, NOT AUTHORIZED, NOT ARMED.** No `live_mandate.json` exists;
> its absence is the safe state and this document does not create one. Numbers
> marked `⟨from sample⟩` are not yet known and are **not** to be guessed — they
> resolve from the matured prospective sample per the stated rules. This is
> brought back for authorization, never self-applied.

One continuously operating controller. **No R2/R3/R4 ladder** — there is no
automatic progression, and a market beyond `max_total_markets` requires a new
explicit authorization, which means a human and a commit.

## The proposal *is* a mandate

The enforcement mechanism already exists: `src/pmus_live_mandate.py` admits
every money-moving order at the transport, immediately before submission, by
**economic effect** rather than action name. So the operational proposal below
is expressed as the mandate document itself. Authorizing the probe means
committing that file; revoking it means deleting it.

The twelve required fields are exactly the twelve operational parameters:

| Field | Value | Where it comes from |
|---|---|---|
| `authorization_id` | ⟨owner assigns⟩ | new, never reused; an old id cannot authorize a new probe |
| `authorized_at` | ⟨owner assigns, UTC⟩ | any approval must POSTDATE this; undated approvals are refused |
| `service` | `l5-continuous-probe` | a mandate is bound to one service |
| `strategy` / `strategy_version` | `l5_continuous_inventory` / `v1.1` | the staged controller, Stage 1 → 2 → 3 |
| `max_spend_per_market` | ⟨envelope⟩ | C's chosen candidate |
| `max_residual` | ⟨envelope⟩ | C's chosen candidate |
| `max_markets` | ⟨from sample⟩ | concurrent, see **Concurrency** |
| `max_total_markets` | ⟨from sample⟩ | lifetime of this authorization |
| `max_total_exposure` → `max_total_spend` | ⟨envelope⟩ | the bankroll ceiling |
| `max_daily_realized_loss` | ⟨envelope⟩ | settlement-confirmed realized only |
| `expiry` | **authorized_at + 72 h** | short by design; a lapsed probe fails closed to `MANDATE_EXPIRED` |

`max_total_spend` **is** the bankroll authorization: the transport refuses the
order that would cross it, so the bankroll is a wire-level fact, not an
intention.

## How the ⟨from sample⟩ numbers resolve

**Concurrency.** The probe needs enough proposal flow to produce evidence
within one authorization window without exceeding the envelope. With observed
participation `p` and `E` evaluations per market-hour:

```
expected proposals / market-hour = p · E
markets needed for R proposals/hour = R / (p · E)
max_markets = min(envelope concurrency, ceil(that), max_total_spend / max_spend_per_market)
```

The third term is the one that actually binds and is not negotiable: the
controller may never have more markets open than the bankroll can fund at the
per-market ceiling.

**max_total_markets** = `max_markets` × ⟨expected market turnover over 72 h⟩,
floored so that a single stalled market cannot consume the authorization.

## Selection and gating

- Source: the recorder's existing eligible universe — the same read-only
  `/status` handoff the shadows consume. No new discovery, no new venue
  permission.
- **Stage 1 decides participation.** `p_act ≥ threshold` at the preregistered
  H = 10 s, thresholds train-only, artifact `l5_act_wait_v1.json`
  (sha `c176813c250bacd0`). A WAIT is a decision and is logged with its reason.
- **Stage 2 decides the action** (relation, side, size) with real
  `last_rel`/`last_size` context.
- A risk-cap or venue veto is attributed to **risk** or **venue**, never merged
  into the model's reason.
- **No new first leg while unrelated residual exposure exists** — carried
  forward unchanged from the spring discipline.
- Entry (`OPEN`) selection remains the known weak point: `p_act` at
  `last_rel=open` is **unvalidated** (PR-AUC 0.21 vs 0.72). See B.

## Maker / post-only

- **Post-only on every order. Taker is disabled, not merely discouraged.**
- No automatic late taker behaviour copied from mature August L5.
- Pair completion at the spring-discipline target (`≤ 0.96`); near-$1
  tolerance stays locked and is not unlocked by this probe.
- Cancel/replace on book movement rather than crossing the spread.

## Settlement and accounting

- Labels stay strict: `submitted → filled → settled → realized`. Nothing is
  realized before venue-confirmed settlement.
- A settlement whose trades lack the venue's `realizedPnl` is
  **SETTLED_REALIZED_UNCONFIRMED** and stays **out of every net** — as
  `aec-itfme-conbra-andpet-2026-09-16` does today.
- Matched-pair arithmetic is **derived**, never realized profit.
- Maker rebates, taker rebates, liquidity rewards and incentives are accrued
  separately and never summed into trading P&L. Pending is not income.
- The probe's cohort is stamped per service on every ledger row and **never
  merges** with the legacy or R1 cohorts.

## Exact stop conditions

The probe halts — cancel-only, positions preserved — on **any** of:

1. `analysis/l5/authorization/HALT` present (read **before** `armed()`);
2. mandate absent, malformed, expired, or bound to another service;
3. any envelope consumed: `max_total_spend`, `max_spend_per_market`,
   `max_residual`, `max_markets`, `max_total_markets`;
4. `max_daily_realized_loss` reached on **settlement-confirmed** realized P&L;
5. any transport refusal (`MandateRefusal`) — the probe stops, it does not retry
   at a smaller size;
6. any unhandled exception, venue auth failure, or ledger/venue quantity
   disagreement at reconciliation.

And two rules about *not* restarting, which are the lesson of 2026-09-16:

- **No automatic relaunch** after a completed or crashed probe. Money-moving
  sessions fail closed; read-only collectors self-recover.
- **Continuation, settlement, restart, resume and `advance_gate` can never
  enlarge or replace the active mandate.** A new market beyond
  `max_total_markets` needs a new authorization.

## What authorization does *not* include

Raising any cap; adding markets; enabling taker; near-$1 pair tolerance;
assuming any rebate or reward before it is credited and source-accounted;
tuning k, the thresholds, the model or the envelope from live results.
