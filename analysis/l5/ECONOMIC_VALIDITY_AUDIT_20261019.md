# Economic-validity audit: do our negative shadow results represent
executable L5-style trading?

**Verdict: not yet. The accounting is sound; the execution assumptions
underneath it are largely unvalidated; and L5's own ledger shows we are
measuring a different business.**

Nothing in production was changed to produce this. The twelve simulations
and the pre-registered one-tick experiment continue unchanged.

---

## 1. Verified accounting

- **The arithmetic is correct and reproducible.** Modelled fills →
  inventory → settlement → net was re-derived independently (by hand, and
  again through the shipped `settle`) and matches production exactly for
  every settled window, including the pair/residual split.
- **Settlement prices are authoritative.** Venue `settlementPx`, terminal
  state required, binary-only. Verified refusing 1800z's `0.9900` on a
  non-terminal state — the exact defect class this venue produced here
  before.
- **The window ledger is complete, with no gaps**: 1700z…1930z at 15-minute
  spacing, 11 windows, 11 settled. Windows before 1700z were discarded in
  the declared v2→v3 discontinuity, not silently dropped.
- **Restart continuity holds.** All 8 pre-restart windows are
  byte-identical after two deploys (0 mismatches). Inventory carried;
  `boot` and `tally_reset` report what was dropped.
- **No fill is granted on a touch or a cancellation.** Confirmed in code
  and in data: one fill (1700z 17:11:57) shows `swept: False`, filling on
  cleared volume alone, so both paths are exercised and distinguishable.

## 2. Validated execution assumptions

- **The inputs are real venue data, not synthetic.** Authenticated ladder
  queue depths span 2 → 16,554 contracts; prints are the venue's own.
- **The conservative rules are conservative in the ways claimed**: a
  shrinking level is not queue advancement, an unknown queue fills only on
  a sweep, a sweep through a known queue fills nothing.
- **Scenario identity is explained, not a bug.** CONSERVATIVE == BASE ==
  OPTIMISTIC in 0 of 24 policy-windows because every queue was known and
  every fill cleared the strictest bar. The corollary stands: the
  conservative column currently carries **no** safety margin.

## 3. Unvalidated assumptions

**(a) The fill model has never been compared against a single one of our
own real fills.** No dataset in this repo pairs our fills with the book
state, queue depth and prints at their timestamps. Every consumer of
`l5_maker_fill_model` is a synthetic unit test or the live shadow. The
central rule — volume must clear `queue_ahead + qty` — is unmeasured
against reality.

**(b) The volume-versus-queue credit is unverified, and a winning window
depends on it.** 1730z's pair-making fill was `COMP NO @0.68` at
`tte=43`, **11 ticks behind the touch**, behind **16,554** contracts,
credited on 16,753 of cleared volume in the final 43 seconds. Whether
aggregate print volume sweeping multiple levels should be credited against
our level is exactly the assumption that cannot be checked without (a).

**(c) Fees: `$0.0000 CONFIRMED` is cross-series extrapolation.** The 293
maker fills are a cohort of **55 non-BTC15m markets** at unknown (larger)
size. The BTC15m ledger labels it CONFIRMED.

**(d) Rebates: the reported `$0.0000 UNCREDITED` is a non-observation, not
a measured zero.** `CONFIRMED_MAKER_REBATES_USD = $2.1300` was
venue-credited across those 293 fills, and L5's own BTC15m ledger contains
**111 MAKER_REBATE, 73 TAKER_REBATE and 20 REWARD** rows. Rebates
demonstrably credit on this venue and this account; we have simply never
observed one at q=1, where any notional-scaled rebate would fall below
measurement resolution.

**(e) The adverse-selection markout has a side-dependent reference bug.**
`ref` is `bid_at_placement` for UP but `ask_at_placement` for DOWN, while
the controller passes `book["bid"]` as the close reference for **both**.
DOWN fills therefore compare an ask to a bid and are biased favourable by
one spread. Verified empirically: 1845z `OPEN NO` reports
`moved_against_us: -0.0100` with wire 0.7300 → 0.7200 — a 1-tick spread
and no real move, scored as favourable. Split by side: UP n=37, 81%
adverse, mean +$0.0546 (like-for-like); DOWN n=32, 59% adverse, mean
+$0.0572 (mismatched, understated by ~1 tick).

**(f) The markout's documented horizon is wrong.** The docstring says
"between placement and window close"; the code receives the bid at the
*fill-detection tick*. Horizon is seconds-to-minutes, not to expiry.

