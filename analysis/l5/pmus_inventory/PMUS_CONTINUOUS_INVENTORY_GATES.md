# What prevents OPEN → EXTEND → COMPLEMENT → EXTEND → LOCK on PMUS BTC15m

Answers from the actual code and authoritative venue evidence. Nothing imported from
the Gemini R2 lane. No unrelated service touched; the SOL executor is not referenced.
State: branch `claude/l5-pmus-economic-results-7c116e`, head `2d950f56`, tree clean.

## First, a correction to my own previous turn

I said PMUS has "one book, so you cannot buy the complement." **The order path does
support a distinct complement order** — `outcomeSide: OUTCOME_SIDE_YES|OUTCOME_SIDE_NO`
is a real field, and `pmus_q1_executor.place_limit_buy` already places opposite-side
BUYs. I was wrong to frame it as unsupported.

What survives, and is the thing that matters economically: the venue serves **one
instrument per window** (`l5_order_body.STATIC_CONTRACT`: *"One binary instrument per
window. Long side pays when the close is at or above the open"*), and a NO order's
`price.value` is wired as the **long-side** price `1 − X`. So holding 10 long at 0.26
and 10 short at 0.73 costs 0.99/share and pays exactly 1.00/share at settlement — a
guaranteed $0.01. Selling the long at 0.27 realizes the identical $0.01. **The
complement is cash-equivalent to the exit**; it differs only in tying up $0.99 of
collateral until settlement instead of freeing it immediately. That equivalence is what
makes "matched inventory" here not a separate profit source.

## 1. Can the executor submit multiple orders in one market after its first fill?

**Two, and only two.** `run_campaign` is a straight line: `place_limit_buy(first_leg)`
→ `wait_for_fill` → complement → exit. Hard bounds are module constants, not knobs:
`QTY = 1` (line 79), `RESIDUAL_CAP = 1` (line 82), and the docstring's *"exactly ONE
campaign per invocation, no auto-restart or scale"*.

## 2. Can it buy both UP and DOWN in the same market?

**Yes.** The complement is an opposite-`outcomeSide` BUY, and the NO wire mapping is
implemented and proven: `wire_px = 1 − price` when the side ends in `NO`, with
`would_cross` correctly treating NO ask as `1 − YES bid`.

## 3. Can it maintain inventory after every fill and immediately decide again?

**No.** There is no inventory object and no decision loop. `run_campaign` carries a
single scalar `first_cost` and returns at each stage. Worse for re-entry: `preflight`
refuses outright if we already hold the market —
`_position_qty(pos, slug) != 0 → "existing_position_in_target_market"` (line 521) — and
again on `open_orders_present` (line 527). So a second invocation against a market where
inventory exists is rejected before any order.

## 4. The exact conditions preventing EXTEND

Six, all independent — each alone blocks it:

1. `QTY = 1` — every leg is one contract.
2. `RESIDUAL_CAP = 1` — a second same-side buy breaches the residual bound.
3. `CAMPAIGN_RISK_CAP` $1.00, structurally enforced by `entry_ok` (first leg ≤ 0.80)
   and `complement_bound` (pair ≤ 0.99).
4. One `run_campaign` per invocation, straight-line control flow, no loop.
5. `preflight` requires a **flat position** in the target market.
6. `preflight` requires **no open orders**.

## 5. THE ARCHITECTURAL GAP: there is no WS→order path at all

`grep -c "websocket|ws_evidence|/v1/ws/" src/pmus_q1_executor.py` → **0**.

The executor's decision source is **REST polling** of `/v1/markets/{slug}/bbo`
(`fresh_bbo`). The WebSocket book lives in the read-only recorder, which by design
imports no order capability. So "use the WS book as the live decision source" is not a
gate to relax — **that wiring does not exist**. This is the single largest piece of
missing machinery for continuous in-window management: REST BBO polling cannot drive
decisions at the ~5–10 Hz the book actually moves.

## 6. The 10 YES @ .26 → NO @ .73 sequence

**I cannot locate that sequence in this repository's execution evidence.** Our live
executor is `QTY = 1`; a 10-share fill cannot have come from it. Searches of
`data/`, probe state and session journals surface only L5's own normalized trader fills
and censuses. **I need the session id or date to attribute it rather than guess.**

What *is* documented, and is the known NO-side failure: on **2026-09-05/06, 24 of 24**
NO-side post-only orders priced in NO terms were rejected, because the venue read them
as NO at `1 − X`, which crosses (`l5_order_body` docstring).

Mechanically, a NO buy at 0.73 with YES ask 0.26 implies YES bid ≤ 0.25, so NO ask is
`1 − 0.25 = 0.75`. A post-only NO BUY at 0.73 therefore rests **two ticks behind the
NO touch** — it is not rejected and not crossing; it simply fills only if the market
comes to it. That is the structural reason maker-only complements often never fill, and
it is independent of the pricing bug.

## 7. Is that failure still present?

**The pricing bug: no, fixed in both places** — `l5_order_body.venue_price()` and
`pmus_q1_executor.place_limit_buy`'s `wire_px`.

**The behind-the-touch maker complement: yes, inherent.** `place_limit_buy` is
post-only (`participateDontInitiate: True`) with no taker path, so any complement priced
better than the touch waits on the market.

## 8. Current maker/rebate rules — authoritative

- **Taker θ = 0.0695** per `p(1−p)` per share. Confirmed twice: live market object
  `feeCoefficient: 0.0695`, and `src/pmus_fees.py` reproduces all 99 rows of the
  published `docs.polymarket.us/fees` table with banker's rounding (effective
  2026-09-17; previously 0.06).
