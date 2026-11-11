# `l5 launch` — one approval, one application

**Delivered for #214. Nothing is deployed, armed or ordered; the operator
halt remains engaged.**

The owner's routine interaction is two lines:

```
Approve BTC15m 2026-11-09T14:30:00Z | maker only | $1.00 exposure | $5.00 daily halt | expires 2026-11-09T15:00:00Z
  digest: f44f548a847c2ba99394cd1f9ddf9969171aa51fcc6075f99ad4548a94a88495
```

and then `apply --approved-plan <artifact> --approve <digest>`. No token
copying, no digest entry by hand, no Railway variable toggling.

---

## What it is, and is not

A **composition layer**. `src/l5_launch.py` owns no limit, no latch, no
ceiling and no opinion about whether a trade is wise. Every gate is read
from the accessor that already enforces it:

| gate source | what it supplies |
|---|---|
| `l5_live_authorization.authorization_preflight` | the arming gates |
| `l5_pmus_live_runner.operator_halted` | the local halt, same expression `try_arm` reads |
| `l5_probe_preflight.preflight` | runtime readiness, incl. recorder read-back |
| `l5_campaign_approval.campaign_decision` / `digest_of` | the campaign verdict and digest |
| `l5_pmus_auth_book.slug_start` / `slug_for` | slug ↔ UTC |

It holds **no transport and no credentials**, so it cannot price, submit,
cancel or amend anything. It is not a second controller.

---

## Phase 1 — `prepare`, read-only

One verdict: `READY_FOR_APPROVAL` or `BLOCKED`, with **every** gate named
pass or fail. A check that appears only on failure cannot be confirmed as
having run — which is how the 08:30Z window was lost.

Three rules the incident put there:

1. **Both halts, as two gates.** `local_operator_halt_clear` and
   `watchdog_latch_clear`. Either alone stops arming, and at 08:30Z the
   latch was CLEAR while the local halt was engaged.
2. **A `market=null` authorization verdict is never readiness.** The
   startup preflight evaluates the document alone and says so itself.
3. **UNKNOWN is never READY.** Absent venue bounds, absent runtime config
   and an absent recorder read-back each BLOCK.

`prepare --save <file>` writes the artifact the owner approves.

## Phase 2 — `apply`, the approval carried not minted

The caller names the digest the owner approved. The document in the saved
artifact is hashed and refused unless the two match, so this cannot publish
anything the owner did not see. `EnvApprovalStore.put`'s invariant — *"the
executor may not write its own approval"* — is about the **trading
process** and is untouched; this is an operator-side tool, and it still may
not invent an authorization.

- refuses a `BLOCKED` plan — applying over a failed gate is 08:30Z again
- refuses if the runtime regressed between approval and application
- verifies **durable read-back** against the digest; the gate hashes what
  is *stored*, so a reformatted document is a different document
- **idempotent** — a retry after a timeout is safe
- `--unlock-shadow` and `--reset-operator-halt` are **separate explicit
  asks**. Absent them, `L5_SHADOW_ONLY` is not written at all and the halt
  is untouched. A reset needs a reason **and** all eight
  `HALT_RESET_PRECONDITIONS` satisfied from the runtime's own telemetry
- writes only `WRITABLE`: the approval, the market, the label, the
  deadline, the lock. **No limit, no taker switch, no capital figure** —
  asserted structurally, not promised

**Absent a `--write-cmd`, `apply` is a dry run by construction.** The
Railway write is the operator's action through whatever path they trust.

## Phase 3 — execution and shutdown

`execution_report` keeps the five states apart — WOULD_SUBMIT, submitted,
acknowledged, filled, settled — and never sums them. This programme has
mislabelled each at least once.

`shutdown_plan` ends authorization at the **earlier** of the window end and
the campaign expiry, restores the structural lock by default, and requires
cancellation *first* when anything is outstanding: a lock blocks submission
and does **not** flatten a resting order. No rollover, no extension, no
capital increase without a fresh approval.

---

## The dry run, nine steps

| # | step | result |
|---|---|---|
| 1 | `prepare` against the **real halted production heartbeat** | BLOCKED, seven blockers named, incl. `local_operator_halt_clear` and `auth_preflight_is_market_specific` |
| 2 | `prepare` on a complete runtime | **READY**, owner's one line emitted |
| 3 | `prepare` with the halt engaged (the 08:30Z case) | BLOCKED on `local_operator_halt_clear` alone |
| 4 | `apply` with a wrong digest | `APPROVED_DIGEST_MISMATCH`, nothing applied |
| 5 | `apply` when the runtime regressed | refused, `blocking` named |
| 6 | halt reset with no evidence | `HALT_RESET_PRECONDITIONS_UNMET`, all eight listed |
| 7 | halt reset with full evidence + `--unlock-shadow` | applied; `L5_LIVE_HALT` and `L5_SHADOW_ONLY` written, reset recorded |
| 8 | `shutdown`, clean book | due at window end; verify, restore lock, report |
| 9 | `shutdown`, order resting | **cancellation ordered before the lock**; re-entry halted pending reconciliation |

Nothing was sent in any step.

---

## Two defects the dry run found in my own work

**A fake gate.** `taker_unreachable` graded on
`submission_gate.blocked is not None` — true whenever the heartbeat is
readable at all, and silent about taker execution. A gate that passes for
the wrong reason is worse than an absent one, and it is the same defect
class that lost the window. It now grades the four real taker facts from
`L5_EFFECTIVE_RUNTIME_CONFIG`, with absent config UNKNOWN.

**The digest could never match.** `build_document` stamps `issued_utc` from
the clock, so the CLI's `apply` — which re-derived the document — computed a
different digest from the one the owner had approved seconds earlier, and
refused every time. The one-approval flow was unusable until `prepare
--save` / `apply --approved-plan` carried the artifact instead.

---

## Tests

73 in `tests/test_l5_launch_orchestrator.py`, covering #214's whole
negative list: both halts; expired and mismatched approvals; wrong market;
wrong and failed deployment SHA; shadow lock; missing Q1; unconfirmed
reconciliation; uncertain orders; duplicate approval application; window
expiry and shutdown; zero orders, partial fills and completed pairs; and
**each** taker path individually. The operator-halt regression from
`2c7c20b7` is re-asserted here so a launcher change cannot quietly undo it.

Fixtures reuse `test_l5_probe_preflight.live_hb` / `good_plan` — the
established shape of gate values observed on the running deployment — so
the two artifacts cannot drift apart.

---

## What this does not do

It does not make the economics better. Zero venue-confirmed fills remains
the programme's blocker, and nothing here is evidence for or against the
edge. What it removes is the class of failure where an approved window is
spent on a prerequisite nothing displayed.
