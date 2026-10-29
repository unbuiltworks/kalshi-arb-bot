# Railway cost audit, 2026-09-18T16:2xZ

Project `arb-ev-wallet-shadow` (`50fb7357-…`), environment `production`
(`f3642d27-…`). **38 services, all `state: live`, all `numReplicas: 1`, all
`us-east4-eqdc4a`. 14 volumes.**

**Read-only. NOTHING WAS STOPPED, SCALED, DELETED OR REDEPLOYED.** Classification
first, as instructed.

## CORRECTION (17:1xZ): my first headline was wrong

I first reported "700 GB provisioned, 76.1 GB used, 624 GB paid-for and empty"
and called it the largest line on the bill. **That is not a cost.** Railway's
own reference says:

> "You are only charged for the amount of storage **used** by your volumes."
> "Down-sizing a volume is not currently supported, but increasing size is
> supported"

So the 50 GB figure is simply the **Pro-plan default volume size**, not an
over-provision anybody chose, the idle 624 GB is billed at nothing, and
shrinking is both unnecessary and unsupported. The recommendation to shrink
twelve volumes is withdrawn entirely.

Storage actually billed is the **76.1 GB used**, and two services we want are
68% of it: `pmus-ws-evidence-recorder` (27.1 GB) and `l5-exec-shadow` (24.8 GB).
There is no storage saving available here worth pursuing.

### What the measured numbers actually say

Sampling settles which services cost anything at all:

| service | class | CPU | memory | running? |
|---|---|---:|---:|---|
| `r5ft-recon-7b75689c-buildcheck` | ONE-OFF | 0.0 | 0.0 GB | **no** |
| `polymarket-l5-replay-once` | ONE-OFF (crashed) | 0.0 | 0.0 GB | **no** |
| `kalshi-incentive-shadow` | SUPERSEDED (cron) | 0.0023 | **0.080 GB** | **yes** |

Two corrections follow from that.

**The ONE-OFF services cost nothing.** They are not running -- zero CPU and
zero memory across all 61 samples. Deleting them is hygiene and clarity, not
saving. That also means they are not urgent.

**The cron-dormant services are NOT dormant.** A schedule of `0 0 1 1 *` fires
once a year, but the containers are up continuously at ~80 MB each. Eleven of
them is roughly **0.9 GB of memory held around the clock** for work that runs
on January 1. This is the one genuine, ongoing saving in the project, and it is
the opposite of where I first pointed.

## Classification

### CORE — keep running, P0 evidence path (2)
| service | evidence |
|---|---|
| `pmus-ws-evidence-recorder` | live at 16:21:22Z: `conn: connected`, `auth: ok`, 160 subscribed markets, 5,710,067 messages. Serves `/trades` to the PMUS ladder. CPU 0.18. |
| `l5-exec-shadow` | CPU 0.32 avg, disk growing 24.8 GB. Zero-order isolation must be preserved. |

### ACTIVE SHADOW — running and writing; keep for now (10)
`pmus-sports-forward-collector` (CPU 0.14) · `l5-5m-recorder` (0.056) ·
`pmus-shadow-breadth` (0.043) · `gemini-l5-shadow` (0.014) ·
`gemini-5m-collector` (0.014) · `kalshi-l5-shadow` (0.008) ·
`l5-v1-shadow` (0.007) · `l5-fidelity-runner` (0.005, near-idle) ·
`l5-shadow-loop` (0.004) · `l5-v1-1-shadow` (0.004)

`l5-fidelity-runner` is near-idle (CPU 0.005, disk flat at 1.09 GB) — worth
confirming it is still doing work rather than parked.

### HALTED — DO NOT TOUCH (2)
| service | evidence |
|---|---|
| `l5-r1-probe` | CPU exactly **0.0** across all 61 samples. Confirmed halted. |
| `pmus-spring-l5-probe` | **FLAG.** Disk flat (1.300217856 → 1.30023424 GB, i.e. not writing), current CPU 0 — but average 0.45 and max 1.24 vCPU over the window. That is real compute for something that must stay halted. Most likely a restart loop rather than trading, but it is an inference. Worth confirming the latch directly. No action taken. |