- **Maker rebate = 0.0125·p(1−p)**, credited at fill, tracked as income, never netted
  against edge.
- **Taker rebate: not ours** — tiers start at $250,000 prior-month taker volume.
- **No liquidity-incentive program on BTC15m.** The complete `category=CRY` listing is
  **54 markets on one program** (`crypto_20260924`, $400/day pool, targetSize 15,000,
  df 0.3) and **none is an up/down market**.
- Executor **docstring** still says `0.06` (line 25) while the **code** correctly calls
  `_fees.taker_fee`. Stale comment, not a live bug — worth fixing so it cannot mislead.

The rebate is ~18% of the taker fee; at p = 0.5 it is $0.0031/share.

## 9. The tape question (Q8), answered by identity rather than another replay

Because a long+short pair at total cost `C` pays exactly 1.00/share at settlement,
"repeated alternating purchases producing positive matched inventory" is **cash-
identical to profitable round trips on one instrument**. That is already measured on
gate-passing, duplicate-free, cap-uncensored data: **11 windows, 2,401 first legs,
cumulative −$637.05, −$0.22 per leg**, with every variant negative at useful capacity.
And "completed pairs never lose" is selection, not edge — a completed pair *is* a
profitable round trip by construction.

So the honest answer to Q8 is that the favourable case exists but is not reliably
reachable: the measured distribution of these round trips is negative.

## 10. Smallest code change for OPEN + one EXTEND/COMPLEMENT

Not implemented here — design only, as asked. Smallest viable set, all inside
`pmus_q1_executor.py` plus one new consumer:

1. **Replace the two blocking preflight checks with bounded tolerances**, not deletions:
   `existing_position_in_target_market` → allow when `|position| ≤ MAX_RESIDUAL`;
   `open_orders_present` → allow when every open order is ours in this slug and within
   the campaign's order budget. (~10 lines)
2. **Add an inventory record and a decision loop.** Replace the straight line with
   `state = {up_qty, dn_qty, up_cost, dn_cost}` and a `while clock() < stop_at` loop
   that, after each authoritative fill, re-evaluates both sides. (~60 lines)
3. **Raise exactly three constants into env-gated bounds** with fail-closed defaults
   equal to today's values: `QTY`, `RESIDUAL_CAP`, `CAMPAIGN_RISK_CAP`. Defaults
   unchanged means arming nothing by accident.
4. **Feed it the WS book.** A small read-only subscriber that writes latest
   `bid/ask/bid_qty/ask_qty` per slug to a file or socket the executor reads, so the
   recorder keeps its no-order-path guarantee and the executor stops polling REST.
   (~80 lines, new file)
5. **An EXTEND predicate that cannot mean "average down."** Per the standing rule: an
   extension is admissible only if the *new unit alone* clears its own bound —
   `expected_exit − price − fees > 0` computed without reference to existing cost
   basis. Reject any rule whose justification is the blended average.

## 11. Bounded live-probe design

One market, one window, dual-key armed as today. `QTY = 1`, `MAX_RESIDUAL = 2`,
`CAMPAIGN_RISK_CAP = $2.00`, order budget 4 submissions, maker-first with a single
late taker completion under the existing all-in rule, deadman cancel-all on every exit
path, `$1.00` daily realized-loss stop unchanged, no auto-repeat after completion or
crash. Success is **not** profit: it is evidence that OPEN → one EXTEND or COMPLEMENT
executed with authoritative fills and correct inventory accounting through a live
window.

**One caveat, stated once and not relitigated:** on this venue EXTEND adds directional
risk with no complement-based path to positive expectancy, and the measured expectancy
of the round trips it produces is negative. I would not arm this to chase P&L. It is
defensible only as a **mechanism test** — proving the machinery can manage inventory
continuously — with the economics judged separately afterwards.
