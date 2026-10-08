# COHORT E1 — LIVE RUNNER AUTHORIZATION MANIFEST

> # ⛔ NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE IS SET.
>
> **No live order has been placed. No order is authorized. Nothing is armed.**
> No credential, signing key or session id is set by any code in this package,
> and nothing here can set one. No Railway service was created, deployed or
> modified. `analysis/l5/stage6/` (S6P), `analysis/l5/live_exec5/` (XR2W) and
> its fingerprint, the running `l5-book-shadow` collector, and A6 (**FILLED /
> settlement unresolved / NOT REALIZED**) were **read only and never
> written**. `analysis/l5/live_exec2/` was **read and not modified**. No
> Kalshi path was touched.
>
> **This artifact exists to be authorized or refused by the owner. It cannot
> authorize itself and does not claim to be authorized.**
>
> `LABEL: FROZEN, DISARMED ARTIFACT — NOT HISTORICAL REPLAY, NOT SHADOW, NOT
> SUBMITTED, NOT FILLED, NOT SETTLED, NOT REALIZED.`

**Frozen spec fingerprint**

```
78ad6ff97258c05553b7b3d3b62e6a3eb1c3b468fd15313ce104e8a633fb554f
```

Probe `COHORT-E1-LIVE`. Derived from LIVE-L5-EXEC-1-XR2W
(`ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562`) by
deep-copying its SPEC plus a literal DIFF whose values are **also**
deep-copied, so the aliasing defect documented in
`analysis/l5/audit/FROZEN_SPEC_MUTATION_AUDIT.md` is **not inherited**. The
fingerprint above is a **hard-coded literal** compared against a
recomputation, so `assert_frozen()` cannot be vacuous; it is asserted at
import and at every boot, and a perturbation test drives every invariant
path one at a time and requires each to raise.

**No parameter below was chosen, tuned or re-derived by this lane.** Each was
handed down frozen, and each names its source.

---

## 1. Exact quantity and order

| | |
|---|---|
| **first-leg quantity** | **0.13 shares**, every attempt, no exceptions |
| source | `E1_COHORT_DESIGN.md` §1.4 — settlement cash is cent-rounded with banker's rounding, so `0.13 × $0.04 = $0.0052 → $0.01` while `0.12 × $0.04 = $0.0048 → $0.00`. 0.13 is the smallest grid quantity at which the settlement gain is observable at all |
| **max first-leg price** | **$0.60** — inherited from `analysis/l5/stage6/bounds.py`, **not re-chosen** |
| **outstanding orders** | **ONE at a time**, one market at a time |
| **order type** | maker only, post-only (`participateDontInitiate`), tick 0.01, offset 0 at the same-side best bid |
| **sequence** | maker first leg → reassess → complement sized to the ACTUAL fill → settle/audit |
| **forbidden** | **NO TAKER RESCUE. NO CHASING. NO RE-QUOTE.** The re-quote is refused at the latch, not merely by policy |
| **maker rebate at this size** | **$0.00, structurally.** `0.0125 × 0.13 × p(1−p) ≤ $0.000406`, which cent-rounds to $0.00 at **every** price. That is correct and deliberate. Rebates are E2. **Nothing is sized for rebates** |
| serializer | `src/l5_order_body.py`, the proven one. **Not duplicated**, asserted by test |
| order mutation path | `analysis/l5/live_exec2/orders.py`, unmodified |

## 2. Max attempts per window

**20.** An attempt is one first-leg placement. Bounded by a durable
**bounded-count NAMED-SLOT latch** (`O_CREAT|O_EXCL` per named slot,
session-scoped, consumed **before** each POST, **never released on
failure**) — **not a counter**, because a counter is read-then-write and two
processes can race two orders into one slot. Twenty named files; exactly one
caller wins each. Attempt 21 is refused by the latch **and** independently by
the exposure tracker. A complement consumes a slot named off its own attempt,
so it can never take an attempt another first leg needed.

## 3. Max exposure per window

**$1.56** = `20 × 0.13 × $0.60`. Arithmetic, not a judgement. Strictly below
the owner's **$5.00** per-window hard limit. Checked **before** every attempt
by projecting that attempt's notional, so the bound is never discovered by
breaching it. **This remains an INDEPENDENT limit** — see item 4.

## 4. Max cohort exposure and loss

### 4.1 The forward-looking $5.00 invariant

Evaluated at **every decision and every submission**:

```
    realized_cohort_loss
  + maximum remaining loss on ALL currently unresolved residual inventory
  + maximum loss of THE ATTEMPT ABOUT TO BE SUBMITTED
  <= $5.00
```

