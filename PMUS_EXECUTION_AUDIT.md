# Phase 1: execution-model audit and bounded live-probe plan

**No orders were submitted. Nothing was deployed. No risk limit was touched.**

Two of my earlier conclusions are corrected by this audit, both in the same
direction: the instrument we need already exists and I had been measuring the
wrong one.

---

## 0. Current state

**Branch:** `claude/august-l5-forensic-replay-788cml`, clean, at `83332abee`.

**Railway:** 59 services in `arb-ev-wallet-shadow`. Two matter here and both
were left exactly as found:

- **`l5-pmus-live`** (`87f1834e`) — **running, credentialed, and DISARMED.**
  Deployed 2026-10-09T02:12 from a *different* branch
  (`claude/gemini-btc-economic-viability-h3u7zl`). Heartbeat confirms
  `armed: false`, `owner_arm_token: false`, `live_orders: 0`,
  `realized_total: 0`, and `ORDER:NOT_ARMED` 2,526 times. Preflight PASSED;
  arming is a separate owner act that has not been performed. Account buying
  power $243.15, key fingerprint `d51b44a3fa83`. **I did not touch it.**
- **`pmus-btc15m-tape`** (`1f0c6aaf`) — the full-depth recorder behind
  `data/s3_cohort`.

Also present and untouched: `pmus-ws-evidence-recorder`,
`pmus-spring-l5-probe`, `pmus-q1-executor`, `l5-e1-safety-monitor`.

## 1. Audit of previous PMUS BTC15m live probes

**There have been none.** Every historical live order is sports:

| slug family | orders |
|---|---|
| `aec-` moneyline | 1,220 |
| `asc-` spread | 160 |
| `atc-` three-way | 36 |
| `astatc-` | 29 |
| **`cpc-` BTC15m** | **0** |

So there is **no live BTC15m execution evidence at all**, and the execution
model has never been validated on this market. That is the single most
important finding in this audit, and it is the gap Phase 2 exists to close.

What live evidence does exist (sports, 868 COMPLETE-stage orders):

| outcome | n | share |
|---|---|---|
| no_fill_cancelled | 581 | 66.9% |
| **filled** | **125** | **14.4%** |
| rejected | 162 | 18.7% |

Order IDs are venue-assigned (e.g. `CFSBABHC0TN1`), and inventory, book state
at decision, and fill quantity are recorded per order.

## 2. Which execution predictions were right, and which were wrong

**Right:**

- **Completion rate magnitude.** Replay predicted 14–40% completion; live
  sports delivered **14.4%**. The replay sat at the optimistic end but the
  order of magnitude held.
- **Distance from the touch is what matters.** Filled completes rested a
  median of **1 tick** from the best opposite ask; unfilled ones **2 ticks**
  (p75 7, max 48). Rest time did not separate them (7.71 s filled vs 6.99 s
  unfilled — that is the deadline, not a cause). Our placement work had
  already converged on distance-from-touch as the live variable.

**Wrong, and these are model defects rather than bad luck:**

1. **The replay models no rejections at all. 18.7% of live orders were
   rejected.** Of 91 rejections with an authenticated book, 21 were
   `CROSSED_AUTHENTICATED` — we submitted a price that crossed the book while
   believing we were resting — and 36 were unverifiable because the book was
   stale or unreadable. A replay that cannot reject is not a model of this
   venue.
2. **The live translation produces TAKER orders where the replay assumes
   resting makers.** On BTC15m right now, the engine's translation of L5's
   price returns `"mode":"TAKER"` and the safety overlay vetoes it:
   `SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` **2,514 times**. As configured, the
   live engine would place approximately **zero** maker orders on BTC15m. Our
   entire replay is about resting quotes. These are different strategies.
3. **Re-quoting on touch_moved is wired for OPEN and EXTEND but excluded for
   COMPLETE.** 36 OPEN and 35 EXTEND orders exited that way; **0 of 868
   COMPLETEs did.** In 176 of 475 unfilled completes L5's own touch moved past
   our resting price, and in 106 of those the opposite ask reached that new
   touch — an upper bound of 22.3% of unfilled completes had an
   L5-consistent reprice available that we never made.
