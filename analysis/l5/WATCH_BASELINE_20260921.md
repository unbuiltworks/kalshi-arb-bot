# Hourly-watch baseline — BRANCH-QUALIFIED

The `:28` accounting watch reported five "reportable" items on 2026-09-21 that
were **not drift**. Every one traced to owner-issued authorizations on a branch
the baseline did not name. The baseline was written against one branch and read
against another.

## The baseline is per-branch. State which.

| pinned value | `claude/l5-execution-instrumentation-io6nzi` | `claude/gemini-btc-economic-viability-h3u7zl` |
|---|---|---|
| `envelope_sha` | `b3bff654b55a6792…` ✅ as pinned | **`47f257f208017841…`** |
| latch files | **9** ✅ as pinned | **11** |
| cohort dirs | **5** ✅ as pinned | **6** |
| `continuous_ledger.jsonl` | `89cf9de6…` ✅ byte-identical | `89cf9de6…` |
| auth constants | `None` / `None` / `None` | `None` / `None` / `None` |

**The q=1 execution lane lives on the Gemini branch.** Read it there, or the
watch will keep reporting authorized work as unexplained drift.

### Why the Gemini branch's `envelope_sha` differs, with a commit behind it

`envelope()` gained two **named sub-envelopes**, each with its own hash, so a
grant names the bounds that actually gate:

```
envelope_shadow_q001_sha  c5e88347601e24885e8cdb3cfb5b7a803b0b872948cce896c80a60023da51887
envelope_live_q1_sha      4e2818d53735999cda6c3de439f4a9e87a3cb2a98f9e7c10c2af125e145ed6b5
```

`b3bff654…` is still correct for the instrumentation branch, which has no q=1
lane. Neither value is a reversion to any superseded envelope.

## SPENT — NEVER REUSE

Added to the standing never-reuse list, alongside
`OWNER-2026-09-17-GEMINI-CANARY-1` and
`OWNER-2026-09-18-GEMINI-CONTINUOUS-1` … `-7` and
`OWNER-2026-09-19-GEMINI-CONTINUOUS-8`:

- **`OWNER-2026-09-21-GEMINI-Q1-LIVE-1`** — SPENT. Latch on disk.
- **`OWNER-2026-09-21-GEMINI-Q1-LIVE-2`** — SPENT. Latch on disk, state
  `INTENT_PERSISTED`. This is the grant LIVE-2 traded under.

Their latch files and `COHORT-2026-09-21-Q1LIVE-1` are **explained committed
evidence, not drift.** A twelfth latch or a seventh cohort would be reportable.

## The sixth Gemini fill is real and is explained

Run `35573430715`, `GEMI-BTC05M2609210745-UP`, **YES FILLED 0.1400 q 0.01 at
2026-09-21T07:41:24Z**, order id `145828836318553127`, `orders_live_after_stop 0`.
Settled: `settlement_value_yes_side "0"`, `position_at_expiry "0.01"` YES.

**EXPECTED −$0.0014 directional residual. SETTLED, NOT REALIZED** —
`fills_cash` / `reconstructed_cash` are a RECONSTRUCTION and
`inferred_settlement_value` is `null`, so no attributable cashflow exists.
**Gemini confirmed realized trading P&L remains $0.00.** Never summed with a
matched-pair line.

The watch should now expect **six** fills, not five, and a settled 0.01 YES
position on that contract.

## Current hashes on the Gemini lane branch (commit `cd57d9e1`)

```
executable_sha  c6204a62d2c3580deb015f5515e6ba565c7b992c7266750447728278f06da86b
envelope_sha    47f257f208017841d633c1bf5e2420eda2ca87b0ab502da34bd9d7e19690cf22
bundle_sha      a283403719f3d5bec0ab109d4e03f8e167b5863cdf83bf881946335c4bd0058e
```

`c1ada77a…` (which I quoted earlier from `b2c19ffd`) is **superseded** and must
not be authorized against.

## Collector

`l5-live-habitat` is on deployment `18a0cd2e-24d8-4c6a-8e32-8a5dddd959ef` and is
healthy. **Do not redeploy it.** The `pmus_flow` gap of
2026-09-21T03:55:13Z–06:07:17Z **self-recovered without a restart**; its cause
was the liveness-map coverage defect, and the interval stays **CENSORED**.
