# Three-window arming: steps 1 and 3 done, step 2 blocked. NOT ARMED.

Deployed shadow-only and verified. **The engine is shadow-locked and unarmed. No
authorization payload was written. No order can reach the venue.**

---

## Step 1 — deploy the tested fix, SHADOW_ONLY enabled ✅

| | |
|---|---|
| deployment | `339b1e5a-1419-4218-aff6-7a343bca88fb`, **SUCCESS** 15:25:41Z, us-east4 |
| commit | **`9323a8c115a06be6ced653fcee02725a667c05ff`** |
| engine code vs the tested fix | `git diff --stat 0febc315..HEAD -- src/ tests/ deploy/` is **empty** |
| shadow lock | set to `1` **before** the push, with `skipDeploys`, so the new container never started unlocked |

`L5_SHADOW_ONLY` is fail-safe: `shadow_only()` returns True unless the value is
exactly `"0"`.

## Step 3 — pre-arm state, cross-confirmed from two independent sources ✅

Engine heartbeat 15:27:29Z on the new deployment:

| | |
|---|---|
| armed | **false** |
| submission gate | **blocked: true, reason `SHADOW_ONLY_LOCK`** |
| `shadow_lock` | **true** |
| previous campaign | `approval_state: approval_expired` (was 8/8 windows, `not_after` 15:15:00Z) |
| open venue orders | **`live_orders: 0`** |
| reconciliation | **`unresolved_accounting.count: 0`** |
| halt latch | `CLEAR`; `halt_local_env: false` |
| book | connected, `stream_age_s 0.0` |
| ledger | day −1.332800, all-time −12.95410000 |

Independent `l5-halt-watchdog`: 20-second sweeps, `cancel_enabled: true`,
`open_orders: 0`, `overdue`/`unverified`/`still_open`/`not_ours` all empty,
`read_error: null`, heartbeat fresh, `halt: false`.

## Step 2 — verifier passes; the residual-decision check is **unobtainable** ⛔

**`L5_EFFECTIVE_RUNTIME_CONFIG`: `PASS: true, checked: 26, failed: []`**, read
from the money path's own accessors rather than variable text. Every preserved
limit confirmed:

| check | effective | required |
|---|---|---|
| `opening_notional_budget_usd` | **0.80** | 0.80 |
| `max_worst_case_deficit_usd` | **2.00** | — |
| `aggregate_exposure_usd` / in-force (frozen at boot) | **5** / **5** | 5 |
| `campaign_additional_loss_usd` | **5.00** | 5.00 |
| `daily_realized_loss_halt_usd` | **5** | 5 |
| `maker_pair_ceiling` | **0.96** | 0.96 |
| `max_live_orders` | **1** | 1 |
| `maker_only_forbids_defensive_crossing` | **true** | true |
| `defensive_execution_enabled` | **false** | (not graded) |
| `shadow_only` | **true** | (not graded) |
| `mandate_signing_key_absent_from_runtime` | **true** | true |

### The blocker

**"One correct shadow-window residual decision" cannot be produced in
shadow-only mode. It is circular, and the circularity is caused by my own
design choice.**

The chain, each link verified in source rather than assumed:

1. `probe_leg_order` and `probe_leg_qty_cap` are called from **exactly one
   place** — `l5_live_execution.py` inside `probe_round`, lines 5528 and 5760.
   Nothing else in `src/` calls them.
2. The shadow controller (`l5_shadow_controller.py`) **does not call them at
   all** (0 references). It is an independent modelled path.
3. `ShadowOnlyTransport.BLOCKED = ("submit",)` — no submission, therefore no
   real fill, therefore no real inventory, therefore **no residual**.
4. The fix is **deliberately inert when flat**: cheapest-first is preserved
   byte-for-byte at zero residual, specifically so a flat market's behaviour is
   unchanged.

So: a shadow-locked executor cannot acquire a residual, and when flat the fixed
code is *indistinguishable from the old code by design*. Confirmed empirically —
this deployment has emitted **zero** `leg_to_place` rows and **zero**
`L5_LIVE_DECISION` rows, because the executor's decision path does not run while
unarmed.

**The only way to observe a live-executor residual decision is with real fills,
which requires the armed state this prerequisite gates.** I am not going to
declare this satisfied, and I am not arming past it.

---

## Two ways forward — your call

### Option A: make the prerequisite satisfiable (recommended)

Add a **boot-time self-check** to the live runner that runs the residual-side
logic against the two recorded states and emits the verdict — in the container,
on the deployed artifact, with zero venue interaction:

```
L5_RESIDUAL_SIDE_SELFCHECK {"PASS":true,
  "cases":[{"state":"1245z up 1.4 / dn 2.8","signed":"-1.4","leg":"UP","ok":true},
           {"state":"0130z dn 1.87",        "signed":"-1.87","leg":"UP","ok":true},
           {"state":"0145z dn 1.56 (no-op)","signed":"-1.56","leg":"UP","ok":true},
           {"state":"flat (cheapest-first)","signed":"0","leg":"DOWN","ok":true}]}
```

This is the behavioural analogue of the verifier that already exists for
configuration — *"the only configuration that matters is the one the running
process actually has."* It is additive diagnostics: no new service, no strategy
change, no new capability, and it makes this class of prerequisite checkable for
every future lifecycle fix rather than just this one.

Cost: ~20 lines, its own tests, and one more shadow-only deploy (~3 minutes).
**I have not written it, because it is code on the money-capable service beyond
what you approved.**

### Option B: accept the substitute evidence and arm

The evidence that does exist, in full:

- deployed commit **`9323a8c1`**, and `git diff 0febc315..HEAD -- src/ tests/`
  **empty** — the deployed engine code is byte-identical to the commit I tested;
- **22 targeted tests** reproducing the exact 1245z and 0130z wrong-side states
  and pinning 0145z unchanged, against that code;
- full regression **58 failed / 13,351 passed / 23 errors** — exactly the
  established baseline, zero new failures;
- verifier **26/26** with every limit above confirmed in the running process;
- the **first live window captures the residual decision** as its first
  evidence, and if the fix were somehow absent the behaviour is simply the old
  behaviour, bounded by the unchanged $0.80 / $2 / $5 / $5 limits.

What Option B cannot give you is in-container proof of the *logic* before money
moves. That gap is the whole of the difference between the two options.

---

## Ready to arm the moment you choose

Nothing below is written yet. The payload is generated at arm time because the
validator compares `issued_utc` against its own clock.

- fresh campaign id `l5-btc15m-sidefix-<date>-<8 hex>`
- `max_windows: 3`, series `cpc-btc-updown-15m-`, `max_total_exposure_usd: 5`,
  `max_daily_loss_usd: 5` (inside the `$20`/`$5` code ceilings, which intersect
  and never widen)
- **automatic expiry = the third window's close**, so the campaign dies on its
  own with no manual disarm
- submission budgets **tightened** pro-rata for three windows, never expanded:
  OPEN 6, COMPLETE 9, REPLACE 12, backstop 21 (from 16 / 24 / 32 / 56 for eight)
- `L5_SHADOW_ONLY` → `0` last, in the same single write, so there is one restart
