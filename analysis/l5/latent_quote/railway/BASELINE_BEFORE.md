# Railway baseline recorded BEFORE any change — latent-quote shadow deployment

Captured 2026-10-02 from `describe-environment`, project `arb-ev-wallet-shadow`
(`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`), environment `production`
(`f3642d27-26dc-4e21-9170-bc76290c076f`).

| | |
|---|---|
| services | **54**, every one `state: live` |
| volumes | **21** |
| buckets | 0 |
| shared variables | 0 |
| **staged changes, environment-wide** | **`staged: null`, every service `stagedChangeCount: 0`** |

## Services that must not be touched, and their exact state at baseline

| service | id | branch | volume | latest deployment |
|---|---|---|---|---|
| `pmus-ws-evidence-recorder` | `71b6ea99` | `feature/l5-transfer-backtest` | `pmus-ws-evidence-recorder-volume` → `/state/pmus-ws` 50 GB | `ed464f2d` SUCCESS 2026-10-02T17:12:51Z |
| `btc-ref-collector` | `6cf313d3` | `claude/l5-pmus-economic-results-7c116e` | none | `a6b10767` SUCCESS 2026-10-02T12:56:16Z |
| `pmus-btc15m-tape` | `1f0c6aaf` | `claude/gemini-btc-economic-viability-h3u7zl` | none | `fb6de07c` SUCCESS 2026-09-27T22:45:31Z |
| `l5-pmus-live` (executor) | `87f1834e` | `claude/gemini-btc-economic-viability-h3u7zl` | `l5-pmus-live-state` → `/state/l5-live` | **null** |
| `pmus-q1-executor` | `fbd8eab5` | `feature/l5-transfer-backtest` | none | `0d425dd2` SUCCESS 2026-09-06T15:32:37Z |
| `l5-r1-probe` (executor, HALTED) | `6111a5d4` | `claude/l5-execution-instrumentation-io6nzi` | `l5-r1-probe-state` | `490ce8ac` SUCCESS 2026-09-17T12:47:15Z |
| `pmus-spring-l5-probe` | `d84d9a68` | `feature/l5-transfer-backtest` | `pmus-spring-l5-probe-volume` | `c30724d7` SUCCESS 2026-09-15T12:46:08Z |

Gemini services at baseline, all left untouched: `gemini-l5-shadow`,
`gemini-l5-queue-shadow`, `gemini-econ-shadow`, `gemini-5m-collector`,
`gemini-fair-value-shadow`, `gemini-maker-shadow`, `gemini-l5-faithful`, with
volumes `gemini-l5-shadow-state`, `gemini-econ-state`, `gemini-5m-tape`,
`gemini-btc15m-state`, `gemini-l5-faithful-state`.

## Volume capacity, which governs the storage decision

21 volumes exist: twenty at 50,000 MB and `l5-research-state` at 256,000 MB —
about **1.26 TB provisioned**, each mounted on exactly one service.

**No existing volume is deleted, moved, resized, shared or reassigned.** A new
isolated volume is attempted for the shadow service; if provisioning is refused
for capacity or plan reasons the fallback is **append-only stdout log
persistence**, the pattern `pmus-btc15m-tape` and `btc-ref-collector` already
use with no volume at all.
