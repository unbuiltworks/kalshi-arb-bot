# ARB Gemini BTC5 q10 V5.1 — Claude Code Review Findings

Date: 2026-08-30
Reviewer: Claude Code (independent inspection; no live canary run, frozen release not modified)

## Scope and evidence base

- Frozen executable release inspected read-only at exact SHA
  `ff4bc84b40b5bf79c80406c8e0d7ade77ac58e85` (`release/gemini-q10-production-v5-1-final`),
  via a detached git worktree. The ref was not touched.
- Historical fixed checkpoint `f1c70209a57764695b45fec586859a05d7640770` inspected for
  the proven three-attempt fresh-depth retry.
- Control branch `aws-migration-foundation` reviewed, including the four new files named
  in the handoff.
- The frozen release's focused safety suite was re-run in this review environment:
  **58/58 passed**, matching the canary evidence.
- The control-branch retry tests pass (3/3), the diagnostic compiles, and 6 new
  diagnostic gate tests added by this review pass.

Verdict up front: the first V5.1 canary was a **safe no-trade run whose failure to
reach order entry is now fully explained by code inspection**. The depth-acquisition
regression is real and is the single highest-leverage fix. However, three additional
fail-open/race defects in the *rescue and reconciliation* path should be fixed in the
same next release, because fixing the qualifier makes order entry — and therefore those
paths — much more likely to execute.

---

## 1. Severity-ranked findings

### F1 — HIGH (root cause of the failed canary): one-shot `fresh_top` in the qualifier

**Where:** `src/gemini_liquidity_reward_live_probe_q10.py::_candidate` (line 137) →
`src/gemini_short_crypto_btc5m_maker_canary_q1.py::fresh_top` (line 178).

`fresh_top` opens exactly one websocket, subscribes to `<symbol>@depth5@100ms`, and has
exactly two failure modes, which are precisely the two observed error types:

- `RuntimeError("no fresh Gemini BTC5m depth")` — messages arrived but no valid
  top-of-book (`0 < bid < ask < 1`, both sides nonempty) parsed within the 4s budget;
- `TimeoutError` — `asyncio.wait_for(ws.recv(), ...)` expired with no message, or
  `websockets.connect(open_timeout=5.0)` timed out.

Every other stage of `_candidate` fails differently: `discover_btc5m` raises
httpx-typed exceptions (`ReadTimeout`, `HTTPStatusError`, …), and `_reward_snapshot`
returns `None` silently (no `QUALIFIER_RETRY` at all). Therefore:

> **Each observed `QUALIFIER_RETRY` with `RuntimeError`/`TimeoutError` was a poll in
> which an active BTC5M event existed inside the 240–330s expiry window, the reward
> program was enabled, the event was reward-listed with zero qualifying makers — and
> the candidate was lost purely on depth acquisition.**

That is materially worse than "no opportunity existed": reachable zero-competition
windows were being discarded by a known-transient failure class.

**Regression classification.** The 2026-08-27 incident
(`docs/ARB_SHORT_CRYPTO_DEPTH_PREFLIGHT_INCIDENT_20260827.md`) produced a proven fix —
3 independent bounded subscriptions, no REST/stale fallback, fail-closed
(`gemini_short_crypto_btc5m_inventory_maker_canary_q1_v2.py`, checkpoint `f1c7020`).
That file and its regression test are **present in the frozen V5.1 release** but wired
only into the q=1 inventory lane. The q10 lane never received it: V2's
`hot_fresh_top` cache patch activates only *after* `_wait_candidate` returns
(`gemini_q10_production_executor_v2.py::hot_wait_candidate`), and V5 re-implemented the
in-campaign cache natively while leaving `probe._candidate`'s pre-candidate
`base.fresh_top` untouched. So V5.1 did not remove the fix; the fix was never ported to
the q10 qualifier, and the failure class predictably resurfaced there.

**Also collapsed observability:** `probe._wait_candidate` emits only
`error_type` (no message, no stage), and all eight non-exception gates return `None`
indistinguishably. This is why a 10-minute canary could end `NO_CANDIDATE` with the
root cause formally uncertain until this review.

