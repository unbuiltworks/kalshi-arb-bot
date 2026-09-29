# R5 q10 candidate manifest — `b683920d`

Status: **READY, NOT ARMED.** No Railway change made. No authorization claimed.

## Gate check

| criterion | result |
|---|---|
| replay control trustworthy | **PASS** — rung 0 reproduces frozen window-by-window: 25/25, `6.116725359099999999999999992`, 44 completions, 136 maker fills |
| production lifecycle regressions | **PASS** — 412 targeted tests |
| CANCEL_FAILED cannot reproduce the R4 block | **PASS** — 4 regressions incl. the full chronology |
| fill → inventory → next LOCK5 decision | **PASS** — `test_r4_exact_chronology_two_sided_quote_then_late_fill` |
| UNKNOWN fails closed | **PASS** — unreadable order keeps its full reservation, blocks its side, halts the tick |
| reporting agrees with canonical ledger | **PASS** — `native_lane`, `submitted_total`, `accounting_coherent` |
| no new failures | **PASS** — 9970 passed, 20 failed, all 20 pre-existing |
| R1 lineage | **ACCEPTANCE True**, `undisclosed []` |

## Economics carried into R5

| | $/window |
|---|---|
| historical frozen baseline | **6.1167253591** |
| conservative shadow comparator | **5.9220629591** |
| **execution-faithful (R6)** | **5.9220629591** |
| target | ≥ 5.00 — **cleared** |

The 3.18% the unsupported refills represent is **not** assumed recoverable.
Both the conservative comparator and the execution-faithful ladder are carried
forward; they agree.

## Envelope — unchanged from R4

| control | value |
|---|---|
| artifact | `b683920d8cb52a641966ec7b64fffb84ec98fd9d`, one build |
| clip | 10 (`L5_NATIVE_CLIP`) |
| markets | 1 concurrent, 1 total |
| max spend per market | $81.00 |
| max total spend | $81.00 |
| max residual | $20.00 |
| daily realized-loss halt | $20.00 |
| authorization | single use, fresh ledger, **new id required** (R4's carries an orphan) |
| expiry | +4 h from approval |
| restartPolicyType | NEVER |

Frozen policy untouched: θ 0.96, variant B, hold 60 s, `residual_cap_k` 2,
LOCK5 $5, `floor_guard` False, `complement_bias` None, taker completion,
queue_aware fill model in the adapter.

## Railway steps — NONE PERFORMED

1. `connect-service-source` → `b683920d...` (one build).
2. `set-variables`, `skipDeploys` — `L5_LIVE_PROBE_MARKET` **empty** (venue
   discovery selects); fresh `nativeq10_r5_*` ledger/journal/state;
   `L5_NATIVE_ENGINE=1`; `L5_NATIVE_CLIP=10`;
   `PMUS_PREFLIGHT_ACCOUNT=d51b44a3fa83`; `L5_AUDIT_ONLY` and every
   `L5_RECOVER_*` / `L5_AUDIT_*` cleared; optionally
   `L5_REQUIRE_FULL_WINDOW=1` so only a not-yet-started window is eligible.
3. `set-variables`, `skipDeploys` — mandate `OWNER-2026-09-29-NATIVEQ10-R5`,
   `strategy_version b683920d-native-q10`, the envelope above.
4. `set-variables`, `skipDeploys` — arming triple last, new session id.
5. `redeploy`.

Window selection needs no timing: discovery takes the in-progress window if
≥240 s remain, else the next. At 10:56:00Z it selected
`cpc-btc-updown-15m-2026-09-29-1100z` with 240 s of lead, tick 0.01, min qty
0.01.

## Remaining uncertainties

1. **`CSDKDZZVRYAQ`'s rejection cause is UNKNOWN.** Post-only refusal at a
   one-tick touch is the leading hypothesis and cannot be tested from existing
   data. `native_tick` now records the book, so R5 will capture what R4 could
   not. If it is systematic it reduces the figure above by an amount n=1 cannot
   bound.
2. **Throughput unproven.** Reclaiming the 3.18% needs several sequential
   orders inside a few ticks; no live run has shown it.
3. **The ladder is computed at clip 50; R5 runs clip 10.** q10 remains 9.42×
   below the frozen clip, and exactly one live authenticated fill exists.
4. **Fees stay where frozen models them** — the ladder isolates execution.

## Verdict

**READY** for one bounded q10 live probe on `b683920d`, on the envelope above,
pending explicit live authorization. Not armed, not deployed.
