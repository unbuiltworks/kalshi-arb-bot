# STAGE-6-S6P — the matched-pair probe protocol

## NOT AUTHORIZED / DISARMED

**Nothing in this protocol has been authorized, armed, deployed, or run against
the venue. No order has been previewed or submitted. No latch slot has been
consumed. No owner value appears anywhere in this repository, and no
order-capable module from `analysis/l5/stage6/` is deployed to Railway.**

The four `PMUS_S6P_*` arming variables are unset, the authorization pin
`PMUS_S6P_AUTHORIZED_FINGERPRINT` is unset, and
`python3 -m analysis.l5.stage6.runner` as committed prints this protocol's
state and exits with the DISARMED refusal before any order mutation. A test
asserts that unpatched build refuses
(`test_disarmed_by_default_and_no_owner_values_in_the_tree`).

### What the owner would be authorizing if they said yes

> **ONE** post-only first leg of at most **1.00 share** at a price at or below
> **$0.60**, in **ONE** named `btc-updown-15m` window, followed by **AT MOST
> ONE** post-only complement sized to the first leg's **actual filled
> quantity** at a price that keeps the fee-safe combined pair cost at or below
> **$0.96**; a cancel of whatever rests at **TTE 120 s**; and **no repeat**.
> **Maximum arithmetic loss $0.60.**

That is the whole of it. Arming does not authorize a second pair, a second
market, a re-quote, a taker order, a residual rescue, or an automatic relaunch
after a crash or a completion. Each of those is absent from the code, not
merely switched off — there is no flag that turns any of them on.

---

## 1. Frozen identity

| | |
|---|---|
| Probe | `STAGE-6-S6P` |
| Spec module | `analysis/l5/stage6/spec.py` |
| **Spec fingerprint** | **`32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8`** |
| Parent | `LIVE-L5-EXEC-1-XR2W`, `ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562` |
| Parent modified by this build | **No** |
| Fee schedule | `src/pmus_fee_schedule.py`, `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629` |
| Lineage | EXEC-1 → EXEC-1-X → XR → XR2 → XR2W → **S6P** |
| Authorization pin env | `PMUS_S6P_AUTHORIZED_FINGERPRINT` (unset) |

The fingerprint is a **hard-coded literal** in `spec.py`, not recomputed from
the object it checks, so `assert_frozen()` cannot be vacuous. `assert_frozen()`
calls XR2W's own `assert_frozen()` first, then the fee schedule's, then
`bounds.assert_bounds_coherent()`, then this build's invariant list, then the
fingerprint. A change to the proven executor breaks this build rather than
being silently absorbed into it. `spec.diff_report()` prints the literal diff
against XR2W.

### Why this is a new probe and not another A7

XR2W tests one thing: that a single post-only 0.01-share order serializes
correctly and crosses the wire. It succeeded as a wire test. It **cannot** test
the thesis, because the thesis is not "an order can be sent":

> passive first-leg fill → fee-safe complement attempt → partial-fill
> accounting → bounded residual → cancellation/deadman → settlement
> reconciliation

Every link in that chain is absent from XR2W by construction — one leg, no
complement, no pair economics, no partial-fill accounting beyond a single
all-or-nothing 0.01-share fill, and a residual bounded at $0.0099. Another
one-leg run would produce another wire result and no evidence about the thesis.

**The loss envelope widens deliberately, from $0.0099 to $0.60.** It has to: at
0.01 shares the venue's 0.01 quantity grid gives a leg no distinguishable
partial-fill state, so the central thing under test would be unobservable. 1.00
share gives 100 distinguishable partial-fill states and keeps the loss bound
under a dollar. This widening is recorded in `diff_report()` as
`loss_envelope_widened: true` with its justification, not buried.

---

## 2. Every bound, with its dollar value

