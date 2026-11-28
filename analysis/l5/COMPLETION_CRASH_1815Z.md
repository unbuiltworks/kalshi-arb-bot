# Why no complement ever rested: the completion re-entry crashed every time

P0 answer for 1815z (125 consecutive `order_resting: false`) and 1745z
(seven cancel/replace cycles in 24 seconds). Every figure below is from the
production tape of deploy `1d673c49`.

## 1. The four candidate causes, answered

| candidate | answer |
|---|---|
| intentionally suppressed by policy? | **No.** The policy said COMPLETE 14 times in 93 seconds, every one `ok: true`, `reason: RISK_REDUCING_COMPLETION`. |
| cancelled, rejected, expired, awaiting reconciliation? | **No.** No order was ever created — no pre-submit, no ack, no venue id, nothing to cancel or expire. |
| valid quote available but never submitted? | **Yes.** 14 priced DOWN legs at own 0.50–0.53, pair 0.96–0.99, `locked_pnl_usd` 0.01–0.04. |
| two-order slot interference? | **No.** The slot was free; the crash happened before any limit was consulted. |

## 2. Root cause

`L5_LIVE_ERROR {"error": "TypeError: can't multiply sequence by non-int of
type 'decimal.Decimal'"}` — **14 occurrences, each 20–40 ms after its own
`L5_BOOK_COMPLETION_TRIGGER`**, one-to-one with the 14 COMPLETE decisions.

`l5_live_execution.handle` has two routes into the same three statements
(`l5_live_execution.py:7145`, `:7157`, `:7216`):

    want = dec["l5_size"] * self.lam        # self.lam is a Decimal

* **L5 route** — `dec = FT.l5_native(row)`, which coerces `price` and
  `l5_size` through `X._d` to Decimal and returns None if either is
  unreadable.
* **Controller route** — `dec = dict(controller["decision"])`, taken
  **verbatim**, with no coercion.

`maybe_controller_open` passes `l5_size=TSP.opening_leg_max_contracts()`,
a Decimal — so **openings worked**. `maybe_book_completion` seeds its
controller re-entry from `TS.complement_order`, whose `leg` carries
`"price": str(px)` and `"qty": str(q)` — so **every completion raised**.

Reproduced exactly:

    >>> "1" * Decimal("1")
    TypeError: can't multiply sequence by non-int of type 'decimal.Decimal'
    >>> Decimal("1") * Decimal("1")
    Decimal('1')

### Why the tape could not say so

`emit("L5_BOOK_COMPLETION_RESULT", ...)` sits **after** the placement call,
so the exception skipped it. The tape therefore held the decision, the
trigger, and a bare `L5_LIVE_ERROR` with no key, market or coid — three
records of one event with nothing linking them. Zero
`L5_BOOK_COMPLETION_RESULT` rows exist for the whole residual.

This is the same signature already recorded in the comment above
`_bc_capture`: *"Across the 2026-11-01 campaign the policy returned COMPLETE
145 times — 109 of them at a pair cost inside the 0.96 ceiling, median
0.9200, minimum 0.6600 — and not one became an order."* That author added
the observability and deliberately avoided a behaviour change; the emit
never fired, because the crash precedes it. **The controller completion
re-entry has never once placed an order.**

## 3. 1745z: why that window *did* complete

