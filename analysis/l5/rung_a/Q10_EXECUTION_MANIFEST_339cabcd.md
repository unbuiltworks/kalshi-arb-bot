# Q10 execution manifest — candidate `339cabcd`

Status: **PREPARED. NOT APPROVED, NOT ARMED, NOT DEPLOYED.** No Railway
change has been made. No authorization is claimed by this document.

## Release-candidate review, `de72ab80` -> `339cabcd`

18 files, +1314 / -189.

**Scope.** Every source change is PMUS market discovery, resolution or its
plumbing:

| file | scope |
|---|---|
| `src/pmus_market_row.py` (new, 115) | the one canonical row read |
| `src/l5_pmus_transport.py` (195) | `resolve_market` delegates; `series_provenance`; `pin_market_metadata` |
| `src/pmus_book_tape.py` (72) | `series_windows` extracted; `series_slugs` behaviour unchanged |
| `src/pmus_micro_executor.py` (23) | row read delegated, behaviour byte-identical |
| `scripts/l5_pmus_live_oneshot.py` (119) | venue selection, pre-window pin, refusal path |
| 3 Dockerfiles (+7 lines) | COPY the new module into the closures that import it |
| 5 test files, 3 docs | supporting |

**Files verified byte-identical (empty diff):** `l5_order_body.py`,
`pmus_l5_native_engine.py`, `pmus_native_parity.py`,
`pmus_native_money_path.py`, `pmus_native_live_overlay.py`,
`pmus_native_venue.py`, `pmus_completion_shadow.py`, `pmus_live_mandate.py`,
`l5_live_authorization.py`, `l5_preview_gate.py`, `l5_live_execution.py`,
`pmus_lock5_translation.py`, `l5_live_book_feed.py`, `pmus_fees.py`,
`pmus_auth.py`, `audit_economic_complete.py`.

**No safety gate weakened.** Diffing `l5_pmus_transport.py` for
`gate(` / `ok &=` / `_fail(` / `raise` yields exactly three changed lines:
the removed `raise LookupError` of the deleted fallback chain, and the
`_fail(... "market_readable" ...)` call reflowed to carry the new `where=`
string. No gate is added, removed, reordered or made non-blocking. The
mandate, single-use authorization, UNKNOWN fail-closed handling,
order/inventory reconciliation, exposure limits, order-time fresh-book
enforcement, preview/echo validation, authoritative fills and paired
accounting are untouched files.

**Frozen PROFIT_LOCK_5.**

    windows 25   mismatched []
    frozen_mean 6.116725359099999999999999992
    live_mean   6.116725359099999999999999992
    rounded     6.1167253591

theta 0.96 - variant B - hold 60.0 s - clip 50 - residual_cap_k 2 -
LOCK5 $5 - floor_guard False - complement_bias None - fill model
queue_aware in the adapter. `l5_order_body.build` unchanged:
`price == {"value","currency"}`, `tif == TIF_GTC`, `yes_is == "UP"`.

**Tests.** 294 passed across parity, money path, wire level, high churn,
adversarial, the three live-mandate suites, capacity rung manifest,
entrypoint composition, the 2215z regression suite and both transport
suites. Full repo: 20 failed / 9928 passed, all 20 pre-existing and
unchanged from baseline.

**Accepted residual risk (owner-approved).** Single-route resolution has no
fallback. If the gateway stops serving `/v1/market/slug/` for this series,
the probe refuses the window rather than trading on unconfirmed
constraints. Fail-closed by design; the pre-window pin only converts a
TRANSIENT outage from fatal to survivable.

## Earliest venue-confirmed upcoming window

Read live at 2026-09-28T23:30:44Z, from the venue's own series listing:

| slug | window | status | tick | min qty | createdAt |
|---|---|---|---|---|---|
| **`cpc-btc-updown-15m-2026-09-28-2345z`** | **23:45:00–00:00:00Z** | MARKET_STATUS_CLOSED (pre-open) | 0.01 | 0.01 | 2026-09-28T12:16:00Z |
| `cpc-btc-updown-15m-2026-09-29-0000z` | 00:00–00:15Z | CLOSED (pre-open) | 0.01 | 0.01 | 12:16:00Z |
| `cpc-btc-updown-15m-2026-09-29-0015z` | 00:15–00:30Z | CLOSED (pre-open) | 0.01 | 0.01 | 12:16:00Z |

`cpc-btc-updown-15m-2026-09-28-2330z` was in progress at read time and is
excluded as a target: a probe should own a whole window.

`MARKET_STATUS_CLOSED` on a pre-open window is the venue's normal shape, not
a defect — `market_open_tradable` correctly refuses it before the open and
passes at the open, verified live on 2230z.

**RECOMMENDATION: leave `L5_LIVE_PROBE_MARKET` EMPTY** and let the new
venue discovery select at boot. Pinning a slug by hand is exactly what
produced the 2215z failure, and a pin left over from a previous run is a
standing hazard. With the pin empty the probe asks the venue, takes the
window in progress if >=240 s remain, else the next one, and refuses
outright if the venue lists nothing — before claiming its authorization.