| Bound | Value | Why (never fitted) |
|---|---|---|
| First-leg quantity | **1.00 shares** | 100 distinguishable partial-fill states on the venue's 0.01 grid, sub-dollar loss bound. Chosen for observability of partial fills, not for expectancy. |
| First-leg price ceiling | **$0.60** | Makes the loss bound arithmetic; leaves $0.36 of raw room under the pair ceiling. |
| First-leg notional | **$0.60** | `1.00 × $0.60` |
| Pair cost ceiling (fees in) | **$0.96 / contract** | CLAUDE.md spring discipline, copied. Near-$1 tolerance is LOCKED. |
| Minimum locked pair gross | **$0.04 / contract** | `$1.00 − $0.96` |
| Max residual | **1.00 shares / $0.60** | The complement is sized to the first leg's actual fill and never above it, so the residual cannot exceed the first leg. |
| Max inventory imbalance | **1.00 shares** | Same construction. |
| **MAX POSSIBLE LOSS (session)** | **$0.60** | First leg fills in full at the ceiling, complement never fills, residual settles to zero. Both legs are BUYs, so cash out is bounded by what we pay. |
| Max daily realized loss | **$0.60** | One probe per authorization, no automatic repeat, so the daily bound *is* the session bound. |
| Max total strategy exposure | **$0.60** | This is the only money-moving path if armed. |
| Concurrent markets | **1** | CLAUDE.md. |
| **Outstanding orders at any instant** | **1** | The legs are strictly sequential; the complement is not placed until the first leg is cancelled or terminal. |
| Total `POST /v1/orders` per session | **2** | Two named durable latch slots, one per leg. |
| First legs | **1** | A second first leg is a new experiment. |
| Complement submissions | **1** | No re-quote, no re-price, no second send. |
| Window length | **900 s** | Inherited. |
| TTE placement/withdrawal cutoff | **120 s** | Inherited from `live_exec2.timing`; unknown TTE counts as past the boundary. |
| First-leg → complement handover | **TTE 510 s** | An *equal* division of the placeable stretch (900 − 120 = 780 s): **390 s to each leg**. Derived from the two inherited numbers, not chosen. |
| Maker rebate assumed | **$0.00** | Structural: `round_half_even(0.0125·C·p·(1−p), $0.01)` is $0.00 below 1.62 shares. At 1.00 share the rebate cannot be anything else, so no bound or gate may depend on it. |

Every entry in `bounds.BOUND_PROVENANCE` carries `fitted: false`, and a test
asserts it for all of them. `bounds.assert_bounds_coherent()` runs at import and
at every boot and fails closed if any bound contradicts another — for example if
the handover TTE fell to or below the withdrawal cutoff, or if the pair ceiling
reached par.

### Why the pair-cost gate is tighter than "$0.96"

The gate prices **both** legs at the **taker** coefficient (0.0695 effective
2026-09-17) even though both are post-only and cannot take, and counts the
rebate as exactly **zero**:

```
fee_safe_pair_cost = p_first + p_complement
                   + taker_fee(p_first) + taker_fee(p_complement)
                   - 0
```

That is strictly more expensive than anything the venue can charge a post-only
pair, so a pair clearing this gate is fee-safe under every role outcome —
including the one where our post-only assumption turns out to be wrong. The
cost of that conservatism is about **4 cents of complement headroom at every
first-leg price**, and it is reported rather than hidden:

| First-leg fill VWAP | Complement ceiling if fees ignored | Fee-safe complement ceiling | Conservatism cost |
|---|---|---|---|
| $0.40 | $0.56 | **$0.52** | 4¢ |
| $0.44 | $0.52 | **$0.48** | 4¢ |
| $0.48 | $0.48 | **$0.44** | 4¢ |
| $0.60 | $0.36 | **$0.32** | 4¢ |

Worked example at a $0.44 fill: complement at $0.48, raw pair $0.92, fees
$0.0171 + $0.0173, fee-safe pair cost **$0.9544**, locked gross **$0.0456** per
contract. One tick higher ($0.49) does not clear, and a test asserts that too —
otherwise $0.48 would not be the maximum.

The ceiling is found by walking **down** the 0.01 grid rather than solved
algebraically, because the fee is a rounded function of the complement price
itself and an algebraic inverse would be an approximation of the schedule —
i.e. a second schedule.

**Near-$1 pair tolerance is not implemented.** There is no near-par branch, no
alternate ceiling and no flag: it cannot be switched on, only written.

---

## 3. The complete decision path