### F2 — HIGH (double-mutation race): dead-man stale threshold is below the executor's worst-case heartbeat gap

**Where:** `src/gemini_q10_v5_deadman.py` (`STALE_SECONDS_DEFAULT = 3.0`) vs
`src/gemini_q10_production_executor_v5.py::_bounded_fok` and the reconcile helpers.

The executor heartbeats via `_heartbeat()` at specific points, but several legitimate
in-flight sequences exceed 3.0s between heartbeats:

- `_bounded_fok`: heartbeat at `RESCUE_FOK_PENDING` → `maker.request(timeout=3.0)` →
  `_find_row` (up to 20×50ms = 1.0s) → next heartbeat ≈ **4.0s+**;
- any reconcile step: `_order_by_client` issues up to 3 REST calls at
  `timeout=4.0` each — worst case **>10s** with a slow venue.

The live workflow does not override `GEMINI_Q10_V5_WATCHDOG_STALE_SECONDS`, so an
armed sidecar can classify a *live, mid-rescue* executor as dead and invoke
`recovery.recover()`, which cancels campaign orders and can place its own FOK
(`{trade_id}-recovery-v5fok-N`) concurrently with the executor's FOK
(`{trade_id}-v5fok-N`). Recovery reconstructs residual only from the two *original*
campaign order IDs (`recovery._state_contract`) and never checks the durable state's
`pending_rescue` / `rescue_last_client_id` fields, so it cannot see an in-flight
executor rescue. Two all-or-none flattens for the same residual can both fill →
position inversion (e.g., flat YES plus a fresh naked NO 10-lot), outside the $0.30
rescue accounting.

Each individual FOK still respects its own $0.30 worst-case-at-limit bound, but the
*combination* creates new naked exposure. Probability is low; severity if it fires is
high; the margin is structurally negative (3.0s threshold < 4.0s+ designed gap).

### F3 — HIGH (fail-open reconciliation cluster): "order not found" is treated as "zero fill"

`_order_by_client` (`src/gemini_kalshi_xrp_maker_live_canary.py:189`) returns `None`
on any non-200 or missing row, and `_filled(None) == 0`. Three downstream consumers
turn that into fail-open behavior:

- **F3a — double FOK on history lag.** `_bounded_fok`
  (`gemini_q10_production_executor_v5.py:236-255`): after an **acked (status 200)**
  FOK, if `_find_row` cannot find the row within ~1s, `filled == 0` falls through to
  the *next attempt*, placing a second FOK for the full residual. If attempt 1 actually
  filled, attempt 2 over-executes (COMPLETE route: buys the complement twice). An acked
  order that cannot be found is an **ambiguity**, not a kill.
- **F3b — phantom clean exit.** `_cancel_original_pair` → `probe._reconcile`: if both
  lookups fail transiently, `yf = nf = 0` → `ZERO_FILL_CLEAN_EXIT` with
  `open=False` even when the websocket loop just broke on `pull_reason == "FIRST_FILL"`.
  The state contradiction (ws saw a fill; reconcile says zero) is not cross-checked.
  The naked fill is then caught only by the workflow's post-run account audit, after
  the campaign has already declared itself clean.
- **F3c — history window shorter than the rest window.** The order-history queries use
  `from = now - 120000` (120s) while the V5 rest window is `v1.REST_SECONDS = 180s`.
  If a fill occurs early, the fill event is missed (see F7), and reconcile happens at
  rest-window end, the filled order can fall outside the 120s history window on both
  the `filled` and `cancelled` queries → reported as zero fill. Precondition is a
  missed ws event, but the window being strictly smaller than the exposure window is
  wrong on its face.

### F4 — MEDIUM-HIGH (analytic gap, handoff Q5/Q6): the standing rescue check can count our own resting depth

`rescue.standing_rescue_check` requires top-level depth ≥ q on the side each
hypothetical rescue would cross (`routes_after_fill`: YES-fill → `yes_bid_size`,
NO-fill → `yes_ask_size`). While both maker orders rest, the public `depth5` feed
includes **our own 10-lot on each of those levels** (our YES buy joins the displayed
bid; our NO buy at `1 - ask` is displayed on the ask side). The continuous standing
check during `BOTH_POSTED` can therefore hold with zero *external* rescue depth.