* an unresolved residual's maximum remaining loss is **`filled_qty ×
  fill_price`** — the worst case, which is that it settles worthless;
* a residual is **unresolved until its settlement cashflow is CONFIRMED**;
* the prospective attempt's cap is **`0.13 × $0.60 = $0.078`**, and it is
  counted **BEFORE** submitting. **$5.00 is therefore a true ceiling, not a
  ceiling plus one attempt.** If the sum would exceed $5.00, the new
  first-leg submission is **REFUSED**.

**May reduce the realized component:** only a **settled win whose
settlement/cashflow is CONFIRMED**.

**May NEVER reduce the bound** — each an explicit, recorded refusal, not a
convention, each with its own test asserting the bound is byte-identical
before and after:

1. mark-to-market value;
2. expected or projected settlement;
3. an unfilled or resting complement;
4. pending, accrued or estimated rebates.

### 4.2 Both bounds bind simultaneously

`MAX_EXPOSURE_PER_WINDOW = $1.56` remains an **independent** hard limit.
Neither substitutes for the other: the window bound caps **how fast**, the
forward-looking bound caps **how much**. A tracker with ample forward-looking
headroom is still stopped by the window bound, and vice versa — asserted by
test in both directions.

### 4.3 Hard automatic stops

* forward-looking committed loss (realized + unresolved residuals) ≥ $5.00;
* any window exposure ≥ $1.56;
* any safety-invariant violation;
* **venue error storm — 5 consecutive venue errors, FROZEN for this
  cohort.** Strictly a **SAFETY** stop and **never** an economic threshold:
  it says the venue is not answering us coherently, not that the strategy is
  bad. The **exact** errors are recorded in `venue_error_log`. **No
  automatic retry anywhere**, because a retry could duplicate an ambiguous
  submission. Classified `CONTINUE_INSUFFICIENT_EVIDENCE` with
  `truncation_cause: VENUE_ERROR_STORM`, never `FAIL`;
* **ambiguous submission — NEVER retried.** The venue state is reconciled
  instead.

## 5. Number of windows

**12.** Maximum **240** placements (`12 × 20`). Windows are indexed 0..11 and
the latch refuses any window outside that range.

## 6. Exact eligibility and complement rules

### 6.1 Eligibility — verbatim, all evaluated together before any network call

> **E1** the cohort must be ARMED by the owner's gate for this exact market
> slug, and the frozen fingerprint must be pinned to THIS build. Neither is
> set by any code in this package.
>
> **E2** the cohort verdict must be CONTINUE: not a FAIL, not a positive
> crossing, not stopped.
>
> **E3** no order of ours may be outstanding, and no residual position of
> ours may be open, in any market.
>
> **E4** the window must be open, within the frozen handover TTE, and the
> attempt count for this window must be below 20.
>
> **E5** the forward-looking loss bound must be unbreached: realized +
> unresolved residual worst case + this attempt ≤ $5.00.
>
> **E6** projected window exposure after this attempt must remain strictly
> below $1.56.
>
> **E7** the live two-token websocket must be streaming BOTH tokens with a
> strict-timing book fresher than the frozen 2.0 s bound. REST is
> reachability only and may NEVER order the tape.
>
> **E8** the book must be two-sided and executable on both tokens.
>
> **E9** the first-leg price must be at the same-side best bid (offset 0), on
> the 0.01 tick, at most $0.60, and strictly below the live ask so a
> post-only order cannot take.
>
> **E10** the COMPLEMENT must be feasible: the cheapest complement price that
> satisfies the fee-inclusive pair-cost ceiling must be at or above $0.01 and
> on the tick. An attempt whose complement could not be priced at all is
> refused BEFORE the first leg, never discovered afterwards.
>
> **E11** a bounded-count named latch slot for this window must be claimable.
> The slot is consumed BEFORE the POST and is never released on failure.
>
> **E12** no safety-invariant violation may be recorded.

### 6.2 Complement — verbatim

> **C1** the complement is placed only after the first leg reports a fill,
> and is sized to the ACTUAL filled quantity, never to the placed quantity.
>
> **C2** the complement is the opposite token of the same market.
>
> **C3** the complement price is the highest price on the 0.01 tick that
> keeps the fee-inclusive combined pair cost at or below $0.96 per share, and
> that is also at or below the same-side best bid so the order rests as a
> maker.
>
> **C4** if no such price exists, NO complement is placed and the position is
> recorded as RESIDUAL. The pair is refused, not improved by relaxing the
> ceiling.
>
> **C5** maker only. NO TAKER RESCUE, at any price, at any TTE.
>
> **C6** ONE complement submission per first-leg fill. NO CHASING and NO
> RE-QUOTE: a complement that does not fill is cancelled at handover and the
> residual is recorded, not re-priced.
>
> **C7** the complement quantity tracks the **CUMULATIVE** first-leg filled
> quantity, not merely the first partial. A 0.13 first leg that fills 0.05
> and LATER 0.08 must have the **ENTIRE 0.13** accounted for. The shortfall
> is submitted as an ADDITIONAL TRANCHE at the attempt's ORIGINAL complement
> price.
>
> **C9** a tranche is NOT chasing and NOT a re-quote. It replaces nothing,
> cancels nothing and moves no price; it is triggered by a FILL, never by a
> non-fill, and the two are therefore distinguishable by their trigger. An
> unsuccessful complement is still never replaced at a more aggressive
> price: it is cancelled at handover and its quantity recorded as residual.
>
> **C10** any quantity that cannot LEGALLY or ECONOMICALLY receive a
> complement — a shortfall below the venue minimum 0.01, or one whose
> complement price is infeasible under the frozen fee-inclusive $0.96
> ceiling — is EXPLICITLY RECORDED AS RESIDUAL with its reason. Never hidden
> and never implicit.
>
> **C8** the complement is subject to the same latch, the same
> preview-and-echo requirement and the same deadman.

### 6.3 The minimal cumulative mechanism, and why it is not chasing

The complement price is computed **once per attempt**, from the attempt's
ORIGINAL first-leg price against the frozen `<= $0.96` fee-inclusive ceiling.
It therefore **cannot move**, because its inputs cannot. A tranche is a NEW
order for NEW inventory at that SAME price:

| | re-quote | tranche |
|---|---|---|
| trigger | a **non**-fill | a **fill** |
| what it does to the earlier order | replaces it | nothing — it stays |
| price | more aggressive | identical, by construction |

This mechanism has **no path a non-fill can take**, so it cannot chase.
Tranches are bounded at **13** = `0.13 / 0.01`, the number of 0.01
increments a 0.13 first leg can arrive in — from the venue grid, not a
judgement. Each tranche is its own **named latch slot**, so re-submitting the
*same* tranche raises `LatchSpent`: the no-re-quote rule is enforced at the
latch, not only in the policy.

`account_first_leg()` closes the arithmetic: every filled share is
**COMPLEMENTED or RESIDUAL**, never neither and never silently.

**Pair cost** is fee-inclusive against `src/pmus_fee_schedule.py`
(`ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`, taker
θ 0.0695 effective 2026-09-17). The modelled fee is a **gate input, labelled
MODELLED, and never recorded as an actual fee**. **No rebate is credited
against the gate**, per CLAUDE.md.

## 7. PASS / FAIL / CONTINUE boundaries

### 7.1 There is no PASS. Three outcomes, and only three.

Formal E1 PASS requires **385 placements AND 431 fills**
(`E1_COHORT_DESIGN.md` §2–3). This cohort authorizes **240** placements,
which cannot reach either gate at any fill rate. **The owner accepted that
rather than widening the cohort.** So no PASS outcome exists and no code path
can produce one — asserted by a test that sweeps tracker states and requires
every verdict to be one of exactly three:

| # | outcome | trigger |
|---|---|---|
| 1 | **`FAIL`** | a preregistered failure boundary is reached |
| 2 | **`COMPLETION_SPRT_POSITIVE_CROSSING`** | the completion SPRT walk crosses H1 |
| 3 | **`CONTINUE_INSUFFICIENT_EVIDENCE`** | anything else, including every truncation |

**Outcome 2 carries these four statements as FIELDS on the result object**,
not as prose, so a summary cannot drop them:

* `evidence_specifically_for`: *the maker→maker completion question, and
  nothing else*;
* `is_formal_E1_PASS`: **false**;
* `is_proof_of_positive_expectancy`: **false**;
* `is_authorization_for_E2`: **false**.

### 7.2 FAIL boundaries — read from `E1_COHORT_DESIGN.md` §3, none invented

| boundary | numeric | what it means |
|---|---|---|
| estimand 1 upper bound on `p_fill` below 0.10 | **35 consecutive no-fill placements** (`Beta(1,36)` 97.5% upper = 0.0947) | a passive first leg does not fill at our price under this selector |
| estimand-2 SPRT crosses H0 **AND** per-share upper bound below **−$0.006231** | log-LR ≤ **−2.944439**; outright at **8** consecutive non-completions | completion is at or below the proxy level and the loss exceeds the arithmetic ceiling on any attainable maker rebate |
| any fill timestamped **after** a cancel acknowledgement | **n = 1** | cancellation does not bound exposure; invalidates the design, not just the run |
| any cashflow without sufficient source identity | **n = 1** | instrument fault; nothing may be labelled REALIZED |

### 7.3 CONTINUE_INSUFFICIENT_EVIDENCE always names WHICH limit stopped it

`truncation_cause` is one of `RISK_LIMIT_FORWARD_LOOKING_5_USD`,
`RISK_LIMIT_WINDOW_EXPOSURE_1_56_USD`, `LOW_FILL_RATE_UNDECIDABLE`,
`AUTHORIZED_PLACEMENT_CAP_240`, `DESIGN_HARD_TRUNCATION_4310`,
`SAFETY_INVARIANT_VIOLATION`, `VENUE_ERROR_STORM`,
`AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE`, or `null`.

**If the $5.00 risk limit truncates the experiment the outcome is exactly
`CONTINUE_INSUFFICIENT_EVIDENCE`**, flagged
`truncated_by_the_risk_limit: true` and **distinguished in the record** from a
low-fill-rate truncation, which carries `truncated_by_a_low_fill_rate: true`.
A test asserts the two causes differ.

### 7.4 The honest arithmetic, stated before authorization

240 placements yield ~85 fills only if `p_fill ≥ ~0.35`. At `p_fill = 0.20`
they yield **48**, **short of the SPRT's 81 expected fills under H0**. The
instrument therefore **terminates CONTINUE explicitly** —
`truncation_cause: LOW_FILL_RATE_UNDECIDABLE`, emitted at the point it
becomes arithmetically certain, not discovered at the end.

**Extending the cohort requires a FRESH owner authorization and a new
manifest.** Placements, windows, quantity and the $5.00 ceiling are **not**
increased to make any statistical boundary reachable, and **no parameter moves
on interim results** — asserted by a test that re-checks every frozen
parameter after every one of 240 simulated placements.

## 8. Absolute worst-case dollars

```
UNRESTRICTED:  12 windows x 20 attempts x 0.13 shares x $0.60 = $18.72
```

**$18.72 IS NOW UNREACHABLE.** The forward-looking invariant is evaluated
before every submission and counts the attempt about to be made, so reaching
$18.72 would require passing through $5.00, and the attempt that would cross
it is refused.

```
EFFECTIVE COHORT WORST CASE:                                      $5.00

  realized_loss
