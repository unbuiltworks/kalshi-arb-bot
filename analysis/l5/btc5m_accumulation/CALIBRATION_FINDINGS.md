# BTC5m: calibrating the independent probability — issue #233 Track B

Date 2026-11-12. Read-only. Nothing here is wired to execution and no
threshold in it is a default anywhere.

**These numbers supersede the 142-window figures in `ECONOMICS.md`.** The
tape is now **189 windows / 187 resolved / 27,425 rows**, 2026-11-11 21:00Z
to 2026-11-12 13:25Z (16.5 h), with the venue's own 1-second BRTI average
backfilled across all of it (64,830 buckets, zero gaps).

---

## The answer in one table

Chronological split, never shuffled. 126 early windows, 61 late (OOS).

| variant | all 187 | OOS 61 | per-share (all) |
|---|---|---|---|
| **A** book mid, no gate (shipped) | −$0.0285/win (t −1.78) | −$0.0204 | **−$0.0467 (t −2.66)** |
| **B** venue index, no gate | −$0.0334/win (t −1.93) | −$0.0208 | **−$0.0541 (t −3.09)** |
| **C** venue index + offline gate | −$0.0083/win (t −1.12) | −$0.0069 | **−$0.0764 (t −1.84)** |
| **no-trade** | $0.0000 | $0.0000 | — |

**All three lose, and the per-share losses are now significant at 2–3
sigma.** No-trade beats every variant. On the earlier 142-window tape
variant C read **+$0.0012/window** and its OOS block read **+$0.0115 on 23
fills**; with 45 more windows both turned negative. That is exactly the
small-sample reversal the earlier report declined to treat as an edge.

## B2 — venue mechanics, strike source, causality: verified

The market settles on **BRTI averaged over the 60 s before each window
edge**, and the venue publishes both ends free and unauthenticated. The
strike is `assetPriceTerms.priceToBeat`.

**Checked on all 189 windows: `priceToBeat` equals the index bucket stamped
`window_start − 1` — the 60-second average ending exactly at the window
start — in 189 of 189, zero mismatches, zero missing.** So the strike is
knowable causally at t=0 from the same public feed, and the alignment used
throughout is the settlement series itself rather than a proxy for it.

## B3 — is the independent probability any good?

### It is worse than the book, on every block

| block | log loss, index model | log loss, book mid | Brier, model | Brier, book |
|---|---|---|---|---|
| train | 0.5385 | **0.5347** | 0.1827 | **0.1805** |
| validation | 0.5836 | **0.5756** | 0.2002 | **0.1948** |
| OOS | 0.4401 | **0.4025** | 0.1428 | **0.1310** |

The book mid wins all six comparisons. A probability that is less accurate
than the price you are trading against is not an edge; it is a handicap,
which is why variant B is worse than variant A.

### Fitted candidates, train only, scored once on OOS

| features | LL train | LL val | LL OOS |
|---|---|---|---|
| book only | 0.5371 | 0.5711 | 0.4197 |
| model only | 0.5396 | 0.5857 | 0.4236 |
| model + book | 0.5347 | 0.5732 | **0.4150** |
| model + book + momentum | 0.5332 | 0.5715 | 0.4174 |
| model + book + momentum + tau + sigma | 0.5329 | 0.5737 | 0.4319 |
| momentum only | 0.6267 | 0.6206 | 0.5944 |

**Momentum makes OOS worse**, and adding time-remaining and volatility on
top makes it worse again — the signature of fitting noise. The fitted
momentum weights alternate sign across horizons (10 s **+0.48**, 30 s
**−0.60**, 60 s **+0.20**) in standardised units, which is what collinear
noise looks like, not a mechanism.

**Grok's suggested formula and momentum coefficients were not used as
defaults anywhere.** Momentum entered only as a feature whose weight was
fitted here, and it did not survive.

### The disagreement signal does not order outcomes

Bucketing eligible rows by `model's probability for the favorite − price
paid`, standard errors clustered by window:

| edge bucket | VAL net/share | OOS net/share |
|---|---|---|
| −0.10..−0.05 | −0.0240 | **+0.0315** |
| −0.05..+0.00 | −0.0454 | +0.0852 |
| +0.00..+0.05 | −0.1092 | +0.0341 |
| +0.05..+0.10 | −0.0649 | **−0.0134** |
| +0.10..+0.15 | +0.0517 | +0.0954 |

The sign flips between blocks for the same bucket, and within OOS the
**most negative** edge bucket pays more than the **positive** ones. There is
no monotone ordering in either block. Every bucket is inside one clustered
standard error of zero.

## B4 — what was implemented

**No gate was wired to live execution**, because nothing above validates
one. The strategy specification is untouched: favorite accumulation at
$0.51–$0.90 inclusive on the price paid, repeated small maker increments,
up to 4 concurrent clips, requote on any tick of drift, maker-only, $0.45
ceiling.

What *was* implemented is the freeze/reversal correctness the clause asks
for, pinned by runner-level tests driven through whole sessions
(`tests/test_l5_btc5m_runner_scenarios.py`, **30 tests**):