The $0.30 bound itself is **not** violated — `_bounded_fok` re-prices a fresh
`best_route_after_fill` after our orders are cancelled and fails closed
(`RESCUE_BOUND_NO_LONGER_AVAILABLE`, hard halt) if the route is gone. But the design
claim "full possible q10 first fill is provably rescuable while quotes rest" is
weaker than stated: in the self-contaminated case the guaranteed outcome degrades from
"rescued ≤ $0.30" to "hard halt holding a naked residual". The entry check (taken
before any order is posted) is clean; the YES→NO sequential-gap re-check
(`_run_campaign`, after the YES ack) correctly re-verifies the YES-fill route before
posting NO, but its snapshot is already contaminated by the resting YES order.

**Honest statement of what is proven (Q5/Q6):**
- Proven: both-sides rescue feasibility is checked against a ≤500ms-old book
  immediately before YES posting; the YES-only route is re-verified between the YES ack
  and the NO post; the invariant is re-evaluated continuously (~20Hz loop) while both
  orders rest; rescue itself executes at a limit price whose *worst* fill respects the
  bound; no reward/rebate input can relax any bound (verified: `gemini_q10_rescue_policy`
  imports only fee functions).
- Not provable (and not proven): atomicity between the last book read and venue
  acceptance/first fill. There is an irreducible ~100ms-to-1s window in which the book
  can move; V5's answer to that window is the fail-closed hard halt, not a guaranteed
  rescue. Any future claim should say exactly that.

### F5 — HIGH (orchestration): the false-red mechanism is structural and will repeat

**Where:** `.github/workflows/aws-gemini-q10-v5-1-live.yml`; `src/gemini_q10_v5_deadman_sidecar.py`.

- The sidecar prints one JSON line per tick with `poll_seconds()` defaulting to
  **0.25s** — ~240 lines/minute of `WAITING_FOR_OPEN_CAMPAIGN`/`HEALTHY_HEARTBEAT`.
- The remote script `cat`s `executor.log`, `sidecar.log`, and `post.log` into SSM
  stdout, and the workflow greps `V5_1_ONE_CANARY_COMPLETE` from
  `GetCommandInvocation.StandardOutputContent`, which AWS truncates at **24,000
  characters**. The completion marker is the *last* line, i.e. in the part guaranteed
  to be lost on any long canary. SSM `Status=Success` + marker-missing = false red,
  exactly as observed.
- Same pattern (marker-grep of SSM stdout) exists in
  `aws-gemini-q10-v5-recovery-readiness.yml`, `aws-gemini-q10-v5-sidecar-smoke.yml`,
  `aws-gemini-q10-v5-1-release-qualification.yml`, and the new
  `aws-gemini-q10-v5-1-qualifier-diagnostic.yml` (the last is low-risk only because the
  diagnostic prints almost nothing else).
- **Timeout budget bug:** worst-case remote runtime = image build + 58 tests +
  preflight + 600s qualifier wait + 180s rest + rescue + post-run settlement wait up to
  600s ≈ well over the SSM `--timeout-seconds 1120`. On SSM timeout the shell is
  killed, but the foreground `docker run` container can survive as an orphan — a live
  armed executor with a red workflow and no observer except the sidecar.

**Answer to handoff Q14/Q15:** yes — write one compact JSON result file per phase on
the host (e.g. `$OUT/result.json` containing status, markers, executor RC, post RC),
fetch it with a *second, tiny* SSM command (or S3 output), and keep full logs on disk
only. Sidecar/dead-man logging should print on state transition plus a rate-limited
periodic summary (e.g. every 30s); decisions depend on durable state and venue audits,
not on log volume, so rate-limiting loses no recovery evidence.

### F6 — MEDIUM: dead-man's terminal "no residual" exit ignores the account audit it already has