+ SUM over unresolved residuals of (filled_qty x fill_price)
+ (1 attempt x 0.13 shares x $0.60 = $0.078)
<= $5.00                      checked before EVERY submission

INDEPENDENT PER-WINDOW WORST CASE:   20 x 0.13 x $0.60 =          $1.56
PER-ATTEMPT WORST CASE:                   0.13 x $0.60 =          $0.078
```

Both bind simultaneously; neither substitutes for the other. A test drives
the cohort as hard as the gates allow and asserts the committed worst case
never passes $5.00, and that `$5.00 < $18.72`.

---

## 9. What is recorded, per eligible attempt

Append-only JSONL, fsynced, **provenance on every row** (schema version,
spec fingerprint, fee-schedule fingerprint, venue, timing authority, write
time, pid, deployment). Four deliberately separate ledgers:
`attempts.jsonl`, `events.jsonl`, `venue_money.jsonl`, `settlement.jsonl`.

Per attempt: the exact live two-token websocket state at decision time;
first-leg side, price, quantity; queue/touch state (size resting **at** and
**ahead of** our price); submitted / acknowledged / resting timestamps;
first-leg fill / **PARTIAL** / no-fill with `filled_qty`; complement price
and submission timing; complement fill / partial / no-fill; **COMPLETE vs
RESIDUAL**; time to first fill and to completion; realized pair cost;
residual size and duration; every cancellation and whether a fill raced it;
adverse-selection markouts from the websocket tape; **actual venue fees and
rebates, never modelled**; settlement and realized cashflow when available.

**Labelling is enforced in code, not by convention.** `venue_money.jsonl`
**refuses** a row whose source is not a venue report, and refuses any
cashflow without sufficient source identity. An attempt row **cannot** be
labelled `REALIZED` — the only path to that label is a settlement row with
both settlement **and** confirmed cash from an identified source.

### 9.1 The requirement that matters most

Whatever the classification, the raw tape must be sufficient to recompute,
afterwards and independently:

```
first-leg maker fill rate
  -> conditional maker->maker completion rate
    -> residual rate / loss / duration
      -> pair cost
        -> actual cashflows
