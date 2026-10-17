# L5 HALT reset — OWNER-OPERATED CEREMONY RUNBOOK

**Status: PREPARED, NOT EXECUTED.** Claude has performed no reset, has not
cleared `L5_SHADOW_ONLY`, has not armed and has submitted no order. Every step
below is the owner's to perform in the Railway dashboard.

Prepared 2026-10-17 from the evidence in `L5_CANONICAL.md` §66 (read-only
INSPECT and watchdog restoration) and §67 (`NO_VENUE_CREDENTIALS` root cause).
No code change is involved: the reset utility is behaving correctly.

---

## 0. Fixed facts

| | |
|---|---|
| project / environment | `arb-ev-wallet-shadow` / `production` |
| service | **`l5-halt-watchdog`** (`fb038b8c-c7c1-4599-b06a-54f72bad544f`) |
| volume (holds the latch) | `l5-halt-watchdog-state`, mounted `/data`, `us-east4-eqdc4a` |
| latch digest (`L5_HALT_RESET_EXPECT`) | `40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e` |
| latch reason (`L5_HALT_RESET_CONFIRM_REASON`) | `UNMANAGED_RESTING_ORDER` |
| latched at | `2026-10-16T03:40:12.037515+00:00` by `l5-watchdog` |
| **consumed** id — must NOT be reused | `owner-20261015-1945z-d47r06gg0zrx` |

**Baseline to restore (Phase 5):**

```
Dockerfile Path   deploy/l5_halt_watchdog/Dockerfile
Start Command     python3 -m scripts.l5_halt_watchdog
Restart Policy    Always
```

**Ceremony configuration (Phases 2–3):**

```
Dockerfile Path   deploy/l5_halt_reset/Dockerfile
Start Command     python3 -m scripts.l5_halt_reset
Restart Policy    Never          (the script exits by design)
```

Leave untouched throughout: source repo and branch, Builder, Watch Patterns,
regions and replicas, the volume and its mount path, every variable not named
below, and tracing.

---

## 1. Invariants that hold for the whole ceremony

- **`L5_SHADOW_ONLY` stays set.** Nothing here clears it. No order can reach
  the venue at any point in this procedure.
- **Refusals are safe and repeatable.** All four gates raise `ResetRefused`
  *before* the audit entry is written, so a refused attempt writes nothing and
  **does not consume the reset id**. Fix the named cause and apply again.
- **The restore is a prerequisite, not hygiene.** While the one-shot image is
  the running configuration the watchdog neither sweeps nor serves the halt
  endpoint, so the executor's halt read fails and its gate fails closed —
  submission stays blocked even after the latch is cleared, until the watchdog
  is back and attesting.
- **Never paste a credential into chat, a commit, a log, a shell or an issue.**
  Use the dashboard variable editor only. Nothing in the reset's output
  contains a secret.
- Stage all variable edits and **Apply once**, so a partially-set ceremony
  never boots.

---

## 2. Phase 0 — pre-checks. Abort if any fails

1. `l5-pmus-live` heartbeat shows `order_progress.live_orders 0`,
   `committed_usd {}`, `cash_deployed []`,
   `account_exposure.account_total_worst_case_usd 0`, `pending_terminal []`,
   `quarantined []`.
2. `l5-halt-watchdog` logs show current `L5_WATCHDOG_SWEEP` lines with
   `read_error null`, a fresh `report_age_s`, `open_orders 0`, `overdue []`.
3. The latch is still the one reviewed — confirmed in Phase 2 before any write.

**ABORT** if live orders are non-zero, exposure is non-zero, or sweeps are
stale or erroring.

## 3. Phase 1 — supply the two temporary credentials

Dashboard → project `arb-ev-wallet-shadow` → service **`l5-halt-watchdog`** →
**Variables**.

`PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` **already exist on this service as
names with empty values** (§67). **Edit those two in place** — do not add
duplicates. Paste the values into the dashboard's variable editor only.

Do not Apply yet; continue to Phase 2 so one deploy carries both the
credentials and the image swap.

## 4. Phase 2 — swap to the one-shot image and run the authenticated preconditions

