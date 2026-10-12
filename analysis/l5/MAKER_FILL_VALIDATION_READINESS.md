# One-order live maker-fill validation — readiness, with tested evidence

**HOLD FOR OWNER APPROVAL. No order submitted, executor disarmed, `L5_SHADOW_ONLY=1`,
no capital limit changed, no live gate bypassed.**

## Verdict

| prerequisite | before | now |
|---|---|---|
| 1900z unknown cost | unresolved, blocking | **RESOLVED from the venue's own order row** |
| exact-order cancellation | cancelled by the wrong id | **fixed, 15 new tests + 2 rewritten** |
| independent watchdog | code existed, nothing asserted it | **built and tested (20 tests); needs one volume to deploy** |
| reconciliation | satisfied | satisfied, unchanged |
| automatic halt | pre-trade refusals only | refusals **plus** a durable latch, pending the deploy |

**One blocker remains, and it is infrastructure, not code:** the watchdog needs a
volume to hold the latch and a service to run on. Everything else is done and under
test.

## 1. 1900z — RESOLVED, from the order record, not the balance

The GET-only `pmus-readonly-recovery` image (`post_blocked true`,
`ReadOnlyTransport`, no executor, no ledger, no order body) walked
`/v1/portfolio/activities` and then read the order. Run 21:19:14Z–21:23:24Z.

**Order `D294PDYNPZ7Z`:**

```
marketSlug  cpc-btc-updown-15m-2026-10-12-1900z
ORDER_SIDE_BUY  OUTCOME_SIDE_YES  ORDER_INTENT_BUY_LONG  ORDER_TYPE_LIMIT
limit_price 0.18   quantity 1   cumQuantity 1   leavesQuantity 0
state ORDER_STATE_FILLED        avgPx 0.1800    commission 0.0000
makerCommissionsBasisPoints 0
createTime        2026-10-12T19:10:17.232593835Z
lastTransactTime  2026-10-12T19:10:17.713864582Z   (rested 0.481s)
manualOrderIndicator MANUAL_ORDER_INDICATOR_MANUAL
trade D29DZRQ3RZ6Y   aggressor false  ->  PASSIVE
```

**POSITION_RESOLUTION, independently:** `beforePosition` netPosition 1, cost 0.1800,
avgPx 0.1800; `afterPosition` netPosition 0, qtySold 1, **realized −0.1800** at
19:15:15.201Z. The venue's own realized line agrees with a cost of $0.18 against a
zero settlement.

So: **cost basis $0.18, settled worthless, realized −$0.18, fees $0.00.** Booked
through `reconstruct_cost_basis`, which records its source, and left for the ordinary
settlement path to post — the same code that posts every other loss. Account realized
P&L therefore goes from **+$0.0400 to −$0.1400** once it settles. That is a real loss
being recognised, not a discrepancy being tidied away.

**The balance move was not used.** 243.15505 → 242.97505 is −0.18 and is *consistent*
with the answer, but it is account-wide and proves nothing; the cost comes from
`avgPx` on the order row. The repair entry records that explicitly under
`not_used_as_evidence`.

**Completeness flags preserved.** The first walk (21:12:17Z) was `pages 10,
ended max_pages, complete FALSE, ownership_probe_capped TRUE` with 60 of 1,620 ids
probed, and I reported it as proving nothing — correctly, since the second run found
the order that the first had simply never asked about. The second run is still
`complete: false` (`ended max_pages`, 600 of 615 ids probed): it is **positive
evidence of a specific order**, which a lower bound can carry, and it is still not
evidence of absence about anything.

**The registry keeps the case.** `UNRESOLVED_ACCOUNTING` retains the 1900z row with
its original `cost_basis: UNKNOWN` and `missing_evidence` text, now carrying
`resolved_utc`, `resolved_evidence` and `resolved_not_by`. `unresolved_accounting()`
filters on `resolved_utc`, so the arming block lifts for this entry and for nothing
else; `resolved_accounting()` keeps it visible. A test asserts the resolver reads no
env var, no flag and no runtime state.