```

`recorder.reconstruct_chain()` computes exactly that from the ledger **files
alone** — no tracker, no in-memory state, one argument. A test records a
fixture covering full fill + complete, partial fill + complete, full fill +
residual, partial fill + residual and two no-fills, then reconstructs the
whole chain from a **fresh** reader on the same directory and checks every
step's arithmetic. A second test asserts the chain still reconstructs from a
**risk-truncated** cohort. A third asserts that an incomplete tape reports
`chain_is_computable: false` with what is missing, **rather than guessing**.

**An inconclusive run with a complete tape is worth far more than a crossing
with a lossy one.**

---

## 10. Timing authority

`src/l5_clob_ws.py`, the proven two-token websocket, live-verified
(`both_tokens_streaming: true`, 11,438 and 11,433 frames, endpoint
`wss://ws-subscriptions-clob.polymarket.com/ws/market`). Rows carry
`timing_authority = websocket_strict`; REST stays
`rest_same_second_reachability` and **may not order the tape** — the recorder
**refuses** an attempt row that says otherwise, and refuses one recorded
without both tokens streaming.

## 10.1 AUTHORIZATION RECORD — dedicated namespaces

**E1 reads `PMUS_E1_*` ONLY. It does not read `PMUS_EXEC1_*` at any point,
for any purpose.** The service `l5-live-probe-oneshot` already carries
`PMUS_EXEC1_LIVE` / `_SESSION_ID` / `_ARM` / `_GATE_EVIDENCE` /
`_AUTHORIZED_FINGERPRINT` for XR2W, so sharing the namespace would let a
legacy value participate in arming E1. Two experiments, two namespaces.
**XR2W is completely unchanged** — `analysis/l5/e1_cohort/arming.py` is
additive and touches nothing of XR2W's.