1. **Settings → Build → Dockerfile Path** → `deploy/l5_halt_reset/Dockerfile`
2. **Settings → Deploy → Start Command** → `python3 -m scripts.l5_halt_reset`
3. **Settings → Deploy → Restart Policy** → `Never`
4. **Variables →** confirm `L5_HALT_RESET_INSPECT` is **`YES`** (it is already
   `YES`; this is what makes the reset path unreachable — `main()` is
   `if inspecting: … return 0`, and `inspect()` never writes)
5. **Apply / Deploy once.**

Expect exactly one log line, `L5_HALT_RESET_INSPECT {...}`. Require **all** of:

| field | required value |
|---|---|
| `preconditions.venue.ok` | **`true`** ← the thing this phase proves |
| `preconditions.registry.ok` | `true` |
| `sha256` | `40f9c340…719e` (matches §0 exactly) |
| `l5_owned_working_orders` / `venue_working_orders_seen` | `0` |
| `would_clear` | `false` |

**ABORT AND RESTORE (go to Phase 4, then Phase 5)** if `venue.ok` is `false`,
if `sha256` differs from §0 by a single character, if any L5-owned or
unattributed-automated working order is reported, or if the line does not
appear at all. A changed digest means the watchdog re-latched for a different
reason; the latch must be reviewed again before anything is cleared.

## 5. Phase 3 — the digest-bound reset

Only after Phase 2 passed. Stage **all seven** variables, then Apply once:

| variable | value |
|---|---|
| `L5_HALT_RESET_INSPECT` | `NO` |
| `L5_HALT_RESET_CONFIRM` | `RESET THE LATCHED HALT` (exact, case-sensitive) |
| `L5_HALT_RESET_WHO` | your name — must name a human |
| `L5_HALT_RESET_ID` | a fresh unique id, **not** `owner-20261015-1945z-d47r06gg0zrx` |
| `L5_HALT_RESET_EXPECT` | `40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e` |
| `L5_HALT_RESET_CONFIRM_REASON` | `UNMANAGED_RESTING_ORDER` |
| `L5_HALT_RESET_ATTEST` | your attestation, in your own words |

Gate 4(d) requires the attestation to name the **watchdog-health evidence you
checked** and the **absence of any unresolved alarm**. It is recorded verbatim
as an operator attestation, not as a machine check. Claude has deliberately
not drafted the text you will sign.

**Success:** `L5_HALT_RESET {"ok": true, …}` with
`entry.outcome: "CLEARED"`, `cleared: true`.

**Any `L5_HALT_RESET_REFUSED`:** nothing was written, the latch still holds and
the id is NOT consumed. The message names the gate. Correct it and Apply again,
or abort to Phase 4.

## 6. Phase 4 — stand the service back down: credentials AND authorization

Do all of this **whether or not** Phase 3 succeeded, in one staged batch.

**(a) Delete the two credentials.** Dashboard → **Variables** → **delete**
`PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` from `l5-halt-watchdog`.

Prefer **delete** over blanking, so their absence from the variable list is
itself the verification. (Blanking also fails closed — `has_credentials()`
strips and returns false — which is why §67 found them listed-but-empty, but
deletion leaves no ambiguity.)

**(b) Return `L5_HALT_RESET_INSPECT` to `YES`.** This is the fail-safe
default and the value the image itself bakes in. Left at `NO`, any later boot
of the one-shot image would go straight down the reset path instead of
performing a dry run.

**(c) Delete the six reset authorization variables:**

```
L5_HALT_RESET_CONFIRM
L5_HALT_RESET_WHO
L5_HALT_RESET_ID
L5_HALT_RESET_EXPECT
L5_HALT_RESET_CONFIRM_REASON
L5_HALT_RESET_ATTEST
```

A spent ceremony left populated on the service is a loaded one. The spent id
would refuse with `RESET_ID_ALREADY_CONSUMED`, and a stale `EXPECT` would
refuse at gate 2, so the system still fails closed — but the next operator
must be made to assemble the ceremony deliberately rather than inherit one.
Removing them, together with (b), means a future boot of this image can only
ever produce a read-only dry run until a human re-authorises it from scratch.

