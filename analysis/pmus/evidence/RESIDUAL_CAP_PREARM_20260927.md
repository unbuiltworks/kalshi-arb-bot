# RESIDUAL_CAP_k2 pre-ARM validation — 2026-09-27

**Not armed. No order placed. No change to the policy, executor, k, envelope,
retention floors or stop criteria.**

## 1. Before: `L5_NATIVE`, `PMUS_RESIDUAL_CAP_K` absent

Deployment `2906ef50` (commit `61e9116`), heartbeat 11:12z-minus:

```
"policy": {"name": "L5_NATIVE", "residual_cap_k": null,
           "residual_cap_binds_session": 0, "max_clip_filled": {}}
```

`list-variables` on service `87f1834e` returned 21 names with no
`PMUS_RESIDUAL_CAP_K` among them.

## 2. `PMUS_RESIDUAL_CAP_K=2` set, redeployed disarmed

`set-variables` returned `variableNames: ["PMUS_RESIDUAL_CAP_K"]`,
`skippedDeploys: false`. Deployment `5664adca`, same commit `61e9116`,
SUCCESS 11:12:17z. `list-variables` now returns 22 names including
`PMUS_RESIDUAL_CAP_K`.

## 3. After: heartbeat, deployment `5664adca`

```
"policy": {"name": "RESIDUAL_CAP_k2", "residual_cap_k": "2",
           "residual_cap_binds_session": 0,
           "residual_cap_binds_this_market": 0, "max_clip_filled": {}}
"armed": false        "owner_arm_token": false      "halt": false
"preflight": "PASSED"                "preflight_attempts": 1
"live_orders": 0  "parked_orders": 0  "quarantined": []  "pending_terminal": []
"realized_attributed":   "0.62060000"
"realized_unattributed": "-2.62630"
"realized_total":        "-2.00570000"
"limits": max_usd_per_market 5, max_abs_residual 10, max_total_exposure_usd 5,
          max_concurrent_markets 1, max_live_orders 8,
          daily_realized_loss_halt_usd 5
"lambda": "0.04518752824220514525"     "frozen_rule": "030d8bf"
retired_markets: the same six
```

Reconciliation, same deployment, 11:12:23z:

```
"reconciled": true      "drift": {}          "authority": "VENUE"
"open_orders_at_venue": 0       "uncertainty_cleared": true
"live_orders_pending_terminal": []  "live_orders_released_unacked": []
"quarantined": []       "realized_today": "-2.00570000"
```

Preflight: `account_binding_proven` → `keyfp:d51b44a3fa83|funded:59.69195`,
matching the authoritative post-probe anchor. `owner_arm_token_present`
false and non-blocking: *"arming is a separate owner act; preflight passing
does NOT arm."*

## 4. The gate, exercised

**What a disarmed stream can and cannot show.** Disarmed, nothing is
submitted, so nothing fills, so `residual` never leaves zero, so every action
classifies `FLAT_OPEN` — and the gate sits behind `INCREASE_RESIDUAL`. The
live counter therefore reads `residual_cap_binds_session: 0`, which is the
correct and expected reading and is **not** evidence the gate works. A live
disarmed stream can only show the policy is wired.

So the gate is exercised by shadow replay of **real captured live decisions**
— each row's side, price, size and book are exactly what the deployed
executor emitted — through two executors, `residual_cap_k=None` and
`residual_cap_k=2`, with fills booked.

Two assumptions, both stated because both are load-bearing:

- **Fills are assumed.** Every submitted order is booked as filled in full.
- **`maker_only` is relaxed** to `False`, otherwise the taker-mode rows are
  refused by the frozen path before the cap is reachable and no residual ever
  builds. This can only *increase* how many actions reach the cap, so the bind
  count below is an upper bound, never an understatement.

### `cpc-btc-updown-15m-2026-09-27-0345z`, 9 live decisions

| # | side | l5 px | class | resid | clip | cap | after | NATIVE | k2 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | DOWN | 0.57 | FLAT_OPEN | −0.89 | — | — | — | SUBMITTED | SUBMITTED |
| 2 | DOWN | 0.570001591 | INCREASE | −0.89 | 0.89 | 1.78 | 0.90 | SUBMITTED | SUBMITTED |
| 3 | DOWN | 0.54 | INCREASE | −0.90 | 0.90 | 1.80 | **1.80** | SUBMITTED | **SUBMITTED** |
| 4 | DOWN | 0.55 | INCREASE | −1.80 | 0.90 | 1.80 | 2.70 | SUBMITTED | **RESIDUAL_ABOVE_CAP** |
| 5 | UP | 0.51 | REDUCE | −1.58 | — | — | — | SUBMITTED | SUBMITTED |
| 6 | UP | 0.49 | REDUCE | −0.68 | — | — | — | SUBMITTED | SUBMITTED |
| 7 | UP | 0.48 | REDUCE | −0.17 | — | — | — | SUBMITTED | SUBMITTED |
| 8 | DOWN | 0.49 | INCREASE | −0.17 | 0.90 | 1.80 | 1.07 | SUBMITTED | SUBMITTED |
| 9 | DOWN | 0.63 | INCREASE | −1.07 | 0.90 | 1.80 | 1.97 | SUBMITTED | **RESIDUAL_ABOVE_CAP** |

