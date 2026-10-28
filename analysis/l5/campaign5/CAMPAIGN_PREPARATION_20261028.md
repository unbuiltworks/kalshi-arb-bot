# Next four-window BTC15m campaign — prepared, not activated

Nothing here is armed. The shadow lock is engaged and stays engaged until
the owner approves this scope.

## Risk profile — unchanged from the previously approved campaign

| limit | value |
|---|---|
| eligible windows | 4 |
| max total exposure | $20 |
| deployed cash | $12 |
| daily realized-loss cap | $5 |
| cumulative loss cap | $20 |
| worst-case directional deficit | $3 |
| round allocations / max rounds | 4 / 8 |
| simultaneous resting orders | 1 |
| maker-only | yes |
| two-order exception | OFF |
| OPEN / COMPLETE / REPLACE | 40 / 40 / 64 |
| series | `cpc-btc-updown-15m-` |

## Runtime variables to apply at arming

```
L5_LIVE_AUTH_MAX_WINDOWS=4          L5_MAX_TOTAL_EXPOSURE_USD=20
L5_MAX_CASH_DEPLOYED_USD=12         L5_MAX_L5_DAILY_LOSS_USD=5
L5_MAX_CUMULATIVE_LOSS_USD=20       L5_MAX_WORST_CASE_DEFICIT_USD=3
L5_TS_ROUND_ALLOCATIONS=4           L5_ACC_MAX_ROUNDS=8
L5_MAX_LIVE_ORDERS=1                L5_TS_TWO_ORDER_PAIR=NO
L5_LIVE_AUTH_MAX_OPEN=40            L5_LIVE_AUTH_MAX_COMPLETE=40
L5_LIVE_AUTH_MAX_REPLACE=64         L5_LIVE_AUTH_MAX_SUBMISSIONS=40
L5_WINDOW_STRATEGY=TWO_SIDED        L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD=5
L5_FRESH_BOOK_REQUOTE               (absent — the code path is not deployed)
L5_SHADOW_ONLY=1                    (lifted LAST, only after every check)
```

Every one of these is already at this value on the service except
`L5_SHADOW_ONLY`, which is engaged, and `L5_CAMPAIGN_APPROVAL`, which holds
the expired document.

## The authorization document

The campaign identity is derived from the owner's nonce — `experiment_id()`
appends `camp.<sha256(nonce_sid)[:24]>`, so a new document is a new claim
namespace automatically and a restart of the same campaign re-derives the
same one. **I have not minted it.** `CA.MAX_CAMPAIGN_S` caps a campaign at
four hours and the gate clamps the document's expiry to it, so a document
minted now would very likely read `approval_stale` before use — which is
exactly what happened to `796d4f9f…`. It is minted at arming time on the
owner's go, and its fingerprint and expiry are reported for verification
before the lock is lifted.

The document should now also carry the per-campaign submission budgets, so
the runtime verifier grades the deployed variables against the document
rather than against anything hand-edited:

```json
"max_open_submissions": 40, "max_complete_submissions": 40,
"max_replace_submissions": 64, "max_total_submissions": 40
```

## What is preserved, explicitly

* **historical losses** — the cumulative-loss and daily-loss ledgers are
  env thresholds measured against account-wide realized figures and the
  nonce-keyed baseline. A new nonce captures a fresh *baseline epoch* for
  the $5 additional-loss bound, which is the documented intent ("a new zero
  point costs an owner action"); the $20 cumulative cap and the
  account-wide realized figure it measures are untouched;
* **unresolved obligations** — `unresolved_accounting` currently reads
  `{"count": 0}`. Arming refuses on any unresolved entry, and that gate is
  upstream of everything here;
* **prior claims** — no claim file is ever deleted. The new namespace means
  the new campaign counts only its own; the bare id still counts every
  campaign's, so any fallback sees more spent, never less.

## Pre-arm checklist

1. deployed SHA is exactly the promoted commit and the runtime reports it;
2. `L5_EFFECTIVE_RUNTIME_CONFIG` reads `PASS: true`;
3. approval fingerprint, expiry and nonce verified against the owner's
   message;
4. **the submission-claim ledger under the new `experiment_id` is READ and
   shown empty** — automatic now, which is exactly why it is verified
   rather than assumed;
5. campaign window ledger shows 0 of 4 for the new nonce;
6. `live_orders: 0`, `unresolved_accounting.count: 0`, `committed_usd: {}`;
7. `halt_latch: CLEAR`, watchdog and reconciliation confirmed live;
8. `L5_SHADOW_ONLY` lifted **last**.

Any failure keeps the lock engaged and is reported as the specific blocker.
No automatic relaunch after completion or a crash.

## Deployment of the correction — completed 2026-10-28

| | |
|---|---|
| deployment | `842462fa` **SUCCESS** 11:09Z |
| running SHA | **`ea67b2c5`** on `claude/l5-narrow-campaign-prep` |
| promoted from | `239ce8d2`, fast-forward, ancestry verified |
| shadow lock | **engaged at boot** — `locked: true, blocked: ["submit"], cancel_still_permitted: true` |
| requote | **absent from the tree** (`grep -c fresh_book_requote` = 0) |
| parity | failure sets IDENTICAL to `239ce8d2`; 13,753 → 13,860 passes |

Runtime config reads **`PASS: false`**, and that is the correct verdict
rather than a regression: eight rows report `required:
NO_APPROVAL_IN_FORCE` because the campaign document is expired. Every
`effective` value is already right — OPEN/COMPLETE/REPLACE/backstop
40/40/64/40, exposure $20, daily loss $5, windows 4 — so those rows turn
green the moment a valid document is in force. The derived verifier refuses
to bless a configuration it cannot attribute to an approval, which is
exactly what it was changed to do.

`experiment_id` reads the bare `l5-btc15m-prog24-c1-3bf1daa5`, because the
`camp.<digest>` suffix exists only under an approved campaign. A new
document will produce a new namespace automatically.

### What shadow mode CANNOT verify, stated plainly

The corrected transition fires only when a round **holds a matched pair**,
and a matched pair requires fills. Shadow mode submits nothing, so nothing
fills, so no round ever holds inventory and **`LEG_HOLDS_UNMATCHED` cannot
appear in shadow telemetry at all.**

What is verified live: the deployed SHA carries the correction, the service
runs it against authenticated live books, the two-sided decision path is
reached each tick, and the shadow lock holds. What is verified only by test:
the transition itself — 29 behavioural tests, mutation-checked in both
directions, plus the production state reproduced from the tape.

So the first real evidence for the fix will be the campaign's own
`TWO_SIDED_ROUND` records: the deadlock signature
(`retirement_blocked_by: LEG_HOLDS_*` on a zero residual) must be **absent**,
and `retired: true` on a matched round must appear. That is the first thing
to check in window 1, and it is a reason to watch window 1 closely rather
than to let four windows run unattended.
