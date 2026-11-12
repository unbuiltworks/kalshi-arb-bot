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

---

## Entry gates: tested, negative, and the family is closed

`replay_gates.py`, 189-window tape, 187 resolved, chronological OOS cut at
`2026-11-12-0730z` (63 windows). Window-clustered means. Every arm is
reported against NO-TRADE, which nets exactly zero.

Calibrated *probability* features were already tested and all worsened
out-of-sample log loss. This tests a different hypothesis class: not what
the fair price is, but **when to rest at all**. Resting a maker buy at the
bid should earn half the spread, so a loss of ~4.9c a share is adverse
selection, not mispricing — the bid gets hit because the bid is about to
fall.

Gates tested, each suppressing only new placements (cancels and fills are
untouched, so a gate can never invent an exit): spread width (≥2/3/4
ticks), a quiet book (mid moved ≤1/2/3c over 20s), early-only, late-only,
a favorite-price ceiling (0.60/0.70/0.80), and two conjunctions.

**Every one of the 17 arms is negative out of sample.** The least-bad
positive-looking arm, `SPREAD>=3 AND QUIET<=2c`, is `+$0.0008/window` at
**t = +0.19** on **6 fills / 0.93 shares across 63 windows** — noise, and
promoting it would be fitting the tape.

### Why the family is closed, not merely unlucky

Across the 17 arms, selectivity and per-share result are strongly
**rank-correlated in the wrong direction**:

    Spearman rho(shares traded, net per share) = +0.833   t = +5.84, n = 17

Fewer shares traded goes with a *worse* price per share. Examples:

| arm | shares | net/share |
|---|---|---|
| ungated | 108.58 | −0.0487 |
| SPREAD ≥ 3 | 18.54 | −0.0871 |
| SPREAD ≥ 4 | 5.49 | −0.1959 |
| LATE t ≥ 240s | 9.53 | −0.2515 |

Net-per-window shrinks toward zero under a tight gate only because volume
shrinks, not because the trades got better. The gates are an expensive way
of approximating no-trade.

The mechanism: **the toxicity is a property of the fill, not of the
pre-fill state.** A resting maker buy is only reached when the market comes
down to it, so any state that raises the fill probability is the same state
in which the price is moving against us. The more selective the filter, the
more it concentrates on fills that could only have been caused by a real
move — which is why a wide-spread gate is worse per share, not better.
Filtering on observable pre-fill state cannot separate "I got filled" from
"I got filled because I was wrong".

### What this rules in and out

- **Ruled out:** entry-state filters as a route to positive expectancy for
  $0.51–$0.90 favorite accumulation. Do not re-open this family without a
  *new observable*, not a new threshold on these.
- **Not yet testable:** order-book **imbalance and depth**. The quotes tape
  carries `bid`/`ask` only, no sizes, so queue-ahead and flow imbalance —
  the one feature class that is about the fill rather than the state — has
  no data. Closing that gap is a recorder change, not a strategy change.
- **Unchanged:** no-trade still dominates every measured arm, at every
  clip, depth, band and gate tested.

---

## The replay's first contact with settled money

`live_vs_replay.py`. Every negative conclusion above comes out of the same
replay, so if the replay does not describe reality, none of it stands. On
2026-11-12 the service traded real money under ACCUMULATE for the first
time, which makes the model falsifiable against settled cashflows instead
of only against itself.

Five windows filled and settled. Windows that placed nothing are excluded
rather than counted as flats: 16:15z (preflight failed) and 17:00z /
17:05z / 17:10z, which submitted nothing because the campaign's **OPEN
budget was exhausted** (max 8 per campaign, spent 8 by 17:00z) — a budget
limit, not a decision not to trade. Commission read back as 0.0000 on
every order, consistent with the zero maker fee the replay already
assumes.

| window | fills | qty | cost | settled net |
|---|---|---|---|---|
| 16:20z | 4 | 0.66 | 0.3830 | −0.0930 |
| 16:25z | 4 | 0.58 | 0.3902 | −0.3902 |
| 16:45z | 3 | 0.53 | 0.2886 | −0.2886 |
| 16:50z | 4 | 0.56 | 0.3897 | **+0.1703** |
| 16:55z | 4 | 0.64 | 0.3897 | −0.0797 |

| | replay | live |
|---|---|---|
| net per window | −0.0286 | −0.1362 |
| net per share | −0.0487 | −0.2294 |

19 fills, 2.97 contracts, $1.8412 deployed, **−$0.6812 settled**.

At the replay's own per-window sd of 0.2206, a 5-window mean has
se = 0.0987, so live sits **−1.09 se** from the prediction. The worst live
window (−0.3902) is inside the replay's observed range (−0.3964 to
+0.3111). **Not rejected.**

This is deliberately the weak claim: five windows cannot confirm a model,
only fail to break it. What they do kill is the hopeful reading — that
real fills would behave better than the replay's ADVERSE tier. They did
not. Live came in *below* the prediction, not above.

Two of the five formed matched pairs **above par** — 1.1759 at 16:20z and
1.2197 at 16:55z — which is the structural flip result confirmed again in
live money: both legs of a $0.51–$0.90 favorite cost more than $1.00
together, so completing a flipped position locks a loss.

The sign is unchanged in both: negative live, negative in replay, and
no-trade still dominates every arm tested.


---

## The ramp answered the size question: no

On 2026-11-12 the owner authorized raising the aggregate exposure cap from
$0.45 to **$2.25** (clip $0.10 → $0.25, resting slots 4 → 9, submission
budgets OPEN/COMPLETE/REPLACE 8/8/24 → 48/48/108). The first full-size
window ran at 17:45z.

