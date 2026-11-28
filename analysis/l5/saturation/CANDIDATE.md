# Bounded-residual candidate for PMUS — prepared, not deployed

Nothing is wired into the money-moving engine. No deploy, no ARM, no risk-limit
change. This is the candidate, its unit conversion, its semantics and its gate.

## Production state right now

**A safety latch is set and it did not touch the running process.**
`PMUS_SPRING_SHUTDOWN=YES` on `pmus-spring-l5-probe`, applied with
`skipDeploys: true`. The running container's environment was fixed at its boot,
so the current campaign and the held position are **completely undisturbed** —
no restart, no redeploy, no cancel, no order.

What the latch guarantees: whenever the container next starts, for any reason,
`shutdown_requested()` is consulted **before `armed()` is ever reached**
(`pmus_l5_continuous_session.py:2498`), and the process enters `safe_shutdown`
instead of a new campaign. That path is exactly what was asked for:

> HALT → cancel OUR outstanding orders → VERIFY by a fresh `/v1/orders/open`
> read → stop submitting → **preserve held inventory to settlement, never
> liquidated** → disarm. This path can never place an order.

So an old-policy campaign cannot open again, and the current market is neither
abandoned nor force-closed. It settles.

**Still open, and it needs a decision:** the latch only fires on a container
start. The process that is running now (uptime 1,875 s at 00:34Z, 18 sessions,
holding `aec-mlb-cws-cle-2026-09-14`) keeps running the old policy until it
cycles. To stop it sooner requires a restart, which is a production action I
have not taken.

## The unit conversion — the thing that must not be got wrong

The replay's caps are in **L5 shares**. Production's `residual_cap_units` gates
on `behavioral_residual()`, which is **units** — contracts ÷ the executable
contracts in one behavioral unit. They are related by L5's own p50 fill size for
the state and relation, which is **state-dependent, not a constant**.

    replay L5 shares  →  behavioral units  →  venue contracts / dollars
                      ÷ shares_per_unit    × unit_contracts

Derived from the live state, never defaulted, at the observed
`shares_per_unit = 10.0` and `unit_contracts = 1.88`:

| arm | L5 shares | behavioral units | venue contracts |
|---|---|---|---|
| CONTROL | — | — | — |
| cap20 | 20 | **2.0** | 3.76 |
| cap35 | 35 | **3.5** | 6.58 |
| cap50 | 50 | **5.0** | 9.40 |
| **production today** | **60** | **6** | 11.28 |

**Setting `residual_cap_units = 20/35/50` would have been a ~10× error** and
would have loosened the cap by an order of magnitude rather than tightening it.
Instead the control gates on `behavioral_residual_shares()`, which the engine
already maintains in exactly the replay's unit, so no conversion is needed at
decision time and the factor is only used for reporting.

**Every arm is tighter than production's cap of 6 units (= 60 L5 shares), which
sits above the 20–50 saturation band.** No arm increases exposure. When
`shares_per_unit` cannot be established the report emits `null` with a reason —
never a default of 10.0, because a wrong factor rescales the cap silently.

## What a cap means — and the four things it does not

A cap **stops increasing** directional residual once reached. It does **not**
liquidate the residual, **not** refuse an economically useful complement fill,
**not** wait for a cheap-pair threshold, and **not** force completion at expiry.
Once capped the engine may keep doing anything that reduces residual or grows
matched inventory, under the unchanged executable economics and safety envelope.

Each of those is pinned by a test, and two of them structurally rather than by
assertion: `decide()` takes **no price and no pair** argument, so a saturated
arm *cannot* refuse a repair for being expensive; and it takes **no TTE**, so no
arm can behave differently near expiry or force a completion there.

**One documented consequence, not a defect:** the cap is a stop-adding
threshold, not a hard ceiling. With 10-share fills, cap35 stops at 40 — the fill
taken while just under the cap lands above it. A hard ceiling would require
refusing a partial fill the venue already gave us, which the fill standard
forbids: we can decline to act, never decline what filled.

## Prospective shadow, four arms at once

`src/l5_saturation_shadow.py`. Per decision it records, for CONTROL and each
cap: action, price, size, side, fill/no-fill, maker-fill evidence (null + reason
when the venue serves none), rejection, actual inventory, matched inventory,
residual in both contracts and L5 shares, worst residual, effective pair cost
(null + reason when only one leg is held), venue-confirmed rebate, and at
settlement the matched P&L, residual P&L and stranded residual **kept apart and
never summed into one number**.

**The fill standard is the guarantee.** An arm that would have refused an action
simply does not receive that fill; it can never receive a *different* one,
because no such fill exists in the record. A refusal is a subtraction, never a
substitution — pinned by a test asserting each capped arm's fills are a strict
subset of CONTROL's.

**PMUS economics, not L5's.** Costs are the venue's own cash for our fills and
the rebate is its `commissionNotionalCollected` on our side. The L5 crypto
acquisition fee is **not** applied — it was measured on Polymarket crypto, and
our PMUS maker fills show no such wedge. Liquidity rewards are tracked
separately and never enter profitability; estimated incentives enter nothing.

## Advancement gate

Six criteria, each answered separately, **sample size reported first and
gating the verdict**:

1. materially reduces residual exposure vs CONTROL
2. retains actual supported complement/matched fills
3. positive fee-corrected expectancy on settled shadow campaigns
4. **not achieved from directional residual** — the matched leg must carry the
   result (`matched_pnl > 0` and `matched_pnl ≥ net`)
5. acceptable worst-case stranded exposure
6. **beats CONTROL**, not merely positive

A small sample **fails** on sample size rather than being reported as a bp/$
headline; the default minimum is 30 settled shadow campaigns. A test pins that
an arm which is profitable only because its residual happened to win fails
criterion 4.

## Not done, deliberately

* Not wired into `pmus_spring_l5_session.py`. Integration touches a watched file
  and would redeploy the money-moving service.
* No arm selected. The winner is not chosen on TRAIN; all four run
  prospectively on identical PMUS states and the comparison comes to you first.
* No automatic ARM after a shadow pass.
* No change to dollar exposure, concurrency, daily loss budget or pair ceiling.

3,852 tests pass.