| E1 key | role |
|---|---|
| `PMUS_E1_LIVE` | must be `YES` |
| `PMUS_E1_SESSION_ID` | scopes the latch; a fresh cohort needs a fresh id |
| `PMUS_E1_ARM` | must equal the session id |
| `PMUS_E1_GATE_EVIDENCE` | must equal the authorized **series** `btc-updown-15m`; the exact resolved slug is re-enforced before every submission |
| `PMUS_E1_AUTHORIZED_FINGERPRINT` | must equal `78ad6ff97258c05553b7b3d3b62e6a3eb1c3b468fd15313ce104e8a633fb554f` |

**None of these is set.** Reporting is BOOLEAN ONLY — `armed_env_summary()`
returns presence flags and never a value, asserted by test.

### Latch / state namespace

E1 uses the existing `/state/pmus-ws` mounted volume, in its **own**
directory, with every file further prefixed `e1.`:

```
E1    /state/pmus-ws/e1_cohort/e1.<sid>.<digest>.wNN.sNN.<role>.tNN.submitted
XR2W  /state/pmus-ws/exec1x/<sid>.<digest>.submitted      <- A6. EVIDENCE.
```

**BOOT ASSERTION:** `latch:assert_no_collision_with_xr2w()` runs on every
boot and enumerates **960** slot paths (12 windows × 20 slots × 2 roles × 2
tranche bounds), proving none resolves into XR2W's directory and every one
carries the `e1.` prefix. It refuses a colliding directory, including a
subdirectory of XR2W's — asserted by a test that makes it fire.

**XR2W's spent A6 latch is EVIDENCE and is never touched.** Nothing in
E1's latch module contains `os.remove`, `os.unlink`, `shutil.rmtree`,
`os.rmdir`, `os.rename` or `truncate` — asserted by source scan — and a
test holds XR2W's latch bytes **and mtime** constant across a full E1 slot
lifecycle.

## 10.2 NETWORK CAPABILITY — stated plainly

**E1 IS NETWORK-CAPABLE. This is necessary and correct.** Its job is to
place real orders, and XR2W's proven `orders.py` uses `urllib`, so
importing the production entrypoint pulls `http`, `http.client`, `socket`,
`ssl`, `urllib`, `urllib.error`, `urllib.parse`, `urllib.request` and
`urllib.response` — verified, 9 modules.

**Nothing in this artifact claims E1 cannot reach the network.** A false
safety claim is worse than an acknowledged capability, and this build is
about to receive credentials. The two provable claims are:

1. **the offline tests make ZERO network CALLS** — `urlopen` trapped, 0
   calls across the suite; every transport is a fake;
2. **the real transport is reachable ONLY through the preview-echo gate and
   the E1 named-slot latch** — asserted structurally: in `_submit_once`,
   `require_echo` precedes the latch, which precedes the POST, and a
   refused echo means no POST at all.

