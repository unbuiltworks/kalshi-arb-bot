# PMUS_MONEY_LANE_SWEEP_001 — bounded read-only money-lane inventory

Date: 2026-09-28, ~14:50–15:06Z. Railway project `arb-ev-wallet-shadow`
(`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`), environment `production`
(`f3642d27-26dc-4e21-9170-bc76290c076f`). 48 services.

Purpose: stop rediscovering old executors one service at a time. Read-only;
nothing was modified, stopped, redeployed or rebuilt.

## Method, and why liveness is not `latestDeployment.status`

`latestDeployment.status: SUCCESS` means a deployment settled, not that a process
is running; `SKIPPED`/`REMOVED` does not mean the container stopped. That
mistake is what hid `pmus-spring-l5-probe` until now, and it is what made a
removed deployment look like a stopped process earlier the same day.

Liveness oracle used instead: the Railway **dns** log stream. A running process
resolving a hostname emits a timestamped row naming that host. It is compact,
and it simultaneously answers "is it alive" and "which venue is it talking to".

Controls proving the instrument discriminates:
- `l5-pmus-live` (known stopped, heartbeat silent since 14:30:15Z): `dns: []`.
- `pmus-spring-l5-probe` (known running): `gateway.polymarket.us` at 14:57:13Z.

Credential capability is read from variable NAMES (values are redacted to this
OAuth client): `PMUS_API_KEY_ID` + `PMUS_API_SECRET_KEY` present = the process
can sign a PMUS request. Absent = structurally incapable of a signed order,
whatever its code contains.

## Host → venue map

| host | meaning |
|---|---|
| `gateway.polymarket.us`, `api.polymarket.us` | **PMUS** (our money venue) |
| `data-api.polymarket.com`, `clob.polymarket.com`, `ws-subscriptions-clob.polymarket.com` | Polymarket (public research data / different venue) |
| `api.gemini.com` | Gemini exchange price feed (BTC5m/15m research input) |
| `api.elections.kalshi.com` | Kalshi (different venue) |
| `*.railway.internal` | internal feed from a sibling service |

## Classification

