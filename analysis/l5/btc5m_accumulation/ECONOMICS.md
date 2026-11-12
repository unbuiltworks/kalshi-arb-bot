> **SUPERSEDED in part.** The headline figures below are the 142-window tape. See `CALIBRATION_FINDINGS.md` for the 187-window results and the probability calibration.

# BTC5m favorite accumulation — diagnosis, and the measured economics

Date: 2026-11-12. Service `l5-btc5m-live` (project `arb-ev-wallet-shadow`).
Deployed SHA: `ee5f4e8027cedfe5cb6301711e215f3397a42ee4`
(branch `integration/l5-safe-release-candidate-20261109`, deployment
`69acea6a-c051-4c05-baf5-7dd3ea64c755`, 2026-11-12 08:05Z, SUCCESS).

**Nothing here is realized money.** No order of this strategy has ever
filled. The economics below are the shipped controller replayed over 12
hours of recorded public PMUS books.

## THE HEADLINE

The mechanism was genuinely broken and is now fixed: the policy places
**8.7 entries a window** where the live service placed **one order in four
armed windows**.

The policy as specified **loses money** on the recorded tape. Over 142
resolved windows, net is **-$0.021 a window** and **-$0.0358 a share**
(-$2.98 in total). **No price-band floor and no clip size tested is
positive.** The cause is measured, not assumed: a passive buy of the
favorite at its own bid is filled by flow that knows more than the price
does, and that costs **3.65 cents a share** -- the favorite wins 60.8% of
the time against an average price paid of 64.5 cents.

**Recommendation: merge the mechanism fix disarmed; do not arm this policy.**

---

## A. CURRENT RUNNING STATE

| | |
|---|---|
| service | `l5-btc5m-live`, restart policy NEVER, one approved window then idle |
| deployed SHA | `ee5f4e80` |
| state now | **idle, `market: null`** -- no window configured, nothing armed |
| controller entry rule on the service | `EDGE_GATED` (`L5_BTC5M_ENTRY_MODE` is not set) |
| aggregate exposure ceiling | **$0.45**, unchanged by this work |
| realized P&L, this strategy, ever | **$0.00 -- no fill has ever occurred** |

## B. WHY THE OVERNIGHT CAMPAIGN PRODUCED ONE ORDER

From the service's own `L5_BTC5M_START` / `SESSION_REPORT` / `STEP`
records, 2026-11-12:

| window | preflight | armed | submitted | filled | what stopped it |
|---|---|---|---|---|---|
| 03:45z | FAILED | no | 0 | 0 | `cryptography` / `websockets` absent from the image; every authenticated call raised `ModuleNotFoundError`. 3 decisions recorded `would_submit`, nothing sent |
| 05:00z | PASSED | yes | **1** | 0 | the one order of the campaign; cancelled at the close, never filled. Most steps had `bid: null` -- a one-sided book |
| 06:30z | PASSED | yes | 0 | 0 | **every step `EV_BELOW_MIN`** with `room_usd` about $0.41 of the $0.45 cap unused |
| 08:00z | PASSED | yes | 0 | 0 | same |

Six causes, in order of throughput cost:

1. **The edge gate was arithmetically unreachable** (primary). An entry was
   admitted only when `p_side - price - 0.06 >= 0.01`. The entry rested
   `depth_ticks = 2` under the favorite's bid and `p_side` came from the
   book mid, so on a one-cent book the test reduces to
   `half_spread + 0.02 >= 0.07` -- the entry had to sit about **seven
   cents** under the bid. A 5-minute PMUS book does not offer that. The
   06:30z record shows it exactly: entry priced 0.68, qty 0.14, room
   $0.4080, refused.
2. **One window per deployment.** The runner trades the single window named
   in `L5_BTC5M_MARKET` and then idles for ever. The "overnight campaign"
   was four windows, three armed -- not a night of 5-minute windows.
3. **One entry at a time.** `ENTRY_ALREADY_RESTING` vetoed a second entry
   with $0.35 of the cap free.
4. **Resting two ticks under the bid** -- behind the entire visible queue in
   a 300-second window.
5. **The eligible band was judged on the touch, not the price paid.** A
   touch of 0.52 paid 0.50 and was admitted; a touch of 0.91 paid 0.89 and
   was refused.
6. **A second identical clip hashed to the same client order id** and was
   refused `DUPLICATE_INTENT`. Found by the new acceptance tests, not by
   reading; accumulation could not have worked without fixing it.

Causes 1 and 3-6 are fixed. Cause 2 is untouched: extending one window into
recurring trading is not a code decision.

## C. WHAT CHANGED

`src/l5_btc5m_controller.py` gains a second entry rule, `ACCUMULATE`,
selected by `L5_BTC5M_ENTRY_MODE`. Unset -- the shipped default -- keeps
`EDGE_GATED` unchanged, and the 506 pre-existing BTC5m tests pass
untouched. In `ACCUMULATE`:

* the entry joins the favorite's own bid (`accumulate_depth_ticks = 0`);
* eligibility is `$0.51 <= price <= $0.90` **inclusive, on the price paid**;
* there is **no edge gate**; `ev_per_share` is still computed and reported
  on every decision as evidence and blocks nothing;
* up to `max_resting_entries` clips rest at once;
* a resting entry is requoted on **any** tick of drift from the bid;
* `hedge_enabled = False`, `reserve_mode = "NONE"`: no completing order, no
  pair formation, no L5 replication, and the whole $0.45 is available to
  entries;
* adding to a side stops the moment it is no longer the favorite.

Unchanged and still binding in both modes: the $0.45 aggregate ceiling, the
account-wide cap, both shared halts, the watchdog halt record, startup
reconciliation, the arm token, the per-order authorization claim.
Maker-only; taker disabled.

## D. THE MEASURED ECONOMICS

**Method.** The real `l5_btc5m_controller.decide` in `ACCUMULATE`, $0.45
cap, $0.10 clip, stepped every 5 s to the 270 s entry deadline, over **144
recorded PMUS BTC5m windows (00:00Z-11:55Z, 2026-11-12, 20,900 public
quotes** from service `l5-btc5m-public-recorder`); **142 resolved** from the
venue's own terminal quote. Outcomes are balanced: **65 UP, 77 DOWN**.
Script `replay.py`, output `replay_output_20261112.txt`, tape
`pmus_btc5m_quotes_20261112.jsonl`.

**Fills are bounded, not estimated.** The tape is quotes only -- no prints,
no depth -- so a maker fill cannot be observed. Two rules, reported side by
side and never averaged: **ADVERSE** (filled when the side's own bid falls
*below* our price -- we are filled as what we bought gets cheaper) and
**FAVOURED** (ADVERSE plus filled when the bid *stays* at our price 10 s,
assuming front of queue; an explicit upper bound).

**Fees.** Maker commission is **zero**, read back from the venue
(`commissionNotionalTotalCollected` = 0.0000 on every order of the overnight
campaign), not assumed. Carried through the arithmetic anyway.

### Net by entry-price bucket (ADVERSE, 142 windows)

| price paid | fills | shares | paid $ | win rate | breakeven | adv. sel./share | net $ |
|---|---|---|---|---|---|---|---|
| 0.51-0.54 | 90 | 16.71 | 8.7477 | 0.446 | 0.524 | -0.0771 | **-1.2877** |
| 0.55-0.59 | 91 | 15.57 | 8.8214 | 0.514 | 0.567 | -0.0528 | -0.8214 |
| 0.60-0.64 | 88 | 13.78 | 8.5134 | 0.612 | 0.618 | -0.0053 | -0.0734 |
| 0.65-0.69 | 73 | 10.55 | 7.0565 | 0.699 | 0.669 | +0.0297 | +0.3135 |
| 0.70-0.74 | 61 | 8.19 | 5.8879 | 0.741 | 0.719 | +0.0222 | +0.1821 |
| 0.75-0.79 | 54 | 6.74 | 5.1753 | 0.610 | 0.768 | -0.1581 | -1.0653 |
| 0.80-0.84 | 48 | 5.69 | 4.6404 | 0.852 | 0.816 | +0.0368 | +0.2096 |
| 0.85-0.89 | 37 | 4.07 | 3.5376 | 0.784 | 0.869 | -0.0854 | -0.3476 |
| 0.90 | 2 | 0.22 | 0.1980 | 0.500 | 0.900 | -0.4000 | -0.0880 |
| **all** | **544** | **81.52** | **52.5782** | **0.608** | **0.645** | **-0.0365** | **-2.9782** |

Aggregated into wider bands, with standard errors:

| band | fills | net/share | s.e. | t |
|---|---|---|---|---|
| 0.51-0.59 | 181 | -0.0649 | 0.0372 | -1.75 |
| 0.60-0.69 | 161 | +0.0108 | 0.0374 | +0.29 |
| 0.70-0.79 | 115 | -0.0641 | 0.0442 | -1.45 |
| 0.80-0.90 | 87 | -0.0244 | 0.0422 | -0.58 |

**No band is significantly positive.** The only positive point estimate
(0.60-0.69) has t = +0.29.

### Totals and uncertainty

| quantity | estimate | s.e. | t | 95% CI |
|---|---|---|---|---|
| net per window | **-$0.0210** | 0.0183 | -1.15 | [-$0.057, +$0.015] |
| net per filled share | **-$0.0358** | 0.0202 | -1.77 | [-$0.075, +$0.004] |

Median per window is **+$0.070** while the mean is **-$0.021**: 78 winning
windows against 62 losing ones, with the losses larger. That left skew is
the signature of collecting small amounts and occasionally paying a lot --
what a passive bid on a directional instrument earns.

### Throughput and capital

| | |
|---|---|
| entries placed | 1,256 over 142 windows -- **8.7 a window**, against 1 order in 4 armed windows live |
| fill rate | 43.3% |
| binding constraint | `NO_EXPOSURE_ROOM` on 2,125 decisions -- the **cap**, not the opportunity |
| other refusals | `OUT_OF_BAND` 941, `BOOK_UNUSABLE` 1,018, `BOOK_STALE` 83, `MAX_RESTING_ENTRIES` 10 |