## 10.3 THE LIVE PRODUCTION PATH

`e11fc1ab…` declared a production path it did not have. The armed branch is
now wired end to end, and a test drives **this exact function** armed:

```
Railway entrypoint   scripts.l5_e1_cohort_run:__main__
 -> runner:main  [ARMED BRANCH]
      arming:fingerprint_pinned      PMUS_E1_AUTHORIZED_FINGERPRINT
      authorization:series_authorized PMUS_E1_GATE_EVIDENCE
      progress:CohortProgress.rehydrate      <- RESTART RESUMES
      authorization:SessionAuthorization
 -> provider:LiveWindowProvider
      selector:select_window -> src.l5_market_binding:fetch_binding
      authorization:authorize_selected       <- BOUNDARY 1 (series)
      provider:assert_ws_both_tokens_healthy
      live_book -> src.l5_clob_ws            <- TIMING AUTHORITY
 -> runner:eligible
 -> runner:run_cohort                        <- MANY attempts
 -> authorization:enforce_before_submission  <- BOUNDARY 2 (exact slug
                                                 + both tokens, before
                                                 any transport)
 -> latch:consume_attempt                    <- E1 NAMED-SLOT LATCH
 -> orders:BoundedOrderTransport.request     <- PROVEN XR2W PRIMITIVES
```

**Reused, not rebuilt:** `src/l5_market_binding.py` for BTC15m market and
token resolution (it already refuses unidentified windows), and
`src/l5_clob_ws.py` for the proven two-token websocket. **No second
discovery stack and no second book stack** — asserted by test.

### Authorization semantics, fixed rather than weakened

A single static `PMUS_E1_GATE_EVIDENCE=<slug>` cannot authorize twelve
windows. **The slug check is NOT deleted** — deleting it would make this
spec state a rule the code does not enforce, the defect class that
superseded three fingerprints. Instead there are now **two** boundaries,
both narrower than the original:

1. **SESSION** — `PMUS_E1_GATE_EVIDENCE` names the authorized **series**
   (`btc-updown-15m`). Only markets the **frozen BTC15m selector** produced
   during this authorized session may be traded; the session ledger is
   append-only and a slug is never removed.
2. **PER SUBMISSION** — the **specific resolved slug and BOTH token ids**
   are enforced immediately before **every** first-leg submission, before
   any transport exists.

A **complement** may only belong to its own first leg's already-authorized
market; `authorize_complement` refuses any other, so the complement path
has no route to a market of its own.

**Per window the raw tape records:** selected slug, market id, both token
ids, selection provenance, and the authorization result.

### Durable progress

State lives on `/state/pmus-ws/e1_cohort`, append-only, with **no reset
path** (no `remove`/`unlink`/`rmtree`/`rmdir`/`rename`/`truncate`/`"w"` in
the module — asserted). On boot the cohort **rehydrates** windows used,
placements, window exposure, realized loss and unresolved residuals, so
**a restart resumes and never resets**. Twelve restarts do not grant twelve
cohorts — asserted by test. XR2W's spent A6 latch at
`/state/pmus-ws/exec1x` stays untouched; it is evidence.

### The live book, against REAL frames

The proven probe captured **11,523** frames on `btc-updown-15m-1791372600`:
**2** book snapshots and **11,438** `price_change` events. A `price_change`
has **no top-level `asset_id`, no `bids` and no `asks`** — the payload is
nested in `price_changes`, one entry per token, each carrying `best_bid`
and `best_ask` directly. Both shapes are now handled first-class, with the
bests read **directly from the entry**, and the frame's top-level `market`
(the condition id) **verified against the pin** so a stray subscription
cannot update the cache.

**Size is never invented.** A snapshot's ladder gives real queue depth at
the best bid. A `price_change`'s `size` is at the *changed* price — 0.01
and 0.99 in the captured frame, while the bests were 0.67 and 0.32 — so it
is used **only** when the changed price *is* the best bid on the BUY side,
and otherwise `size_at_our_price` is `None` with its reason recorded.

**Staleness is per-token book update, not silence.** A token whose top of
book has not moved within the freshness bound reads unhealthy **even while
frames pour in for the other token** — asserted by a test that snapshots
both, then runs 60 s of `price_change` for one token only and requires the
other to be refused.

### Fails closed

Stale-window rollover; websocket loss before submission; a silent token;
REST offered as timing authority; a derived complement offered as a second
token; book for another market; slug or token mismatch; selector refusal;
window-budget exhaustion. Each yields **no snapshot at all**, never a
degraded one.

## 11. Preconditions the owner must still supply

