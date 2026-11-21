# Railway activation manifest — `pmus-sports-forward-collector`

Issue #167 comment 5514826329. This is the exact configuration for the
FIRST deployment of the keyless PMUS sports forward collector as a NEW,
fully isolated service. It is a manifest for the operator/ChatGPT to
apply; nothing here is deployed from the research session.

## Service

| field | value |
|---|---|
| project | `arb-ev-wallet-shadow` |
| environment | `production` |
| service name | `pmus-sports-forward-collector` (NEW — never repurpose an existing service) |
| source | GitHub `unbuiltworks/kalshi-arb-bot`, branch `feature/l5-transfer-backtest` |
| build | root `railway.json` on this branch pins `deploy/pmus_sports_collector/Dockerfile` (builder DOCKERFILE) — the first source deployment cannot fall back to the repo's trading Dockerfile or the research image |
| start command | `python3 -m src.pmus_sports_collector` (also pinned in `railway.json` deploy.startCommand and the image CMD) |
| restart policy | ALWAYS (pinned in `railway.json`) |
| healthcheck | none (worker process; liveness = `heartbeat.json` under the state root) |

## Volume (REQUIRED before any durability claim)

| field | value |
|---|---|
| mount path | `/state/pmus-sports` |
| purpose | raw daily JSONL (`raw/pmus_YYYYMMDD.jsonl`), `state.json`, `heartbeat.json` |

**GO for "restart-durable collection" requires this volume to exist and be
mounted BEFORE acceptance.** Without it the collector still runs correctly
but every redeploy/restart starts from an empty container-local root —
that state must be reported as container-local, never durable.

## Environment variables

Set NONE for the default posture (MLB + active-incentive-program markets,
5s poll, 8 rps budget, 24-market cap, root `/state/pmus-sports`). Optional
tuning knobs (all read-only behavior):

```
PMUS_COLLECTOR_LEAGUES=mlb            # e.g. "mlb,cfb,nfl" to widen
PMUS_MARKET_PREFIXES=aec-,a5c-
PMUS_COLLECT_INCENTIVE_MARKETS=1
PMUS_POLL_SECONDS=5
PMUS_RATE_BUDGET_RPS=8
PMUS_MAX_MARKETS=24
PMUS_DISCOVERY_SECONDS=60
PMUS_INCENTIVES_SNAPSHOT_SECONDS=300
PMUS_COLLECTOR_ROOT=/state/pmus-sports
PMUS_CA_BUNDLE=                       # optional TLS override; empty = system TLS
```

**Do NOT attach or share variables from any trading service.** The service
needs no secrets of any kind; it talks only to the public keyless
`gateway.polymarket.us` with GETs (structurally tested:
`tests/test_pmus_sports_collector.py::test_structurally_read_only_keyless`).

## Post-deploy acceptance checklist

All boxes must pass before the service is called accepted; run them in
order against the NEW service only.

1. **Isolation**: every PRE-EXISTING Railway deployment ID in the project
   is unchanged (in particular `l5-ops-research-live` and
   `l5-transfer-supervisor-shadow`); the new service has no shared
   variables and no secrets.
2. **Correct image**: the deployment's build log shows
   `deploy/pmus_sports_collector/Dockerfile` (not the trading bot's
   Dockerfile, not `research/Dockerfile`).
3. **Heartbeat advances**: `/state/pmus-sports/heartbeat.json` exists with
   `mode == "READ_ONLY_COLLECTOR"` and its `t`/`cycles` advance across two
   reads ≥60s apart.
4. **Rows accumulate**: `raw/pmus_<today>.jsonl` grows between the same
   two reads; rows include `bbo_book` entries with `source:
   "gateway.polymarket.us"`, and any missing public ask side appears as
   `ask_touch_qty: null` with `ask_book_missing: true` (never a number).
5. **Discovery refreshes**: a `discovery` row newer than startup lists
   current targets and the rate plan; if MLB is live, MLB moneylines are
   present; active incentive-program markets are present.
6. **Incentive snapshots land**: an `incentives_snapshot` row exists with
   `active_market_slugs` and full program payloads.
7. **Restart durability (only WITH the volume mounted)**: force one
   restart/redeploy; after it, `state.json.restarts` incremented, the
   same daily file gained new rows (no truncation — line count only
   grows), and `heartbeat.json` resumes advancing. If the volume is not
   mounted, record the service as running-but-container-local and do NOT
   claim durability.
8. **Rate safety**: heartbeat `effective_poll_s` ≥ configured poll and
   consistent with ≤ `PMUS_RATE_BUDGET_RPS`; no sustained 429 errors in
   raw `error` rows.
