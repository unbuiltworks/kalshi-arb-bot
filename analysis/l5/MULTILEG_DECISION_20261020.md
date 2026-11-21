# Multi-leg BTC15m: decision memo

Evidence base: the 2026-10-19 2315z live fill (order-ID-linked capture), and
the FINALIZED two-leg simulator output for 2315z and 2330z with settlement
known. Nothing here has been deployed and no limit has been changed.

---

## 1. What the October 19 fill actually invalidates

The recorder captured the submission state of the filled order
(`l5f-1d34e72f9f4f30f390ff718f`, venue `D6XA5J4G10JH`):

```
own_px 0.89   resting BID   qty 0.31
book   bid 0.9300 / ask 0.9400   frame age 0.065s   feed authenticated_ws
queue_ahead 12959.03      level_depth 604.00       stale false
```

The fill rules in `l5_maker_fill_model`:

* `CONSERVATIVE` fills when `cleared >= queue_ahead + our qty` -> needed
  **12,959.34** shares of direction-correct volume.
* `BASE` fills when `cleared >= queue_ahead` -> needed **12,959.03**.
* `OPTIMISTIC` fills on a sweep -- "a print strictly THROUGH our price".

The order rested ~90 seconds. The whole 15-minute window consumed 2,228
prints. Direction-correct volume through 0.89 in 90s was two or three orders
of magnitude below 12,959.

**CONSERVATIVE and BASE would both have rejected this fill. OPTIMISTIC alone
would have predicted it.** The fill was real, venue-confirmed, and settled.

### The precise modelling error

`Ladder.ahead_of_us` is documented as "everything that would trade BEFORE us:
better-priced rungs plus our own level. Price priority first, then time
priority at our rung." Summing better-priced rungs into a *queue* is the
error. Those orders are not ahead of us in any queue -- they are at different
prices. When the market falls to our rung, the bids at 0.93/0.92/0.91/0.90
are **withdrawn or repriced**, not consumed. The model's own guard, "a
cancellation ahead of us is never progress", is correct within our rung and
wrong when applied across rungs, because across rungs cancellation *is* the
mechanism by which price reaches us.

**Invalidated:** *"a bid below the touch fills only after the aggregate depth
at all better prices has been consumed by direction-correct trade volume."*
The correct statement: a bid below the touch fills when price arrives at it,
and price arrives by quote withdrawal, which trade volume does not measure.

**NOT invalidated, and still sound:** within-rung FIFO; "a touch is never a
fill"; "unknown queue means unknown fill, not a fill". And this fill was
adversely selected -- we bought UP at 0.89 while the bid was 0.93, i.e. the
market came down four ticks to reach us. Deep bids fill *because* the price
is moving against them. That is a cost, not a free option.

**Direction of the correction:** conservative/base systematically UNDER-fill
resting orders below the touch. For a *complement* leg that is the pessimistic
direction, so correcting it should make two-leg economics look better, not
worse. It also means residual estimates built on the same model are unreliable
in both directions.

---

## 2. Does the data support profitable sequential multi-leg trading?

FINALIZED cells, $1 envelope, settlement known, modelled fills:

| window | policy | pair cap | up | dn | matched | residual | matched P&L | residual P&L | **net** |
|---|---|---|---|---|---|---|---|---|---|
| 2315z | COMPLEMENT_ONLY | 0.99 | 1.04 | 1.04 | 1.04 | 0.00 | +0.0413 | 0 | **+0.0413** |
| 2315z | SKEWED | 0.99 | 1.01 | 1.44 | 1.01 | -0.43 | +0.0404 | -0.0301 | **+0.0103** |
| 2330z | COMPLEMENT_ONLY | 0.98 | 1.02 | 1.02 | 1.02 | 0.00 | +0.0204 | 0 | **+0.0204** |
| 2330z | COMPLEMENT_ONLY | 0.99 | 1.01 | 1.01 | 1.01 | 0.00 | +0.0101 | 0 | **+0.0101** |
| 2330z | SKEWED | 0.99 | 1.01 | 1.42 | 1.01 | -0.41 | +0.1270 | -0.1166 | **+0.0104** |

Three findings, all mechanical rather than fitted:

**(a) Pair cost 0.96 and 0.97 are unreachable.** `eligible_ticks: 0` in every
window at every envelope. The engine never quotes, never fills, never earns.
The deployed `BTC15M_PAIR_CAP = 0.96` is therefore not a conservative setting
-- it is an off switch, and it explains the flat/negative deployed results
directly.

**(b) The tighter cap does not merely trade less, it trades ASYMMETRICALLY.**
In 2345z at cap 0.98: `up 1.02, dn 0, matched 0, residual 1.02` -- the first
leg filled and the complement never did, leaving stranded directional
inventory. At cap 0.99 the same window completed: `matched 1.04, residual 0`.
A tight cap selects for exactly the windows where the complement is
unavailable, which is the worst possible selection.

**(c) COMPLEMENT_ONLY dominates SKEWED.** Mean net +0.0239 vs +0.0103, and
COMPLEMENT_ONLY carried **zero** residual in all three cells while SKEWED
carried -0.41 to -0.43. SKEWED's larger matched gain (+0.1270) was almost
exactly cancelled by its residual loss (-0.1166). Paying for directional
exposure bought nothing.

