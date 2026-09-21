# The q=1 bounded live manifest — BUILT, PROVEN, NOT ARMED

**Branch:** `claude/gemini-btc-economic-viability-h3u7zl`
**Generated from code:** `python3 -m src.gemini_execution.live_manifest_q1`
**Armed:** **NO.** `REQUIRED_AUTH_ID`, `AUTHORIZED_SHA` and `AUTHORIZED_ENVELOPE`
are all `None` in the shipped tree. Nothing in this commit can reach the venue's
mutation surface, and a test proves it from the unpatched module.

The research phase is closed. What follows is the engineering, each safety
requirement with the thing that enforces it and the test that drives it to its
limit and one step past.

---

## 0. The only outcome that counts

```
STARTING CASH  →  actual fills and fees  →  actual settlement  →  ENDING CASH
```

Rebates are a separate ledger and are never netted into that line. No
mark-to-market. No theoretical pair profit. No shadow P&L substituted for cash.

**What the first run can answer:** executability at q=1 (a 100× larger order sits
in the same queue but is far likelier to fill *partially*); whether the whole
cash chain reconciles to the cent; the realized sign of one complete cycle; and,
as a by-product of the position reads, **which position representation the venue
actually uses**.

**What it cannot answer:** expectancy. One window is n=1. A single settled cycle
is a fact about that cycle, and calling it an edge would be the same error as
calling a shadow fill money.

---

## 1. The envelope

Read from `live_envelope.LIVE_Q1`; the manifest generator never restates a number.

| bound | value | why this number |
|---|---|---|
| `action_size` | **1 contract** | the point of the test: a settlement cashflow that is a cent-visible fact, not a rounding artifact |
| `max_same_side_run` | 2 | admits **one** EXTEND after the OPEN. The modal L5 run is 2.9, so this reaches the behaviour without funding it; a cap of 1 would make EXTEND impossible and silently turn the frozen policy into a one-shot |
| `max_imbalance` / `max_residual` | 2 contracts | = `action_size × max_same_side_run` |
| `max_yes` / `max_no` | 2 contracts each | |
| `max_total_spend_usd` | $2.50 | **filled** cost |
| `max_committed_usd` | **$2.50** | filled + resting + the rung being sent — the real cash bound |
| `max_residual_usd` | $2.00 | the directional leg is the part L5's own history measures as negative, so it is bounded in dollars and not only in contracts |
| `max_working_orders` | 2 | |
| `max_concurrent_markets` | 1 | |
| `max_actions` | 12 | one `order.place` **attempt** or one `order.cancel` **attempt** each; KEEP is free |

**Worst-case cash at risk: $2.50.** The committed cap is the most cash that can
leave the account, and a contract that resolves against pays nothing back, so it
is also the most that can be lost.

`envelope_sha` = `4e2818d5…`. The driver's authorization hash covers **both**
sizes (`envelope_shadow_q001_sha` and `envelope_live_q1_sha`), because the choice
between them is one constructor argument and an authorization that named only one
would leave the other changeable without review.

**The live size is never a default.** `live_envelope.DEFAULT` is the 0.01 shadow
envelope. A driver gets q=1 only by being handed `LE.LIVE_Q1` explicitly, in a
reviewed commit. A bigger size is a decision, not an upgrade that arrives.

---

## 2. The sequence

| step | mutates | does | refuses if |
|---|---|---|---|
| 0 PREFLIGHT | no | terms accepted; balances readable → **record STARTING CASH**; positions readable **and flat**; active orders empty; ledger clean; authorization names this executable and this envelope | any read is unreadable, any position or order exists, or the authorization does not name the running code |
| 1 BIND | no | one BTC5m symbol, TTE ∈ [30, 300]; `WindowGuard` re-checks the symbol every turn; the process market lease refuses a second | a market is already bound in this process |
| 2 RUN | **yes** | FROZEN_L5_NATIVE decides side, price and timing; maker-or-cancel only; every frame a BUY; q=1; a slot is spent **before** the frame goes out; an already-correct resting rung is KEPT free | the live envelope refuses the rung, naming **every** bound it breaks |
| 3 RECONCILE | no | after **every** fill: read positions, `position_ledger.reconcile` against our ledger, record which representation the venue sent | verdict is `DISAGREE` or `UNREADABLE` → cancel everything and stop |
| 4 END OF WINDOW | yes | TTE < 60: no new OPEN/EXTEND. TTE < 30: cancel everything, quote nothing. No market state for 20 s: withdraw and stop | n/a — this step only removes exposure |
| 5 SETTLEMENT | no | settled positions with `withCashOuts=true`; `payout`, `costBasis`, `realizedPnl`, `netProfit` taken from the venue's own fields | the contract is not observed SETTLED |
| 6 CASH | no | balances → **ENDING CASH**; transfers → rebate credits, **separately**; `/v1/mytrades` → the venue's own `fee_amount` | a balance change with no attributable source is not attribution |
| 7 THE NUMBER | no | ENDING − STARTING, against the chain in §4 | the chain does not close → the gap is **reported**, never absorbed into a P&L line |