### SUPERSEDED — strong stop candidates, nothing stopped (13)
Eleven are on cron `0 0 1 1 *` — **January 1 only**, i.e. dormant year-round
while still provisioned:
`kalshi-incentive-shadow` · `kalshi-temporal-shadow` · `polymarket-l5-econ-ledger` ·
`gemini-maker-shadow` · `polymarket-temporal-shadow-run` · `polymarket-wallet-shadow` ·
`polymarket-maker-econ-shadow` · `polymarket-maker-ws-shadow` ·
`gemini-fair-value-shadow` · `l5-ops-research-live` · `l5-transfer-supervisor-shadow`

Plus two superseded by the current L5 lane:
`gemini-l5-queue-shadow` (branch `feature/l5-gemini-queue-shadow`) ·
`l5-native-shadow` · `pmus-q1-executor`

### ONE-OFF — build verification and single runs, cleanest candidates (7)
Five `*-buildcheck` services on merged or abandoned branches:
`r5ft-recon-7b75689c-buildcheck` · `r5ft-clean-2ebb50f-buildcheck` ·
`r5ft-sticky-complete-clean-buildcheck` · `r5ft-sticky-complete-buildcheck` ·
`pmus-r5ft-complete-retry-buildcheck`

Two whose latest deployment **CRASHED** and was never revived:
`polymarket-l5-replay-once` (crashed 2026-08-28) ·
`pmus-ws-book-diagnostic` (crashed 2026-09-06)

### UNKNOWN — cannot classify from available evidence (4)
| service | why |
|---|---|
| `l5-ops-research` | `source: null`, `latestDeployment: null`. Nothing to read. |
| `polymarket-temporal-shadow` | `source: null`, `latestDeployment: null`. Note a near-twin `polymarket-temporal-shadow-run` exists. |
| `kalshi-arb-bot` | the repo's namesake service on `feature/l5-transfer-backtest`. Deployed 2026-09-15. Purpose not established from this evidence; it may be the original arbitrage bot, which the L5 branch supersedes — but that is a guess and it is labelled as one. |
| `l5-ops-research-live` | also counted above as cron-dormant; its relationship to `l5-ops-research` is unresolved. |

## What I would do next, in cost order

1. **Stop the 11 cron-family SUPERSEDED services.** ~0.9 GB of memory held
   continuously for work scheduled once a year. This is the only material
   ongoing saving found. None has a volume; none is referenced by running code.
2. **Delete the 7 ONE-OFF services.** Zero saving -- they are already not
   running -- but they are dead surface that reads as live. Two crashed weeks
   ago; five are buildchecks on merged or abandoned branches.
3. **Resolve the 4 UNKNOWNs** before touching them.
4. **Volumes: no action.** Billed on used bytes, and downsizing is unsupported.

`pmus-spring-l5-probe` and `l5-r1-probe` are excluded from all of it.
`pmus-spring-l5-probe` additionally holds a preserved position
(`aec-atp-blaell-hirmor-2026-09-13`) and a long settlement-pending list on its
volume, so it is not a deletion candidate on any timescale.

Nothing here is actioned. Stopping anything is the owner's call.

## Why `pmus-spring-l5-probe` consumes CPU while halted

Answered from its own logs. Every ~12.4 minutes it wakes, runs a shutdown
cycle and emits:

```
PMUS_L5_CAMPAIGN {"kind":"shutdown_idle","verdict":"SHUTDOWN_VERIFIED",
                  "held_preserved":["aec-atp-blaell-hirmor-2026-09-13"], ...}
PMUS_MIGRATION_CLOSE {"stage":"migration_close",
                      "state":"REFUSED_PRECONDITION","flat":false, ...}
```

It is **not trading**. It is a verified-shutdown loop that also retries a
`migration_close` its own preconditions refuse, forever. That wake-verify burst
every 12 minutes is the CPU: current 0, average 0.45, peak 1.24 vCPU, with disk
flat at 1.300 GB (1,300,217,856 -> 1,300,234,240 bytes over the hour, i.e. not
writing). The halt is holding and re-asserting itself on every cycle.

The endless refused retry is wasted work, but stopping it is not free: the
service holds preserved inventory and a pending-settlement ledger. Left alone.

## Relationship to the 2026-09-15 audit

`deploy/DEPLOYMENT_COST_AUDIT.md` addressed **build** cost -- push fan-out,
fixed with watch patterns. This one addresses **runtime** cost. They are
complementary; nothing here contradicts it.
