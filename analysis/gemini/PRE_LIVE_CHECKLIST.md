# MANDATORY PRE-LIVE CHECKLIST — Gemini

**Nothing on this list may be waived. Each item is a control that is
currently absent, inert, or unproven in the shadow collector and that MUST be
active and demonstrated before any order carrying money is sent.**

Shadow safety and live safety are different problems. A read-only collector
is safe because it cannot place an order (`mutations_sent = 0`, no session is
ever constructed). A live probe is safe only because its bounds actually
fire. Several bounds that exist in the code are *not exercised* in shadow,
and this file exists so that is discovered here rather than live.

---

## PL-1 — `MAX_ACTIONS` is NOT an effective bound in the shadow collector

**Status in shadow: INERT. Required live: ACTIVE and tested.**

`WindowGuard.record()` is what increments `actions_taken` and trips
`stop("max_actions %d reached")` at `MAX_ACTIONS = 12`.
**`src/gemini_execution/shadow_soak.py` never calls `guard.record()`.**
`WindowGuard.step()` duly passes `actions_taken=self.actions_taken` into
`plan()`, but that value stays `0` for the life of every window, so the
12-action budget never binds and `actions_remaining` is always reported as 12.

**Why this is acceptable for THIS shadow run, and only this one.** The run is
`L5_CONTINUOUS_FAITHFUL`, whose entire purpose is to observe the L5 inventory
oscillator *uninterrupted*. An action budget that halted the process
mid-window would truncate exactly the behaviour under test, and it can cost
nothing here: the collector constructs no session, references no
`Driver.place` or `Driver.cancel`, and reports `mutations_sent = 0` on every
window by construction. **A budget that cannot be spent cannot be breached.**

**Why it is NOT acceptable live.** With money attached, `MAX_ACTIONS` is the
control that stops an order loop running away inside one window. It is the
difference between a bounded probe and an unbounded one. A live runner that
inherited this collector's call pattern would carry the *description* of a
12-action bound with none of its effect — which is precisely the failure
`WindowGuard`'s own docstring was written against: *"`plan()` took an
`actions_taken` count from a caller that did not exist. A description is not
a control."*

**Required before live:**
1. The live runner calls `guard.record(action)` for **every** action it
   transmits — not every action it plans.
2. A test drives the runner past 12 actions in one window and asserts it
   STOPS, with `stopped_because` naming the budget.
3. The stop is verified to be **terminal for that window** and to leave no
   working orders behind.
4. `actions_remaining` is asserted to decrease in the live evidence record.

*Recorded 2026-09-20. Found while verifying assumption A3 of
`L5_CONTINUOUS_FAITHFUL_SETUP.md`, which had cited the wrong call path.*

---

## PL-2 — the shadow's other bounds are untested under execution

`risk_gates` (imbalance ≤ 0.04, YES ≤ 0.10, NO ≤ 0.10, spend ≤ 0.15) and
`would_breach` are evaluated every cycle in shadow, but no order has ever
been refused *by the venue path* on their account. Before live, each must be
shown refusing a real transmission attempt, not merely a hypothetical rung.

**Note the gap already visible:** `would_breach` checks imbalance and the
per-leg caps but **not `MAX_TOTAL_SPEND`**. Spend is only checked by
`risk_gates`, which is a whole-turn stop rather than a per-action refusal. A
live runner must not rely on `would_breach` alone to keep spend bounded.

---

## PL-3 — deadman and cancellation

`DEADMAN_S = 20.0` causes `plan()` to withdraw on a stale market state, and
`WINDOW_WITHDRAWN` sets `cancel_all`. Neither has ever cancelled a real
resting order. Before live: demonstrate an actual cancel-all on a real
working order, by `orderId`, and prove the ack path records it.

*(The `-1013 invalid orderId format` defect — cancel sent a `clientOrderId`
where the venue requires `orderId` — was found and fixed on
`CONTINUOUS_WINDOW_3`. It must be re-demonstrated live, not assumed.)*

---

## PL-4 — restart policy

Both shadow collectors run `restartPolicyType: ALWAYS`, correct for a
read-only process. **Any money-moving service must run `NEVER`** and must not
relaunch itself after a crash or a completed probe.

---

## PL-5 — settlement and cash reconciliation

Terminal shadow payoff is computed from the venue's published
`resolutionSide`. That says what the CONTRACT did, never what the ACCOUNT
received. Before live, the authenticated cash path must be reconciled
end-to-end on a real settled position, as was done once on
`aec-cs2-mzp-ezt-2026-09-13`.

---

## PL-6 — rebate sizing

The published maker rebate rounds DOWN to the cent daily. At `q = 0.01` a
day's whole accrual is arithmetically below one cent, so a probe at that size
pays every fee and earns no rebate. Any probe intended to observe rebate
economics must be sized accordingly, and any that is not must state that its
rebate line will be `$0.00` by construction.