---

## 3. The four position reads — how the open question gets answered (P0 item 1)

The authoritative account read was never obtainable from this session: no Gemini
credentials exist in any environment reachable here, and one-shot service
creation was denied. **So the manifest answers it in the course of doing its
job**, rather than requiring a separate investigation.

| when | expect | what it answers |
|---|---|---|
| before any order | flat | nothing on its own — a flat ledger agrees with a flat venue under every model. It is the **control** |
| after the first fill (single-sided) | one row | the venue's row shape for a position we actually hold |
| **after the COMPLETE fill** | **the deciding read** | two rows for one symbol → **model A**; one signed row → **model B**; no row at all → **B or C** |
| after settlement | a settled row | whether both legs redeemed separately, and the venue's own `netProfit` |

`position_ledger.reconcile` records the representation it saw and labels the
implied model. It reconciles on the **signed** quantity, which is well defined
under every candidate representation; a ledger that reconciled on the *pair*
would be correct under at most one. `our_matched` is carried through explicitly
labelled **not confirmed state** so no caller can quietly treat it as Gemini
economics.

---

## 4. orderId → fill → position → settlement → cash (P0 item 8)

| link | key | source | note |
|---|---|---|---|
| order | `clientOrderId` (ours) + `orderId` (venue's) | the place ack's `result.orderId`, in the durable mutation ledger written **before** the frame | the two are not interchangeable: reconciliation **matches** on `clientOrderId`, cancel **takes** `orderId` |
| fill | `orderId` | `/v1/mytrades` — price, quantity, `fee_amount`, aggressor | `/v1/mytrades` carries **no outcome**, so the leg is joined from our own placement record, never parsed from our `clientOrderId` string |
| position | `instrumentSymbol` | `/v1/prediction-markets/positions` | reduced to a signed quantity and compared to ours |
| settlement | `instrumentSymbol` | `/v1/prediction-markets/positions/settled?withCashOuts=true` | venue fields; an omitted field is `null`, never `0` |
| cash | balance delta + transfers | `/v1/balances`, `/v1/transfers` | REALIZED is a cash movement **with a source**. Rebates enter nothing until credited |

---

## 5. Safety requirements, and the evidence each one works

All tests in `tests/test_gemini_live_envelope_q1.py` (28) plus the existing
`tests/test_gemini_ws_continuous.py` (68). **1,988 Gemini tests green.**

### PL-1 — the action budget actually binds (item 3)

**It was already closed on the order path, and my own earlier note was wrong
about where the hole was.** `shadow_soak` never calls `guard.record()` — true,
and that is why `MAX_ACTIONS` is inert *in the shadow collector*. On the live
path the accountant is `MutationLedger`: a slot is spent by the **attempt**,
written and fsynced *before* the frame goes out, so a send that times out has
already cost one. `test_the_action_budget_binds_on_the_live_path` drives 12
places, asserts the 13th raises, that only 12 frames were sent, that the ledger
reads 12 and that `guard.actions_taken` tracks it rather than sitting at zero.
`test_a_spent_slot_survives_a_restart` proves a restart cannot re-open it.

### PL-2 — total dollar exposure on the live order path (item 4)

**A second correction to this repository's own record.** `PRE_LIVE_CHECKLIST.md`
says `would_breach` does not check `MAX_TOTAL_SPEND`. That is **wrong**, and
`git log -L` shows the spend check in the commit that created the function. The
real gaps were different, and all four are now closed in `live_envelope`:

1. the gate measured the prospective fill with `q = ACTION_SIZE`, the **module
   constant**, not the qty of the rung being sent — gate and frame could
   disagree about size. `qty` is now a **required** keyword with no default;
2. `inv.spend()` is **filled** cost. Nothing bounded **resting** commitments —
   cash the venue may take at any moment without asking again. Each rung could
   pass its own check while the set of them exceeded the cap together;
3. residual was bounded in contracts, not dollars;
4. nothing bounded how many orders may rest at once.

`test_resting_orders_are_counted_as_committed_cash` rests two rungs at $1.00 and
shows the third refused at $3.00 > $2.50.
`test_the_old_check_would_have_let_that_through` gives the regression teeth: on
the identical state `CP.would_breach` returns `None` — it sees a flat inventory
and approves the very rung the live gate refuses.

A rung **larger** than the envelope size is refused (the 100×-by-typo failure); a
rung **smaller** is allowed, because that is the remainder of a partial fill and
refusing it would leave a real position half-worked with no way to finish.

### Maximum residual dollar exposure (item 6)

`max_residual_usd`, priced at the leg's **average cost** — a mark is not money.
`test_residual_is_bounded_in_dollars_not_only_contracts` builds a residual the
**contract** cap allows and the **dollar** cap refuses, which is the whole reason
it exists as a separate bound. A held leg with no cost basis is charged the
maximum, never nothing.

### One concurrent BTC5m market (item 7)

Three layers: the cohort-prefix check in the constructor, `WindowGuard`'s symbol
binding re-checked every turn, and a process-level market lease.
`test_a_second_market_cannot_be_bound_in_this_process` shows the second bind
refused with `state = DO_NOT_TRADE`. The lease is held by **weak reference**: a
driver that stopped, or was dropped, releases it, because otherwise a bound
meant to stop *concurrency* would stop the *second window ever*.
`test_a_stopped_driver_stays_stopped_after_releasing_its_lease` proves releasing
does not re-arm the object that released it.

**Cross-process exclusion is supplied by the deployment, not by this code:** one
service, one replica, `restartPolicyType: NEVER`. That is stated here rather than
implied.

### Deadman and cancellation (item 5)

TTE < 30 withdraws everything and quotes nothing; 20 s without market state stops
the window; cleanup has its own mutation budget so it can still cancel when
strategy slots are 0; a cancel with no venue `orderId` is **refused rather than
sent malformed** (that exact defect earned `-1013` on the first live cancel and
left the order resting); an ambiguous cancel ack is `SUBMISSION_UNKNOWN` and
stops for reconciliation rather than being read as failure.

### The ledger matches the venue's representation (item 2)

`position_ledger.reconcile`. `AGREE` only when the venue was **read** and its
signed position equals ours exactly; `UNREADABLE` and `DISAGREE` are both
refusals with `tradeable = False`. A row carrying neither a signed `position` nor
an outcome to sign its quantity is `UNREADABLE`, not zero.

---

---

## 5b. The restart state machine (the Grok blocker)

**The defect, as reported:** across `rest → partial fill → second rest → cancel
ambiguous → process dies → restart`, nothing durable said what might still be
resting. `WorkingOrders` knew, and `WorkingOrders` lives in memory. So $2.50 and
$2.00 were bounds *within one process*, not across a restart.

**The rule that generates the fix:** *only an authoritative venue read may
release committed cash.* Not an ack, not a confirmed cancel receipt, not our own
belief that an order filled. Gemini's own acks are the reason — measured
2026-09-18, every cancel in CONTINUOUS-4 returned 200 with an empty result and
**two of the four "cancelled" orders had already FILLED**.

| state | live? |
|---|---|
| `INTENT`, `ACCEPTED`, `PARTIAL`, `FILLED`, `SUBMISSION_UNKNOWN`, `CANCEL_REQUESTED`, `CANCEL_ACKED`, `CANCEL_UNKNOWN` | **potentially live** |
| `REJECTED` (the venue said it did not create it), `VENUE_ABSENT`, `VENUE_TERMINAL` | terminal |

`FILLED` commits no further cash but is still not *proven gone*, so it blocks a
new window until a read clears it — and that read is one the restart must do
anyway. The two questions are kept apart: **`blocks_restart()`** asks "is it
proven gone?", **`remaining_committed_usd()`** asks "how much cash could still
leave?". An order of unknown fate commits its **whole** original notional,
because unknown filled quantity means unknown remaining.

1. **process start** — the append-only ledger replays. A torn final row becomes
   an order of **unknown fate**, never a skipped one (the write that tore is
   exactly the one that happened as the process died).
2. **`recover()`, before anything else** — reconcile against the venue's
   authoritative active-order read. `None` or malformed → `UNREADABLE` →
   `DO_NOT_TRADE`, nothing released.
3. **reconcile** — ours still listed → `BLOCKED`, commitment refreshed from the
   venue's own `filledQuantity`. Ours not listed → `VENUE_ABSENT`, released. An
   active order we do **not** recognise → `BLOCKED`, **and its notional counts**.
4. **after a clean read only** — each released order's dangling mutation attempt
   is settled by client order id. **The slot is not freed**; consumed budget
   never re-opens.
5. **the rest of `recover()`** — inventory, working set, consumed budget; any one
   unreadable → `DO_NOT_TRADE`.
6. **`bind()`** — re-asserts nothing is potentially live, then takes the
   single-market lease.
7. **`place()`** — filled spend + in-process resting + **durable outstanding**
   (de-duplicated against the working set) + this rung, against
   `max_committed_usd`. An unreadable durable total is a refusal, not a zero.

**The exact sequence is a test.** `test_the_reported_sequence_refuses_order_C_after_a_restart`
drives A rests → A partially fills → B rests → A fills further → cancel A
ambiguous → cancel B ambiguous → **the `Driver` object is deleted** → a new one
is built from nothing but the files on disk → the venue reports A and B active →
order C is attempted. **C does not reach the venue**, and the assertion is on
`session.sent == []`, not on a state flag. `test_restart_with_an_unreadable_active_order_read_sends_nothing`
does the same with the read unreadable. `test_only_a_clean_authoritative_read_makes_a_window_eligible`
shows the window becoming eligible only once A and B are gone.

A partial fill is **both** at once: `test_a_partial_fill_is_inventory_AND_commitment_at_the_same_time`
shows 0.4 filled at 0.60 → $0.240 of inventory **and** 0.6 remaining → $0.36 of
commitment, both inside the envelope together.

### The dollar bounds, exhaustively

| bound | combinations | permitted | worst permitted | cap |
|---|---|---|---|---|
| committed cash | **13,500** | 5,610 | **$2.50** | $2.50 |
| directional residual | **720** | 594 | **$2.00** | $2.00 |

The committed assertion is **pure arithmetic on the scenario** — everything
already spent, plus every resting order filling, plus every durable outstanding
order filling, plus this rung filling — and does not reuse the envelope's
internals, so a gate that dropped a term fails here rather than agreeing with
itself. The residual bound rebuilds the post-fill book independently. Both tests
also assert the worst permitted value is **strictly above the next-lower round
number**, so neither passes by being over-tight.

### One defect found while wiring it, and fixed

Cancelling an order recovered from the venue writes a `CANCEL_REQUESTED` row for
a client order id that has no `INTENT` row — no size, no price. `committed_usd`
correctly called the whole total unreadable, and every later placement was
refused *for a reason that is not a risk reason at all*. `cancel_requested` now
carries the leg, price and qty the caller already holds, never overwriting a
field already on record. Pinned by
`test_cancelling_an_order_we_never_placed_stays_computable`.

---

## 6. What was deliberately not done

- **Not armed.** Installing an authorization is a human editing three constants
  in a reviewed commit, and the dual key's live half is not in this repository.
- `AUTHORIZED_ENVELOPE` was **retired to `None`, not re-pointed** at the new
  hash. Re-pointing it would be pre-authorizing an envelope nobody approved —
  the exact thing the constant exists to prevent.
- **`continuous_probe.py` was not touched.** It is in the watchPatterns of
  `gemini-l5-faithful` and `gemini-econ-shadow`; editing it would redeploy both
  and reset the frozen prospective cohort. The shadow envelope is byte-for-byte
  unchanged, which also keeps every shadow window comparable with every other.
- No new strategy, no alpha filter, no toxicity/flow/spread/pair-cost/rebate
  optimisation, no directional prediction. FROZEN_L5_NATIVE is untouched.

---

## 7. The binary decision, fixed in advance

**Continue** — repeat q=1, then consider scaling — only if the cash chain closes
to the cent, the position representation is resolved, and realized cash is
positive or negative by less than the fee/rebate structure explains **and**
repeatable.

**Terminate Gemini L5 work** if no fill is obtainable at q=1, the cash chain does
not close, the venue representation is incompatible with the process, or realized
cash is negative in a way the mechanism explains.

**Not a reason to continue:** a favourable markout, a pair basis below 1.00, a
shadow fill, an estimated rebate, a mark-to-market gain.