1. **authorization, in the owner's own words**;
2. `PMUS_E1_SESSION_ID` — scopes the latch; a fresh cohort needs a fresh
   id, and **Claude never clears a latch**;
3. the four `PMUS_E1_*` arming keys, naming the exact market slug;
4. `PMUS_E1_AUTHORIZED_FINGERPRINT` =
   `78ad6ff97258c05553b7b3d3b62e6a3eb1c3b468fd15313ce104e8a633fb554f`;
5. reconciliation of `bounds.py`'s `MAX_FIRST_LEGS = 1` and
   `MAX_DAILY_REALIZED_LOSS_USD = $0.60` with a 240-placement cohort — those
   are written for a **single** probe and this is many
   (`E1_COHORT_DESIGN.md` §5.14; `bounds.py` is **not** modified here);
6. re-binding of Stage-6's three module sha256s, which that document already
   requires independently;
7. a ruling on `E1_COHORT_DESIGN.md` §1.4 — whether end-to-end settlement
   confirmation of the *matched* leg is in E1's scope, since that is what
   makes 0.13 rather than 0.02 the binding quantity.

**Until the owner supplies these, this is a frozen disarmed artifact and
nothing may be armed.**

## 12. XR2W parity and supersession

`analysis/l5/live_exec5/` is **byte-identical and unmodified**, verified
against the owner's independently recorded sha256 prefixes:

| file | sha256[:16] |
|---|---|
| `analysis/l5/live_exec5/spec.py` | `dda1e2c50be81209` |
| `analysis/l5/live_exec2/orders.py` | `800078a852f14d84` |
| `analysis/l5/live_exec2/latch.py` | `add23515a5a8a201` |
| `src/l5_order_body.py` | `50b9ff1376abea03` |

Reused **by reference**, never copied: the body (`l5_order_body.build` via
`orders.order_body`), the preview envelope, `l5_preview_gate.require_echo`,
`BoundedOrderTransport.request` with its four permitted routes, and the
`VenueHTTPError` / `PreviewRejected` / `SubmissionAmbiguous` taxonomy.

**The latch is the ONLY behavioural difference** — E1's bounded-count named
slots in place of XR2W's single slot — and parity tests prove it over a
single submission: identical request body (including the NO-side 1−X
mapping), identical preview envelope, identical echo accept/reject,
identical route allow/refuse set, identical route sequence
(`PREVIEW → SUBMIT`), identical exception types and boundary flags.

### SUPERSEDED — MUST NOT BE ARMED

Both are recorded in-spec under `supersedes_e1.must_not_be_rearmed` with
their reasons, asserted by test.

| fingerprint | why it is superseded |
|---|---|
| `110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c` | the complement sized to only the first partial, so later first-leg fills fell silently into residual |
| `bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a` | **`entrypoint` still named XR2W's single-order path**, so arming per the spec would have run XR2W's one-order probe through the single-slot latch and measured nothing |
| `f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4` | **it consumed XR2W's `PMUS_EXEC1_*` namespace and XR2W's latch directory**, so a legacy value on `l5-live-probe-oneshot` could have participated in arming E1 — and it implied a network-incapability E1 does not have |
| `e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310` | **its ARMED path terminated BEFORE `run_cohort`** — `main()`'s armed branch was a bare `raise SystemExit`, no `snapshot_provider` existed, and setting the `PMUS_E1_*` keys would have produced twelve empty windows |
| `cdc635190765732e478169e172d2a638f6fed31468379fbf3e3fdc4e3930ae60` | **`live_book` parsed a frame shape the venue does not send** — it dropped 11,438 of 11,523 real frames (99.3%), so the top of book populated once and never moved, and health reported HEALTHY while serving that stale book. **It failed OPEN** |
| `481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486` | **it ordered POLYMARKET.COM identity to POLYMARKET US**: the market was discovered on `gamma-api.polymarket.com`, pinned as two CTF token ids and streamed over Polymarket.com's public CLOB websocket, then serialized into an order to `api.polymarket.us`, which answered **Market not found** five times. It also treated an **absent first-leg bid as `0.00`** and therefore as a cheap maker opportunity |
| `9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920` | **its websocket book parse let a NESTED OBJECT replace the authoritative top of book** -- a nested `bids`/`offers` key inherited the market slug, became a second book event, and the fold applied it LAST. Reproduced on the venue's real frame shape: `bids[0]` 0.9900 became `stats.bids[0]` 0.4900. It also shipped **without the `websockets` dependency**, so the decision feed could never connect at all |
| `9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5` | **its AuthBook staleness bound was 5.0 s, LOOSER than the 2.0 s bound of XR2W Attempt 6** -- the only order path that has produced an evidenced fill -- and there was no evidence for 5.0. A6 read AuthBook two-sided at age 0.084 s, priced DOWN at the own-side touch 0.6700 = 1 − 0.3300, sent wire `price.value` 0.3300, and the venue filled it (`ORDER_STATE_NEW` 02:00:01Z → `ORDER_STATE_FILLED` 02:00:15.69Z, `cumQuantity` 0.01) |

