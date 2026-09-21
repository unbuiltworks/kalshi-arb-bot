# Gemini q=1 lane — exact state, 2026-09-21T10:40Z

**NOT READY at q=1.** Ready to mutate but unauthorized **at q=0.01 only**.
Auth constants are `None`. No order was placed by this session.

## The lane is on a different branch from mine

| | |
|---|---|
| lane branch | `origin/claude/gemini-btc-economic-viability-h3u7zl` |
| head | `b2c19ffd` (automated read-only settlement-gate evidence) |
| last engineering commit | `471cd7fa` 2026-09-21T07:55:38Z |
| my designated branch | `claude/l5-execution-instrumentation-io6nzi` |
| merge-base | `539d3838` — the lane is **+9,861 lines** ahead on `src/gemini_execution/` |

I did not push to the lane branch. My standing instruction is never to push to a
branch other than my designated one without explicit permission.

## SHAs, computed from the lane branch head (not quoted from memory)

```
executable_sha  c1ada77a919c9c0cf226b4eb0c56aed6495f7e8fe1ece9261150aa6c26377763
envelope_sha    47f257f208017841d633c1bf5e2420eda2ca87b0ab502da34bd9d7e19690cf22
bundle_sha      1a6b5be0fecffcd109659fbde0b75f6124f4ecba21952962edb86d79ee711d94
```

`envelope_sha` is **not** `b3bff654…`. The hourly watch pins `b3bff654` as
"UNCHANGED across nine windows", and that is still correct **on my branch**; on
the lane branch the envelope dict gained two named sub-envelopes with their own
hashes, which is what moved it:

```
envelope_shadow_q001_sha  c5e88347601e24885e8cdb3cfb5b7a803b0b872948cce896c80a60023da51887
envelope_live_q1_sha      4e2818d53735999cda6c3de439f4a9e87a3cb2a98f9e7c10c2af125e145ed6b5
```

The watch baseline is stale against the lane branch, not drifting. Same for its
"NINE latch files / FIVE cohorts": the lane has **11 latches** and **6 cohorts**,
the additions being `OWNER-2026-09-21-GEMINI-Q1-LIVE-1`, `…-LIVE-2` and
`COHORT-2026-09-21-Q1LIVE-1` — all owner-issued.

## Authorization state — DISARMED, both grants SPENT

`REQUIRED_AUTH_ID`, `AUTHORIZED_SHA`, `AUTHORIZED_ENVELOPE` all `None`.
`arming.expected_auth_id` = `NO-AUTHORIZATION-IN-FORCE`.

`OWNER-2026-09-21-GEMINI-Q1-LIVE-1` and `-LIVE-2` are **SPENT** and may never be
reused. LIVE-2's latch is on disk in state `INTENT_PERSISTED`, so it cannot arm
again.

## The branch-pin / latch durability defect IS fixed, and proven in production

`25ba070a` ("DEFECT 3 — the latch's own branch pin"). Proof is not the commit,
it is the LIVE-2 run: `can_make_durable()` returned ok, `latch_pushable
{"ok": true}`, the latch was consumed, and the window ran **233 s instead of
refusing in 22 s**. Confirmed fixed.

## ACTUAL SUBMITTED ORDERS, ONE FILL, AND ITS SETTLEMENT

Run `35573430715`, symbol `GEMI-BTC05M2609210745-UP`, from the venue's own
`orderUpdate` frames:

```
07:40:48  YES NEW      0.1400  q 0.01  id 145828836318553127
07:40:48  NO  NEW      0.5800  q 0.01  id 145828836318553137
07:40:57  NO  CANCELED                 id 145828836318553137
07:40:58  NO  NEW      0.6800  q 0.01  id 145828836318554860
07:41:17  NO  CANCELED                 id 145828836318554860
07:41:19  NO  NEW      0.7700  q 0.01  id 145828836318559926
07:41:24  YES FILLED   0.1400  Z 0.01  id 145828836318553127   <-- THE FILL
07:41:30  NO  CANCELED                 id 145828836318559926
07:41:32  YES NEW      0.0200  q 0.01  id 145828836318562378
07:41:32  NO  NEW      0.8700  q 0.01  id 145828836318562392
07:42:43  NO  CANCELED                 id 145828836318562392
07:42:45  NO  NEW      0.9500  q 0.01  id 145828836318576678
07:44:06  YES CANCELED                 id 145828836318562378
```

`orders_live_after_stop 0`. Nothing stranded.

**SETTLEMENT** (`settlement_gate_20260921T075651Z.json`): verdict `SETTLED`,
`settlement_value_yes_side "0"`, `position_at_expiry "0.01"` YES,
`underlying_settlement_price 81493.5127`, source the PUBLIC contract object.