**Answer: qualified yes, but only at pair costs of 0.98-0.99, and the
profitability is small and rebate-dependent.** n=2 finalized windows; this is
a direction, not an expectancy estimate.

---

## 3. Recommended rules

**Entry.** One maker leg at or one tick behind the best bid on whichever side
is cheaper, inside the venue placement band, sized at the venue minimum
multiple that fits the envelope. Never cross. Never place after T+8:00.

**Complement.** Quote the opposite side as soon as the first leg fills, at a
price that makes the combined fee-safe pair cost `<= 0.99`. Re-quote on touch
moves. Do not widen the cap mid-window and do not chase with a taker order.

**Inventory management.** COMPLEMENT_ONLY only -- no deliberate skew. Hard
residual bound of one unit. If the complement has not filled by T+12:00,
cancel by exact venue id and carry the residual to settlement rather than
crossing to flatten; the Oct 19 window shows a crossed exit pays the taker fee
against a 1c gross and is strictly worse.

**Do not** raise the cap above 0.99, add a third leg, or run concurrent
markets.

---

## 4. Expected net economics, including the bad windows

Per matched pair, 1 unit per leg, maker both sides, from the published
schedule:

| pair cost | gross | maker rebate | net with rebate | **rebate as % of gross** |
|---|---|---|---|---|
| 0.99 | $0.0100 | $0.0062 | $0.0162 | **62%** |
| 0.98 | $0.0200 | $0.0062 | $0.0262 | 31% |
| 0.96 | $0.0400 | $0.0062 | $0.0462 | 16% |

Observed COMPLEMENT_ONLY net per window on ~$1 deployed: **+$0.0101 to
+$0.0413, mean +$0.0239**, with zero residual in the completed windows.
Incomplete windows strand ~1 unit of directional exposure whose settlement
outcome is a coin flip on roughly $0.50 of basis -- one stranded leg losing
costs about **12 to 25 completed windows** of gain at these margins.

**This is the decisive number: at a 0.99 pair cap the maker rebate is 62% of
the entire gross, and the rebate has never been observed credited.** The
venue reported `commission: 0.0000` on the Oct 19 fill, and as the system's
own label states, a zero commission is not a rebate. If the rebate is not
actually credited, near-par pair trading nets ~1c per $1 pair and the first
stranded leg erases a day of it.

**$480/day is not reachable on this mechanism at the authorized size.** At
+$0.0239/window and 96 windows/day the ceiling is ~$2.30/day per $1 deployed.
Reaching the target needs ~$200 of capital working per window at the same
efficiency, and the simulator's fills at that scale rest on the queue model
the Oct 19 fill just showed to be wrong.

---

## 5. Infrastructure readiness and required authorization

**Ready, with no new code.** `l5_two_sided_probe` already provides exactly
what is needed:

* **Two named submission slots** -- `probe_leg_sid(exp, leg) = "ts1.<exp>.<leg>"`
  over `PROBE_LEGS = ("UP","DOWN")`. The module's own comment: "Two named
  slots give each leg its own one-shot claim instead of loosening the
  single-submission rule for everything else." Two legs, two claims, no more.
* **Inert by default** -- `active()` is false unless both `L5_TS_EXPERIMENT_ID`
  and `L5_TS_MARKET` are named and the slug matches; no partial arming.
* **Timing already coherent with the watchdog** -- `PLACE_OFFSET_S = 480`
  (nothing placed after T+8:00), `CANCEL_MARGIN_S = 180` (nothing rests past
  T+12:00), `MIN_REST_S = 60`, `MAX_BOOK_AGE_S = 2.0`. Max rest 240s, inside
  the watchdog's 300s with 60s to spare.
* **Completion before deadline** -- `maybe_ts_completion` runs before
  `maybe_ts_deadline`, so the complement decision happens while leg one is
  still resting.
* Existing exact-ID cancellation, independent watchdog, reconciliation,
  $5/market, $5 total, $5 daily realized-loss halt, all unchanged.

**One policy change is unavoidable and it is yours to make.** The engine
cannot trade at all under `BTC15M_PAIR_CAP = 0.96`, and `L5_OPERATING_MODEL`
locks near-par tolerance "unless equivalent economics are source-confirmed for
our account". The Oct 19 fill plus the published schedule source-confirm the
*fee* side (no maker fee, confirmed `commission: 0.0000` on a real fill). They
do **not** confirm the rebate, which is 62% of the economics at 0.99.

**Therefore the recommended next live test is a rebate-confirmation test, not
a profitability test:** one BTC15m window, two legs, pair cap 0.99, $5 total
exposure, whose success criterion is a venue-credited maker rebate appearing
as an actual cashflow on both legs. If it is credited, the strategy has a
thin but real edge worth scaling study. If it is not, near-par pair trading is
not viable and we should stop pursuing it.

Authorization required: (1) raise `BTC15M_PAIR_CAP` to 0.99 for this bounded
test only; (2) a fresh `L5_TS_EXPERIMENT_ID` and exact-slug `L5_TS_MARKET`;
(3) a fresh `L5_LIVE_AUTH_NOT_AFTER` and the owner arm handshake, as before.
No new service, no new executor, no raised risk limit.