**Verify (a)–(c)** by re-reading the service's variable list: the two
credential names and the six authorization names should be gone, and
`L5_HALT_RESET_INSPECT` should read `YES`. The remaining expected names are
`L5_HALT_STATE_DIR`, `L5_ORDER_REGISTRY_URL`, `L5_WATCHDOG_POLL_S`,
`L5_WATCHDOG_REPORT_MAX_AGE_S`, `L5_WATCHDOG_STATE_DIR`, `PORT`,
`PYTHONUNBUFFERED`, `RAILWAY_DOCKERFILE_PATH` and the `RAILWAY_*` set.

## 7. Phase 5 — restore the watchdog. MANDATORY

1. **Dockerfile Path** → `deploy/l5_halt_watchdog/Dockerfile`
2. **Start Command** → `python3 -m scripts.l5_halt_watchdog`
3. **Restart Policy** → `Always`
4. **Apply / Deploy.**

Then require, in the `l5-halt-watchdog` logs:

- `L5_WATCHDOG_SWEEP` lines resuming on the normal cadence;
- `read_error null`, fresh `report_age_s`, `open_orders 0`, `overdue []`;
- **`"heartbeat": {…}` present and `heartbeat_refused` ABSENT.** This is the
  positive proof the latch is cleared and the "no halt" attestation is being
  refreshed. While the latch was held, every sweep carried
  `heartbeat_refused`; its disappearance is the signal.
- `describe-service` matches the §0 baseline field for field.

**If the watchdog cannot be restored, or sweeps do not resume: STOP.** Do not
proceed to the probe. The system is still safe — `L5_SHADOW_ONLY` remains set
and, with no halt endpoint being served, the executor's gate fails closed —
but it is unprotected by the independent detector and nothing further may run
until that is fixed.

## 8. Phase 6 — pre-probe gates. Both required, neither sufficient alone

1. Healthy watchdog sweeps as in Phase 5, sustained, not a single line.
2. A **fresh** `L5_LIVE_PREFLIGHT` from `l5-pmus-live` reading
   `preflight: "PASSED"`, and a heartbeat whose `submission_gate.halt_latch`
   is no longer `HALT` — it will still read `blocked: true` with reason
   `SHADOW_ONLY_LOCK`, which is correct and expected.

Only then is the separately authorised **0.01-contract** cancellation probe
eligible, under the unchanged bounds: `$1` maximum deployed cash, `$5`
exposure ceiling, one market, one live order, exact-order-id cancellation,
45-second hard timeout, terminal-state verification by re-read, automatic
re-HALT, no repeat. **That probe needs its own explicit approval**; clearing
HALT does not authorise it.

---

## 9. Fail-closed summary

| condition | behaviour |
|---|---|
| credentials absent or empty | `has_credentials()` false → `NO_VENUE_CREDENTIALS`; venue preconditions refused; nothing written |
| venue verification fails | gate 4 refuses; nothing written; id not consumed |
| digest differs from the reviewed latch | gate 2 refuses — "you cannot clear a halt you have not read" |
| reason not retyped, or mistyped | gate 2 refuses |
| id reused | gate 3 refuses `RESET_ID_ALREADY_CONSUMED` |
| attestation empty | gate 4 refuses `NO_ATTESTATION` |
| process dies mid-reset | audit entry lands first: an audited `CLEAR_ATTEMPTED` and a still-latched halt |
| watchdog not restored | no halt endpoint → executor's gate fails closed; `SHADOW_ONLY` still blocks submission |
| ceremony variables left populated after the reset | spent id refuses `RESET_ID_ALREADY_CONSUMED`; stale digest refuses at gate 2 — still closed, but Phase 4(b)(c) removes them so the next boot is a dry run and the next reset must be authorised from scratch |
| anything at all | `L5_SHADOW_ONLY` is never cleared by this procedure |

**Observed in production, 2026-10-17:** while the one-shot image was the
running configuration the executor logged
`HALT_GATE:HALT:halt_state_unavailable` 8 times — the halt endpoint was
unreachable and the gate failed closed exactly as designed, rather than
treating an unreadable latch as absent. That is this table's "watchdog not
restored" row, verified rather than assumed.
