# Activation manifest — NATIVE Q10 live probe #3, artifact `de72ab80`

Status: **PREPARED, NOT ARMED.** No variable has been written. Nothing is
deployed. Arming is a separate act and needs explicit authorization.

## Artifact (already built, not rebuilt)

| | |
|---|---|
| Service | `l5-live-probe-oneshot` (84aa9999-6d7a-4980-9a87-96694d8178dd) |
| Commit | `de72ab8045fbce8e4d44b381ff08e2b210e845cf` (source pinned) |
| Deployment to redeploy | `2045ae4e-70a3-43c7-bbc7-f256129f5a02` (SUCCESS) |
| Image digest | `sha256:ac396be1fb3e96d96821b8679f6f190880c27548c8bd4c741973ee943050401f` |
| Dockerfile | `deploy/l5_pmus_lock5_probe/Dockerfile` |
| restartPolicyType | NEVER |
| Builds this step | **0** — redeploy reuses the existing build |

## Selected market

**`cpc-btc-updown-15m-2026-09-28-2215z`** — window 22:15:00Z–22:30:00Z.

Roll rule, stated so it is not a judgement call at arm time: if arming has
not happened by **22:10:00Z**, the pin moves to the next 15-minute window
that leaves at least **4 minutes** of lead before its start, and only the
market pin, the session id, the journal/state paths and the authorization
id change with it. Nothing else in this manifest moves. Lead time is
required because the container took 97 s from deploy creation to first
line on the last run, and `run()` must be waiting before the window opens.

## Environment to install at arm time

Non-arming (set first, `skipDeploys: true`):

```
L5_NATIVE_ENGINE          = 1
L5_NATIVE_CLIP            = 10
L5_LIVE_PROBE_MARKET      = cpc-btc-updown-15m-2026-09-28-2215z
L5_LIVE_AUTH_LEDGER       = /state/pmus-ws/nativeq10_20260928_2215z_auth.jsonl
L5_SESSION_JOURNAL        = /state/pmus-ws/nativeq10_20260928_2215z_journal.jsonl
L5_PROBE_STATE            = /state/pmus-ws/nativeq10_20260928_2215z_inventory.json
PMUS_PREFLIGHT_ACCOUNT    = d51b44a3fa83
L5_OBSERVE_ONLY           = (empty)
L5_RECOVER_ONLY           = (empty)
L5_MAKER_ONLY             = (empty)
L5_MAKER_TREATMENT        = (empty)
L5_IMBALANCE_SUPPRESS     = (empty)
PMUS_LIVE_MANDATE         = the JSON below
```

Ledger, journal and inventory paths are fresh for this window, so the
authorization cannot be inherited, the journal cannot be confused with
2000z's, and `settlement_independent_floor` is computed over this
session's inventory only.

Arming triple (set LAST, in one call, and the deploy follows):

```
PMUS_Q1_LIVE       = YES
PMUS_Q1_SESSION_ID = nativeq10-20260928-2215z
PMUS_Q1_ARM        = nativeq10-20260928-2215z
```

## Mandate — `PMUS_LIVE_MANDATE`

```json
{
  "authorization_id": "OWNER-2026-09-28-NATIVEQ10-2215Z",
  "authorized_at": "2026-09-28T21:42:45Z",
  "service": "l5-live-probe-oneshot",
  "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
  "strategy_version": "de72ab80-native-q10",
  "max_markets": 1,
  "max_total_markets": 1,
  "max_spend_per_market": "81.00",
  "max_total_spend": "81.00",
  "max_residual": "20.00",
  "max_daily_realized_loss": "20.00",
  "expiry": "2026-09-29T01:42:45Z"
}
```

`service` and `strategy` are the literals `rung_a_mandate()` requires
(`RUNG_A_SERVICE`, `RUNG_A_STRATEGY`); a mismatch refuses before any POST
object exists. Single use is enforced by the ledger, not by the env: the
session id is written to the fresh ledger before any order exists and is
spent forever, so a restart, a crash or a second window is refused with
`AUTHORIZATION_ALREADY_USED`. `restartPolicyType NEVER` means there is no
automatic second attempt regardless.

## Economic envelope

| control | value |
|---|---|
| clip | 10 contracts (`L5_NATIVE_CLIP`) |
| markets | exactly 1, concurrent 1, total 1 |
| max spend per market | $81.00 |
| max total spend | $81.00 |
| max residual | $20.00 |
| max daily realized loss | $20.00 |
| authorization | single use, fresh ledger |
| expiry | 2026-09-29T01:42:45Z (+4 h) |

Unchanged frozen policy: theta 0.96, variant B, hold 60 s,
`residual_cap_k` 2, `profit_lock_usd` $5, `floor_guard` False,
`complement_bias` None. Clip is an execution/capacity control only.

## Preflight, under option B

Proofs #3 and #4 are taken as first-minute evidence of this run:

- `account_readable` + `account_binding_proven` + `account_matches_expected`
  against `d51b44a3fa83` must PASS;
- `market_readable` + `market_resolves` + `market_series_is_btc15m` +
  `market_open_tradable` + `tick_constraint_known` +
  `quantity_constraint_known` must PASS.

On failure of either: **no blind retrying for the rest of the window.**
`de72ab80` prints `PMUS_PREFLIGHT_FAIL {check, where, exception,
http_status, detail}` on every `_fail()`, and `L5_LIVE_PROBE` carries
`preflight_failing_check`. The probe stays unarmed, submits nothing, and
the evidence is reported immediately.

The REST book snapshot is non-blocking by design in this artifact; a
failure prints `PMUS_PREFLIGHT_DEGRADED` and the window continues, because
`l5_preview_gate.require_fresh_book` enforces a fresh authenticated
two-sided book at order time on every order.

## Reporting chain

Reported separately, never merged:

`PREFLIGHT → ARMED → DECISION → SUBMITTED → ACK → FILLED → RECONCILED →
SETTLED → REALIZED`

ACK is not a fill. Nothing is called realized before settlement; the
settlement value comes from the venue after the market resolves, and this
one-shot exits before that, so this run's terminal label will be
`NOT_REALIZED` with a settlement audit to follow.

## Out of scope

No Q25, no Q50. No strategy change. No build. No other deploy. No new
service. Pre-existing unrelated sports residual is not to be touched.