```
START
  │
  ▼
ARMING_GATE ─────────────── not armed / not gated / not pinned ──► DISARMED            [T]
  │  four PMUS_S6P_* keys set, gate names this slug, fingerprint pinned
  ▼
PREFLIGHT ───────────────── any local refusal ─────────────────► REFUSED_PREFLIGHT    [T]
  │  TTE > 120 and TTE > 510 (first leg's turn); book two-sided;
  │  price on the 0.01 tick and inside [0.01, 0.99]; would NOT cross
  │  the same-side ask; qty == 1.00; price <= $0.60;
  │  FIRST_LEG latch slot unspent; all inventory bounds satisfied
  ▼
PLACE_FIRST_LEG   preview ─► echo ─► guard ─► latch(FIRST_LEG) ─► guard ─► POST
  │        │
  │        ├── PreviewRejected ───────────────────────────────► PREVIEW_REJECTED      [T]
  │        │     (no network order boundary crossed; latch untouched)
  │        ├── SubmissionAmbiguous ───────────────────────────► AMBIGUOUS_SUBMISSION  [T]
  │        │     (may have crossed; latch SPENT; NEVER retried)
  │        └── anything else ─────────────────────────────────► UNCLASSIFIED          [T]
  ▼
OBSERVE_FIRST   poll; inventory.apply_fill ONCE PER EXECUTION, counted
  │             exactly once by id; bounds re-checked after EVERY single fill
  │
  ├── filled in full ─────────────────┐
  └── TTE <= 510 (handover) ──────────┤
                                      ▼
                              CANCEL_FIRST_LEG  (then one post-cancel sweep, so a
                                │                fill racing the cancel is counted)
            filled_qty == 0 ────┼──────────────────────────────► NO_FIRST_LEG_FILL    [T]
                                ▼
PAIR_GATE   max_complement_price(first-leg fill VWAP)
  │        └── no grid price clears $0.96 ─────────────────────► REFUSED_PAIR_COST    [T]
  ▼
PLACE_COMPLEMENT   qty = inventory.complement_qty()  ← reads filled_qty ONLY
  │        ├── PreviewRejected ───────────────────────────────► PREVIEW_REJECTED      [T]
  │        ├── SubmissionAmbiguous ───────────────────────────► AMBIGUOUS_SUBMISSION  [T]
  │        └── anything else ─────────────────────────────────► UNCLASSIFIED          [T]
  ▼
OBSERVE_COMPLEMENT   poll; PATIENT — no timer forces completion
  │
  ├── complement filled == first filled ───────────────────────► PAIR_COMPLETE        [T]
  └── TTE <= 120 ─────────────────────┐
                                      ▼
                                WITHDRAW  cancel_exact, post-cancel sweep
                                      │
            residual > 0, some matched ┼───────────► PAIR_PARTIAL_RESIDUAL_BOUNDED    [T]
            residual > 0, none matched ┴───────────► RESIDUAL_BOUNDED_DEADMAN         [T]

  ── finally, for every [T] except DISARMED ──
  DEADMAN   POST /v1/orders/open/cancel {slugs:[slug]}   (never retried,
            not gated on arming or on the latch)
  RECONCILE settlement.reconcile → seven separate accounting lines; nothing
            labeled REALIZED without confirmed settlement cash AND a source identity
```

### Side-aware crossing

The venue lists one instrument, so a DOWN/NO order at price X is the long-side
price `1 − X` on the wire (`src.l5_order_body.venue_price`). The DOWN book is
therefore the YES book reflected through 1: `no_bid = 1 − yes_ask`,
`no_ask = 1 − yes_bid`. `PairMachine.side_book()` does this reflection, and the
crossing check uses the complement's own ask. Getting it wrong is not cosmetic:
a complement priced against the YES book would read as crossing when it does
not, and the probe would refuse every complement it ever computed — arriving at
the first-leg-only outcome by arithmetic rather than by the market.

### Partial-fill accounting — the centre of the experiment

The obvious way to write a two-leg probe is "place leg one for Q, then place leg
two for Q". That is wrong in the expensive direction: if the first leg fills
0.37 of 1.00 and the complement is sized at the *intended* 1.00, the second fill
overshoots by 0.63 shares, which is not a hedge but a new unmatched position on
the other side. So:

- **`Inventory.complement_qty()` reads `filled_qty` and nothing else.**
  `intended_qty` is retained only so a record can show the difference. Snapped
  **down** to the 0.01 grid — rounding up would size the complement above the
  fill and create exactly that overshoot.
- `apply_fill` is called **once per execution**, not once per order or per poll
  batch, and bounds are re-checked after each single execution, so a bound
  cannot be crossed and uncrossed inside one loop without being seen.
