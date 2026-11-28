# Complement adoption: already implemented, and what actually blocks completion

Status: **authorized change investigated and found already present**. Three
unverified preconditions closed, 48 deterministic tests added, no ceiling
relaxed, nothing promoted to live.

## 1. The path the authorization describes already exists

`src/l5_live_execution.py` (`FaithfulExecutor`, the two-sided lifecycle)
has been doing this since before either campaign. When a round holds
filled quantity on exactly one leg:

```python
_other = "DOWN" if nxt == "UP" else "UP"
_held  = self.store.position(market, _other)
_mine  = self.store.position(market, nxt)
_need  = _held - _mine                 # the UNMATCHED remainder
if _need > D(0):
    _basis = self.store.leg_cost(market).get(_other)
    _avg   = _basis / _held            # the cost we REALLY paid
    comp   = TS.complement_order(side=nxt, qty=_need, cost_px=_avg,
                                 bid=..., ask=..., book_age_s=...)
...
else:
    leg = dict(comp["leg"])            # the frozen plan is SUPERSEDED
```

and it records `provenance: "DERIVED_FROM_CURRENT_BOOK"` with
`frozen_price_superseded` beside it. The frozen plan is not re-proposed;
it is replaced.

### Measured coverage, ten preserved BTC15m windows

`analysis/l5/residual_exit/preserved/*.ndjson`, 2026-10-24 0100z–0230z and
2026-10-26 2100z–2130z:

| | count |
|---|---|
| ticks with an unexecutable frozen plan (`LEG_WOULD_CROSS`) | **231** |
| …of which `retirement_blocked_by: LEG_HOLDS_INVENTORY` | 224 |
| …that reached the complement block | **143** |
| …that evaluated the complement | **143 of 143** |
| …that could not reach it | 88 |

The 88 break down as **51 `PAST_COMPLETE_CUTOFF`**, **30
`PAST_TERMINAL`**, **6 `MAX_LIVE_ORDERS`**, 1 already submitted. None was
blocked by a stale plan. There is no tick in the record where a round held
one leg, the window was still open, an order slot was free, and the
complement went unevaluated.