4. **Queue position was never recorded.** `obs_executable_depth` is frequently
   `null` with status `STALE`, so the one quantity the replay's fill rule
   depends on was not captured at placement on live orders.

## 3. Recorder sufficiency, and the 404s — fully explained

**The 404s are benign and I can now say exactly why.** All 2,600 of them
(`error` 984 + `book_error` 1,616) occur **after** their window's close time:
`errs_before_close = 0` in every one of the 12 windows checked. The recorder
keeps polling a slug after expiry and the venue delists it. In-window coverage
is complete — 492–759 successful polls per 900 s window, ~2.4–3.0 s cadence,
~36 levels a side. **Fix is to stop polling at close**; nothing about live
coverage is impaired.

**The correction to my earlier claim.** I reported that our instrument sees
only a fraction of book events, based on the shadow's ~27 s REST poll. That
was the wrong instrument. `l5-pmus-live` already runs an **authenticated
websocket** with `stream_age_s` of **0.003–0.091 s** and **491–1,085 book
updates per window**. Sub-second book observation is not a gap we need to
close; it is already deployed. Every sampling-loss figure I gave applies to
the shadow's REST tape, not to the live stack.

## 4. Order lifecycle logging — what exists and what is missing

**Present:** venue order ID, our submit price and quantity, `filled_qty`,
terminal outcome, `end_reason`, session inventory before/after, full book
state at the decision, authenticated book age, transport delta, quote age,
and which gate refused.

**Missing, and each one is required before a fill/non-fill post-mortem is
possible:**

| gap | why it blocks the comparison |
|---|---|
| no venue acknowledgment timestamp | submit→ack latency uncomputable |
| no submit timestamp distinct from the log line | ack→fill latency uncomputable |
| no queue-ahead recorded at placement | the replay's fill rule cannot be scored |
| no cancellation confirmation from the venue | we cannot prove an order was off the book |
| no partial-fill sequence, only final `filled_qty` | partial fills invisible |
| no exchange `createdAt` on the order | our clock vs venue clock unreconciled |

**Phase 2 cannot start until these six are logged.** They are additive
logging on the existing order path, not a rebuild.

## 5. Venue constraints and current safety limits

**Confirmed from the live preflight and the captured spec, not from memory:**

- Order types: `limit`, `stop-limit`. TIF: `good-til-cancel`,
  `immediate-or-cancel`, `fill-or-kill`.
- **`makerOrCancel: true`** — a true post-only flag. "If the order would
  immediately take liquidity, the order is cancelled instead of filling."
  This is the single most useful feature for this probe.
- Tick size **0.01**; **quantity increment 0.01 contracts** (the live log
  shows 13 `QUANTITY_BELOW_MIN_INCREMENT` refusals, so this binds).
- `OrderStatus` is only `open | filled | cancelled` — **there is no
  `rejected` status and no `partially_filled` status.** Rejections arrive as
  HTTP errors at placement; partials must be derived from quantities.
- Fees: `src/pmus_fees.py`, effective-dated, taker coefficient **0.0695**
  since 2026-09-17 04:00 UTC (was 0.06), validated against all 99 published
  price rows. Maker rebate is a credit, never netted against edge. Taker
  rebate is 0 at this account's volume.
- Outcome mapping verified live: `contract_long_is_up`, `venue_yes_is_long`,
  wire labels YES/NO.

**Safety limits already live and unchanged by me:**

| limit | value |
|---|---|
| max USD per market | $5 |
| max total exposure | $5 |
| max absolute residual | 10 contracts |
| max concurrent markets | 1 |
| max live orders | 8 |
| daily realized loss halt | $5 |
| residual policy | `RESIDUAL_CAP_k2`, prospective-net |
| safety overlay | `MAKER_ONLY_NO_TAKER` active |
| arming | requires `PMUS_Q1_SESSION_ID` == `PMUS_Q1_ARM`; starts disarmed |

## 6. Preregistered execution test

**Hypothesis.** A maker-only order resting at the best bid fills more often
than one resting one or two ticks behind it, and all three fill rarely enough
that the replay's queue-clearance rule overstates fill probability.