- Executions are **idempotent by id**. A venue poll can return the same
  execution twice; counting it twice would corrupt both the VWAP and the
  residual bound. An execution with **no id is refused, not counted** — an
  uncountable fill is worse than a missing one. So is one with no price.
- Cost basis is the **quantity-weighted VWAP**, so a lucky partial at a better
  price does not entitle the complement to a ceiling the blend cannot support.
- Liquidity role comes from **`execution.isAggressor` only**, never a bare
  `aggressor` field. Absent the key the role stays **UNRESOLVED** and is never
  promoted to maker.

### Bounded residual

The residual is **bounded, not believed in**. It is carried rather than
flattened because flattening on an arbitrary timer is the behaviour CLAUDE.md
forbids — not because it is thought to have edge. `residual_predictive_skill_assumed`
is `false` in the spec, in every inventory snapshot, and in the reconciliation,
and no mark-to-market figure is produced for it. A bound breach **fails closed**:
stop, cancel, report.

### Cancellation and deadman

- `cancel_exact` by our own order id at the leg's boundary.
- After every cancel, **one more poll** — a cancel can race a fill, and the fill
  that lands first must be counted.
- The market-scoped deadman `POST /v1/orders/open/cancel` runs in `finally`,
  whatever happened, **except** when the terminal is `DISARMED` (nothing was
  ever placed, so there is nothing to cancel and no POST is made).
- The deadman is **not** gated on arming or on the latch: refusing to cancel
  because a flag is unset is the dangerous direction. It is **never retried**.
- No resting order is ever left to ride to settlement.

### The two-slot durable latch

`O_CREAT|O_EXCL`, session-scoped on `PMUS_S6P_SESSION_ID`, in its own directory
`/state/pmus-ws/stage6`, consumed **before** the POST and **never released on
failure**. Two **named** slots, `FIRST_LEG` and `COMPLEMENT` — not a capacity-2
counter, which would permit two first legs and therefore a different experiment.
`consume("COMPLEMENT")` refuses unless `FIRST_LEG` is already spent, because a
complement without a first leg is an unhedged one-leg order under a misleading
name. An unknown POST outcome counts as spent and the probe stops: a spent latch
loses a run, a retried submission risks two live orders.

The proven `orders.submit_one` is reused **verbatim** — preview, echo
requirement, the two guard calls around latch consumption, the single POST. Only
*which slot pays* is routed, by a narrow context manager scoped to one `place`
call and restored in `finally`. Forking `submit_one` would be a second
submission path: the same mistake class as a second serializer.

### Terminal classification

| Terminal | Order boundary crossed? | Action |
|---|---|---|
| `DISARMED` | No | nothing placed; no deadman POST |
| `REFUSED_PREFLIGHT` | No | nothing placed |
| `REFUSED_PAIR_COST` | First leg only | cancel + deadman |
| `NO_FIRST_LEG_FILL` | First leg only | cancel + deadman |
| `PAIR_COMPLETE` | Both | deadman |
| `PAIR_PARTIAL_RESIDUAL_BOUNDED` | Both | cancel + deadman |
| `RESIDUAL_BOUNDED_DEADMAN` | Both | cancel + deadman |
| `PREVIEW_REJECTED` | **No** — preview creates nothing and runs strictly before latch consumption | stop; latch untouched |
| `AMBIGUOUS_SUBMISSION` | **Possibly** — the request reached `POST /v1/orders` | **never retry**; run the deadman; reconcile venue state by reading open orders and executions |
| `UNCLASSIFIED` | Unknown | **never resolved by guess**; treat as possibly crossed |

Classification is by **exception type**, never by reading the latch.

### Settlement reconciliation

`settlement.reconcile()` reports the seven CLAUDE.md accounting lines
**separately** — matched-pair economics, directional residual, spread capture,
credited maker rebates, liquidity rewards, fees, settlement cashflow — and emits
**no blended profit figure** (`blended_profit_figure_emitted: false`).

`realized_pnl()` returns a number **only** when a settlement record carries a
confirmed cash movement *and* a source identity. Otherwise it returns `None`
with a refusal string. Each of these is explicitly **NOT realized**:

- theoretical fills; stale or public BBO opportunities; modeled pair profit
  without fills; shadow inventory;
- mark-to-market gains on the residual;
- estimated maker rebates or liquidity rewards; unconfirmed fills;
- **a venue `realizedPnl` field on a trade row** — the venue's running figure,
  not settlement cash;
