# ARB Release and Live-Canary Process

This document is the default release discipline for ARB strategies that can place real orders. It exists so ChatGPT, Claude Code, and human review use the same evidence hierarchy and do not confuse a green test, a modeled opportunity, or a partial fill with realized arbitrage profit.

## 1. Repository is the source of truth

Substantive strategy code, diagnostics, tests, recovery logic, watchdog logic, launchers, and qualification evidence must be committed before AWS executes them.

Do not leave important diagnostic/business logic only in:

- a chat message;
- an ad-hoc shell heredoc;
- a manually edited EC2 worktree;
- an untracked file;
- a transient Docker container.

AWS should fetch and assert an exact committed SHA.

## 2. Establish current live state first

Before changing a live-capable workstream:

1. identify the exact repository/branch/SHA currently in use;
2. verify the deployment/runtime host directly;
3. audit open orders, positions, durable local state, and account balance;
4. verify which live/recovery/watchdog arms are enabled;
5. distinguish active production from shadow, diagnostics, old releases, and unrelated services.

Do not assume an older chat accurately describes current production.

## 3. Separate control and release lineages

Use the control branch for AWS orchestration, read-only workflows, documentation, and trigger-gated launchers.

Build live strategy changes on a release lineage cut from the exact previous frozen release that supplies the inherited safety behavior.

Never casually merge divergent release/control histories merely to make Git ancestry look simple.

## 4. Candidate qualification sequence

A live-capable candidate should progress through these gates in order.

### Gate A — deterministic tests

Run the inherited frozen safety suite plus new regressions for every changed invariant.

Tests should include failure injection, not only happy paths. Examples:

- acknowledged order cannot be resolved;
- websocket fill contradicts REST reconciliation;
- stale book after mutation;
- cancel result ambiguous;
- residual exceeds selected quantity;
- recovery starts with a pending rescue/completion order;
- dead-man observes account state inconsistent with durable local state.

A synthetic test suite proves code behavior under modeled conditions. It does **not** prove venue behavior or live profitability.

### Gate B — credential-free public-market qualification

Run the exact candidate SHA against current public venue data with live flags off and no credentials.

The diagnostic should answer, explicitly:

- did an eligible market exist?;
- what instrument metadata/grid applied?;
- was market data fresh?;
- what exact quantity/quotes would be selected?;
- what after-fee trade edge exists without speculative incentives?;
- what is the worst first-fill rescue loss?;
- after simulating our resting orders, does rescue remain safe after removing our own displayed liquidity?;
- if there is no plan, what exact gate rejected it?

A clean economic no-op is not an infrastructure failure. A diagnostic/harness failure is not evidence that no opportunity existed.

### Gate C — authenticated disarmed readiness

Build the exact same candidate SHA on the production host and keep all live/recovery/watchdog arms off.

Require:

- exact-image safety tests green;
- clean/no-open durable state;
- zero open account orders;
- zero account positions unless intentionally explained by a prior settled-state workflow;
- sufficient available balance for the bounded canary;
- dead-man idle;
- sidecar waiting;
- order-entry capability disabled.

No order mutation is permitted in this gate.

### Gate D — freeze

Only after Gates A–C support the candidate:

1. create a dedicated final/frozen release branch at the exact candidate SHA;
2. do not move that branch;
3. pin launchers to both the frozen branch and full SHA;
4. record qualification evidence in a dated repository document.

Any later code change creates a new candidate and repeats qualification.

## 5. Live launcher is inert until fresh approval

A live workflow may exist before approval, but it must be incapable of starting from an ordinary push or generic workflow dispatch.

Preferred pattern:

- workflow path trigger watches one dedicated approval file;
- approval file does not exist until fresh authorization;
- workflow asserts the exact frozen SHA;
- approval ID is unique and one-use;
- approval-specific state path must not already exist;
- classification and authorization strings are exact.

Do not reuse an authorization from another release, quantity, strategy, or prior canary.

Authorization to research, code, test, diagnose, freeze, or prepare a launcher is **not** authorization to trade.

## 6. Canary bounds

The first live run of a new frozen release should be intentionally bounded.

At minimum:

- one campaign only;
- continuous/restart trading off;
- explicit maximum quantity/exposure;
- explicit rescue-loss, campaign-loss, and daily-loss limits;
- independent dead-man/watchdog;
- bounded executor lifetime;
- no automatic second campaign;
- no automatic size expansion.

The strategy may select a smaller adaptive quantity when the live market cannot support the maximum safely.

## 7. Mutation/reconciliation rules

Once an order has crossed the venue mutation boundary, uncertainty must fail closed.

Examples:

- HTTP/websocket acknowledgement but order row cannot be resolved → do not assume zero fill;
- FOK acknowledged but result unknown → do not submit another full-size rescue;
- websocket indicates fill but REST reconcile reports zero → treat as ambiguous/open, not clean exit;
- pending maker-completion/rescue state after crash → block layering another automatic rescue until exposure is reconstructed;
- displayed depth that includes our own resting orders → subtract owned quantity before claiming external rescue liquidity.

Durable state should record the exact selected quantity, order IDs, pending rescue/completion IDs, heartbeat, and relevant fill information before/after mutation boundaries.

## 8. Dead-man discipline

The dead-man must not race legitimate executor work.

Its stale threshold must exceed normal bounded API/reconciliation gaps. It should verify both durable residual state and current account-level orders/positions before declaring a campaign safely terminal.

If exposure is ambiguous, recovery should prefer blocking/alerting over submitting a second potentially duplicative flatten.

## 9. Harness/output discipline

GitHub/SSM orchestration must not determine success by searching for a final marker inside an arbitrarily long stdout stream.

Prefer:

- remote shell `set -Eeuo pipefail`;
- compact final reconciliation output;
- full executor/sidecar logs stored on the host or as artifacts;
- log tails only on failure;
- SSM/GitHub timeout budgets longer than the true worst-case bounded executor + watchdog + settlement-reconcile path.

A harness false-red must be classified separately from a strategy/execution failure.

## 10. Evidence and P&L classifications

Use these states precisely:

- **test** — deterministic/synthetic validation only;
- **shadow/read-only** — real data, no mutation;
- **candidate/executable model** — current quotes appear to support a trade, but no order has been submitted;
- **submitted** — venue accepted or may have accepted an order;
- **filled** — fill confirmed from venue/account evidence;
- **matched/pending settlement** — directional exposure is paired but settlement not final;
- **settled/realized** — venue/account confirms final realized dollars.

Never count as realized profit:

- stale quotes;
- settlement-rule mismatches;
- shadow/model P&L;
- expected rebates/rewards not actually credited;
- unconfirmed fills;
- matched positions that have not settled;
- successful safety tests.

## 11. Post-canary evidence

Every live canary must end with account-level reconciliation and one explicit classification:

1. no candidate / no order;
2. order(s) submitted, zero fill;
3. first-leg fill + maker completion;
4. first-leg fill + bounded rescue;
5. matched pair pending settlement;
6. settled realized P&L;
7. ambiguity/hard halt requiring reconciliation.

Record actual order IDs, quantities, prices, fills, completion/rescue path, final orders/positions, and realized P&L without exposing credentials.

## 12. Scaling decision

Do not scale merely because one modeled opportunity was large or one canary was safe.

Scale only from accumulated live evidence showing:

- reliable candidate acquisition;
- acceptable fill rate;
- rescue/completion behavior matches the design;
- no unexplained reconciliation ambiguity;
- realized after-fee P&L is positive;
- opportunity frequency and capital turnover support the dollars/day objective.

The ARB objective is realized dollars/day, not maximizing test pass counts or modeled edge.
