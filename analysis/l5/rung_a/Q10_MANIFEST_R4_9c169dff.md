# Q10 execution manifest R4 — candidate `9c169dff`

Status: **PREPARED. NOT ARMED, NOT DEPLOYED.** No Railway change made. No
authorization claimed.

## Candidate

| | |
|---|---|
| Commit | `9c169dff` (fix `17d60827` + orphan audit `569c3238` + lineage disclosure) |
| Service | `l5-live-probe-oneshot` (84aa9999), existing |
| Dockerfile | `deploy/l5_pmus_lock5_probe/Dockerfile` |
| restartPolicyType | NEVER (already set) |
| R1 lineage | **ACCEPTANCE True**, `undisclosed []` |

## Window selection — no fixed target, a standing rule

`L5_LIVE_PROBE_MARKET` stays **EMPTY**. `select_venue_window` asks the venue
and takes the window in progress if >= 240 s remain, else the next one to
start. There is therefore no deadline to miss and no stale pin to inherit --
the 2215z failure mode is structurally gone.

**Earliest full window with adequate lead, read live at 00:23:30Z:**

| slug | window | lead at read | status | tick | min qty |
|---|---|---|---|---|---|
| **`cpc-btc-updown-15m-2026-09-29-0030z`** | **00:30:00–00:45:00Z** | 389 s | CLOSED (pre-open) | 0.01 | 0.01 |
| `cpc-btc-updown-15m-2026-09-29-0045z` | 00:45–01:00Z | 1289 s | CLOSED | 0.01 | 0.01 |
| `cpc-btc-updown-15m-2026-09-29-0100z` | 01:00–01:15Z | 2189 s | CLOSED | 0.01 | 0.01 |

**Activation rule:** redeploy at any moment when the window in progress has
**under 240 s left** -- i.e. in the last 4 minutes of any quarter hour. Boot
took 26-30 s on the last two runs, so discovery then lands on the NEXT full
window with roughly 3.5-4 minutes of pre-window lead, which is what the
metadata pin needs. For the 0030z window that means redeploying between
**00:26:00Z and 00:29:00Z**; the same rule applies unchanged to 0045z, 0100z
and every window after.

## Railway changes required — NONE PERFORMED

1. **`connect-service-source`** -> `9c169dff...` (one build). The service is
   pinned to `339cabcd`; a bare redeploy would run the old artifact.
2. **`set-variables`, `skipDeploys: true`** -- non-arming. Every path is fresh
   for R4, and the market pin is cleared:

   | variable | value |
   |---|---|
   | `L5_LIVE_PROBE_MARKET` | **"" (empty)** -- venue discovery selects |
   | `L5_LIVE_AUTH_LEDGER` | `/state/pmus-ws/nativeq10_r4_auth.jsonl` |
   | `L5_SESSION_JOURNAL` | `/state/pmus-ws/nativeq10_r4_journal.jsonl` |
   | `L5_PROBE_STATE` | `/state/pmus-ws/nativeq10_r4_inventory.json` |
   | `L5_NATIVE_ENGINE` | `1` |
   | `L5_NATIVE_CLIP` | `10` |
   | `PMUS_PREFLIGHT_ACCOUNT` | `d51b44a3fa83` |
   | `L5_OBSERVE_ONLY` / `L5_RECOVER_*` / `L5_MAKER_ONLY` / `L5_MAKER_TREATMENT` / `L5_IMBALANCE_SUPPRESS` | "" |

3. **`set-variables`, `skipDeploys: true`** -- the mandate. A **NEW**
   `authorization_id` is required: `OWNER-2026-09-28-NATIVEQ10-R3` carries the
   orphan commitment audited in `ORPHAN_COMMITMENT_AUDIT_R3.md` and would
   start with it charged.

   ```json
   {"authorization_id":"OWNER-2026-09-29-NATIVEQ10-R4",
    "authorized_at":"<owner approval time, RFC3339>",
    "service":"l5-live-probe-oneshot",
    "strategy":"BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
    "strategy_version":"9c169dff-native-q10",
    "max_markets":1,"max_total_markets":1,
    "max_spend_per_market":"81.00","max_total_spend":"81.00",
    "max_residual":"20.00","max_daily_realized_loss":"20.00",
    "expiry":"<authorized_at + 4h>"}
   ```

4. **`set-variables`, `skipDeploys: true`** -- arming triple LAST, new session
   id:

   ```
   PMUS_Q1_LIVE       = YES
   PMUS_Q1_SESSION_ID = nativeq10-r4-<yyyymmddThhmmZ>
   PMUS_Q1_ARM        = <same value>
   ```

5. **`redeploy`** inside the activation rule above.

Unchanged: dockerfile path, start command, volume mount `/state/pmus-ws`,
region, replicas, restart policy.

## Envelope — exactly as specified, unchanged

clip 10 - 1 market, concurrent 1, total 1 - $81.00 per market - $81.00 total -
$20.00 max residual - $20.00 daily realized-loss halt - single-use
authorization on a fresh ledger - expiry +4 h - restart NEVER.

Frozen policy untouched: theta 0.96, variant B, hold 60 s, residual_cap_k 2,
LOCK5 $5, floor_guard False, complement_bias None, taker completion
behaviour, queue_aware fill model in the adapter.

## What is different from R3, and why it should now submit

R3 reached ARMED and one DECISION and then died at the first POST:
`pmus_native_money_path` called `transport.request("POST","/v1/orders",body)`
and `PmusTransport` exposes no public `request`. The native path now calls
`PmusTransport.submit_body(body)` -- the certified preview -> echo -> POST
sequence, one implementation, with the mandate guard admitting in front of
it. Proven across the real composition
(`tests/test_production_composition_submit.py`, 13 tests: real
`MANDATE.guard(real PmusTransport)`, only HTTP faked).

## Reporting

`PREFLIGHT -> ARMED -> DECISION -> SUBMITTED -> ACK -> FILLED ->
RECONCILED -> SETTLED -> REALIZED`, separately. ACK is not a fill. Nothing is
realized before settlement; this one-shot exits before resolution, so its
terminal label will be `NOT_REALIZED` with a settlement audit to follow.

## Out of scope

No Q25, no Q50, no second market, no strategy change, no other build or
deployment, no unrelated cleanup. The pre-existing unrelated sports residual
is not to be touched.