**Preregistered predictions, written before any order exists** — the replay's
own numbers from `queue_position.py`, which this test will refute or confirm:

| placement | replay says queue cleared | replay implies fill | prediction |
|---|---|---|---|
| best bid | 73% | — | fills on some orders |
| 1 tick behind | 66% | — | fills less often |
| 2 ticks behind | 62% | — | fills rarely |

**The real prediction being tested is that all three fill far less often than
"queue cleared" suggests**, because queue clearance ignores FIFO position.
I commit in advance: if observed fill rate at the best bid is within a factor
of two of 73%, the replay's rule is vindicated; if it is below 10%, the rule is
refuted and every placement number I have produced is void.

**Design.**
- **Arms:** best bid (0 ticks), −1 tick, −2 ticks. One arm per window,
  rotated in a fixed preregistered order so no arm systematically gets
  particular market conditions.
- **Periods:** minutes 0–4, 5–9, 10–13 of the 15-minute window. **Minute 14 is
  excluded** — measured queue there is ~5,988 shares and it is a different
  market.
- **Order:** `limit`, `makerOrCancel: true`, `good-til-cancel`, quantity
  **0.01 contracts** (venue minimum), cancelled by us after a fixed 120 s rest
  or at minute 14, whichever first.
- **Recorded per order:** the six missing lifecycle fields above, plus the
  full authenticated-WS ladder at placement, queue-ahead at our price, and
  every book update while the order rests.
- **Sample:** 30 orders per arm, 90 total, before any analysis.

**Analysis is specified now and will not be changed after seeing data:**
primary endpoint is fill rate per arm with a Wilson interval; secondary are
time-to-fill, queue-ahead at placement for filled vs unfilled, and
rejection rate by class. **No threshold will be tuned on these observations
before the full order-by-order comparison against replay predictions is
written up.**

## 7. Exposure, budget, and failure behaviour

| control | proposed value | note |
|---|---|---|
| quantity per order | 0.01 contracts | venue minimum |
| **worst-case loss per order** | **$0.01** | a share can settle worthless; no leverage |
| max concurrent live orders | **1** | tighter than the existing limit of 8 |
| max orders for the whole probe | 90 | 30 per arm |
| **total loss budget** | **$1.00** | hard stop; well inside the existing $5 |
| max concurrent markets | 1 | unchanged |
| daily realized loss halt | $5 | unchanged, not reached by this design |
| order type | maker-only | cannot take liquidity by construction |
| cancellation | our 120 s deadline, plus existing deadman | cancel confirmation now logged |
| **residual handling** | **let a filled 0.01 contract settle** | max loss $0.01; chasing a complement would contaminate the fill measurement, which is the point of the probe |

The residual choice is deliberate: this probe measures fill behaviour, not
P&L. Hedging each fill would add a second order whose own fill behaviour
confounds the first.

**Abort conditions:** any rejection class we cannot explain; any order whose
cancellation is not confirmed; any fill larger than submitted; cumulative loss
reaching $1.00; the authenticated book stalling above 5 s.

## 8. What I need from you, and what I recommend first

**Recommendation: do not approve Phase 2 yet.** Approve the logging work
first. Running the probe before the six lifecycle fields in §4 are captured
would spend live orders and still leave us unable to explain a non-fill —
which is the stated definition of success.

Sequence I propose:
1. Add the six lifecycle fields to the existing order path. No new service, no
   strategy change, no arming.
2. Resolve the TAKER-translation finding in §2.2. The live engine currently
   wants to cross on BTC15m; a maker-only probe needs a translation that
   produces a resting price by design, not one vetoed by an overlay.
3. **Then** bring me the 90-order probe for arming approval.

Open question for you: the probe's 0.01-contract minimum means each arm risks
at most $0.30. I have set the total budget at $1.00. Say if you want it lower.

## Labels

No order was submitted in Phase 1. No credential value appears anywhere in
this report or in any artifact it references. `l5-pmus-live` remains disarmed
and was not modified. All sports-derived figures are live and realized; all
BTC15m figures remain replay or observation, never realized.
