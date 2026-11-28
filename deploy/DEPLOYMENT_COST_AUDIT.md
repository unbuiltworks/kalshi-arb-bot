# Deployment cost audit — 2026-09-15

**Headline: 3 of every 4 successful builds on a production push were waste, and
43% of all builds in the measured window came from push fan-out.** Fixed by
scoping watch patterns on seven services. No service deleted, no trading
behaviour touched, no risk limit changed.

## Measurement, and what the numbers do and don't say

Railway keeps only a service's *current* deployment as `SUCCESS` and marks
superseded ones `REMOVED`, so a `SUCCESS`-filtered listing returns roughly one
row per service and is **not** a build count. That artifact is easy to misread
as "30 builds in 9 days"; it isn't. Counts below come from full per-service
listings including `REMOVED`.

`SKIPPED` rows are free — Railway evaluated the watch patterns and built
nothing. They are noise, not cost, and are reported separately.

### Volume in the sampled window

| window | records | SUCCESS | SKIPPED | REMOVED |
|---|---|---|---|---|
| all services, 08:29→12:05 (3.6 h) | 50 | 5 | 41 | 4 |
| `l5-5m-recorder`, 13.4 h | 31 | 1 | 26 | 4 |

`l5-5m-recorder` ran at **9 builds/day and 47 skipped-evaluations/day**. Those
builds were genuine development iterations on the recorder itself, not waste.

## The actual waste: push fan-out

Builds firing in the same minute from one push:

| when | builds | services |
|---|---|---|
| 2026-09-06T17:53 | **10** | 8 shadow/econ services + `l5-ops-research-live` + `l5-transfer-supervisor-shadow` |
| 2026-09-13T00:46 | 2 | `r5ft-sticky-buildcheck`, `r5ft-sticky-clean-buildcheck` |
| 2026-09-15T12:04 | 4 | `pmus-spring-l5-probe` *(intended)*, `kalshi-arb-bot`, `kalshi-l5-shadow`, `r5ft-recon-buildcheck` |

**13 of 30 builds (43%) were redundant fan-out.** On the 12:04 push
specifically, 3 of 4 builds were unwanted.

## Root causes, by class

**A. No watch patterns at all, on the production branch** — rebuilds on *every*
push to `feature/l5-transfer-backtest`:

| service | evidence |
|---|---|
| `kalshi-arb-bot` | RAILPACK, no patterns, **no volume, no variables** — a legacy shell that still costs a build |
| `kalshi-l5-shadow` | RAILPACK, no patterns; a real service (50 GB volume, 7 vars) but rebuilt on unrelated changes |

**B. Deployments used as jobs** — five `*-buildcheck` services, each
`restartPolicyType: NEVER`, whose start command imports modules, prints a line
and exits. `r5ft-recon-7b75689c-buildcheck` watched `src/**` + `tests/**` and
rebuilt the **entire executor Dockerfile** to do it. These are leftover
one-shot diagnostics from past investigations (`7b75689c`, `2ebb50f`,
sticky-complete, complete-retry). Three of the five point at stale branches and
are dormant only because nobody pushes there — a latent cost, not a safe state.

**C. Already correct, verified not changed:**

| service | watch patterns | verdict |
|---|---|---|
| `pmus-spring-l5-probe` | `deploy/pmus_spring_l5/DEPLOY_TRIGGER` | **already explicit-promotion-only** |
| `l5-fidelity-runner` | `deploy/l5_fidelity/**`, `src/l5_fidelity/**`, `artifacts/l5_fidelity/**` | correctly scoped |
| `l5-5m-recorder` | `deploy/l5_5m_recorder/**`, 3 named source files | correctly scoped |
| L5_CHAIN_OBSERVER | *no service exists* | nothing to scope |

The executor requirement — "a git push, research commit, documentation change,
FIDELITY change, collector change or runtime-data update must not redeploy it"
— **was already satisfied** and needed confirmation rather than a change.
Editing `src/l5_fidelity/**` cannot rebuild the executor, and never could.

## Changes made

Seven services repointed at dedicated sentinels under `deploy/_promote/`.
`watchPatterns` only — `restartPolicyType`, start commands, variables, volumes
and limits untouched on every one.

| service | before | after |
|---|---|---|
| `kalshi-arb-bot` | *(none — every push)* | `deploy/_promote/kalshi-arb-bot` |
| `kalshi-l5-shadow` | *(none — every push)* | `deploy/_promote/kalshi-l5-shadow`, `src/kalshi_l5_shadow.py` |
| `r5ft-recon-7b75689c-buildcheck` | `src/**`, `tests/**`, `deploy/pmus_spring_l5/**` | `deploy/_promote/buildcheck-r5ft-recon` |
| `r5ft-clean-2ebb50f-buildcheck` | *(none)* | `deploy/_promote/buildcheck-r5ft-clean` |
| `r5ft-sticky-complete-clean-buildcheck` | *(none)* | `deploy/_promote/buildcheck-r5ft-sticky-clean` |
| `r5ft-sticky-complete-buildcheck` | *(none)* | `deploy/_promote/buildcheck-r5ft-sticky` |
| `pmus-r5ft-complete-retry-buildcheck` | *(none)* | `deploy/_promote/buildcheck-pmus-r5ft-retry` |

## Before / after estimate

On a production-branch push:

| | builds triggered |
|---|---|
| before | **4** (probe + 3 unintended), or up to **10** on a broad source change reaching `src/**` |
| after | **1**, and only if `DEPLOY_TRIGGER` itself was edited — otherwise **0** |

Against the measured fan-out, **13 of 30 builds (43%) would not have occurred**.
A `src/**` or `tests/**` commit, which previously rebuilt the full executor
image for `r5ft-recon-buildcheck`, now builds **nothing**.

This is an estimate from one measured window, not a billing reconciliation.
The dominant remaining cost is almost certainly **runtime**, not builds: the
project runs ~35 services, many of them dormant shadows still consuming
compute. That is a larger saving than build minutes and is **not** addressed
here, because it means stopping services and the instruction was to inventory
before deleting.

## Inventory for your decision — nothing deleted

Recommended for **deletion**, pending your call — all five are one-shot
diagnostics that have served their purpose:

`r5ft-recon-7b75689c-buildcheck`, `r5ft-clean-2ebb50f-buildcheck`,
`r5ft-sticky-complete-clean-buildcheck`, `r5ft-sticky-complete-buildcheck`,
`pmus-r5ft-complete-retry-buildcheck`

Recommended for **investigation**: `kalshi-arb-bot` has no volume, no
variables and no watch patterns. It looks like a legacy shell, but "looks like"
is not evidence and it shares its name with the repository, so it is scoped
rather than removed.

**Preserved untouched:** the executor, recorder, FIDELITY runner, and every
shadow/econ/collector service. None was stopped, deleted or reconfigured beyond
the seven watch-pattern changes listed above.
