# Route B: production readiness

## 1. Exact commit identification

| | |
|---|---|
| branch | `claude/l5-wallet-ingestion-fix-kqcqoa` |
| commit to promote | **recorded by the promotion commit itself** (see §3) |
| currently deployed | `202456d3` on `claude/l5-narrow-campaign-prep`, deployment `e0fc7bc1`, 2026-10-29T11:15:05Z SUCCESS |
| PR #193 | **not merged, not included** — `claude/l5-mandate-authorization` is a separate branch and none of its commits are on this one |

New files: `src/l5_book_completion.py`, `src/l5_latency_trace.py`.
Modified: `src/l5_pmus_live_runner.py` (hook + row store + call-site
`finally`), `src/l5_live_execution.py` (two declared attributes),
`deploy/l5_pmus_live/Dockerfile` (one COPY).

## 2. Regression parity against the deployed baseline

Full suite, same flags (`-p no:randomly`):

| | failed | errors | passed |
|---|---|---|---|
| baseline | **21** | **14** | 14437 |
| this branch | **21** | **14** | 14472 |

**Zero new failures.** `+35` passed are the new suites. The 14 errors are the
pre-existing `test_pmus_status_live_handoff.py` collection errors; one of
the 21 (`test_the_image_copies_every_module_the_entrypoint_can_reach`,
flagging `src/l5_daily_action_ledger.py`) belongs to PR #193's module and is
in both columns.

Route B's own coverage: 17 hook tests, 28 decision tests, 8 latch tests,
14 latency tests.

## 3. Deployment readiness

The service builds on `watchPatterns: ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`,
so moving the branch head alone builds **nothing** — the 06:37→11:09 gap that
cost an earlier campaign its activation window. Promotion is therefore:

1. append a dated block to `deploy/l5_pmus_live/DEPLOY_TRIGGER` naming the
   commit, the new flags and the limits left unchanged;
2. commit and push to the **deploy** branch the service tracks;
3. confirm a new deployment reaches SUCCESS and the heartbeat reports the
   new commit before anything is armed.

**`L5_BOOK_COMPLETION` is left UNSET by the promotion.** The code ships
inert: `BC.enabled()` is false, `maybe_book_completion` returns on its first
line, and the engine is byte-identical to today. Turning it on is a separate,
reversible variable write.

## 4. Rollback

| failure | action |
|---|---|
| the feature misbehaves | unset `L5_BOOK_COMPLETION`. No redeploy: the hook returns on its first line. **This is the primary rollback.** |
| the deployment itself is bad | Railway rollback to `e0fc7bc1` (`canRollback: true`), which is `202456d3` — today's running image |
| an order is resting when either happens | the window-end deadman, the authorization-expiry sweep and the watchdog's own cancel path are untouched and all still cancel by venue id |

The feature flag is the rollback, which is why it is a flag and not a
constant.

## 5. Runtime checks before any order

Read from the heartbeat, not assumed:

- `preflight: PASSED`
- `submission_gate.halt_latch: CLEAR` **and** `halt_why: null`
- `halt_latch_telemetry.record_t` within 900 s of now (watchdog freshness)
- `submission_gate.shadow_lock: false`
- `reconciled` true / book not UNCERTAIN
- `armed: true` with the campaign's `nonce_sid` matching the installed document
- `L5_TS_TWO_ORDER_PAIR` absent, `L5_DEFENSIVE_COMPLETION` absent
  (maker-only; the latter also refuses any mandate path by name)
- `max_cash_deployed_usd: 12`, `max_unmatched_contracts: 5`,
  `max_live_orders: 1`
- `journal_errors: 0`

With `L5_BOOK_COMPLETION=1`, additionally: `book_completion:*` counters
appear and `L5_BOOK_COMPLETION_TRIGGER` carries
`triggered_by: BOOK_NOT_L5_ACTION`. If no `book_completion:*` counter ever
increments, the hook is not running and that is a defect, not a quiet pass.

## 6. What the promotion does NOT include

No limit change. No watchdog change — the halt path was investigated and
found **working** (record 6 s old, consumed correctly; my earlier
stale-record report was a lifetime counter misread). No new service. No
PR #193 commits. No change to the L5-action entry path.