## 12.1 The entrypoint, and the defect it closes

`SPEC['entrypoint']` on `bfcff9ca…` was **inherited verbatim from XR2W**. It
named `scripts.l5_exec1xr2w_run` with a call graph through
`live_exec3.runner:run_experiment` and XR2W's **single-slot** latch, and
there was no E1 entrypoint in `scripts/` at all. The deep-copy fixed the
aliasing and then faithfully copied a value that **needed to change** — the
same class of bug as the old XR2W fingerprint-pin defect.

**95 tests here and 731 across the lineage passed while that was true**,
because every test started from the E1 runner's own functions and never from
the declared entrypoint. That gap is what the new regression tests close.

```
DECLARED ENTRYPOINT      scripts.l5_e1_cohort_run:__main__
  -> analysis.l5.e1_cohort.runner:main
  -> analysis.l5.e1_cohort.runner:boot -> spec:assert_frozen
  -> E1 COHORT RUNNER    analysis.l5.e1_cohort.runner:run_cohort  (MANY)
       -> :e1_preflight -> :eligible
       -> :submit_first_leg -> :_submit_once
            PROVEN XR2W PRIMITIVES
              orders:order_body       -> src.l5_order_body:build
              orders:preview_request  -> src.l5_order_body:preview_envelope
              src.l5_preview_gate:require_echo
  -> E1 NAMED-SLOT LATCH analysis.l5.e1_cohort.latch:consume_attempt
  -> PROVEN XR2W TRANSPORT
       orders:BoundedOrderTransport.request -> POST /v1/orders
  -> :submit_complement -> latch:consume_complement  (named TRANCHE slot)
```

**MUST NOT RESOLVE TO** — a negative assertion in the spec and in test:
`scripts.l5_exec1xr2w_run`,
`analysis.l5.live_exec3.runner:run_experiment`,
`analysis.l5.live_exec2.latch:consume`,
`analysis.l5.live_exec2.orders:submit_one`.

Enforced three ways: the declared call graph contains none of them; the
entrypoint, runner and latch sources (docstrings and comments stripped)
import neither `live_exec3` nor XR2W's latch; and a test monkeypatches
XR2W's `latch.consume`, `live_exec3.runner.run_experiment` and
`orders.submit_one` to **raise**, then drives the cohort to completion.

**The entrypoint is exercised, not merely resolved.** A test resolves
`SPEC['entrypoint']` the way an operator would, drives it through multiple
placements against a fake transport, and proves **order #2 reached the
transport** in a **second, distinct named latch slot** — the check a
one-order runner fails while passing import resolution.

## 13. Provenance

| artifact | path |
|---|---|
| frozen spec `5e1413ca…1fe0` | `analysis/l5/e1_cohort/spec.py` |
| bounded-count named-slot latch | `analysis/l5/e1_cohort/latch.py` |
| append-only recorder + chain reconstructor | `analysis/l5/e1_cohort/recorder.py` |
| stopping rules + forward-looking bound | `analysis/l5/e1_cohort/stopping.py` |
| runner (disarmed) | `analysis/l5/e1_cohort/runner.py` |
| offline tests, no network | `analysis/l5/e1_cohort/test_e1_cohort.py` |
| the design this implements | `analysis/l5/measurement/E1_COHORT_DESIGN.md` |
| preregistered boundaries, reused unchanged | `analysis/l5/measurement/cohort_design.py` |
| THE serializer, reused not duplicated | `src/l5_order_body.py` |
| the only order mutation path, unmodified | `analysis/l5/live_exec2/orders.py` |
| parent spec `ac6318d5…2562`, read-only | `analysis/l5/live_exec5/spec.py` |
| frozen bounds, read-only, unmodified | `analysis/l5/stage6/bounds.py` |
| fee schedule `ecc240bd…1629` | `src/pmus_fee_schedule.py` |
| timing authority | `src/l5_clob_ws.py` |
| the aliasing bug this does NOT inherit | `analysis/l5/audit/FROZEN_SPEC_MUTATION_AUDIT.md` |
| behavioural source of truth | `L5_OPERATING_MODEL.md`, GitHub issue #167 |