- **a balance change without a source identity** — another service shares this
  account;
- **the locked pair gross implied by a cleared gate.**

A credited rebate is a **negative** `commissionNotionalCollected` on our own
execution. A positive value is a charge and is reported separately. An absent
field is absent — never read as zero credited. Credited rebates are never netted
into realized P&L.

If the realized pair cost on **actual fills** ever exceeds $0.96 — which a
post-only buy cannot cause, since it cannot fill above its limit — line 1 raises
an **ANOMALY** flag telling the reader not to treat the pair as locked and to
investigate before any further probe.

---

## 4. Reuse: no duplicate primitives

| Primitive | Module | Status |
|---|---|---|
| Order serializer | `src/l5_order_body.py` | **imported, unmodified** — `build()`, `preview_envelope()`, `venue_price()` |
| Order mutation path | `analysis/l5/live_exec2/orders.py` | **imported, unmodified** — four permitted routes, `VenueHTTPError`, `SAFE_RESPONSE_HEADERS`, `REDACTED_REQUEST_HEADERS`, `PreviewRejected`, `SubmissionAmbiguous` |
| Frozen parent spec | `analysis/l5/live_exec5/spec.py` | **imported, unmodified**; its fingerprint is unchanged |
| Timing / TTE | `analysis/l5/live_exec2/timing.py` | imported, unmodified |
| Fee schedule | `src/pmus_fee_schedule.py` | imported, unmodified |
| Latch mechanism | `analysis/l5/live_exec2/latch.py` pattern | re-expressed as two named slots in `stage6/pair_latch.py`; the one-slot module itself is unmodified |

`test_no_module_in_this_package_builds_its_own_order_body` asserts that no file
in `analysis/l5/stage6/` contains `marketSlug`, `ORDER_TYPE_LIMIT`,
`ORDER_ACTION_BUY`, `TIME_IN_FORCE_` or a `"request":` envelope key. A duplicate
serializer is what killed Attempt 5 with an HTTP 400, and the repository records
the same class of bug happening once before that; this package contains none.

Verified wire contract (asserted offline): a DOWN order at $0.49 serializes as
`price = {"value": "0.51", "currency": "USD"}`, `quantity` a JSON **number**,
`participateDontInitiate: true`, `outcomeSide: OUTCOME_SIDE_NO`, and the preview
request is `{"request": body}`. The complement leg is the first place this 1−X
mapping is exercised on **both** sides in one session — and it is exercised
through the proven serializer, not re-derived.

---

## 5. Offline test results

```
$ python3 -m pytest tests/test_stage6_matched_pair.py -q
50 passed
```

No network, no credentials, no arming. Every test runs the **real** state
machine; only the venue is fake, because the adapter is the machine's one seam.
The arming gate is satisfied by a monkeypatch **inside the test process only**.

The fake's clock is **stepped, not counted**: `tte_s()` is idempotent and time
moves only when the machine finishes an observation round and calls its `poll`
hook. An earlier version advanced TTE on every `tte_s()` call, which made
outcomes depend on how many times the machine happened to ask — adding a log
line changed the result. That is fixed.

Coverage, in the order the mandate names it:

1. **First leg partial-filled → complement sized to the ACTUAL fill.**
   0.20 + 0.17 = 0.37 of an intended 1.00, spread over two poll rounds; the
   complement is placed for **0.37**, on the DOWN side, and is asserted `!=` the
   intended 1.00.
2. **Complement never fills → bounded residual → deadman.** Residual 0.50
   shares / $0.2000, within every bound; both legs cancelled; deadman ran once;
   `residual_predictive_skill_assumed` false; no mark-to-market figure.
3. **Preview rejection.** `crossed_order_boundary` false, latch untouched, one
   place attempt, no retry, nothing realized. Also covered on the complement leg.
4. **Ambiguous submission.** `crossed_order_boundary` true, latch spent, **one**
   attempt, no complement attempted, no retry, deadman still ran.
5. **Pair cost above $0.96 → refuse.** Gate refusal at $0.60/$0.40 and at
   $0.48/$0.48; the machine reaches `REFUSED_PAIR_COST` and never places a
   complement; the maximum complement price is on tick and one tick higher does
   not clear.