**So the answer to requirement 7 is: zero previously refused decisions
become newly actionable orders, because the path being authorized is not
missing.** My earlier summary of this (`LEG_WOULD_CROSS` as the refusal,
a constant `pair_cost 0.9500` as the complement's verdict) was wrong on
both counts and is corrected here: `0.9500` is the *planned opening* pair
in the `TWO_SIDED_PROBE` stage, and the refusal attached to it is
`PAIR_UP:CASH_COMMITMENT`, not `LEG_WOULD_CROSS`.

## 2. What the complement actually decided

163 evaluations:

| outcome | n | pair cost |
|---|---|---|
| **adopted** (`DERIVED_FROM_CURRENT_BOOK`) | **7** | 0.85 – 0.96, edge 0.04 – 0.15 |
| **`COMPLEMENT_PAIR_ABOVE_CAP`** | **156** | median **1.16** |

Distribution of the 156, against `max_pair_cost 0.96`:

| pair cost | n | what completing would lock in |
|---|---|---|
| ≤ 0.98 | 11 | 2–4¢ profit, refused by the 0.96 ceiling |
| 0.98 – 1.00 | 3 | ~breakeven before fees |
| 1.00 – 1.05 | 8 | 0–5¢ **loss** per contract |
| **≥ 1.05** | **134** | **≥ 5¢ loss per contract, guaranteed** |

A matched UP+DOWN pair redeems exactly $1.00. 142 of 156 (91%) refusals
were at or above $1.00 — completing them converts stranded inventory into
a *certain* loss, not an uncertain one. Requirement 3 preserves the
pair-cost ceiling, so none of the 156 becomes an order under this change,
and 142 of them should never become one at any ceiling.

Only the 11 at ≤0.98 are refused by policy rather than by arithmetic, and
they sit above a 0.96 cap I am instructed to preserve.

### The shape of the 156 is adverse selection, not a stale price

Representative: first leg filled at **0.24**, complement then priced
0.73 → 0.77 → 0.82 on successive ticks; 2115z filled at **0.4016** and the
complement stood at 0.78 (pair 1.18). The cheap resting leg fills
*precisely when* the market is moving toward it, and by the time it is
filled the other side has repriced past completion. That is an
entry-selection problem. Repricing the complement cannot fix it, because
the complement is already priced correctly — it is the first leg's fill
that was selected against.

## 3. The three preconditions that were NOT verified, now closed

Requirement 2 lists eight things to verify before adoption.
`complement_order` already checked five of them and derived two more:

| requirement | before | where |
|---|---|---|
| opposite side | side *validity* only | `COMPLEMENT_SIDE_UNKNOWN` |
| actual filled quantity | ✅ | `qty=_need`, floored to the venue grid |
| executable maker price | ✅ | post-only, no-cross assert, tick grid, price band |
| **available book depth** | ❌ **unchecked** | — |
| quote freshness | ✅ | `BOOK_STALE` (2.0s), `gap_suspected` |
| realistic fees | ✅ | `fee_floor`, maker/taker θ, both-outcome positivity |
| actual inventory cost | ✅ | `leg_cost / position`, from the durable ledger |
| **correct market** | ❌ **implied, never asserted** | — |

Added, all refuse-only:

* **`COMPLEMENT_DEPTH_INSUFFICIENT`** — the side the maker leg would
  *rest* on must display at least the quantity being placed. `resting_side`
  is BID for UP and ASK for DOWN; the defensive unwind is a taker and
  reads the opposite side, so the two are now selected by separate
  helpers and a test pins the direction. A touch can be set by one
  resting contract, and completing against it hands the realised cost of
  leg one to a quote that cannot absorb leg two. Unreadable depth is not a
  refusal (the deployed caller passed none, and failing closed there would
  strand inventory the engine completes today); zero and negative depth
  **are** refusals, because they are readings, not absences.
* **`COMPLEMENT_NOT_OPPOSITE_SIDE` / `COMPLEMENT_HELD_SIDE_UNKNOWN`** —
  the complement side is asserted against the side actually held.
* **`COMPLEMENT_MARKET_MISMATCH` / `COMPLEMENT_SIDE_MISMATCH`** — the
  market the complement was priced from is carried on the result and
  checked at the adoption site before the leg replaces the plan.

Neither identity guard can fire from the deployed call site, which passes
`market` and `_other` from the same variables a few lines above. That is
why they are asserted: an adoption that silently matched the wrong
market's book, or doubled the side already held, would look exactly like a
correct one in the record.

**The depth check refuses none of the seven real adoptions.** Displayed
depth on the resting side ran **96x to 4097x** the complement quantity.
It is inert at the size we trade and binds at a size we do not yet trade.

Requirement 4's other two clauses were already satisfied and are
unchanged: one outstanding order is enforced by `MAX_LIVE_ORDERS` (it
refused 6 of the 88), and the adoption path reads resting orders from the
durable ledger rather than bypassing reconciliation.

## 4. What is actually binding, in order

1. **Entry is ~100s late.** First decision landed at **+79s to +98s**
   across 0100z–0230z. Root-caused and fixed separately (`a1b11967`): the
   settlement sweep's 86 serial reads at 1.5 rps. 81 of the 88
   complement-unreachable ticks died on `PAST_COMPLETE_CUTOFF` /
   `PAST_TERMINAL` at ~+610s — those ticks get more chances with earlier
   entry, though whether a completable pair *appears* is a price question,
   not a time question, and the 156 above-cap refusals say the price
   mostly did not come.
2. **Capital.** 65 refusals of `PAIR_UP:CASH_COMMITMENT` (e.g.
   `2.6449 > 1.65`): new first legs refused because the round's obligation
   exceeds `env_per_round`. Sequential replay puts the ceiling at
   **$0.789/window** at `allocs=4`, $0.947 at 8.
3. **Adverse selection on the first leg**, as above. This is the one with
   no fix in hand and the largest claim on the remaining gap to $1/window.

## 5. What is NOT claimed

No fill, no profit, and no rebate. The seven adoptions are the only
complements that became orders in the record; whether each *filled* is a
separate question the order records answer, and nothing here counts a
displayed quote, a priced complement or a hypothetical order as a fill.
This change produces **zero** additional actionable orders on the recorded
evidence — it removes three ways a future adoption could be wrong.

## 6. Smallest bounded live validation

What would establish whether complement adoption increases completed pairs
and settled net profitability — **not authorized, proposed only**:

* **Precondition**: promote `a1b11967` (sweep cap) first and set
  `PMUS_SETTLEMENT_SWEEP_CAP=4`. Without it, entry is ~100s late and the
  experiment measures the delay rather than the completion logic.
* **Scope**: one market, BTC15m, **4 consecutive windows**, existing
  `$20` exposure ceiling, `$5` daily-loss halt, `$12` cash envelope,
  `round_allocations` unchanged, maker-only, one outstanding order.
* **Hypothesis, pre-registered**: with entry at ≤+10s instead of +85s,
  completed pairs per window rise from the campaign mean of **1.98
  matched contracts** and the complement adoption rate rises from
  **7/163 = 4.3%**.
* **Primary endpoint**: settled net P&L per window, from
  `retire_settled_market` on a venue-terminal state with a binary
  `settlementPx` — settlement-only labelling, no mark-to-market.
* **Secondary, pre-specified**: adoption rate; pair-cost distribution of
  refusals (does the ≥1.05 mass shrink?); matched contracts; stranded
  residual at settlement; `PAST_COMPLETE_CUTOFF` count, which should fall
  to ~0 if the sweep fix works.
* **Stop rules**: halt on the first window with settled net < −$1.25, on
  any `L5_LIVE_ERROR` in the money path, on any complement adopted at
  pair cost > 0.96, or on cumulative −$5.
* **Decision rule**: 4 clean windows with settled net ≥ +$0.50/window and
  ≥ 3 completed pairs/window justifies extending to 8; anything less
  returns to replay.

This needs a separate safety review and explicit authorization. It is not
started.
