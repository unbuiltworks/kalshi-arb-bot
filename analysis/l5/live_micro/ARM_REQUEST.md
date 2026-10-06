# LIVE-L5-MICRO-1 — arming request. **STATE: DISARMED**

Built, tested, **not armed**. No order submitted. No wallet signing. No
capital moved. Separate from frozen S3 (`8b20a259…87df`, verified untouched
and still collecting) and from the residual-efficiency study.

**41 safety and execution-path tests pass** (`tests/test_live_micro.py`).

---

## The nine items you required before any order can be submitted

### (1) Exact market / category
- **Venue:** Polymarket US (PMUS)
- **Category:** crypto — BTC up/down
- **Series:** `btc-updown-15m`
- **Concurrency:** **one market at a time**
- The specific slug is **not chosen by me**. It is named at arm time by your
  `PMUS_MICRO_GATE_EVIDENCE` key, is slug-scoped, and never auto-selected or
  auto-advanced. A different slug than the one you gate is **refused**
  (tested).

### (2) Minimum permitted order quantity — **UNKNOWN until arm time, by design**
- Source: the venue's own `minimumTradeQty`, read live per market.
- Requirement: the metadata must **explicitly advertise `minimumTradeQty < 1`**;
  absent or ≥1 **fails closed as a SKIP**, never a guess.
- Quantity is then whatever that minimum and a **~$0.01 target notional**
  imply. It is not a chosen size.
- Tick: **0.005 required**; any other live tick **fails closed**.
- **I cannot pre-fill this number.** `gateway.polymarket.us` is denied by
  this container's network policy, so the live minimum is unreadable from
  here. Stating a figure would be invention. It is read and enforced at
  arm time, and the run refuses if it is not advertised.

### (3) Maximum dollars losable in **one market** — **$0.05**
- Durable **cumulative first-leg notional cap: $0.05 per market**, read from
  the campaign ledger **across invocations** (not per-run).
- Per-leg cap **$0.02**; max gross pair capital **~$0.04**.
- Worst case is total loss of deployed capital, and deployment is hard-capped
  at $0.05. **Maximum loss in one market: $0.05.**

### (4) Maximum directional shares and dollars
- **Max residual shares:** equal to the **actual first-fill quantity** —
  never a parameter, always the realised fill.
- **Max residual dollars: $0.02.**
- Any residual above the actual first-fill quantity is reported as a
  **CAP BREACH**, never rounded or truncated (tested).
- **No martingale. No averaging-up to recover a loss.** Both asserted in the
  frozen spec.

### (5) Maximum simultaneous resting orders — **1**
- Maker-only, `participateDontInitiate` (post-only). **Taker not permitted.**
- **Genuine partial fills permitted**, and a partial is **never promoted to a
  full fill** by state-string matching (tested), nor is a partial complement
  ever treated as matched.

### (6) Cancellation / deadman behaviour
- **Market-scoped cancel-all in a `finally` block on every exit path**,
  including crash and exception paths.
- **POST mutations are never retried.**
- **No auto-restart. No auto-scaling.** One invocation is one market.

### (7) Daily realized-loss limit — **−$1.00**
- Checked **before every mutation**.
- "Realized" here means **FILLED + SETTLED + CASH_CONFIRMED only** —
  consistent with the ledger's definition, not mark-to-market.

### (8) Frozen strategy fingerprint
```
69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118
```
`analysis/l5/live_micro/spec.py`. Hard-coded literal, so `assert_frozen()`
cannot be vacuous; verified to catch a tampered risk parameter.

### (9) Exact owner authorization required to arm
**Four** environment keys, all yours to set — **I must not and did not set
any of them**:

| key | value |
| --- | --- |
| `PMUS_MICRO_LIVE` | `YES` |
| `PMUS_MICRO_SESSION_ID` | `<session id>` |
| `PMUS_MICRO_ARM` | `<the same session id>` |
| `PMUS_MICRO_GATE_EVIDENCE` | `<exact market slug>` |

