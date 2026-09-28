# Live Rung-A mandate — FINAL, FOR OWNER APPROVAL. **NOT ACTIVATED.**

No `live_mandate.json` is written by this document. Current state, unchanged:
`HALT` present, `PMUS_Q1_ARM` empty, the expired
`OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT` still expired, and **the
$0.49320 remaining live-research budget provides ZERO authority for this
experiment.** This is a separate ask for separate money.

Supersedes `PROBE_MANDATE_DRAFT.md`, whose preconditions 1–5 are now discharged.

## The mandate, exactly

```json
{
  "authorization_id": "OWNER-2026-09-28-LOCK5-CAPACITY-RUNG-A",
  "authorized_at": "<the moment you approve, RFC3339 UTC>",
  "service": "l5-live-probe-oneshot",
  "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
  "strategy_version": "e48960f7",
  "max_markets": 1,
  "max_total_markets": 1,
  "max_spend_per_market": "1.00",
  "max_total_spend": "1.00",
  "max_residual": "1.00",
  "max_daily_realized_loss": "1.00",
  "expiry": "<authorized_at + 4h>"
}
```

All 12 `REQUIRED_FIELDS` are present; a missing or empty field is refused, not
defaulted. Money fields are Decimals and a negative value is refused.

## Maximum dollars at risk: **$1.00**

$1.00 and not a cent more, and the bound is structural rather than intentional:

* one contract at BTC15m prices costs at most $1.00 of principal, and $1.00 is
  also the worst case — the position settling worthless;
* `max_spend_per_market` and `max_total_spend` are both `1.00`, so a second
  market cannot add a second dollar;
* `max_total_markets: 1` stops the probe physically after one market rather than
  reselecting;
* `max_residual: 1.00` is worst-case dollars on unmatched shares, equal to the
  spend at this size;
* `admit()` runs under an exclusive `flock` and charges a resting order at its
  own price, so two concurrent submissions cannot both be admitted against the
  same dollar;
* the mandate/GuardedTransport boundary is the artifact's **only** risk/sizing
  authority — `pmus_l5_ramp_ladder` is absent from the 44-file capability graph,
  which is the specific mechanism that turned an authorized $0.05 into a
  committed $19.3295 on 2026-09-16.

## Loss-stop behaviour

* `max_daily_realized_loss: 1.00` — at or beyond it, `admit()` refuses; a
  refusal is **final**, and neither LOCK nor a completion the frozen policy
  permits can override it.
* **Fail closed, no relaunch.** The one-shot does not automatically repeat after
  a completed or crashed session. `restartPolicyType NEVER`.
* **Observability failure = abort.** `ProbeMonitor.require_healthy()` raises on a
  heartbeat older than 90 s or a missing required event kind; unlike the
  read-only recorder, this path fails LOUD.
* **Arming is one-shot.** Bounded pre-arm retry (45 s deadline, whitelist of
  three transient conditions) is not authorization renewal, and never retries
  after any order has been submitted.
* **ACK is not FILL.** Inventory and cash move only from `VENUE_FILL` or
  `RECONCILIATION`, so the stop is evaluated against venue truth.
* `expiry` at +4h: an unattended process cannot outlive its authorization.

The stop is $1.00 because the maximum risk is $1.00. At this rung there is no
scenario where the loss stop binds before the position itself is exhausted; it
exists so the mechanism is exercised and audited at a size where being wrong
about it costs one dollar.

## Target market / window binding

* **Series:** PMUS BTC15m, one market, one window.
* **Binding:** the specific BTC15m slug selected at arming time is written into
  the probe manifest, the observability journal, and
  `paired_shadow_live.json` (`market` and `window`). The BTC15m slug carries its
  own window boundary, so the slug *is* the window binding.
* Changing market or window after arming is one of the arm-retry abort
  conditions; it cannot be re-armed into a different market under the same
  authorization.
* **Not** any of the 25 checkpoint windows — those are closed and settled. The
  probe binds a future window, and `expiry` at +4h admits one.

## Progression to Rung B — the exact conditions

Rung A advances **only** when all of the following hold. The ladder in
`PROBE_CAPACITY_LADDER_DRAFT.md` is preserved unchanged.

1. **≥1 authenticated fill** on the probe's own order, sourced `VENUE_FILL`.
2. **Venue reconciliation agrees** with local inventory — position and cash — at
   window close.
3. **Settled**, with `authoritative_cash_pnl` populated from settlement. Not
   marked, not modelled, not estimated.
4. **The paired log is complete**: `unresolved() == []`. Every intended action
   has a resolved live side. A window with any unresolved row is not a
   measurement.
5. **Parity recorded** for every intended action, including the
   `shadow_fill_confirmation_rate`, and the `SHADOW_ONLY` rows named explicitly
   if there are any.
6. **No mandate refusal was overridden**, no observability abort, no unexplained
   ACK-without-resolution.
7. **Owner reviews the paired record and approves Rung B explicitly.** Rung B is
   a new mandate with a new `authorization_id`, not a reuse of this one.

A rung that fails **does not retry at a larger size. Ever.** A `SHADOW_ONLY`
outcome — the shadow model counted a fill the venue never gave — is a *result*,
and the correct response is to re-examine the queue-aware treatment that
produced +6.1167/window, not to raise the clip.

## Preconditions — all now discharged

| # | precondition | state |
|---|---|---|
| 1 | BLOCKER 1 closed (entrypoint composes the wiring), reviewed | DONE — `PROBE_ARTIFACT_REVIEW_8039c416.md` §2 |
| 2 | BLOCKER 2 closed (ramp ladder unreachable), reviewed | DONE — §3 |
| 3 | REVIEWED baseline advanced with every execution-class delta classified | DONE — `REVIEWED = "e48960f7"` |
| 4 | REPO_TESTS_001 resolved against this exact artifact | DONE — 4590 passed / 0 failed across all 249 probe-reaching test files |
| 5 | Checkpoint25 reported, owner ADVANCE taken on the numbers | DONE — `CHECKPOINT25_FREEZE.md`, ADVANCE accepted |
| 6 | Paired shadow-vs-live logging in place for the first window | DONE — `src/pmus_paired_log.py`, in closure, 33 tests |
| 7 | **Image built and service pinned, `restartPolicyType NEVER` verified** | **NOT DONE — deliberately. Requires approval first.** |
| 8 | **`HALT` removed and `live_mandate.json` added as two separate committed acts** | **NOT DONE — this is the approval itself.** |

Rows 7 and 8 are the only remaining steps, and both are yours.

## What `strategy_version: "e48960f7"` pins

`e48960f7` is the commit of the last change to any of the **44 files in the
artifact's reachable capability graph**. Commits after it touch only `analysis/`
and the lineage record, which are out of closure, so the deployable artifact is
bit-identical to `e48960f7` (verified: `git diff e48960f7..HEAD` over the closure
is empty). A mandate that does not name what it authorizes is how "the thing we
reviewed" and "the thing that ran" come apart.