| issue #233 acceptance scenario | class |
|---|---|
| dual resting orders | `TestDualRestingOrders` |
| partial fills | `TestPartialFills` |
| same-side runs | `TestSameSideRuns` |
| completion (never attempted) | `TestCompletion` |
| restart | `TestRestart` |
| uncertain state | `TestUncertainState` |
| cancel race | `TestCancelRace` |
| rollover | `TestRollover` |
| settlement | `TestSettlement` |
| hard caps | `TestHardCaps` |
| halt / watchdog | `TestHaltAndWatchdog` |
| **favorite reversal with pending orders** | `TestFavoriteReversalFreeze` |

Reversal is pinned four ways: the last order on the abandoned side precedes
the first order on the new one; no duplicate client order id survives the
reversal; nothing is left resting or non-terminal on the abandoned side;
and inventory held there is **carried to settlement, never dumped** (every
order is a post-only BUY).

### One contract clarified by a failing test

The module claims "one UNCERTAIN row stops every new submission until a
later poll reads it cleanly." A first test marked a row uncertain that the
venue could still read, and the next poll legitimately cleared it — the
guard was working, the test was wrong. It now drives the real case (the
venue stops answering for that order) and pins **both** halves: an
unreadable order halts submission, and a clean read releases it. The halt
lasts exactly as long as the uncertainty.

## Fee basis — PR #231 reviewed as the issue requires

PR #231 establishes this account is **Tier 1: taker Θ=0.095 and NO maker
rebate**. Every economic figure here assumes maker fee **zero with no
rebate**, which is exactly Tier 1, and the venue read back
`commissionNotionalTotalCollected` of 0.0000 on every order of the
overnight campaign. **PR #231 does not change any BTC5m number**; its 32
dependent failures are in BTC15m / q1 / r4 paths. It removes the last place
a hidden rebate could have rescued these economics.

## What is still not obtainable

- **Depth.** The public endpoint publishes no size at any level, so queue
  position is unobservable and fills stay bounded (ADVERSE / FAVOURED),
  never estimated. Calibrating queue against venue-confirmed fills needs
  fills, and this strategy has never had one.
- **More than 16.5 hours.** Railway log retention starts at the recorder's
  current deployment (2026-11-11 21:02Z). Everything here is one day of one
  regime; the recorder accrues forward from here.

## Bottom line

No candidate probability, momentum feature, or adverse-selection/reversal
filter improved conditional EV out of sample. Adding 45 windows turned the
only previously-positive variant negative. **No-trade dominates all three
variants**, and the shipped configuration's per-share loss is now
significant at t = −2.66.

---

# Addendum — hedging the flip, measured

The owner asked to "mathematically figure out how to hedge when it flips."

## The arithmetic

Hold `q` shares of the old favorite at average cost `c`; the favorite
flips; buy `q` of the new favorite at `d`. A matched pair pays $1 whichever
way it settles:

```
hedged   net/share = 1 - (c + d)      DETERMINISTIC
held     net/share = p_held - c       expected, still random

hedging beats holding  iff  1 - (c + d) > p_held - c  iff  d < 1 - p_held
```

Resting as a **maker** on the new favorite pays its bid, `d = 1 - old_fav_ask`,
so the test reduces to **`p_held < old_fav_ask`**: hedge when the side you
hold is worth less than what someone will still pay for it. Paying the ask
(taker) gives `d = 1 - old_fav_bid` and the much harder test
`p_held < old_fav_bid`.

## The structural problem

This strategy only ever buys a favorite at **$0.51 or more**. When that side
stops being the favorite, the other side *is* the favorite, so it also
costs about **$0.51 or more**. The pair therefore costs about **$1.02 or
more**, which locks a guaranteed loss. There is no pricing of the hedge that
escapes this: it is a consequence of the band, not of execution.

## Measured, 187 windows, ADVERSE fills, chronological split

| arm | matched % | net/window | s.e. | t | OOS 61 |
|---|---|---|---|---|---|
| **NO-TRADE** | — | **$0.0000** | — | — | **$0.0000** |
| HOLD (shipped) | 13.7% | −$0.0283 | 0.0160 | −1.76 | −$0.0192 |
| **HEDGE** (maker, ceiling $1.00) | 48.4% | **−$0.0263** | **0.0045** | **−5.88** | −$0.0264 (t −3.32) |

**Hedging does not reduce the loss. It makes the loss certain.** The
standard error collapses from 0.0160 to 0.0045 because a matched pair is
deterministic, so the result moves from a noisy negative to a **5.9-sigma**
negative. That is the opposite of what a hedge is wanted for.

Paired per window, HEDGE − HOLD is **+$0.0019 (s.e. 0.0143, t +0.13)** —
better in 62 windows, worse in 111, equal in 14. Indistinguishable from
zero, and more often worse than better.

The realized **blended** two-sided book cost on windows that ended holding
both sides averaged **$1.054** (min $0.804, max $1.472), with **94 of 183**
above $1.00. That blend includes accumulation on both sides across a flip,
not only the hedge leg — the controller refuses any single hedge order
above `hard_pair_ceiling` — but it is what the book actually cost.

## Verdict

No-trade dominates every arm. Hedging the flip is sound arithmetic applied
to a position whose entry price makes it unprofitable: the $0.51 floor on
both legs guarantees a pair above par. Hedging is therefore available as
**loss-variance reduction**, not as a route to profit, and it is not
promoted to a default.