1745z's completions came through the **L5 row route** (`self.handle(row,
now)`), which coerces. Its seven cancel/replace cycles in 24 seconds are a
separate matter — the requote chasing the touch upward, priced on the
completion ceiling (~0.98) rather than the pair cap — and are documented in
`CAMPAIGN_CD_LIFECYCLE_RECONSTRUCTION.md` §1. **No crash there.** The
1815z-class failure is specific to inventory whose origin is CONTROLLER,
which is all autonomously opened inventory.

## 4. Venue-confirmed ledger (supersedes the hand reconstruction)

`L5_WINDOW_ACCOUNTING` phase CONFIRMED is the authority and I should have
used it from the start instead of reconstructing from fills.

| window | pairs (own cost) | matched P&L | residual | residual P&L | **realized net** |
|---|---|---|---|---|---|
| 1745z | 0.99, 0.98, 0.95, 0.98 | +0.1412 | 0.69 DOWN | −0.2896 | **−0.1484** |
| 1800z | — | 0.0000 | 1 DOWN | −0.1200 | **−0.1200** |
| 1815z | 1.00 | 0.0000 | 1 UP | −0.4600 | **−0.4600** |
| 1830z | — | 0.0000 | 1 UP | −0.4700 | **−0.4700** |
| 1845z | — | 0.0000 | 1 DOWN | +0.5800 | **+0.5800** |
| 1900z | — | 0.0000 | 1 UP | −0.1900 | **−0.1900** |
| 2000z | 0.93 | +0.0550 | 1 UP | +0.6150 | **+0.6700** |

Campaign C = **−$0.8084**, Campaign D = **+$0.6700**, total **−$0.1384**.

**Two corrections to the earlier document.** 1745z completed **four** pairs,
not three plus a partial: the fourth is UP 0.57 / DOWN 0.41 (`l5f-501a8627`)
at pair **0.98**. I had mis-paired that UP leg with the 0.64-wire DOWN fill
and called it 0.93. So the real population is **six pairs** — 0.99, 0.98,
0.95, 0.98, 1.00, 0.93 — of which **four of six are at or above 0.98**.
Matched settled P&L across all six is **+$0.1962**; strands are
**−$0.3346** net, five losing and two winning.

## 5. l5f-501a8627: resolved, no missing terminal

Submitted 17:50:20 DOWN own 0.4100 (wire 0.5900), `queue_ahead` 1622. Its
ack carried `venue_order_id: null` and the next progress read raised
`HTTPError`; reconciliation at 17:50:22 cleared the uncertainty against the
venue. It then polled `ORDER_STATE_NEW` until 17:52:16 and **filled at
17:52:20**: `L5_FILL_CALIB_FILL` qty 1 @ 0.5900, `L5_FILL_CALIB_FEES_ACTUAL`
fee 0, `L5_FILL_CALIB_TERMINAL` `ORDER_STATE_FILLED` `venue_confirmed: true`.
`L5_ACC_DEADLINE` shows the 120 s TTL cancel raced that fill and lost:
*"filled before the cancel took; booked with its cost basis"*.

So the fill is **fully venue-attested**. My earlier claim of a "missing
terminal lifecycle record" was wrong in substance: the terminal exists, on
the `FILL_CALIB`/`ACC_DEADLINE` path rather than as an `ORDER_PROGRESS`
terminal row. The window's fill reconstruction is trustworthy.

## 6. Counterfactual, stated honestly

A resting order is not a fill, and nothing here counts a book touch as one.

Completing 1815z at pair 0.97 would have **locked +$0.03**. It would *not*
have "avoided −$0.46": that −$0.46 is the realised outcome of a position
whose expected value was approximately zero. At pair cost `p`, completing
and carrying to settlement are both worth `1 − p` in expectation, so
completion buys **certainty, not edge**.

The fix is therefore worth about **+2¢ per completion it unblocks**, plus
the removal of the variance that currently dominates every result: today two
winning strands (+$0.58, +$0.615) masked five losing ones (−$1.53). Across
all seven strands, locking ~2¢ each is ≈ **+$0.14** against the **−$0.3346**
actually realised — but that comparison is an n=7 outcome, driven by which
way BTC moved, and must not be read as the expected gain.

## 6b. The stable resting-quote counterfactual, measured

The engine's 14 intended complements were own 0.50-0.53 against the 0.46
basis, i.e. pair 0.96-0.99. Measured over all 125 recorded books of that
residual's life, asking only **did the touch reach that price** -- never
whether it filled:

| a quote resting at | touch reached it |
|---|---|
| pair 0.96 (own 0.50) | **17 / 125** samples |
| pair 0.97 (own 0.51) | 19 / 125 |
| pair 0.98 (own 0.52) | 21 / 125 |
| pair 0.99 (own 0.53) | 30 / 125 |

**Reached is not filled.** The venue publishes no per-order queue position,
and `queue_ahead_bound` at those 17 moments, against a need of 1 contract,
was: 99, 979, 191, 800, 900, 110, 126, 81, 140, 877, 81, 470, 141, 937,
**1.0**, 869, 68. Only one of the seventeen -- 18:27:55, touch pair 0.82,
queue 1.0 -- sits inside the 10x-of-need multiple
`QUEUE_AHEAD_MAX_MULTIPLE` is calibrated at. On the rest a resting quote
would have been behind hundreds of contracts at its own price.

So the stable-resting-quote counterfactual is **not** "+$0.49 recovered on
1815z". It is: the quote would have existed instead of not existing, it
would have been reachable on about one sample in seven, and on all but one
of those the queue ahead of it makes a fill unlikely.

### What the cheap moments actually were

The late samples (18:26:44 onward) show the pair falling to 0.82-0.94. That
is not the complement getting generous -- it is **our own leg winning**. At
18:26:56, pair 0.87 means the DOWN complement cost 0.41, so the market put
UP's own price at ~0.59 against our 0.46 basis: the leg was ahead by 0.13,
and completing would have locked exactly that 0.13. Mark-to-market and
locked pair agree, as no-arbitrage requires.

That is the general shape, and it holds in both directions -- 2000z's
residual went the other way and carrying it won by 3.5c. **A cheap pair is
the market telling you that you are ahead.** Completing then converts an
unrealised gain into a realised one at the same expectation; it does not
create edge.

Which is why the fix matters operationally rather than in expectation: with
a $5 daily-loss halt and $20 of exposure, a stream of small locked results
survives where a coin-flip portfolio does not. Today two winning strands
(+$0.58, +$0.615) masked five losers (-$1.53) and the campaigns still came
out at -$0.1384. The same positions, completed whenever the pair cleared
the ceiling, would have produced a much narrower distribution around a
slightly positive number -- and would have stopped the engine spending its
only order slot, its decision cadence and its whole residual cap on an
operation that could not place an order at all.

## 7. The k=1/2/3 replay is moot until this ships

Its premise is false. More residual headroom cannot buy more completed pairs
while every completion attempt raises before placing an order — a higher `k`
would only have admitted **more unmatched inventory**, i.e. more of exactly
the exposure that lost the money. Running it now would produce a confident
number about a mechanism that does not exist.

It should be re-run after the fix, in a form where a completion actually
places, and with these two gaps marked in the output: **trade prints are
memory-only and unrecoverable**, so any fill rate is an upper bound from
displayed depth; and **`venue_event_t` is null on all 18 fills**, so fill
latency is unmeasurable and only bracketable.

## 8. The smallest executable change

One contract, applied to the route that lacked it — `l5_live_execution.py`,
controller branch of `handle`:

* coerce `price` and `l5_size` through `X._d`, the same helper
  `FT.l5_native` uses, before anything reads them;
* an unreadable number, or a non-positive size, becomes the **named
  refusal** `PMUS_TRANSLATION / CONTROLLER_DECISION_UNREADABLE:<field>`
  rather than a TypeError or a silent zero;
* nothing else in the mapping is touched, and the L5 route is unchanged.

It is a type fix, not a parameter. It changes no price, size, limit, ceiling,
cap or gate, and it cannot turn a refusal into a submission — it only lets a
refusal be *reached* instead of raising on the way there.

Tests: `tests/test_l5_controller_numeric_contract.py` (25) — the exact
production error string, both routes' contracts, the real `complement_order`
leg asserted permitted and string-typed, coercion ordering before the three
multiplication sites, and that the refusal sits after `prov` exists so it
refuses instead of raising `NameError`. Six of them fail on the
pre-fix tree.

Full-suite parity after both fixes: **80 failing ids, identical to the
baseline set** (`diff` empty; 54 failed + 26 errors, same 80).

Also fixed, reporting-only: `emit_fill_evidence` passed the venue's wire
`avg_px` as `fill_own_px`, so for DOWN fills that field held `1 − own`.
`BE.fill_evidence` falls back to `fill_own_px` for `first_leg_own_px`, which
every `pair_if_*` term is priced from, so a DOWN fill whose pre-submit had
been evicted produced pair arithmetic wrong by `1 − 2p`. Now converted by
side through `TSP.wire_price` (recognised sides only), with `fill_wire_px`
kept beside it. Tests: `tests/test_l5_fill_own_px_convention.py` (35),
parameterised over all 18 real fills. No historical money record is
rewritten.

**Not deployed.** HALT stays asserted; no campaign, cap change, crossing or
capital change is requested here.
