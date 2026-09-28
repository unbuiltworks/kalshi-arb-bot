# BTC15m PROFIT_LOCK_5 capacity-probe artifact — selection and pin

Prepared 2026-09-28 at n=17. **Nothing armed, nothing deployed, no orders.**
Checkpoint-25 isolation verified: the scorer, collector, ledger, completeness rule
and distribution module are untouched and the frozen q50 ledger regenerates
identical at n=17.

## 1. Selected composition — the one-shot spine, plus three additions

**Spine: `scripts/l5_pmus_live_oneshot.py`.** Chosen because it is the only thing in
the tree already bounded to ONE market with an authorization that is *spent when
used*, and because its own docstring states the invariant the whole design rests on:
the 24/7 observer must keep "no path that can POST, and no caller of
`PmusTransport.arm()`". It already composes proven parts rather than reimplementing
them — `l5_live_authorization`, `l5_live_book_feed`, `l5_live_execution`,
`l5_maker_execution` (passive maker acquisition), `l5_pmus_live_wiring`,
`l5_probe_manifest`, `l5_session_journal`, `l5_window_actions`, `pmus_auth` — and it
already has authoritative venue reconciliation (`reconcile_from_venue`, the venue is
the authority) plus action/order tables for shadow-vs-live comparison.

**Rejected, with reasons:** `pmus_q1_executor` is hard-capped at q=1/$1 as constants
and cannot express a ladder. `l5_pmus_live_runner` is a *continuous* service and is
the wrong shape for a bounded probe. A new framework is excluded by instruction and
would be the fourth executor.

### The three things the spine does not yet have

| need | source to compose in | status |
|---|---|---|
| frozen BTC15m PROFIT_LOCK_5 decision | `src.pmus_completion_shadow.settlement_independent_floor` + the frozen policy constants, imported **read-only** | **NOT YET WIRED** |
| taker completion where the frozen policy requires it | `pmus_micro_executor.place_limit_buy(..., post_only=False)` — the existing `micro_complement_taker_*` primitive, already gated by `taker_completion_ok` | **NOT YET WIRED** |
| mandate + wire enforcement | `src.pmus_live_mandate` — `load_mandate()`, `order_cash_effect()`, `counts_against_market_cap()`, `GuardedTransport` | **NOT YET WIRED** |

Importing the frozen scorer read-only is what keeps checkpoint-25 isolated: the probe
consumes the decision, it does not own or alter it.

## 2. The pin

| field | value |
|---|---|
| entrypoint module | `scripts/l5_pmus_live_oneshot.py` |
| decision source | `src/pmus_completion_shadow.py` (read-only import; θ=0.96, variant B, PROFIT_LOCK_5 $5.00) |
| execution core | `src/l5_live_execution.py`, `src/l5_maker_execution.py`, `src/pmus_micro_executor.py` |
| transport / auth | `src/l5_pmus_transport.py`, `src/pmus_auth.py`, `src/l5_order_body.py`, `src/l5_preview_gate.py` |
| authorization | `src/pmus_live_mandate.py` + `analysis/l5/authorization/live_mandate.json` |
| container | **TO BE BUILT** — a new one-shot image whose COPY list is the AST import closure of the entrypoint. `deploy/l5_pmus_live/` is the CONTINUOUS service and is **not** it. |
| Railway service | **TO BE DECIDED** — reuse an existing one-shot service or the existing `l5-live-probe-oneshot`; **no new service may be created** |
| start command | `python3 -m scripts.l5_pmus_live_oneshot` with `restartPolicyType: NEVER` |
| commit SHA | **NOT YET PINNED** — 3b must set the new reviewed baseline first |
| mandate schema | the 12 REQUIRED_FIELDS of `src/pmus_live_mandate.py`, every one explicit, `frozen=True`, with an `expiry` |

**The SHA is deliberately unpinned.** Pinning it before 3b would pin drift.

## 3. Blocker status against THIS artifact

