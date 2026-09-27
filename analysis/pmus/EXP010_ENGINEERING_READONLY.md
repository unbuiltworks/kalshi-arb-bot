# EXP-010 read-only engineering (items 1–8)

No mandate created, no HALT removed, nothing armed, redeployed or ordered.

## 1. Canonical live executor architecture after the 09-16 breach

Four layers, each independently sufficient to stop a spend
(`analysis/l5/live_probe/EXECUTION_ARCHITECTURE.md`):

```
committed HALT file ──► shutdown_requested()   [before armed() is consulted]
committed mandate ───► load_mandate()          [absent = NO_MANDATE = no orders]
every campaign pass ─► expiry + latch re-read  [stops itself, unattended]
every order ─────────► GuardedTransport        [POST /v1/orders, at the wire]
```

- **Authorization is the immutable mandate**, not an arm token and not a rung.
  `src/pmus_live_mandate.py`: frozen dataclass, **every field required** (a
  mandate missing one "is not a weaker mandate, it is not a mandate"), expiring.
  Continuation, settlement, restart, resume, `advance_gate` and historical rung
  approvals all run *underneath* it.
- **Spend is priced by economic effect, never by name.** `order_cash_effect()`
  undoes the venue's long-side wire convention (a NO leg at 0.43 is charged
  $0.43×qty, not the 0.57 on the wire), charges a SELL at worst case, charges an
  unrecognised action at full contract value, and **refuses** an unpriceable body.
- **`counts_against_market_cap()` now returns `True` for every stage**, including
  ones not yet invented. There is no list left to forget to extend.
- **Admission takes an exclusive `flock`**, re-reads consumption from the durable
  ledger, decides, and writes its commitment **inside the same critical section**.
  A commitment is charged **in full** until a venue terminal row proves a smaller
  fill; cancels/retries release only their own unfilled remainder.
- **Monitoring detects, never prevents.** On 09-16 the only thing between the
  campaign and 11 markets was a 20-minute chat reminder that stopped being
  re-armed.

Canonical live path: **service `l5-r1-probe`** running
`src/pmus_l5_continuous_session.py` → `src/pmus_spring_l5_session.py` →
`src/pmus_micro_executor.py`.

## 2. Every order path is behind GuardedTransport — including COMPLETE

`POST /v1/orders` is reached from exactly one place in the executor stack,
`src/pmus_micro_executor.py:384` (`t.request("POST", "/v1/orders", body)`), where
`t` is the guarded transport; `src/l5_pmus_transport.py:1110` is the wrapped
transport itself. The architecture names four executor call sites
(`pmus_q1_executor`, `pmus_q1_executor_v4`, `pmus_micro_executor`,
`pmus_migration_close_live`), all wrapped.

**COMPLETE/complement orders are covered by construction, not by a list:** they
are ordinary `place_limit_buy` calls through the same transport, and
`counts_against_market_cap()` returns `True` for every stage. The
`micro_complement_taker_*` and `spring_complete_*` stages have no separate path.

## 3. The two 09-16 faults are already pinned by regression tests

Both exist at HEAD and pass — I verified rather than duplicated them
(`tests/test_live_mandate_adversarial.py`):

| test | fault pinned |
|---|---|
| `test_5c_a_rung_raising_the_default_cannot_raise_the_mandate` | historical rung escalation cannot change immutable mandate limits |
| `test_6_and_7_complete_consumes_the_envelope_and_is_cut_off_by_it` | COMPLETE cash counts against market/total spend |
| `test_7c_no_stage_name_exists_that_escapes_the_envelope` | the stage-name allow-list can never regress |
| `test_8b_retrying_a_cancelled_order_does_not_hand_back_the_filled_cash` | commitment charged in full until a terminal row proves less |

`tests/test_live_mandate_{adversarial,deployment,ledger_invariant}.py` +
`test_mandate_concurrency_lifecycle.py`: **77 passed.**

## 4. Smallest change for ECONOMIC_COMPLETE's taker completion

**Smaller than I previously reported. No new machinery is required.** My earlier
claim that the proven path "has no taker code path at all" was true of the
*session layer* docstring but wrong about the *executor* it calls:

- `src/pmus_micro_executor.py` `place_limit_buy(..., post_only: bool = True)`
  already supports `post_only=False`, and **already calls it** at line 467 as
  stage `micro_complement_taker_{attempt}`.
- It is guarded: `TAKER_PRICE_STALE` refuses a taker order that would not cross
  (`if not post_only and price < fresh["ask"]`).
- `src/pmus_q1_executor.py::taker_completion_ok(first_cost, ask_cost)` already
  implements the all-in pair check *including* the taker fee, sourced from
  `pmus_fees` (θ_taker 0.0695, the post-2026-09-17 coefficient).

So the minimal change is **wiring, not capability**: have the session's completion
decision consult the shadow's marginal rule
(`pmus_completion_shadow.marginal_complete`, θ=0.96, per-level) and, when it
qualifies, call the existing `post_only=False` complement path instead of the
maker-only one. The safety envelope is untouched — same transport, same
`order_cash_effect`, same mandate caps. **Not implemented, not deployed.**

## 5. Capacity-ladder design (advancement on fills + reconciliation)

`max_residual` is **dollar-denominated** in the mandate (`_MONEY`, compared
against `residual_usd`) and stays independently bounded — it is the control that
prevents unsettled exposure circumventing the cumulative stop.

| | q10 | q25 | q50 |
|---|---|---|---|
| advancement gate | — | q10 passed | q25 passed |
| `max_residual` (USD, independent) | 2.00 | 2.00 | 2.00 |
| worst case = q × p, so entry band | p ≤ 0.20 | p ≤ 0.08 | p ≤ 0.04 |
| `max_spend_per_market` (completes counted) | 10.50 | 25.00 | 49.00 |

Advancement requires, per rung: ≥3 markets with an authoritative fill at the
rung's clip; `filled_qty ≥ 0.90 ×` intended; settlement reconciled and
authoritative cashflow matched; zero accounting/safety discrepancy; cumulative
realized inside the envelope. A discrepancy ⇒ **DISARM and diagnose**.

**None of this is currently authorized** — see BUDGET_001: $0.49320 remains, and
every rung budget above exceeds it.

## 6. The 5.31-contract fill is SPORTS capacity evidence, not BTC15m

`analysis/l5/r4_orders.jsonl`, 2026-09-07, 10 filled orders, max **5.31 @ $0.47**
(`CBER077HYQ3R`). Markets: **`aec-atpcq-aleher-tundur-2026-09-07`,
`aec-atpcq-norgom-ollwal-2026-09-07`, `aec-atp-petbru-alecan-2026-09-07`** — all
**ATP tennis**. Produced by `pmus_spring_l5_session` at the owner-approved R4
sizing (per-action $2.50).

**Explicitly labelled: tennis/sports capacity evidence. It is NOT BTC15m capacity
evidence.** We have **zero** authenticated live fills on any BTC15m market. Every
BTC15m clip figure — q5 included — is modeled.

## 7. Authenticated account state — NOT ESTABLISHED, fails closed

Credentials live on **`l5-r1-probe`** (`PMUS_API_KEY_ID`, `PMUS_API_SECRET_KEY`,
plus the spring arm trio and a durable Railway volume). They are **not reachable
from this container**, and no value was requested, printed, copied or moved.

Balance, positions, open orders and unsettled markets are therefore **UNVERIFIED**.
Per the fail-closed rule that is a refusal, not a warning. What *is* established
read-only: the **HALT file is present** ("HALTED 2026-09-18 — the authorization
EXPIRED") and the on-disk **mandate is expired** (`expiry 2026-09-18T00:41:20Z`,
`authorization_id OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT`,
`service l5-r1-probe`). Note its `max_daily_realized_loss` was **$6.00**, above the
$5.00 halt — it must not be reused as-is.

Hardening note, non-blocking: `sealedVariableNames: []` on `l5-r1-probe`, so the
credentials are stored unsealed.

## 8. No new service, no redeploy

Nothing was created, nothing redeployed. All of the above is read-only inspection
of committed code, committed authorization artifacts, and Railway variable
*names*. `pmus-q1-executor` remains untouched as the historical fail-closed q=1
canary.
