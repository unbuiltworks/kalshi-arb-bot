# Next-stage state of record

Captured 2026-09-16T00:5xZ. Everything here is source-backed; nothing is inferred.

## 1. Repository

| | |
|---|---|
| branch | `claude/l5-execution-instrumentation-io6nzi` |
| HEAD at capture | `65f7806de6c8e8a01fb79286eb730af01f42fb41` |
| default/parent lineage | `feature/l5-transfer-backtest` (PMUS serving branch) |

## 2. Serving Railway deployments

| service | serviceId | deployment | artifact | status |
|---|---|---|---|---|
| `l5-exec-shadow` (R/S/D + ADAPTED reporters) | `5dced43a-b6b2-4ba9-8126-c9918072ac73` | `4d3deb26-1dff-4786-b413-d38ff667b488` | `3cb575cb` | SUCCESS |
| `pmus-spring-l5-probe` (**HALTED**) | `d84d9a68-2e5b-4f74-9694-5f422417c74f` | `c30724d7-d8b4-474c-9d08-09195d692a3e` | `6adcc4c4` | SUCCESS, `SHUTDOWN_VERIFIED` |

Project `50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, environment `f3642d27-26dc-4e21-9170-bc76290c076f`.
Promotion for `l5-exec-shadow` is gated on `deploy/_promote/l5-exec-shadow`; commits that do not touch it deploy as SKIPPED.

## 3. Durable state

| path | owner | contents |
|---|---|---|
| `/state/l5-research` | l5-exec-shadow | R/S/D + ADAPTED stores (volume-backed, survives restart) |
| `/state/l5-shadow` | l5-shadow | maker/two-sided/inventory outcome streams |
| `/state/pmus-spring` | **HALTED** PMUS probe | legacy executor state — **never shared** |
| `/state/l5-live-probe` | *(reserved, unused)* | next-stage probe root, deliberately distinct |
| `analysis/l5/cross_shadow_live/ledger.jsonl` | in-repo | 1,140 venue/outcome rows |

## 4. Experiment lineage

```
60dbe57b  gate frozen BEFORE any code or observation
e4c37a99  experiment starts (cap5 / cap8 / cap13)
25fb1e6d  COPY allowlist fix (reporter shipped without its modules)
a5525555  settlement wired (gate could not otherwise be cleared)
af1d0f57  20th window -> FAIL; decomposition added
1457a5d1  decomposition instrument corrected (3 defects)
84acb4d9  finalised result recorded in L5_STATUS.md
```

**ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 = FAIL**, finalised and unmodified.
FAILURE_MODE = BROAD_LEAKAGE. No arm passed A-2, A-3 or A-6.

## 5. Frozen / halted, unchanged

- **R/S/D** frozen — grid, arms, label and thresholds untouched since `01900cc1`.
- **Legacy PMUS executor HALTED** on the `PMUS_SPRING_SHUTDOWN` latch; latch not cleared, no orders, no restart.
- Ledger D **−$44.4898** across 34 venue-confirmed markets; 12 SETTLED_REALIZED_UNCONFIRMED held out of the net.
- PMUS budget note: `budget_permits_normal_operation` flipped False→True at the 2026-09-16 UTC rollover (`realized_today` reset −24.5858 → 0). Nothing recovered; the shutdown latch is still what holds the probe.

## 6. What is prepared and what is not

Prepared: preflight, incentive join, accounting chain, reconciliation, venue envelope, promotion/rollback.
**Not chosen:** the exposure envelope. `src/l5_live_probe/envelope.py` raises `EnvelopeNotChosen` until an owner sets all five bounds, and the preflight reports every one as a blocker until then.
**Not built:** any money-moving executor. There is no order verb in `src/l5_live_probe/`.
