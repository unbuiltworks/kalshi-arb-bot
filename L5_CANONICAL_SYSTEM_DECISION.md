# One canonical system — the shortest safe path

## What is running

| service | SHA | mode | auth | can place orders? |
|---|---|---|---|---|
| **`l5-pmus-live`** | serving, BTC15m, policy `RESIDUAL_CAP_k2` frozen `030d8bf` | **shadow-only, `armed=false`** | **working throughout** | no — no live authorization |
| **`pmus-spring-l5-probe`** | **`6adcc4c43e67`** (deployment `952ae54f`, SUCCESS) | **`SHUTDOWN_VERIFIED`**, cancel-only, rung **R1**, requote unset, arming triple absent | **FIXED** | no — structurally incapable in shutdown |

Neither is placing orders. They are **not** two implementations of one strategy: different engines,
different market families (BTC15m vs sports/props), different completion ceilings (0.96 vs par).

## What was fixed

The spring probe's 401. `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` set to Railway **references** to
`l5-pmus-live`'s, then the **same build** redeployed so the credential was the only variable. First
authenticated venue read in twelve days succeeded:

```
safe_shutdown ... "sweep":{"seen":0,"cancelled":[],"foreign":[],"errors":[]}
```

`sweep_orphan_orders` calls `_get_with_retry(t, "/v1/orders/open")` and appends to `errors` on
failure. `errors: []` is the proof. No key generated, rotated, revoked, read or printed.

Boot also ran clean: `PMUS_R5FT_LEDGER_REPAIR` (2 markets `already_reconciled`) and
`PMUS_R5FT_SCOPE_REPAIR`, which reports **69 of 70** canary filled markets already `settled_here`.

## The outstanding exposure is a LEDGER artifact, not venue exposure

This is the part that decides the architecture, and it is evidence, not opinion:

- `l5-pmus-live`'s account-wide scan — *"every market with a position OR a resting order, manual and
  L5 alike"* — reports `markets_counted: []`, `account_total_worst_case_usd: "0"`,
  `capacity_usd: "5"`. The shared account has **no venue positions** in the spring markets.
- The spring probe reports `held_preserved: []`, `still_open_ours: []`, `foreign_open: []`,
  `open_orders_at_venue: 0`.
- The scope repair says 69/70 canary markets are settled at the venue.

So the **−$17.2602 unsettled floor is computed from the spring probe's own ledger on its own
volume**, over markets the venue has already settled. The venue is flat.

**Therefore `l5-pmus-live` does not and cannot inherit venue exposure from the spring probe.** What
*is* outstanding is our **booking** of ~96 markets' realized outcomes, which lives only on the
spring probe's volume. Retiring that service before it drains would destroy that record — which is
exactly "hiding outstanding positions". **It must drain. It must not be deleted.**

## Can the live runner use the corrected execution logic? NO — not without new code

`grep -c pmus_spring_l5_session src/l5_pmus_live_runner.py` → **0**.

The cancel guard and R5FB exist only in `pmus_spring_l5_session.py` / `pmus_l5_continuous_session.py`.
`l5_pmus_live_runner` uses `l5_live_execution` + `l5_two_sided_shadow`, a different completion path
with a 0.96 `maker_pair_ceiling` and its own `l5_defensive_completion` (off). Porting the requote
into that engine is **new implementation on a live money path** — I have not done it and will not
without your word.

## The decision, stated plainly

**`l5-pmus-live` is the canonical system, and the shortest safe path to continuous live operation
does not include the requote fix.**

Why it is canonical: credentials never broke; it carries the $5 envelope, the independent
account-wide $5 daily halt, cumulative and exposure gates; its settlement attestation demonstrably
works (**43 `VERIFIED_L5_GROSS_SETTLEMENT` rows**); `l5-halt-watchdog` is SUCCESS and running; and
BTC15m gives 96 windows/day, which is what "continuous" means here.

What that costs: the COMPLETE-requote work is spring-engine code and does **not** come with it. R5FB
is built, tested and pushed, but it only runs in the engine that is not canonical.

**The spring probe's remaining job is to drain its 96-market ledger and then be retired** — not run
in parallel. That removes the second implementation without losing a single record.

## What still prevents real orders

| on `l5-pmus-live` (the canonical path) | |
|---|---|
| **no live authorization** | the Oct 25 campaign expired 20:30Z and was not re-armed. **This is the only hard blocker.** |
| cumulative loss gate | `l5_realized_since_baseline` is **−$5.2457**; `L5_MAX_L5_CUMULATIVE_LOSS_USD` is the one number I have not read, because reading service variables returns credentials alongside it |
| account-wide daily halt | **$5.00** headroom today (UTC day rolled 00:00Z; yesterday closed −$13.6405 and does not carry) |
| everything else | clear — auth works, watchdog runs, exposure capacity $5 of $5, `open_orders_at_venue: 0` |

On the spring probe: reconciliation is draining; `combined_loss` −$17.2602 vs −$5.00 still refuses,
and will resolve as the ledger books the already-settled markets.

## Next required action — one of two, and it is yours

1. **If `l5-pmus-live` is the canonical system (my recommendation):** issue a fresh campaign approval
   for it — `approved`, `nonce`, `series cpc-btc-updown-15m-`, `issued_utc`, `expires_utc`,
   `max_windows 3`, `max_total_exposure_usd 5`, `max_daily_loss_usd 5` — and tell me the
   cumulative-limit value (or that I may read non-secret config variables). Nothing else is needed;
   it runs tonight at the existing $5 envelope with every gate intact.
2. **If the requote fix must be in the canonical system:** authorize porting it into
   `l5_live_execution`. That is new code on a live path, needs its own tests and offline bar, and is
   slower than (1).

**Frozen either way:** $5 envelope, $1.50 per-market cap, 3-market maximum, −$5 stop, `l5_touch`,
par ceiling, `combined_loss_above_stop`. No new service, branch, strategy or capital.