**Where:** `src/gemini_q10_v5_deadman.py::check_once` (lines 83–99),
`src/gemini_q10_v5_deadman_sidecar.py::run`.

The terminal `STALE_HEARTBEAT_NO_DIRECTIONAL_RESIDUAL` branch requires
`exact_residual && residual == 0 && active_known_orders == 0` — all three derived from
`_order_by_client` lookups that read as zero/absent during a venue REST outage. The
`RecoveryAudit` already carries `account_positions` / `account_orders`, but the gate
does not use them: the sidecar can exit (terminal) while the account holds directional
exposure. Additionally, `sidecar.run()`/`deadman.run()` have no exception handling: one
transient `RuntimeError` from `_flat_snapshot` kills the watchdog process entirely.

### F7 — MEDIUM: `GeminiMakerSession` has no disconnect detection; fill detection can silently stop

**Where:** `src/gemini_kalshi_xrp_maker_live_canary.py::GeminiMakerSession._read`;
consumed by `_run_campaign`'s `BOTH_POSTED` loop.

When the authenticated websocket closes, `_read`'s `async for` simply ends; no flag is
set and `maker.events.get()` blocks forever. The `BOTH_POSTED` loop keeps running on
book-cache freshness alone, blind to fills for up to the 180s rest window (then F3c
applies). Cancels still work (`_cancel_maker` falls back to REST), but fill *detection*
has a single, silently-failing source. The loop should treat `maker.reader.done()` as
an immediate pull condition.

### F8 — MEDIUM (diagnostic parity — **fixed on the control branch in this review**): missing one-sided premium gate

The frozen qualifier still enforces `probe.MAX_ONE_SIDED_PREMIUM = $6.50` inside
`_candidate` (line 147). `V5.1.install()` replaces only `v1._entry_model_ok` and
`v1.MIN_AVAILABLE` — it does not touch the probe. The committed diagnostic reproduced
every probe gate *except* this one, so a book like YES 0.68 / NO 0.25 (edge $0.62,
premium $6.80) would report `QUALIFIED` while the live V5.1 qualifier returns
`NO_CANDIDATE`. That is exactly the false signal the diagnostic exists to prevent.

**Fixed:** `deploy/aws/v51_qualifier_diagnostic.py` now applies the premium gate,
reading the cap from the probe module at runtime so it cannot drift, and
`tests/test_v51_qualifier_diagnostic_gates.py` (6 tests) covers: qualified book,
premium-gate parity, cap sourced from probe, depth retry-then-success, depth
exhaustion after exactly 3 attempts, and rescue-gate classification. The diagnostic
workflow's validation step runs these tests.

### F9 — LOW-MEDIUM: V5.1's "20c–80c band" is partially inert; effective per-side cap is $0.65

Because the probe's $6.50 premium gate runs during qualification, no candidate with
either side above $0.65 ever reaches V5.1's entry gate, whose stated band is
0.20–0.80. This is conservative (not a safety issue) but means the V5.1 admission
relaxation is narrower than documented, and it reduces opportunity frequency. Decide
deliberately in the next release: either document the effective 0.20–0.65 band, or
raise `probe.MAX_ONE_SIDED_PREMIUM` to $8.00 with the $22.10 cash floor re-checked
(0.80 × 10 = $8.00 principal + fees still fits the documented reserve math). Do not
change it silently.

### F10 — INFO (verified sound; keep as-is)

- **Rescue math** (`gemini_q10_rescue_policy`): both COMPLETE and UNWIND priced
  explicitly; limit prices derived tick-by-tick so the *worst* FOK fill respects the
  bound; `standing_rescue_check` requires **both** hypothetical q10 first fills safe;
  fees are the conservative ceil-per-leg schedule; **no reward/rebate input exists in
  the module** (handoff Q9: pass).