NATIVE 9 submitted, final residual −2.87, peak 2.87.
k2 7 submitted, final residual −1.07, peak **1.80**, 2 binds.

### `cpc-btc-updown-15m-2026-09-27-1100z`, 3 live decisions (k2 deployment)

| # | side | l5 px | class | resid | clip | cap | after | NATIVE | k2 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | DOWN | 0.04 | FLAT_OPEN | −0.90 | — | — | — | SUBMITTED | SUBMITTED |
| 2 | DOWN | 0.02 | INCREASE | −0.90 | 0.90 | 1.80 | 1.80 | SUBMITTED | SUBMITTED |

The third row quantised to 0 contracts and submitted nothing under either
policy. 0 binds.

### Every branch of the gate is traversed

- **Row 1, 1100z-1**: `FLAT_OPEN` — cap not evaluated, dash not "allowed".
- **Row 3**: `after == cap == 1.80` — **equality passes**, on live inputs.
- **Row 4**: `2.70 > 1.80` — refused.
- **Rows 5–7**: `REDUCE_RESIDUAL` — cap not evaluated at any depth; the
  repair walks the residual −1.80 → −0.17.
- **Row 8**: `1.07 < 1.80` — passes again. **The cap is not a latch**: it
  re-opens the moment repair happens, which is the difference between a
  residual bound and a same-side lockout.
- **Row 9**: refused again once the residual has rebuilt.
- **Every k2 refusal falls on a row NATIVE submitted.** The subset property
  holds on live inputs, not only in tests.

### Retention on this sample, and why it is not an estimate

0345z: action retention 7/9 = **0.778**, contract retention 4.33/6.13 =
**0.706**. 1100z: 2/2 = 1.000 both.

Both clear the declared floors (0.75 / 0.70) — 0345z only just. **Nine
decisions is far too small to estimate retention**, the side mix is whatever
happened to be captured rather than a representative window, and the relaxed
`maker_only` inflates the bind count. The floors are declared over the probe,
not over a fragment. Nothing here should be read as evidence about retention
in either direction.

## 5. A defect found and fixed in the shadow harness

The first run booked decisions from two different markets into one inventory
ledger, because it took `market` from the first row and applied it to all of
them. That manufactured residual that never existed — the same class of error
as the `""` market key. It reported action retention 0.636 and contract
retention 0.546, both below the floors, and both wrong. The harness now walks
one ledger per market. The numbers in §4 are from the corrected run.

The harness is analysis-only and is not on the execution path.

## 6. State at the end of this validation

Armed: **no**. Orders placed: **none**. Live orders 0, parked 0, quarantined
0, pending terminal 0. Accounting anchor `-2.00570000`, unchanged. Policy
`RESIDUAL_CAP_k2` wired and reporting, with zero live binds because a
disarmed executor cannot reach the gate.

## 7. Finding: a FAILED preflight is never logged. Not fixed here.

While confirming the state was clean I watched 1115z sit at
`"preflight": "FAILED"` from 11:15 through `preflight_attempts: 8` with **no
`L5_LIVE_PREFLIGHT` record emitted at all**. It later passed on attempt 13 at
about 11:20 — the venue had not yet listed the market on REST, which is the
benign case the retry backoff exists for. But the silence is not benign.

`src/l5_pmus_live_runner.py:305`:

```python
blockers = rep.get("blocking_failures")
# log every PASS, and a FAIL only when what blocks it changes
if self.preflight_status == "PASSED" or blockers != self.last_blockers:
    emit("L5_LIVE_PREFLIGHT", rep)
self.last_blockers = blockers
```

`PmusTransport.preflight()` **never returns a `blocking_failures` key**. Its
report carries `checks`, each with its own `passed` and `blocking` flags;
`blocking_failures` exists only in `src/l5_preview_session.py`, a different
module. So `blockers` is always `None`, `roll()` sets `last_blockers = None`,
`None != None` is False, and the emit condition reduces to
`preflight_status == "PASSED"`.

**Only a PASS is ever logged. A failing preflight is silent forever**, and the
sole evidence it is failing is the heartbeat's `preflight` field and a rising
`preflight_attempts`. The intent in the comment is right; the key is wrong for
this transport.

Status:

- **Pre-existing.** The same line is at `283` in frozen `e2bd442`, and
  `git diff e2bd442 -- src/l5_pmus_live_runner.py` touches neither
  `maybe_preflight` nor `blocking_failures`. Not introduced by RESIDUAL_CAP.
- **Fail-closed.** `try_arm()` is reached only on PASSED, so a silent failure
  cannot arm anything. It is an observability defect, not a safety one.
- **Pre-ARM relevant anyway.** If the probe will not start, the executor does
  not say why, and stop criterion 5 and the arm gate both read preflight
  state. Diagnosing a stalled probe would mean reading heartbeat counters and
  guessing.

**Not fixed in this pass**, because the instruction for this validation was to
change nothing in the executor. The one-line repair would be to compare the
failing check names — `[c["check"] for c in rep.get("checks", []) if not
c["passed"] and c.get("blocking", True)]` — instead of a key the report does
not contain. It needs its own authorization, its own test, and its own
disarmed deploy.
