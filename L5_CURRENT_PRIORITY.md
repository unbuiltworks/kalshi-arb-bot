# L5 CURRENT PRIORITY

This is the operational north star for L5 work. Read this together with `L5_STATUS.md` before starting material L5 work. `L5_STATUS.md` preserves evidence/history; this file states what we are doing now. Update this file only when the operational state materially changes. Never rewrite historical evidence to fit current priorities.

## Objective

Get the L5-adapted engine producing safe, executable, realized positive economics. Do not create new strategy workstreams unless current evidence requires one.

## P0 — Polymarket US Sports

Polymarket US sports is the shortest current path to a bounded live L5-adapted probe.

Current state:
- Continuous L5-style inventory engine: RUNNING, currently halted/disarmed for money movement.
- Authenticated sports collection: RUNNING.
- Shadow decisions: RUNNING.
- Money-moving execution path: EXISTS.
- Settlement/accounting: VENUE-CONFIRMED on prior real-money activity.
- Strict spring evidence: 3 SPRING_MATCHED completions on 1 distinct market, all at pair cost 0.9800.
- Required next independent evidence: a second distinct same-category market through the existing evidence pipeline.
- `spring_le_096_completions` is a legacy compatibility name and actually counts <=0.98 completions. It MUST NOT be interpreted as evidence of true <=0.96 completion. Report <=0.96 and <=0.98 separately.
- Resolve the apparent q1 order near ~$0.27 versus existing $0.05 cumulative-notional/market envelope inconsistency without changing either merely to make preflight pass.
- Do not arm, clear shutdown latches, or relax economics/category thresholds merely to obtain a trade.

Shortest path:

**collect continuously -> second independent same-category market -> exact deployed-artifact preflight -> explicit bounded live-probe decision**

## P1 — Gemini Crypto 5m/15m + Sports

Current state:
- Crypto 5m/15m collection: RUNNING.
- Continuous-inventory queue shadow: RUNNING; do not reduce Gemini evidence to old R/S/D pair experiments.
- Strict queue opportunities/completions exist in the queue-aware work; venue is not disqualified by failed R/S/D or cap5/8/13 arms.
- Gemini canary/order transport: EXISTS in repository work but is not the currently deployed execution path.
- Venue-confirmed settlement accounting: MISSING; theoretical/mid-proxy settlement is not realized P&L.
- Gemini sports collection: MISSING/not established in the current lane audit.

Next priority for Gemini: keep shadow collection running and establish venue-confirmed settlement/accounting before treating a live probe as capable of producing realized results. Do not tune a new strategy to compensate for this infrastructure gap.

## Reference Venue

**Polymarket.com is the L5 observation/reference venue, not an execution destination for this project.**

L5's public Polymarket.com crypto activity is used to infer sequential behavior and test fidelity. Absence of BTC/ETH 5m/15m instruments on Polymarket US is known and is not a blocker to the intended sports-transfer program.

## Frozen Evidence

- R/S/D prospective experiment: FROZEN / FAILED. Do not rescue or retune it without a materially new hypothesis.
- `ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0` cap5/cap8/cap13: FROZEN / FAIL. Run caps alone did not qualify prospectively; do not rerun cap3/cap4 or alter its preregistered thresholds after the result.
- Legacy losing PMUS executor/accounting state: HALTED. Do not restart or clear its latch merely to create activity.

These failures falsify specific implementations. They do not establish that L5-style transfer to sports or Gemini as a venue is impossible.

## Critical L5 Findings To Preserve

- 0.96 is a trajectory/economic destination in the historical evidence, not an established L5 entry threshold. L5 often forms two-sided inventory nearer ~$0.99 and improves economics through subsequent accumulation.
- The analytical OPEN/EXTEND/SWITCH/COMPLETE vocabulary is our ontology, not proven L5 internal logic.
- Historical matched edge and residual exposure are tightly coupled; prospective residual-cap work showed broad residual leakage. Do not hide directional residual risk behind pair-profit reporting.
- Incentives/rebates must remain separately accounted and attributable. Estimated or unattributable incentives cannot rescue a shadow gate.

## Anti-Drift Rule

Before starting work, ask:

**Does this directly move P0 Polymarket US sports toward its bounded live probe, move P1 Gemini toward executable/settleable validation, or materially improve fidelity to observed L5 behavior needed for those lanes?**

If no, it is probably not the priority.

Do not let Polymarket.com execution/access, generic arbitrage, legacy PMUS accounting, old Gemini experiments, infrastructure cleanup, or unrelated strategy ideas displace P0 unless new evidence changes the priority.

## Profit / Status Discipline

Use the status chain:

**historical replay -> shadow -> live probe -> submitted -> filled -> settled -> realized**

Never call shadow inventory, theoretical fills, modeled pair edge, mid-proxy settlement, estimated rebates/rewards, or mark-to-market gains realized profit.

For live economics account separately for:
1. matched-pair economics
2. directional residual P&L
3. spread capture
4. maker rebates
5. liquidity rewards
6. fees
7. settlement/redemption cashflows

## Standing Start/Finish Instruction

At the beginning of every material L5 task, read `L5_CURRENT_PRIORITY.md` and `L5_STATUS.md` and establish current GitHub/deployment state rather than assuming an old conversation reflects production.

At the end of material work, update `L5_CURRENT_PRIORITY.md` only if the operational state actually changed. Preserve source provenance and historical evidence.