6. **Frozen fingerprint self-check.** `assert_frozen()` passes; perturbing the
   spec makes it fail; perturbing an invariant makes `assert_envelope_preserved`
   fail by name; the parent XR2W fingerprint is asserted unchanged.

Also covered: per-partial inventory updates and VWAP blending; execution
idempotency by id; refusal of an id-less execution; the `isAggressor`-only role
rule; quantity snap-down; refusal to open a complement with no counted fill;
the cancel/fill race sweep; partial complement → bounded residual; latch slot
single-use and ordering; fail-closed without a session id; a spent slot refused
at preflight; unknown TTE as past the boundary; no first leg inside the
complement's half of the window; local would-cross refusal; price-ceiling and
quantity refusals; the realized-label discipline including the venue
`realizedPnl` and source-identity cases; the seven separate accounting lines;
the rebate's structural $0.00; and the pair-cost ANOMALY flag.

Regression check: `tests/test_live_exec1xr.py`, `tests/test_pmus_fees.py` and
`tests/test_pmus_fee_schedule.py` — **209 passed**, so the proven executor, its
spec and the fee schedule are unchanged by this work.

---

## 6. What this protocol CANNOT establish

Stated here, and carried inside the fingerprinted spec as
`spec.CANNOT_ESTABLISH`, so no reader of a result can mistake its scope.

- **That the strategy is profitable.** One pair in one market is one sample. It
  has no statistical power over expectancy, and a single favourable settlement
  would be indistinguishable from luck.
- **That L5 would have acted the same way.** S6P is an execution-mechanism test.
  Action-by-action fidelity against L5 is a separate comparison and this probe
  does not perform it. Per CLAUDE.md, positive P&L with materially different
  behaviour is not evidence of replication.
- **That the $480/day target is reachable.** Nothing here measures capacity,
  frequency, or concurrency.
- **That maker rebates contribute anything.** At 1.00 share the rebate is
  structurally $0.00 on the venue's cent floor, so the probe is deliberately
  blind to rebate economics.
- **That the residual has predictive skill.** The probe bounds the residual; it
  does not and cannot evaluate it, and the repository's evidence does not
  support residual directional skill.
- **That a complement would have filled better later.** The complement is quoted
  once and not re-quoted, so the probe observes one patience path, not the
  distribution of them.
- **That post-only guaranteed a maker role.** Role is read from
  `execution.isAggressor` after the fact; an absent field leaves it UNRESOLVED
  and the probe reports UNRESOLVED rather than maker.
- **Anything at all about profitability if no leg fills.** A run ending
  `NO_FIRST_LEG_FILL` is a successful test of the cancellation path and evidence
  of nothing else.

One further honest limit, not in the spec list: the **handover at TTE 510 s** is
a structural choice, not a measured one. The two legs compete for the single
outstanding-order slot, so something must end the first leg's turn; an equal
split of the placeable window is defensible but it is not evidence that 390 s is
the right amount of patience for either leg. The probe cannot tell us that.

---

## 7. Operational discipline

- **Money-moving sessions fail closed.** No retry loop, no supervisor, no
  self-relaunch after a crash or a completion. Read-only collectors self-recover
  and run 24/7; this is not one.
- **Untouched by this work:** A6, S3, the running `l5-book-shadow` collector, the
  XR2W executor and its spec and fingerprint, and all unrelated SOL
  infrastructure. No evidence was deleted. No credentials appear anywhere; the
  request-header redaction allowlist is inherited from `live_exec2.orders`,
  which records header *names* only and never values.
- **No parameter tuning or fitting** was performed. No number here was chosen by
  looking at a backtest, a grid, or a replay.

## 8. What the owner is being asked to review

1. The frozen fingerprint
   `32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8` and the
   diff against XR2W (`python3 -m analysis.l5.stage6.spec` prints the
   fingerprint; `spec.diff_report()` prints the diff).
2. The widening of the loss envelope from $0.0099 to **$0.60**, and whether the
   partial-fill observability it buys is worth it.
3. The $0.60 first-leg price ceiling and the 1.00-share size.
4. The taker-on-both-legs fee conservatism, which costs ~4¢ of complement
   headroom at every price.
5. The TTE 510 s handover as the thing that ends the first leg's turn.
6. That a complement is quoted **once** and never re-quoted.

Nothing proceeds without that review. `python3 -m analysis.l5.stage6.runner`
prints all of the above and refuses.