| window | submitted | filled | shares | deployed | settled |
|---|---|---|---|---|---|
| 17:45z | 16 | 9 | 3.55 | $2.2309 | **−$0.8709** |

It reached the cap almost exactly ($2.2309 against $2.25) and the budgets
stopped binding (OPEN 2/48, REPLACE 9/108), so this is the strategy running
unconstrained rather than throttled.

**Per share — the only size-invariant comparison:**

| regime | windows | shares | net | net/share |
|---|---|---|---|---|
| $0.45 cap | 5 | 2.97 | −0.6812 | **−0.2294** |
| $2.25 cap | 1 | 3.55 | −0.8709 | **−0.2453** |
| all | 6 | 6.52 | −1.5521 | −0.2381 |

Five times the size moved per-share economics by −0.0160, i.e. not at all
in any useful sense. **The loss scales with size rather than being diluted
by it.** One window at the new size lost more (−$0.87) than the five before
it combined (−$0.68).

This closes a hypothesis that was worth testing: that the measured loss was
an artefact of trading too small to matter. It was not. Live per-share is
now −0.2381 against the replay's −0.0487, i.e. 4.9× worse, and the pair at
17:45z cost **1.2262** — the third above-par pair in six windows.

A per-*window* mean across the two regimes is meaningless, so the regimes
are reported apart and per-share carries the comparison.


---

## "But we win a bunch of windows"

True, and it is not an illusion. `winrate.py`, 185 windows that traded:

- **54.6%** of windows are net positive (101 of 185)
- **59.3%** of shares settle on the winning side

Buying the favorite is supposed to win more often than it loses. That is
what a favorite is. The win rate was never the problem.

**The payoff is asymmetric, so the bar is not 50%.** Buy at `p` and you
make `1 − p` when right and lose `p` when wrong, so the breakeven win rate
*is* the average price paid:

```
average price paid    0.6419   ->  BREAKEVEN win rate  64.2%
actual win rate                                        59.3%
gap                                                    -4.9 points

right  +$0.3581 a share      wrong  -$0.6419 a share
needs right:wrong of 1.79    actual 1.46
```

That 4.9-point gap is the entire loss. "Most" has to mean more than 64%,
not more than 50%.

### Reading a short live run without fooling yourself

The live loss decomposes into the structural gap and luck:

```
live               -0.2381 /share
  structural       -0.0316   (pay 62.5%, model wins 59.3%)
  luck             -0.2065   (live won 38.7% vs the model's 59.3%)
```

**87% of the live loss is variance, not mechanism.** UP won 5 of the 6
live windows and we were accumulating DOWN in 4 of them; going 1-for-6
when the true rate is 54.6% has probability 0.072, about 1 in 14 —
unusual, not anomalous.

So the raw live figure **overstates** how bad this is, and comparing it
head-on against the replay's per-share number (as an earlier revision of
`live_vs_replay.py` did, calling live "4.9x the replay's loss") is
misleading. The number that carries is the structural 4.9 points, which
rests on 185 windows rather than six.


---

## The per-price table looks like a fixable loss. It is not.

`replay.py`'s per-price breakdown is the most seductive table in this
directory:

| band | win rate | breakeven | gap | net$ |
|---|---|---|---|---|
| 0.50–0.54 | 0.468 | 0.523 | −5.5 | −1.24 |
| 0.55–0.59 | 0.494 | 0.568 | −7.4 | −1.56 |
| 0.60–0.64 | 0.531 | 0.619 | −8.8 | −1.73 |
| 0.65–0.69 | 0.667 | 0.668 | −0.1 | −0.02 |
| 0.70–0.74 | 0.728 | 0.718 | +1.0 | **+0.10** |
| 0.75–0.79 | 0.675 | 0.768 | −9.3 | −0.79 |
| 0.80–0.84 | 0.848 | 0.817 | +3.1 | **+0.20** |
| 0.85–0.89 | 0.822 | 0.869 | −4.7 | −0.23 |

The three cheapest buckets carry −$4.53 of the −$5.28 total, and at
0.50–0.54 the "favorite" wins only **46.8%** — below a coin flip. It reads
like a mechanism: near 0.51 the market is a near tie, the favorite label is
within noise, and a maker fill means the lead was already going.

**It does not survive testing**, and the reasons are worth keeping:

1. It conditions on the price a fill *happened* at — chosen after the fact.
2. Most volume sits in the cheap buckets (63.3 of 108.6 shares), so they
   carry most of the loss even at an equal per-share rate.
3. The two positive buckets are +0.0101/share on 73 fills (t ≈ +0.2) and
   +0.0316 on 53 (t ≈ +0.6). Both are indistinguishable from zero.
4. Raising the band floor is **not the same experiment**. Skipping a 0.55
   favorite does not hand you its 0.70 fill later — the trajectory changes.

`band_floor_oos.py` runs the executable version, chronologically split:

```
best floor in sample       0.80   -0.0265/window  t -2.18
that same floor OOS               +0.0225/window  t +2.21
that same floor FULL tape         -0.0105/window  t -1.17
best floor OUT of sample   0.55   (the in-sample pick did not transfer)

arms positive IN sample      0 of 7
arms positive OUT of sample  5 of 7
```

**Floor 0.80 is significantly negative in one half and significantly
positive in the other.** A real edge does not change sign at 2σ on both
sides. And with 0 of 7 arms positive in sample against 5 of 7 out of
sample, the later block is a kinder **regime**, not a vindication of any
floor. Over the full tape, floor 0.80 is −0.0105/window.

Worth recording honestly: 0.80 does roughly **half** the damage of the
shipped 0.51 floor (−0.0105 vs −0.0283 full-tape). Less bad is not
profitable, and it buys that by trading far less — the same way every
gate in the sweep "improved" things.