**And the protections stayed under test.** The eight tests that proved a deleted row,
an empty ledger or an unreadable ledger still refuse arming now inject a synthetic
standing entry, so those holes remain covered rather than retiring with the case.
43 tests pass.

**A finding worth more than the $0.18:** that fill was **PASSIVE, at wire 0.18, with
commission 0.0000, after resting 0.481 seconds.** It is the first venue-attested
PMUS BTC15m *maker* fill in this programme's records, and it was a manual order —
which is also what the owner has been reporting all along.

## 2. Exact-order cancellation — FIXED

The old `deadman()` had three defects in four lines: it iterated
`state["submitted"]` (permanent, never-pruned history, so every order ever sent
would be re-cancelled at every window end), it passed the **client** order id to a
route whose own docstring says that "would silently cancel nothing", and it recorded
success on any non-raising call.

The replacement cancels by `venue_order_id` from `live_orders()`, then **classifies
by a venue re-read, never by the ack**:

- `cancelled` only on a venue-confirmed terminal state;
- `still_open` when the ack returned 200 but the order is still working → `mark_uncertain`;
- `unverified` when the re-read raises → `mark_uncertain`;
- `no_venue_id` when there is nothing to name → `mark_uncertain`;
- a cancel that **raised** but re-reads terminal is cancelled, recorded with
  `resolved_by_reread: true`, and never retried;
- fills found on the way are booked through `apply_order_progress` with their cost
  basis — the exact failure that produced the 1900z share — and booked once however
  often the deadman runs.

Because every bad outcome marks the store uncertain, and `SafetyLimits.refuse()`
returns `ORDER_STATE_UNCERTAIN` before every other check, a failed cancel stops the
next submission. One test asserts that end to end.

**Evidence:** 15 new tests in `tests/test_l5_deadman_exact_cancel.py` covering the
id, the submit-log, ambiguous acks, 200-but-still-live, unreadable orders, partial
fills, full fills, idempotence, expiry, restart from the persisted ledger, and
multi-order partial failure. **All 15 fail against the old function** — I checked by
stashing the fix. Two pre-existing tests encoded the old, wrong contract and were
rewritten with the reason stated in each docstring. The shared `FakeTransport` gained
an `order()` route; its first version answered `cumQuantity 0` for an order whose own
ACK had reported a fill, and the store rightly refused that with
`CUM_QTY_WENT_BACKWARDS` — a venue cannot un-fill a fill, so the double was wrong,
not the code.

## 3. Independent watchdog — BUILT AND TESTED, one infra item short

`src/l5_halt_gate.py` was already the right shape and had 31 tests. What never
existed was a process to **assert** the latch. Now:

- **`src/l5_watchdog.py`** — `CancelOnlyTransport` (GET, plus exactly
  `POST /v1/order/{id}/cancel`; no submit, no preview, no market-scoped cancel, no
  account sweep — absent, not flagged off, because this PMUS account is **shared**
  and a slug-scoped cancel would hit another desk's orders) and `Watchdog.sweep()`.
- **`src/l5_halt_server.py`** — GET-only HTTP serving the one latch key, so the
  executor reads it via `L5_HALT_URL`. No `do_POST`/`do_PUT`/`do_DELETE` exist, so
  writes answer 501. A missing latch is a 404, which the gate reads as *not
  permitted*.
- **`scripts/l5_halt_watchdog.py`** + **`deploy/l5_halt_watchdog/Dockerfile`** — the
  image holds four modules and nothing that can place an order or clear a halt.

**The venue is the shared state, and that is the design.** The two services cannot
share a filesystem, and a heartbeat channel could itself wedge. It is not needed:
an order still resting past the executor's own cancellation deadline is visible at
`GET /v1/orders/open`, read from the authority rather than the suspect. A dead
executor, a hung one, a crashed container and a lost cancel all present identically.

**A test found a real defect before any of this ran.** `HaltGate`'s only path to
`True` is a *fresh, explicit* "no halt" — absence reads as a halt — so a watchdog
that merely stayed quiet on a clean sweep would have stopped the executor
permanently. `sweep()` now calls `heartbeat_no_halt()` on a clean sweep, which
`HaltAsserter` refuses to write over a latched halt, so it cannot undo its own latch.
A failed venue read writes **nothing**: it neither latches on a transient blip nor
attests falsely, and the record simply ages out past `max_age_s`, after which the gate
refuses.

**Tested (20 tests, plus the 31 existing, all 60 green):** transport refuses submit /
preview / sweep / DELETE; no account-wide route appears in the code (checked over
code lines, not docstrings); an overdue order latches and the executor's own gate then
refuses; the latch outlives the asserting process; the asserter has no clear, delete,
release, unhalt or resume; halt is asserted **before** the cancel is attempted, so a
cancel loop that dies leaves the executor already stopped; a 200 ack with the order
still live is **not** a cancellation; an ambiguous cancel resolves by re-read and is
never retried; an unverifiable cancel is never reported cancelled; a partial fill is
reported as residual and never traded out; an assert-only watchdog says in words that
the order is **still resting**; cancel requires `L5_WATCHDOG_CANCEL=YES`; and the
end-to-end HTTP path (404 → refuse, heartbeat → permit, latch → refuse, writes → 501).

**What is still needed, and it is the remaining blocker:**

1. a volume at `/data` on an **existing** service — `l5-e1-safety-monitor` is the
   natural host (it exists, is idle, and already holds PMUS credentials), so this
   adds no service, but a volume is the one new resource and it is the owner's call;
2. `L5_HALT_URL=http://<watchdog>.railway.internal:8080` on `l5-pmus-live`;
3. `L5_WATCHDOG_MAX_ORDER_AGE_S` set comfortably above the executor's own
   cancellation deadline, or the watchdog races a healthy order;
4. `L5_WATCHDOG_CANCEL=YES` only when the owner wants the cancel power armed.

Without the volume the latch does not survive a watchdog restart, and a latch that
dies with its process is not a latch. I have not created it.

## 4. Venue minimum quantity and tick size — VERIFIED FROM THE VENUE

Read off the market payload that came back with the 1900z resolution, not from
memory or from our own config:

```
minimumTradeQty         0.01
orderPriceMinTickSize   0.01
feeCoefficient          0.0695
```

## The experiment, recalculated at the verified minimum

| parameter | value |
|---|---|
| quantity | **0.01 shares** — the venue's own `minimumTradeQty` |
| price grid | 0.01 (`orderPriceMinTickSize`) |
| side/mode | maker only, post-only; `MAKER_ONLY_NO_TAKER` unchanged |
| price | join the best bid, never cross; refuse if best bid > **0.70** |
| **maximum cash at risk** | **$0.0070** (0.01 x 0.70) |
| **worst-case settlement loss** | **−$0.0070**, i.e. under one cent — a filled UP share settling 0.0000, exactly what 1900z did |
| market selection | current BTC15m window with a coherent authenticated book (`stream_age_s < 2`), full ladder so `queue_ahead` is **known** not `None`, ≥ 8 min to expiry, no exposure in any other market, ledger coherent everywhere, `orders_uncertain` false |
| outstanding orders | exactly one, ever |
| cancellation | by **venue order id** at T+120s, or 180s before expiry, whichever first, confirmed by re-reading `/v1/order/{id}`; ambiguous ack re-read, never retried |
| if filled | carry 0.01 shares to settlement. No taker complement, no second order |
| abort | any safety refusal, any `mark_uncertain`, any book incoherence → cancel and stop. No automatic repeat |

Our size does not affect what the experiment measures: we are last in the queue at
our price either way, so whether cumulative volume reaches our position is
independent of how much we put there. 0.01 shares is therefore both the minimum risk
and fully informative about the queue model.

The model's CONSERVATIVE/BASE/OPTIMISTIC verdict and `queue_ahead_at_placement` are
logged **before** the outcome is known, then compared against the venue's own
`cumQuantity`, `avgPx`, `createTime`/`lastTransactTime`,
`commissionNotionalTotalCollected` and `aggressor` flag — the same fields that
resolved 1900z, which is what makes the comparison auditable.

**If the owner prefers a size the venue has demonstrably filled:** 1.00 share at
≤ 0.70 is $0.70 at risk and −$0.70 worst case, and matches `D294PDYNPZ7Z` exactly —
1 share, passive, filled in 0.481s. More comparable, 100x the risk. Still trivial in
absolute terms. The minimum-risk answer to the question as asked is 0.01.

---

# Final readiness: the four checks the owner asked for

## A. The 1900z repair, verified (14 tests, `tests/test_l5_1900z_repair.py`)

Driven against a store in exactly the production state of 20:54Z — `1900z|UP`
quantity `1.0000`, `leg_cost` `0`, which is what `SETTLE_REFUSED` reported every
loop:

| property | evidence |
|---|---|
| books quantity and cost together | position `1.00`, leg cost `0.18000`, `book_violations` empty afterwards |
| cost came from the order row | `cost_reconstructions[0].source` carries `orders: ["D294PDYNPZ7Z"]`, `avgPx 0.1800` and the `not_used_as_evidence` note about the balance |
| booked **exactly once** | three further boots return `None`; one reconstruction row, ever |
| **survives restart** | re-opened from the same path twice: position and cost intact, repair does not re-run, still one row |
| refuses a moved-on ledger | quantity `2.0000` instead of `1.0000` → `L5_LIVE_REPAIR_REFUSED`, nothing written |
| refuses an existing cost | a leg already holding `0.17` is not overwritten |
| settlement posts the loss | `retire_settled_market(yes_value=0)` → `settlement_pnl -0.18000` |
| **account realized lands on −$0.14** | `+0.0400` attested (G1) then `−0.18000` → `realized_total "-0.14000"` |
| the loss is attributed, not dumped | `realized_unattributed` stays `0` |
| settled once across a restart | realized unchanged after re-open; market retired |
| reconciliation not weakened | authority still `VENUE`; an AST check proves the repair assigns nothing matching `realized`, so it cannot move the loss halt |
| registry machinery not weakened | with a synthetic **standing** entry, a coherent book still trips `unresolved_discrepancies` — the silent-clearing case |
| gates and lock unchanged | `shadow_only() True`, `BLOCKED ("submit",)`, `10 / 5 / 20 / NO_RECYCLING` |

−$0.14 is nowhere near the −$5 daily halt or the −$20 cumulative gate.

## B. Can `l5-e1-safety-monitor` host the watchdog?

Established from the service, not assumed:

- **It is not running.** `E1_MONITOR_END` at 2026-10-10T14:26:37Z after
  `ran_for_s 3607.9` against `E1_MONITOR_MAX_RUNTIME_S`, 357 polls. It is a
  one-shot, time-boxed monitor for a single E1 window and it exited two days ago.
  So there is nothing to disrupt.
- **No mutable state to share.** The service has no volume
  (`volumeMounts` absent), so there is no E1 state a co-tenant could corrupt.
  It wrote nothing to the venue either: `venue_writes_attempted 0`,
  `orders_cancelled_by_this_monitor 0`.
- **No new credential exposure.** It already holds `PMUS_API_KEY_ID` /
  `PMUS_API_SECRET_KEY` for the same account. PMUS issues account-wide keys, so
  the watchdog would gain no privilege the service does not already have, and
  the watchdog image contains no code that can submit — the restriction is
  structural rather than credential-scoped, which is the only form available.
- **But reusing it destroys a working artifact.** Hosting the watchdog means
  replacing that service's `dockerfilePath` and `startCommand`, which is the E1
  monitor's entire configuration. Railway bills usage, not services, so a
  dedicated service costs the same as repurposing this one — and keeps E1
  re-runnable.

**Recommendation: a dedicated service, not E1's.** Same cost, no artifact
destroyed. If the owner is finished with E1 for good, repurposing is safe on
every other axis.

Also worth noting: that monitor's own `E1_MONITOR_END` reported
`"MISSING_SETUP": "E1_MONITOR_ALERT_WEBHOOK is not set, so alerts are LOG ONLY
and nothing is delivered to a person"`. It could not act either. The watchdog
being built here is the first thing in this programme that can.

### Incremental cost

| item | rate | this deployment |
|---|---|---|
| volume | **$0.15 / GB-month**, metered per minute on actual usage | the latch is a few hundred bytes → effectively **$0.00** |
| container | ~$10 / GB-month memory, ~$20 / vCPU-month | a Python process polling every 20s, ~60 MB, near-zero CPU → **well under $1/month** |

**Total: under $1 a month.** Alternative considered: a storage bucket
($0.015/GB-month, free egress) — `l5_halt_gate` was deliberately written against
PUT/GET/DELETE so it can sit on one. Rejected because a Railway bucket issues one
key pair with write access, so the executor would hold credentials that can write
the latch; the volume-plus-GET design leaves the executor's only verb a
credential-free GET on an internal URL. The container cost is identical either way.

## C. Watchdog behaviour under failure (29 tests, all green)

| failure | behaviour, tested |
|---|---|
| executor dead / hung / crashed / lost cancel | all present identically — an order resting past the deadline — and all latch; with `L5_WATCHDOG_CANCEL=YES` the exact id is cancelled and verified |
| halt must outlive its asserter | the gate still refuses after the `Watchdog` object is destroyed |
| **watchdog restart** | a fresh process on the same volume finds its own latch; its clean-sweep heartbeat is **refused** (`"refusing to overwrite a latched HALT"`) and the executor stays stopped — a restart is not an all-clear |
| stale heartbeat | refuses with `halt_record_stale`, with **no halt written** — staleness is its own refusal |
| watchdog stops sweeping | the executor stops by omission; a live attestation is required, not merely the absence of a halt |
| ambiguous order state | `leavesQuantity` decides, the state string only when absent — `PARTIALLY_FILLED` is **not** terminal (a substring match on "FILLED" would retire a resting order); unknown state is not terminal; unparseable `leavesQuantity` is not terminal |
| ambiguous cancel ack | 200 + still live → `still_open`, never cancelled; raised + terminal on re-read → cancelled once, never retried; unreadable → `unverified` |
| partial fill during cancel | reported as residual, `cum_qty 0.40`; one cancel sent and nothing resembling a flattening order |
| blind watchdog | writes nothing: neither latches on a blip nor attests falsely |
| cancel power unarmed | latches and says in words that the order is **STILL RESTING** |
| **exact-order-only preserved** | only ids from the venue's own open list are cancelled; no slug-scoped or account-wide route exists in the code (checked over code lines, excluding docstrings); the owner's manual order `D23N1EHTTZ7D` cannot be named by a sweep it does not appear in |
| asserter cannot clear | no `clear`, `delete`, `release`, `unhalt` or `resume`; the store has no `delete` |
| ordering | the halt is asserted **before** the first cancel, so a cancel loop that dies leaves the executor already stopped |

## D. Venue minimums: quantity verified, notional NOT

From the venue's own market payload (returned with the 1900z resolution):

```
minimumTradeQty         0.01
orderPriceMinTickSize   0.01
feeCoefficient          0.0695
```

**There is no minimum-notional field in that payload.** What we have instead is
what the venue has actually accepted, from our own attested order rows:

| order | qty | limit | notional | outcome |
|---|---|---|---|---|
| `D0VC6VJDMZ7J` | 0.13 | 0.62 | **$0.0806** | `CANCELED` — it rested, so it was accepted |
| `D294PDYNPZ7Z` | 1.00 | 0.18 | $0.1800 | `FILLED`, passive |
| `D23N485QAZ89` | 2.00 | 0.84 | $1.6800 | `FILLED` |

So **$0.08 notional is attested as acceptable and anything smaller is
UNVERIFIED.** A 0.01-share order at 0.70 is $0.0070 of notional — an order of
magnitude below anything we have evidence for, so proposing it as "the minimum"
would be proposing an untested rejection path, and a rejected order teaches
nothing about queues.

The way to settle it without risk is `POST /v1/order/preview`, which this
programme has already established is where the venue enforces minimum quantity
(`min_quantity_enforced_by_venue`: "POST /v1/order/preview rejection of a
sub-minimum qty"). A preview is not an order and the existing
`pmus-preview-oneshot` service already does exactly this. **I have not run it**:
it is a POST, and the instruction was to submit nothing. It is a one-line ask if
the owner wants the true floor established first.

## E. The experiment, within the standing limits

Sized to be **meaningful** rather than merely minimal, and to match the one
PMUS BTC15m maker fill we can actually audit:

| parameter | value |
|---|---|
| quantity | **1.00 share** — 100x the venue's `minimumTradeQty`, and the exact size of attested fill `D294PDYNPZ7Z` |
| notional | $0.18–$0.70, comfortably above the **$0.08** attested floor |
| side/mode | maker only, post-only; `MAKER_ONLY_NO_TAKER` unchanged |
| price | join the best bid, never cross; refuse if best bid > **0.70** |
| **max cash at risk** | **$0.70** against the **$10** gate — 7% of it |
| **worst-case settlement loss** | **−$0.70**, a filled UP share settling 0.0000, exactly what 1900z did |
| residual | 1.00 share of the **5**-contract cap — 20% |
| cumulative loss headroom | $0.70 against the $20 gate; realized would go −$0.14 → −$0.84 worst case |
| market rule | current BTC15m window, coherent authenticated book (`stream_age_s < 2`), full ladder so `queue_ahead` is **known** not `None`, ≥ 8 min to expiry, no exposure elsewhere, ledger coherent, `orders_uncertain` false |
| outstanding orders | exactly one, ever |
| cancellation | by **venue order id** at T+120s, or 180s before expiry, whichever first, verified by re-reading `/v1/order/{id}`; ambiguous ack re-read, never retried |
| watchdog deadline | `L5_WATCHDOG_MAX_ORDER_AGE_S = 300`, comfortably outside the executor's own 120s, so the watchdog cannot race a healthy order |
| if filled | carry 1.00 share to settlement. No taker complement, no second order |
| abort | any safety refusal, any `mark_uncertain`, any book incoherence → cancel and stop. No automatic repeat |

Recorded, with the model's verdict logged **before** the outcome is known:
`queue_ahead_at_placement`, `displayed_depth_at_price`, `ticks_behind_touch`, the
CONSERVATIVE/BASE/OPTIMISTIC prediction; then the venue's `order_id`,
`createTime`, `insertTime`, `lastTransactTime`, `cumQuantity`, `leavesQuantity`,
`avgPx`, `state`, `commissionNotionalTotalCollected`,
`makerCommissionsBasisPoints`, the `aggressor` flag, eligible volume through the
price while resting, the cancel's venue-confirmed terminal state, and settlement.

Those are the same fields that resolved 1900z, which is what makes the comparison
auditable rather than a story.

## Go / no-go

**Code: GO.** The three priorities are done and tested, with no regression.

**Deployment: NO-GO until two things the owner alone can authorise.**

### Infrastructure change required

1. A service for the watchdog (dedicated, recommended; or repurpose the idle
   `l5-e1-safety-monitor`), built from `deploy/l5_halt_watchdog/Dockerfile` on
   this branch, with `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`.
2. **A persistent volume mounted at `/data`** on that service. Cost: $0.15/GB-month
   on a few hundred bytes, so effectively nothing; the service's compute is under
   $1/month. Without it a watchdog restart loses the latch, and a lost latch is a
   silently cleared halt.
3. `L5_HALT_URL=http://<service>.railway.internal:8080` on `l5-pmus-live`.
4. `L5_WATCHDOG_MAX_ORDER_AGE_S=300`, `L5_WATCHDOG_POLL_S=20`.
5. `L5_WATCHDOG_CANCEL=YES` — only if the owner wants the watchdog's cancel power
   armed. Without it the watchdog latches and reports the order still resting.

Then, before any order: confirm in the logs that the executor's own
`halt_gate()` is live (not `None`), that a test latch asserted by hand stops it,
and that the heartbeat refreshes.

### Live order authorization required

> One post-only BTC15m maker order, **1.00 share**, at or below the best bid,
> never above **0.70**, maximum cash at risk **$0.70**, worst-case settlement
> loss **−$0.70**, cancelled by venue order id at T+120s, one order only, no
> repeat, with `L5_SHADOW_ONLY` lifted for that single window and restored
> afterwards.

Nothing else changes: no limit, no taker, no concurrency, no second window.
