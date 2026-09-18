# Railway cost audit, 2026-09-18T16:2xZ

Project `arb-ev-wallet-shadow` (`50fb7357-…`), environment `production`
(`f3642d27-…`). **38 services, all `state: live`, all `numReplicas: 1`, all
`us-east4-eqdc4a`. 14 volumes.**

**Read-only. NOTHING WAS STOPPED, SCALED, DELETED OR REDEPLOYED.** Classification
first, as instructed.

## The headline: storage is provisioned ~9x larger than it is used

Every volume is provisioned at **50 GB**. Measured usage (1-hour averaging
window):

| service | used GB | of 50 |
|---|---:|---:|
| pmus-ws-evidence-recorder | 27.12 | 54% |
| l5-exec-shadow | 24.83 | 50% |
| pmus-sports-forward-collector | 4.24 | 8% |
| l5-5m-recorder | 3.67 | 7% |
| l5-v1-1-shadow | 3.17 | 6% |
| l5-v1-shadow | 2.38 | 5% |
| pmus-shadow-breadth | 1.61 | 3% |
| kalshi-l5-shadow | 1.60 | 3% |
| gemini-5m-collector | 1.52 | 3% |
| l5-shadow-loop | 1.37 | 3% |
| gemini-l5-shadow | 1.34 | 3% |
| pmus-spring-l5-probe (HALTED) | 1.30 | 3% |
| l5-fidelity-runner | 1.09 | 2% |
| l5-r1-probe (HALTED) | 0.84 | 2% |

**700 GB provisioned, 76.1 GB used — 10.9%. 624 GB is paid-for and empty.**

Two services account for 68% of all bytes actually stored, and both are ones we
want. The other twelve hold ~1–4 GB each on 50 GB volumes.

This is almost certainly the single largest line on the bill, and it is
addressable without stopping a single service. Caveat worth checking before
acting: Railway volumes generally resize UP easily and DOWN not at all, so the
realistic move may be create-small / migrate / delete-large per service rather
than an in-place shrink. **Not attempted.**

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

1. **Shrink the 12 over-provisioned volumes.** 624 GB idle, no service stops.
   Biggest saving, lowest risk. Verify the resize-down constraint first.
2. **Delete the 7 ONE-OFF services.** No volumes, no dependents, two already
   crashed. Pure cleanup.
3. **Stop the 11 cron-dormant SUPERSEDED services.** They fire once a year.
4. **Resolve the 4 UNKNOWNs** before touching them.

Nothing in this list is actioned. Stopping anything is the owner's call, and
`pmus-spring-l5-probe` and `l5-r1-probe` are excluded from all of it.