| service | alive | host reached | PMUS creds | arm/mandate vars | class |
|---|---|---|---|---|---|
| pmus-spring-l5-probe | YES 14:57Z | PMUS gateway + api | **YES** | `PMUS_SPRING_ARM`, `PMUS_SPRING_LIVE`, `PMUS_SPRING_REARM_GATE`, `PMUS_SPRING_SHUTDOWN`, `PMUS_MIGRATION_CLOSE_SLUG`, `PMUS_RESIDUAL_QUARANTINE` | **MONEY_CAPABLE_ACTIVE** |
| pmus-ws-evidence-recorder | YES 14:58Z | PMUS gateway + api | **YES** | none (`L5_PROBE_DRYRUN_*`, `PMUS_PREFLIGHT_ON_START`) | **MONEY_CAPABLE_ACTIVE** |
| l5-pmus-observer | YES 14:58Z | data-api.polymarket.com only | **YES** | none | **MONEY_CAPABLE_ACTIVE** (credentialed; not observed on a PMUS host) |
| pmus-btc15m-tape | YES 14:58Z | PMUS gateway | no | none | READ_ONLY |
| pmus-sports-forward-collector | YES 14:58Z | PMUS gateway | no | none | READ_ONLY |
| pmus-shadow-breadth | YES 14:59Z | PMUS gateway | no | none | READ_ONLY |
| l5-shadow-loop | YES 15:00Z | PMUS gateway | no | none | READ_ONLY |
| l5-native-shadow | YES 15:01Z | PMUS gateway | no | none | READ_ONLY |
| l5-exec-shadow | YES 14:59Z | data-api.polymarket.com | no | none | READ_ONLY |
| l5-fidelity-runner | YES 15:00Z | data-api.polymarket.com | — | — | READ_ONLY |
| l5-v1-shadow | YES 15:00Z | internal (ws-evidence-recorder) | — | — | READ_ONLY |
| l5-v1-1-shadow | YES 15:00Z | internal (ws-evidence-recorder) | — | — | READ_ONLY |
| l5-5m-recorder | YES 15:00Z | clob.polymarket.com | — | — | READ_ONLY |
| gemini-maker-shadow | YES 14:59Z | api.gemini.com | — | — | UNRELATED |
| gemini-econ-shadow | YES 15:00Z | api.gemini.com | — | — | UNRELATED |
| gemini-l5-faithful | YES 15:00Z | api.gemini.com | — | — | UNRELATED |
| gemini-l5-shadow | YES 15:00Z | api.gemini.com | — | — | UNRELATED |
| gemini-l5-queue-shadow | YES 15:01Z | api.gemini.com | — | — | UNRELATED |
| kalshi-l5-shadow | YES 15:01Z | api.elections.kalshi.com | — | — | UNRELATED |
| polymarket-wallet-shadow | YES 15:01Z | clob.polymarket.com | — | — | UNRELATED |
| polymarket-temporal-shadow-run | YES 15:00Z | clob.polymarket.com | — | — | UNRELATED |
| polymarket-maker-econ-shadow | YES 15:01Z | clob + ws-subscriptions | — | — | UNRELATED |
| polymarket-maker-ws-shadow | YES 15:02Z | clob + ws-subscriptions | — | — | UNRELATED |
| polymarket-l5-econ-ledger | YES 14:57Z | data-api.polymarket.com | — | — | UNRELATED |
| l5-pmus-live | no (since 14:30:15Z) | — | yes | arm triple present, unset | INACTIVE |
| l5-live-probe-oneshot | no | — | yes | `PMUS_LIVE_MANDATE` empty, arm triple empty | INACTIVE (the Rung-A artifact, DISARMED) |
| pmus-q1-executor | no | — | — | — | INACTIVE |
| l5-r1-probe | no | — | — | — | INACTIVE |
| l5-live-habitat | no | — | — | — | INACTIVE |
| pmus-preview-oneshot | no | — | — | — | INACTIVE |
| pmus-readonly-recovery | no | — | — | — | INACTIVE |
| gemini-5m-collector | no | — | — | — | INACTIVE |
| kalshi-arb-bot | no | — | — | — | INACTIVE |
| kalshi-temporal-shadow | no | — | — | — | INACTIVE |
| gemini-fair-value-shadow | no | — | — | — | INACTIVE |
| l5-ops-research-live | no | — | — | — | INACTIVE |
| l5-transfer-supervisor-shadow | no | — | — | — | INACTIVE |
| r5ft-recon-7b75689c-buildcheck | no | — | — | — | INACTIVE |
| kalshi-incentive-shadow | no (CRASHED) | — | — | — | INACTIVE |
| pmus-ws-book-diagnostic | no (CRASHED) | — | — | — | INACTIVE |
| polymarket-temporal-shadow, polymarket-l5-replay-once, l5-ops-research, l5-window-collector | no deployment at all | — | — | — | INACTIVE |
| r5ft-sticky-complete-buildcheck, r5ft-sticky-complete-clean-buildcheck, r5ft-clean-2ebb50f-buildcheck, pmus-r5ft-complete-retry-buildcheck | not probed individually | — | — | — | UNKNOWN (build-check leftovers, 2026-09-12/13) |

## `pmus-spring-l5-probe` — why it exists and what it owns

Source: repo `unbuiltworks/kalshi-arb-bot`, branch `feature/l5-transfer-backtest`,
**no `commitSha` pin** — it tracks that branch's head. Dockerfile
`/deploy/pmus_spring_l5/Dockerfile`, watch pattern
`deploy/pmus_spring_l5/DEPLOY_TRIGGER`, `restartPolicyType: ALWAYS`.

Start command (verbatim) rewrites its own source at boot before running:

    sh -c 'sed -i "s/^REARM_RUNG = \"R5F\"$/REARM_RUNG = \"R5FT\"/" \
      src/pmus_l5_continuous_session.py && python3 -m src.pmus_r5ft_ledger_repair \
      && exec python3 -m src.pmus_l5_continuous_session'

Observed behaviour, ~13-minute cycle (14:18:03Z, 14:31:08Z, 14:44:23Z):
`PMUS_L5_CAMPAIGN kind=shutdown_idle verdict=SHUTDOWN_VERIFIED`, `refusals: 0`,
`rearm_recheck: null`. It is in verified shutdown-idle, not trading a campaign.

Its one remaining order path is `PMUS_MIGRATION_CLOSE`, a reduce-only BUY on a
single pinned slug, capped at `abs(residual)` and unable to open, extend or cross
zero. Every cycle it returns `REFUSED_PRECONDITION`. Its own labels state the
governing rule: "a check that cannot be established FAILS".