`BOOK_STALE` is a replay artefact: the tape is sampled every ~2 s and the
replay ages each quote to the step it is used at, while live the runner
reads the book itself and reports the fetch round-trip as the age.
`BOOK_UNUSABLE` is real -- a one-sided book with no bid, which the live
05:00z window showed for most of its steps.

### No parameter rescues it

| band floor | net/window | | clip | net/window |
|---|---|---|---|---|
| $0.51 | -$0.0210 | | $0.05 | -$0.0221 |
| $0.55 | -$0.0148 | | $0.10 | -$0.0210 |
| $0.60 | -$0.0229 | | $0.15 | -$0.0252 |
| $0.65 | -$0.0132 | | $0.22 | -$0.0254 |
| $0.70 | -$0.0121 | | | |
| $0.75 | -$0.0189 | | | |

Every one is negative. The sweep is also non-monotone, so none of the
differences between them is a signal to tune on.

### Fill type does not rescue it either

Decomposing the FAVOURED tier by how the fill happened:

| fill type | fills | win rate | avg price | net/share | t |
|---|---|---|---|---|---|
| bid fell through our price | 528 | 0.625 | 0.661 | -0.0365 | -1.77 |
| bid dwelled at our price 10 s | 22 | 0.682 | 0.709 | -0.0268 | -0.30 |

Both negative. There is no subset of fills here that pays.

## E. A CORRECTION TO MY EARLIER READING

On a first 34-window sample (09:00Z-11:55Z only) this replay returned
**+$0.032 a window** and I reported that the controller's assumed $0.06
adverse-selection penalty "is not visible". **That was wrong, and the cause
was the sample.** Those three hours settled 24 DOWN against 10 UP -- BTC
fell through them -- and a trending tape flatters "the favorite keeps
winning". Splitting the full tape shows it directly:

| block | windows | UP/DOWN | net per window |
|---|---|---|---|
| 00:00-04:16 | 51 | 25 / 26, balanced | **-$0.0345** |
| 09:00-11:55 | 34 | 10 / 24, skewed | **+$0.0321** |
| whole tape | 142 | 65 / 77 | **-$0.0210** |

The adverse selection the old gate charged was **directionally right** --
measured at **-3.65 cents a share**, against the 6 cents it assumed. What
was wrong with the gate was its *calibration against a book-derived
probability*: comparing `mid` to a price two ticks under the bid made the
test unreachable rather than merely strict. Removing it as a mandatory
condition was still the right change, because a gate that can never pass is
not a risk control. But the cost it was pointing at is real.

## F. ONE BOTTLENECK

Not order count, and no longer evidence volume. It is **the entry price**.
A post-only buy of the favorite at its own bid is, on this venue and this
instrument, filled by better-informed flow at a cost of about 3.6 cents a
share, and nothing in the specified policy pays for that. To be profitable
the policy needs a probability estimate **independent of the book** -- the
venue index, which `l5_btc_reference` already supplies to the live runner
and which is *not* in this quote tape -- so that
`p_side - price - adverse selection` is a real test rather than a
restatement of the price. That is a hypothesis. It is not tested here,
because this tape cannot test it.

## G. THE NEXT CONSTRAINT IF IT IS EVER ARMED: REPLACE, NOT OPEN

Every order is charged to one of three owner-set budgets
(`L5_LIVE_AUTH_MAX_OPEN` / `_COMPLETE` / `_REPLACE`), and
`Btc5mExecutor._order_class` charges it to **REPLACE** once an order of
ours on that side has been cancelled before. Accumulation requotes on every
tick of drift, so after the first requote almost everything is a REPLACE:

| class | total | per window, mean | per window, max |
|---|---|---|---|
| OPEN | 246 | 1.7 | 4 |
| COMPLETE | 138 | 1.0 | 12 |
| **REPLACE** | **872** | **6.1** | **23** |

If `L5_LIVE_AUTH_MAX_REPLACE` is sized for the old one-entry pair strategy
it will stall accumulation after the first requote whatever the controller
decides, and the symptom will be an `AUTHORIZATION:...` refusal rather than
an entry reason. The values are not readable from this session. **These are
owner-set authorization controls and were left exactly as they are.**

## H. WHAT IS NOT CLAIMED

* No fill, no settlement, no realized P&L for this strategy -- ever.
* The replay's fills are bounds from a quote tape, not observed fills.
  Queue position is unobservable from public data and is not modelled.
* 12 hours of one day is not a regime sample. The negative result holds in
  both the balanced and the trending block of it, but a longer tape could
  move the magnitude.
* The band floor, clip size and resting limit are **not** tuned; the
  shipped defaults are the owner's contract values.
* `analysis/l5/btc5m_cap_policy_replay.py`, cited in the controller's
  `completion_reserve` docstring as the source of a 42-window cap-policy
  measurement, is **not present in this repository**. That citation is
  unverifiable from this tree.