| # | blocker | status against the pinned composition |
|---|---|---|
| **3a** | two arming sites | **RESOLVED.** See §4. `test_l5_live_oneshot` 66/66, invariant green. |
| **3b** | reviewed-artifact lineage drift | **OPEN — the gating item.** Names `src/pmus_micro_executor.py` and `src/pmus_q1_executor.py`; the first is *in* this artifact, so it cannot be waived as irrelevant. |
| **3c** | signing unverified (`_cffi_backend`) | **OPEN.** `pmus_auth` signing is in the artifact's critical path. |
| **3d** | `_LIVE_STATUS` removed, 14 errors | **OPEN.** Required: live visibility of decisions/orders/fills/inventory/LOCK state/reconciliation. Monitoring failure is a **probe ABORT condition**, though not an order-admission control. |
| **3e** | `l5_pmus_live` has no `railway.json` | **IRRELEVANT, conditionally — and provable only once the image and service are pinned.** The artifact does not use `deploy/l5_pmus_live/`; that is the continuous service, now permanently disarmed by 3a. Not yet claimed as proven. |

## 4. 3a — RESOLVED, with the reason it existed

**Why the second `t.arm()` existed.** It is **merge residue, not a designed feature.**
`def9b8e1` (2026-09-26) merged two lines that had diverged at `c668665`: the "P39
harness", which grew a continuous runner loop with its own arming retry, and the
canonical execution core. The arming path came in with the harness half. Its own
commit message describes the two halves as "each proven; the halves had never met" —
and when they met, the one-shot's stated invariant was broken and the guard test went
red.

A continuously running process that can arm itself is the precise shape of the
2026-09-16 breach, where authorization to START was read as authorization to
CONTINUE.

**The change.** `try_arm()` and both of its call sites are removed from
`src/l5_pmus_live_runner.py`. Nothing else in that file changed; the test was **not**
weakened. This only ever *removes* capability, so it cannot fail open — which is why
it is preferred over a flag. `test_l5_live_oneshot`: **66 passed**, including
`test_only_the_oneshot_probe_arms_anything`.

### The regression this exposed — MUST be closed before authorization

`tests/test_l5_arm_retry.py` **no longer collects**, because its subject is gone. That
test pins a real live failure: on 1545z the process was armed-eligible with preflight
PASSED and the whole window ran `armed: false` with zero orders, because arming was
attempted exactly once and hit a transient `orders_uncertain()` that cleared 12
seconds later.

**Two tests in this repo encoded mutually exclusive designs** — "only the one-shot may
arm" and "the runner must retry arming" — and the `def9b8e1` merge is what put them in
the same tree. The one-shot invariant is authoritative, so the retry requirement must
be **ported into the one-shot, not deleted**: `run()` currently calls `probe.arm()`
exactly once and a refusal aborts the probe, so the 1545z lesson is now implemented
**nowhere**.

That test is deliberately left red rather than removed. **Deleting it would be exactly
the "merely weaken the test" move that was ruled out.** Porting arm-retry into the
one-shot is a change to the most safety-sensitive code in the repo and is listed as
open work, not slipped in.

## 5. Remaining work before an authorization decision

1. **3b** — produce the exact diff from `03f881ca`, classify every execution-class
   delta (including this session's own), pin new reviewed blobs and a SHA.
2. **3c** — repair the environment so the documented signing tests execute and pass,
   without touching signing semantics.
3. **3d** — smallest `_LIVE_STATUS` restoration/replacement giving in-flight
   visibility; wire monitoring failure to abort.
4. **Port arm-retry into the one-shot** and retarget `test_l5_arm_retry` there.
5. **Wire the three missing capabilities** of §1 into the spine.
6. **Build the one-shot image**, pin service and start command, then discharge 3e.
7. **Capacity ladder + fresh bounded mandate** for owner review — small real clips
   first, progression only from clean authenticated/reconciled fills, q50 as target
   *effective throughput* across incremental fills, never a single 50-lot order.

**The existing $0.49320 is NOT authority for this experiment.** A fresh bounded probe
manifest goes to the owner after checkpoint25.