If you prefer an explicit pin, the roll rule is: activate by **23:41:00Z**
for 2345z; otherwise pin the next window with >=4 minutes of lead and change
only the slug, session id, ledger/journal/state paths and authorization id.

## Exact Railway changes required — NONE PERFORMED

Service `l5-live-probe-oneshot` (84aa9999-6d7a-4980-9a87-96694d8178dd),
project `arb-ev-wallet-shadow` (50fb7357), env production (f3642d27).

**Step 0 — source pin (one call, `connect-service-source`).** The service is
pinned to `de72ab80`. It must be re-pinned to
`339cabcd` — **this builds once**; a redeploy alone
would run the old artifact.

    repo unbuiltworks/kalshi-arb-bot
    branch claude/l5-pmus-economic-results-7c116e
    commitSha 339cabcd...

**Step 1 — non-arming variables (`set-variables`, `skipDeploys: true`).**
Every one of these is currently set to the spent 2215z run's value and MUST
be replaced or cleared:

| variable | new value | why |
|---|---|---|
| `L5_LIVE_PROBE_MARKET` | **"" (empty)** | currently `...-2215z`. Left as is, the probe would bind to a finished window. |
| `L5_LIVE_AUTH_LEDGER` | `/state/pmus-ws/nativeq10_<window>_auth.jsonl` | fresh single-use ledger |
| `L5_SESSION_JOURNAL` | `/state/pmus-ws/nativeq10_<window>_journal.jsonl` | fresh |
| `L5_PROBE_STATE` | `/state/pmus-ws/nativeq10_<window>_inventory.json` | fresh; the floor is computed over this session's inventory only |
| `L5_NATIVE_ENGINE` | `1` | unchanged |
| `L5_NATIVE_CLIP` | `10` | unchanged |
| `PMUS_PREFLIGHT_ACCOUNT` | `d51b44a3fa83` | unchanged |
| `L5_OBSERVE_ONLY`, `L5_RECOVER_ONLY`, `L5_RECOVER_*`, `L5_MAKER_ONLY`, `L5_MAKER_TREATMENT`, `L5_IMBALANCE_SUPPRESS` | "" | unchanged, already empty |

Because the pin is empty, `<window>` cannot be known before boot. Use a
run label instead, e.g. `nativeq10_r3`, and let the report record which
window the venue gave. Fresh paths per run is the invariant, not
window-named paths.

**Step 2 — mandate (`set-variables`, `skipDeploys: true`).**

    PMUS_LIVE_MANDATE = {
      "authorization_id": "OWNER-2026-09-28-NATIVEQ10-R3",
      "authorized_at":    "<owner approval time, RFC3339>",
      "service":          "l5-live-probe-oneshot",
      "strategy":         "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
      "strategy_version": "339cabcd-native-q10",
      "max_markets": 1, "max_total_markets": 1,
      "max_spend_per_market": "81.00", "max_total_spend": "81.00",
      "max_residual": "20.00", "max_daily_realized_loss": "20.00",
      "expiry": "<authorized_at + 4h>"
    }

`service` and `strategy` are the literals `rung_a_mandate()` requires; a
mismatch refuses before any POST object exists.

**Step 3 — arming triple, LAST (`set-variables`, `skipDeploys: true`).**

    PMUS_Q1_LIVE       = YES
    PMUS_Q1_SESSION_ID = nativeq10-r3-<yyyymmddThhmmZ>
    PMUS_Q1_ARM        = <the same value>

A NEW session id is required: reusing `nativeq10-20260928-2215z` would
reuse a spent identity.

**Step 4 — run (`redeploy`).** Redeploy the `339cabcd` deployment created by
step 0. `restartPolicyType` is already `NEVER`; no change needed.

**Unchanged:** `dockerfilePath deploy/l5_pmus_lock5_probe/Dockerfile`,
start command, volume mount `/state/pmus-ws`, region, replica count.

## Envelope, unchanged from the approved Q10

clip 10 - 1 market, concurrent 1, total 1 - $81.00 per market - $81.00
total - $20.00 max residual - $20.00 daily realized-loss halt - single-use
authorization - expiry +4 h - restart NEVER.

## Reporting on the run

`PREFLIGHT -> ARMED -> DECISION -> SUBMITTED -> ACK -> FILLED ->
RECONCILED -> SETTLED -> REALIZED`, separately. ACK is not a fill. Nothing
is realized before settlement; the one-shot exits before resolution, so its
terminal label will be `NOT_REALIZED` with a settlement audit to follow.

## Out of scope

No Q25, no Q50, no second market, no strategy change, no other build or
deployment. The spent 2215z variables stay untouched until step 1 replaces
them. The pre-existing unrelated sports residual is not to be touched.
