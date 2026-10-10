# L5 Program — Scoped Claude Code Instructions

These instructions apply to work under `analysis/l5/`. The root `CLAUDE.md` describes legacy Kalshi arbitrage; it is NOT the strategy brief for L5. Do not modify unrelated Kalshi, sports, or SOL executors.

## Mission and proof
Reverse-engineer Polymarket trader `l5zn1bwom8etsk` sequentially: at each observed market state predict the next side, price, quantity, timing, and inventory response. Adapt only where actual venue execution supports positive net expectancy. The $480/day net realized target is a measurement target, never an assumption. L5 is not reducible to a static YES+NO<$1 arb unless proven.

## Single pipeline
Public L5 activity -> provenance-preserved normalized history -> sequential action replay -> behavioral model -> continuous realtime shadow -> action-by-action comparison -> owner-authorized bounded live probe -> confirmed settlement cashflows. Preserve and consolidate existing code, collectors, reports, and branches; do not rebuild or launch a new strategy lane by default. Claude Code implements; ChatGPT/user direct research and stage advancement.

## Operating mode
The objective is a persistent AUTONOMOUS SYSTEM. Shadow autonomy is permitted when read-only: continuously ingest, decide, account inventory, compare to L5, and report. Live autonomy requires separate explicit owner authorization, independently effective kill switch / watchdog, exact-owned-ID cancellation and verification, startup reconciliation, durable exposure budgets, deadman, notifications, and venue-specific settlement accounting. Never infer live authorization from a request to 'get it running', a previous experiment, or an environment value in another lane. Never broaden one-window authorization into recurring trading.

## Stop repeating solved work
PMUS order submission, maker fills, and settlement have already been demonstrated. Do not propose another first-leg-only or tiny E1 capability probe as the strategic milestone. E1 is an execution diagnostic, NOT the continuous-inventory L5 strategy. Preserve its safety code disarmed unless explicitly authorized. Avoid new deployments, branches, services, or full historical reruns when existing artifacts answer the question. Every proposed deployment must identify the exact previously unmeasured failure mode, why existing data cannot answer it, the expected decision it enables, incremental cost, and rollback.

## Stage gates
1. Inventory the actual GitHub branches, current Railway services/commits, and existing results; do not trust old chat summaries as production truth.
2. Identify ONE highest-value divergence between L5's next action and the current engine using existing data. Compare side, price, size, timing, inventory, and outcome with provenance.
3. Evaluate executable net economics with conservative queue/fill assumptions, adverse selection, maker/taker fees, incentives only when confirmed, residual losses, settlement charges, and capacity. If evidence is negative or missing, label BLOCKED and state the missing mechanism; do not optimize around it.
4. Run continuous read-only shadow automatically on existing infrastructure; compare every action and measure false actions, missed actions, inventory and economic drift.
5. Advance to bounded live only on explicit owner approval of venue, strategy, size, exposure, daily loss, concurrency, monitoring, cancellation and duration. Start with a supervised bounded probe, then independently verified autonomous safety before unattended live operation.
6. Scale only after repeated filled, settled, cashflow-confirmed net-positive probes. Never claim shadow or modeled profit is realized.

## Every update: concise decision record
Report: (a) CURRENT RUNNING STATE (service, commit, mode, live orders), (b) NEW EVIDENCE since last update, (c) P&L STATUS (historical/shadow/submitted/filled/settled/realized), (d) ONE BOTTLENECK, (e) NEXT ACTION that directly changes a GO/BLOCKED decision, (f) COST/DEPLOYMENTS added, and (g) whether owner authorization is required. No 'one step away' claims. No redundant readiness loops. Keep a running completed-work ledger and do not reopen completed gates absent new contrary evidence.

## Safety and separation
Never touch unrelated services or credentials. Preserve raw data, ownership tape, and volumes. Do not use venue/geographic restriction evasion. Any ambiguous POST halts re-entry pending authoritative reconciliation. Cancel only exact owned IDs and verify terminal state. A cancelled order does not flatten filled residual inventory. Autonomous watchdog must be independent of the trading process and able to act or fail safe, not merely print logs. A chat session is not persistent supervision.