**(g) No modelled order is ever cancelled or re-priced.** Real maker
operation involves cancel/replace, and queue position after a re-quote is
not modelled at all.

**(h) One order per scenario at q=1**, against an L5 that ran far larger.
See §5.

### My own double-count, corrected

`LOSS_DECOMPOSITION_20261019.md` attributed "−$1.26 of ARGMAX's −$2.50" to
adverse selection. **That was double-counting.** The markout is a
short-horizon price move on the same units whose settlement outcome is
already inside `residual_net`. The additive decomposition that is exact is
pair (+$1.2550) versus residual (−$3.7550); the selection figure is a
separate diagnostic, not a slice of that loss. The mechanism claim (we
fill when the market sweeps through us) survives; the attribution of half
the loss to it does not.

## 4. Evidence of genuinely negative expectancy

**Weak, and weaker than I previously implied.**

- 11 windows, 2.75 hours, one asset, one regime. ARGMAX: 2 positive, 9
  negative.
- One of the two positives (1715z, +$0.53) was a **single unmatched YES at
  0.47 that settled at 1.0** — zero matched pairs, pure coin flip that won.
- The pair line is positive in all three policies; the residual line is
  negative in all three. Residual is a near-zero-expectation gamble, so
  its sign over 11 windows is not evidence of anything.
- 5 of 11 windows produced zero matched pairs, and in those the loss
  scales close to linearly with the residual cap (see §5).

**Honest reading: the data show negative outcomes consistent with
approximately zero-edge pair economics plus directional variance, at a
size where frictions dominate. They do not establish negative expectancy.**

## 5. Two structural findings

**The residual cap amplifies, it does not prevent completions.** Replaying
the *same recorded tape* with the cap varied (ARGMAX, CONSERVATIVE):

| cap | total net | behaviour |
|---|---|---|
| 1 | **−$0.62** | 1700z −0.31, 1730z +0.57 |
| 2 | −$1.96 | |
| 3 (live) | **−$2.50** | 1700z +0.05, 1730z +0.29 |

It is not monotone: cap 3 *helped* 1700z (+$0.05 vs −$0.31) by enabling 3
pairs, and *tripled* the loss in the five no-completion windows. The
binding constraint on completion is the **0.96 pair cap against a market
that has moved**, not the residual cap. Caveat: this holds the fill
sequence fixed, so it is a bound on one tape, not a prediction — a
different cap changes what we quote.

**L5 was never running our business.** From L5's own realized BTC15m
ledger (5,533 resolved windows, raw Polygon activity):

| | L5 | our shadow (ARGMAX) |
|---|---|---|
| cash deployed / window | **~$1,097** | **~$0.77** |
| net / window | **+$62.52** (median +$44.66) | −$0.44 |
| return on deployed cash | **+5.70%** | ≈ −57% |
| winning windows | **4,056 / 5,533 = 73.3%** | 2 / 11 |
| rebate / reward rows | 111 maker, 73 taker, 20 reward | 0 |

**A factor of roughly 1,400 in size per window.** At q=1 a one-cent tick
is ~1.3% of a $0.75 position and ~0.001% of a $1,100 one. Half a tick of
captured spread and a sub-resolution rebate are the whole credit side of
our ledger and a rounding error on L5's. This is an observable fact from
L5's data, not an inference.

## 6. The single most important missing fact

> **The realized fill probability and queue-advance rate for a q=1 maker
> order resting at the touch on PMUS BTC15m.**

Every negative number depends on modelled fills from a model that has
never been compared against one real fill of ours. Until that is measured
we cannot distinguish:

- *the strategy loses money at this size*, from
- *the fill model grants us fills a real q=1 order would never receive —
  and grants them precisely when the market sweeps through our level,
  which would manufacture the adverse selection we then measure.*

Scale is **not** the missing fact: §5 measures it. Fill realism is.

The cheapest honest way to obtain it is a bounded place-and-cancel probe
that records our own queue position and whether we filled — exact-order
cancellation is already proven on this account. **That is a live-order
action and is not proposed here; it needs separate authorization.** No
offline substitute exists in this repository.

## What I did not establish

Item 2 as specified — comparing fill-model predictions against actual
historical PMUS maker fills — **cannot be done with available data**, for
the reason in §3(a). Reporting it as "agreement quantified" would have
required inventing the comparison set.