- **Config ordering** enforced: rescue ≤ campaign ≤ daily (`v5._config_ok`).
- **No new campaign over unresolved state** (handoff Q8): `v5.run()` refuses on open
  durable state; `v5_runtime` routes restarts to recovery-only and exits afterward;
  `_read_state` unreadable → refuses order entry; `_preflight_flat` raises unless the
  account is flat; non-continuous mode forces exactly one campaign. The one caveat is
  F3b (a mislabeled clean exit closes state early; the account-level preflight and the
  workflow's post-run audit remain as nets).
- **Accounting vocabulary** (handoff Q10): state classifications observed are truthful
  (`LIVE_Q10_V5_OPEN_NOT_REALIZED`, `MATCHED_NOT_SETTLED`, `SETTLED_ACCOUNT_FLAT`,
  `rewards_realized="SEPARATE_ACCOUNTING_REQUIRED"`); nothing labels matched-open or
  modeled values as realized.
- **FOK safety paths that are already fail-closed:** ack exception → ambiguous hard
  halt; ack ≠ 200 → rejected hard halt; nonzero partial ≠ quantity → exchange-ambiguity
  hard halt; stale book before send → hard halt (all covered by unit tests).
- **Frozen-release discipline in workflows:** exact-SHA assertion before build; trigger
  file requires pinned commit + fresh approval id; state-path reuse refused (exit 41).

---

## 2. Answers to the review questions

**Q1 (exact code paths of the retries):** `probe._wait_candidate` → `_candidate` →
`base.fresh_top` one-shot websocket; `RuntimeError` = subscribed but no valid top in
4s, `TimeoutError` = no frame / connect timeout. See F1. Because `fresh_top` is the
*last* stage of `_candidate`, each such retry implies every upstream gate had passed on
that poll.

**Q2 (venue transient vs own bug):** the individual failures are expected venue-data
transients (documented 2026-08-27); the *bug is ours*: a known-transient single-shot
read in the hot qualifier path with no retry, plus observability that collapses eight
distinct gates into `None` and all exceptions into a bare type name.

**Q3 (is the 3-attempt retry safe to integrate):** yes. It is pre-mutation, fail-closed,
fresh-subscriptions-only, no REST/stale fallback; exhaustion inside `_candidate` is
caught by `_wait_candidate`'s own retry loop and cannot crash V5; the only cost is up
to ~12.5s per poll instead of 4s inside a 600s wait. It widens no price, loss, or
freshness bound.

**Q4 (same retry for the final pre-mutation read?):** No — keep the stricter path. In
V5 the final pre-mutation read is the persistent `GeminiTopCache.wait_fresh(max_age_ms=500)`,
not `fresh_top`; it should stay a single bounded fail-closed check. Retrying at that
point would extend the decision-to-post window, which is exactly where freshness
matters most. (The probe's own `run()` uses `fresh_top` pre-mutation, but V5/V5.1 do
not call `probe.run()`.)

**Q5/Q6 (full-q10 rescue proven before any fill? sequential gap?):** proven at every
decision point against a fresh book, re-verified continuously, and re-verified for the
YES-fill route between the YES ack and the NO post; **not** atomic with venue
acceptance, and the standing depth check can be satisfied by our own resting size (F4).
The $0.30 bound on any *executed* rescue holds by construction (worst-fill-at-limit
pricing); the unprovable residue is handled by hard-halt, not by a bound violation.

**Q7 (races/partials/acks/reconnects fail-closed?):** FOK partial, FOK reject, FOK ack
exception, stale book, cancel-race fill-after-cancel: yes, fail-closed and unit-tested.
Not fail-closed today: acked-but-unfindable FOK (F3a), reconcile lookup failure
(F3b), history window vs rest window (F3c), maker-session silent disconnect (F7),
dead-man stale threshold vs in-flight rescue (F2).

**Q8/Q9/Q10:** see F10.

**Q11 (test taxonomy):** all 58 focused tests are synthetic unit tests (fakes and
monkeypatching; `--network none` in the workflow). They prove policy math, gate
wiring, and fail-closed branches. Zero tests exercise real Gemini market data,
accounts, or timing. The live execution question — real fill → rescue/complete within
$0.30 → reconcile → settle → realized — remains **unanswered**; the canary proved only
safe no-trade behavior. This is safety evidence, not execution evidence, and the
release-qualification workflow should keep saying so.

**Q12 (safe integration test):** the committed read-only diagnostic is the right
instrument: it runs the frozen image's own discovery/fee/rescue code against live
public data with zero credentials and no mutation, and its `reason_counts` over a long
window doubles as a depth-reliability soak (per-attempt failure rates appear in
`depth.failures`). Run it until it reports either ≥1 `QUALIFIED` poll or a stable gate
distribution; that converts `NO_CANDIDATE` from a mystery into a measured funnel.

**Q13 (smallest next release patch):** Tier A below.

**Q14/Q15 (durable results, rate-limited dead-man):** yes to both — see F5.

---

## 3. Proposed minimal patch set

The control branch and the release lineage have diverged (the q10 modules exist only on
the release lineage), so release-code fixes must land on a new release ref cut from
`ff4bc84…`, e.g. `release/gemini-q10-production-v5-2`. The frozen `-v5-1-final` ref
stays untouched.

### Applied in this review (control branch, no live-path change)

1. `deploy/aws/v51_qualifier_diagnostic.py` — one-sided premium parity gate (F8),
   cap read from the probe module at runtime.
2. `tests/test_v51_qualifier_diagnostic_gates.py` — 6 gate-classification tests using
   injected stubs whose fee math mirrors the frozen release.
3. `.github/workflows/aws-gemini-q10-v5-1-qualifier-diagnostic.yml` — runs the new
   tests in its validation step; test file added to trigger paths.

### Tier A — required to make the next canary informative (new release ref)

**A1. Qualifier depth retry (F1).** Additive, mirroring how V5.1 itself is layered.
Copy the already-reviewed `src/gemini_btc5m_fresh_depth_retry.py` from the control
branch into the release lineage, plus one new module:

```python
# src/gemini_q10_v5_2_runtime.py
"""V5.2 = V5.1 admission + proven 3-attempt fresh-depth retry in the qualifier."""
from __future__ import annotations

import asyncio

from src import gemini_btc5m_fresh_depth_retry as depth_retry
from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_q10_v5_1_runtime as v51_runtime

_original_fresh_top = base.fresh_top


async def _reliable_fresh_top(symbol: str, timeout: float = 4.0):
    return await depth_retry.reliable_fresh_top(
        _original_fresh_top, symbol, timeout_seconds=timeout,
    )


def install() -> None:
    # Affects exactly the pre-candidate qualifier reads (probe._candidate); V5's
    # in-campaign path reads the persistent book cache and is unchanged.
    base.fresh_top = _reliable_fresh_top


async def run():
    install()
    return await v51_runtime.run()


if __name__ == "__main__":
    asyncio.run(run())
```

Tests: port `tests/test_gemini_btc5m_fresh_depth_retry.py`, plus one test asserting
`install()` leaves `probe.base.fresh_top` pointing at the retry wrapper and that
exhaustion inside `_candidate` still surfaces as a caught `QUALIFIER_RETRY` (not a
crash). Live workflow change: run `python -m src.gemini_q10_v5_2_runtime`.

**A2. Qualifier observability (F1).** In `probe._wait_candidate`, extend the emit:

```python
emit("QUALIFIER_RETRY", error_type=type(exc).__name__,
     error_message=str(exc)[:240], order_entry=False)
```

(One line; keeps the log compact; the read-only diagnostic remains the full-detail
instrument.)

**A3. Durable workflow result + timeout budget (F5).** In
`aws-gemini-q10-v5-1-live.yml` (control branch): stop `cat`-ing full logs into SSM
stdout (`tail -c 4000` each); write
`{"executor_rc":…,"post_rc":…,"marker":"V5_1_ONE_CANARY_COMPLETE"}` to
`$OUT/result.json`; fetch it with a second SSM command that only `cat`s that file;
raise `--timeout-seconds` to cover the worst-case path (≥ 2400s) and give the executor
container an explicit name so a timed-out run can be found and stopped.

### Tier B — small fail-closed fixes that should ride the same release (they gate the paths Tier A unlocks)

**B1 (F3a).** `_bounded_fok`: after a 200 ack, `row is None` →
`{"status": "RESCUE_FOK_STATUS_UNKNOWN", "hard_halt": True, "flattened": False}` —
never a second attempt on an unresolved ack.

**B2 (F3b).** `_run_campaign`: if `pull_reason == "FIRST_FILL"` and reconcile returns
`yf == nf == 0`, or if an acked order's reconcile row is `None`, treat as
`RECONCILE_AMBIGUOUS` hard halt with `open=True` instead of `ZERO_FILL_CLEAN_EXIT`.

**B3 (F3c).** `_order_by_client`: widen the history window from 120s to
`max(2 * REST_SECONDS, 600)`s (or thread the campaign `posted_at` in).

**B4 (F2).** `deadman.STALE_SECONDS_DEFAULT` 3.0 → 15.0 (still far inside the 240s+
expiry margin), and have `recovery.audit()` treat a durable `pending_rescue` /
`rescue_last_client_id` with an unreconciled outcome as `exact_residual=False`
(ambiguous → blocked for manual review, never a second automatic FOK).

**B5 (F6).** Dead-man terminal no-residual exit additionally requires
`report.account_positions == 0 and report.account_orders == 0`; sidecar/deadman `run()`
wrap `check_once()` in try/except with a rate-limited error line instead of dying.

**B6 (F5).** Sidecar prints only on state change plus a ≥30s periodic summary.

**B7 (F7).** `BOTH_POSTED` loop: `if maker.reader is None or maker.reader.done(): pull_reason = "MAKER_SESSION_LOST"; break`.

**B8 (F4).** `_standing` during resting quotes: subtract our own resting quantity from
the same-side top level before the depth check when our quote price equals that level
(conservative full-Q subtraction; a partially filled own order only makes the check
stricter).

Tier B intentionally excludes anything that widens admission (F9 stays a documented
decision, not a default change).

---

## 4. Release-qualification plan for `release/gemini-q10-production-v5-2`

1. Cut the branch from `ff4bc84…`; apply Tier A + Tier B; nothing else.
2. In-image: existing 58 focused tests + new tests (retry wiring, FOK-unknown halt,
   reconcile-ambiguity halt, deadman account gate, sidecar rate limit, history window)
   — all under `--network none`.
3. Run the (already committed) read-only qualifier diagnostic workflow against the new
   image SHA for at least one full expiry cycle window; require its final JSON to show
   `DEPTH_UNAVAILABLE_AFTER_RETRIES` at a materially lower rate than single-attempt
   failures (`depth.failures` gives both numerator and denominator) and at least one
   poll classified beyond the depth stage.
4. Run the recovery-readiness and sidecar-smoke workflows against the new SHA.
5. Only then request fresh authorization, quoting the new SHA.

## 5. Exact evidence required before requesting the next live canary

- New release SHA with Tier A+B, 58+new tests green **inside the release image**.
- Diagnostic run(s) on the new SHA showing the qualifier funnel end-to-end: per-gate
  `reason_counts`, depth attempt/failure counts, and ideally ≥1 `QUALIFIED` poll (or an
  explicit decision that the current market regime simply lacks zero-competition
  windows — a `MARKET_GATE`-dominated funnel is a valid, diagnosable answer).
- Confirmation the live workflow reads its completion markers from the compact result
  file (A3), so a long run cannot go false-red.
- Fresh explicit user approval naming the new SHA, one campaign, continuous off,
  existing loss bounds unchanged ($0.30 / $1.00 / $3.00, $22.10 floor).

And for classifying that canary afterward (handoff Q10): **submitted** = 200 acks +
client IDs in durable state + venue order rows; **filled** = venue order-history
`filledQuantity > 0` (ws events alone insufficient); **matched** = reconciled
`yes_fill == no_fill > 0`; **settled** = positions cleared and
`_wait_flat_after_settlement` true; **realized** = available-USD delta reconciled
against modeled fills+fees, with rewards accounted separately and the ledger/state
files archived. No stage may be claimed from the stage before it.

---

*This review ran no live canary, armed nothing, and made no change to
`release/gemini-q10-production-v5-1-final`.*