**ACCOUNTING, STRICT.** Submitted → filled → settled. The YES leg settled at
zero, so the $0.0014 that left on the fill is gone. `fills_cash "-0.0014"` and
`reconstructed_cash "-0.0014"` are a **RECONSTRUCTION**, and
`inferred_settlement_value` is `null` — no attributable cash movement has been
confirmed. **EXPECTED −$0.0014 directional residual. SETTLED, NOT REALIZED.**
No matched pair. Never sum this with a matched-pair line.

## WHY IT IS NOT READY AT q=1

The run was bounded by a q=1 envelope and **executed at q=0.01**.

`LIVE_Q1` gates the order path correctly — `ws_continuous` calls
`LE.would_breach(..., qty=qty, env=self.env)` with the rung's actual qty. But
the rung's qty is built upstream in `continuous_probe.py`:

```python
ACTION_SIZE = D("0.01")          # line 64
...
"qty": str(ACTION_SIZE),         # line 414
```

The envelope is a **cap, not a size setter**, and `ACTION_SIZE` was never wired
to `env.action_size`. The LIVE-2 latch corroborates it in durable form:
`"amount": "0.01"`, `"max_residual": "0.04"` — the shadow bounds, on a run the
owner authorized at q=1.

**Consequence:** strictly safer than authorized, no bound breached, and **not
the q=1 economic test that was authorized.** A $0.0014 position cannot produce
a cent-visible settlement cashflow, which is the whole point of q=1.

**The fix is one wiring change**: the rung qty must come from the active
envelope (`env.action_size`), not the module constant. It belongs on the lane
branch, which I cannot push to.

## READY/NOT READY

| | |
|---|---|
| capability (transport, terms, payload, latch durability) | **READY** — `execution_readiness` blocking list is exactly `["armed"]` |
| at q=0.01 | **READY_TO_MUTATE_BUT_UNAUTHORIZED** |
| at q=1 (the authorized economics) | **NOT READY** — size wiring defect above |

The 07:56Z readiness report carries `max_cash_exposure_usd 0.00550000` and
`smallest_valid_quantity 0.01`: it certifies the 0.01 lane, not the q=1 lane.

## FRESH AUTHORIZATION MANIFEST — what the owner must issue

**Not valid until the size-wiring fix lands**, because `executable_sha` changes
with it. Issuing against `c1ada77a…` today would authorize the 0.01 lane again.

| field | value |
|---|---|
| identifier | `OWNER-2026-09-21-GEMINI-Q1-LIVE-3` (LIVE-1 and LIVE-2 are SPENT) |
| executable SHA | **recompute after the fix**; currently `c1ada77a919c9c0cf226b4eb0c56aed6495f7e8fe1ece9261150aa6c26377763` |
| envelope SHA | `47f257f208017841d633c1bf5e2420eda2ca87b0ab502da34bd9d7e19690cf22` (unchanged by the fix; re-verify) |
| — live sub-envelope | `4e2818d53735999cda6c3de439f4a9e87a3cb2a98f9e7c10c2af125e145ed6b5` |
| bundle SHA | **recompute after the fix**; currently `1a6b5be0fecffcd109659fbde0b75f6124f4ecba21952962edb86d79ee711d94` |
| cohort | `COHORT-2026-09-21-Q1LIVE-1`, one unused slot |
| expiry | owner's choice; LIVE-1/-2 precedent is a single named BTC5m window |

Risk bounds, every one, from `live_envelope.LIVE_Q1`:

| bound | value |
|---|---|
| `action_size` | **1** |
| `max_same_side_run` | 2 (admits one EXTEND after the OPEN) |
| `max_yes` / `max_no` | 2 / 2 contracts |
| `max_total_spend_usd` | $2.50 |
| `max_committed_usd` | $2.50 |
| `max_residual_usd` | $2.00 |
| `max_working_orders` | 2 |
| `max_concurrent_markets` | 1 |
| `max_actions` | 12 |
| `cohort_prefix` | `GEMI-BTC05M` |
| maker TIF | MOC (maker-only) |
| TTE entry band | 240–280 s |
| worst-case loss | $2.50 |

**I have not self-authorized and will not.** No constant was set, no workflow
changed, no order placed.

## Continuous-inventory mechanics preserved

Unchanged and not regressed to OPEN → one complement → done: `max_same_side_run
2` admits an EXTEND, and LIVE-2's observed behaviour was OPEN on both legs then
the NO leg walking 0.58 → 0.68 → 0.77 → 0.87 → 0.95 across four re-rungs while
the YES leg filled — sequential inventory state, repeated action, exactly the
shape the model must keep.