Dual-key and **isolated**: arming q1/v4 can never arm micro, and arming
micro never arms q1/v4 (tested both directions). Missing or mismatched keys
leave the probe **DISARMED**, which is the normal state.

---

## Placement / repricing — frozen, contemporaneous only

| rule | value | provenance |
| --- | --- | --- |
| offset from touch | **0 ticks** | measured median offset at every inferred level rank |
| reprice on displacement | **0.06 in price** | L5 fill-offset p99 = 6 ticks on the 0.01 grid; **held in price, not ticks**, since the PMUS crypto tick is half |
| reprice on staleness | **69 s** | L5's maximum observed absence from a token |
| fixed order lifetime | **none** | a quiet book at the touch never reprices |
| leave-behind persistence | **not assumed** | persistence was CONSISTENT_WITH_REPOSTING |
| TTE shutdown | **120 s** | withdrawal-associated TTE median 102 s vs 485 s continuation |

A reprice is a cancel/replace and **is never a fill**.

## Ledger — the seven states, enforced in code

```
SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
          → CANCELLED → SETTLED → CASH_CONFIRMED
```
The state machine **only advances** (backwards transitions raise), CANCELLED
is reachable from any pre-settlement state, `FILLED` is refused while
`filled_qty < intended`, and `CASH_CONFIRMED` is refused before `SETTLED`.

Recorded per order: venue order id, **actual maker/taker classification from
the venue** (an assumed value raises), full lifecycle history, queue
information where observable, fees, and **actually credited** liquidity
rewards on their own line — a reward without a venue source raises, and
estimated rewards are not counted.

**Absolutely no touch=fill.** A fill requires a venue-reported fill on our
own order id.

### Language discipline, enforced not just stated
- **SUBMITTED is not a fill** — a submitted order reports `filled_qty = 0`.
- **A fill is not profit** — a filled, unsettled order reports
  `n_orders_REALIZED = 0`.
- **Mark-to-market is not realized** — every markout block carries
  `LABEL: MARK_TO_MARKET_NOT_REALIZED`.
- **Only FILLED + SETTLED + CASH_CONFIRMED is REALIZED** (tested through all
  three stages).

## Markouts

Real markouts after **every** fill at **+1 / +5 / +10 / +30 / +60 s** and at
**settlement**, from an observed bid. A horizon with no observation is
`None` with a reason and is **never carried forward** from a nearer horizon —
that would understate adverse selection.

## Accounting — five lines, never summed into a headline

`matched_pair_economics` · `directional_residual_pnl` · `fees` ·
`credited_rewards` · `final_net`

`directional_residual_pnl` reads `UNRESOLVED_UNTIL_SETTLEMENT` and
`final_net` reads `UNRESOLVED_UNTIL_CASH_CONFIRMED` until they genuinely are.

## What this probe measures — the economics finding enters here, not the rules

The audit found L5's covered sample had **positive matched-pair economics
(+$22,568)** and a **larger negative directional residual (−$25,314)** — a
**residual drag ratio of 1.1217**, i.e. the residual destroyed 112 % of the
matched gains.

So the probe's **primary question** is:

> Can our residual controls preserve matched acquisition **without**
> reproducing L5's residual losses?

The ledger computes our own `RESIDUAL_DRAG_RATIO` and reports it beside
`L5_REFERENCE_DRAG_RATIO = 1.1217`. **This is a measurement target, not a
trading rule** — no placement, sizing or withdrawal value was changed to
chase it.

---

## Honest limits on what this probe can tell you

- **It cannot answer the profitability question.** At a $0.05 per-market
  cap, any P&L is dominated by discreteness and fees. It measures
  **execution mechanics** — do we get maker fills, where in the queue, at
  what markouts, with what residual drag — not expectancy.
- **PMUS reward economics remain UNKNOWN** for our account (authenticated
  endpoints unreachable; signing forbidden). Credited rewards are recorded
  if and when they appear, and are never assumed.
- **The venue minimum quantity is unverified from here**, per item (2).

## State

**DISARMED.** I will not arm this. To proceed, authorize **this exact
fingerprint** (`69904fd4…9118`) and the risk envelope above, and set the four
keys yourself.