Failing preconditions each cycle:
`venue_qty_equals_ledger_qty` (`None vs 45.01`),
`fresh_executable_quote_and_depth` (`age_s=0.0 ask=None depth=None`),
`incremental_locked_loss_within_cap` (`None <= 0.50`),
`combined_loss_stays_above_stop` (`-17.260200 - None = None vs -15.00`).

All four fail because the venue read returns null, not because the process is
disarmed. **If the quote read starts succeeding, three of the four could
resolve.** The refusal is contingent on a data outage. That is the risk.

Its log states the reason it stays up: "halted and holding the container up on
purpose: exiting here would be read as a crash and roll the service back to the
previous, possibly ARMED, build." With `restartPolicyType: ALWAYS` and an
unpinned branch source, that warning is consistent with its own configuration.

## Outstanding PMUS exposure

| item | value | source |
|---|---|---|
| residual | **−11.67** on `aec-county-not-ham-2026-09-08` (`up=16.67`, `dn=28.34`, ledger qty `45.01`) | spring-probe 14:44:23Z |
| unsettled floor | **−$17.260200** | same |
| combined loss vs budget | −$17.260200 of −$30.00 (`budget_permits_normal_operation: true`) | same |
| realized today | $0 | same |
| open orders | **0** (`zero_existing_orders: ok, got 0`) | same |
| pending settlements | **96**, `settled_now: 0`; audit walk blocked by `HTTP 429` and `activity_walk_short_of_since` | same |
| held, preserved | `aec-atp-blaell-hirmor-2026-09-13` | same |
| BTC15m lane (`l5-pmus-live`) | flat: zero positions, zero open orders, zero unsettled; `realized_total −4.50680000` | l5-pmus-live final read |

## Safest non-trading quiescence, per competing executor

Not executed. Recorded for authorization.

**pmus-spring-l5-probe.** Do not crash it, do not redeploy it, do not change its
variables with a deploy. Every one of those rebuilds from an unpinned branch
head. The genuinely safe order, cheapest first:
1. Do nothing. It is already `SHUTDOWN_VERIFIED`, holds 0 open orders, and its
   only order path is refused. This costs one container and no money.
2. If it must be made incapable rather than merely refusing: revoke the PMUS API
   key it holds at the venue, or blank `PMUS_API_SECRET_KEY` with
   `skipDeploys: true`. Both leave the running process unable to sign, and
   neither triggers a build. Note the running container keeps the value it
   started with, so a variable blank alone does not disarm the live process —
   venue-side key revocation is the only change that binds immediately, and it
   would also disarm every other service sharing that key.
3. Stopping the container at all should be a deliberate, separately authorized
   act that first pins `commitSha` and sets `restartPolicyType: NEVER`, so what
   comes back if it restarts is a known build. That is a config write and is
   outside the current freeze.

**pmus-ws-evidence-recorder** and **l5-pmus-observer** are credentialed and live
but show no arm or mandate variable and no order activity. Same ladder applies;
neither is urgent.

## Railway resources consumable later

Not acted on. `polymarket-temporal-shadow`, `polymarket-l5-replay-once`,
`l5-ops-research`, `l5-window-collector` have no deployment at all. The four
`r5ft-*`/`pmus-r5ft-*-buildcheck` services are 2026-09-12/13 build-check
leftovers. `kalshi-incentive-shadow` and `pmus-ws-book-diagnostic` are CRASHED.
Nine large volumes (50 GB each) back shadow services that are not running.
An `EnvironmentPatch` has been staged and uncommitted since 2026-09-24T20:29:56Z.

## Blockers on the `e7178999` $1 Rung-A probe

1. Unrelated residual exposure exists (−11.67 / −$17.2602), which the Rung-A
   mandate's own restriction forbids operating alongside.
2. A second credentialed, live process with a (currently refused) order path
   exists on the account: `pmus-spring-l5-probe`.
3. Two further credentialed live processes, `pmus-ws-evidence-recorder` and
   `l5-pmus-observer`, are not accounted for as disarmed by evidence.
4. Four `r5ft-*` build-check services remain UNKNOWN.

Nothing else was found. The artifact itself is ready: `l5-live-probe-oneshot`
pins `commitSha e7178999`, `deploy/l5_pmus_lock5_probe/Dockerfile`,
`restartPolicyType NEVER`, `staged: null`, `PMUS_LIVE_MANDATE` empty, arm triple
empty, and its BTC15m lane is flat.
