# PMUS BTC15m late-favorite / opposite-longshot — DISCOVERY findings

**EVERY NUMBER IN THIS FILE IS `DISCOVERY`.** No order was placed, prepared or
previewed. No executor was built. No production service, variable or deployment
was changed. Gemini untouched. No reserved window read. No threshold optimized.

Lane `claude/pmus-btc15m-late-favorite-wb70f1`, based on `f3516fe`.
Preregistration frozen in `PREREGISTRATION.md`; window classes in
`windows.json`; states hashed before settlement was joined
(`states_nooutcome.json`, sha256 `fbe773a7a4d0680de…`).

## Answer to the primary question

> When BTC is sufficiently far from its window-open reference late in a PMUS
> 15-minute window, is the favorite systematically underpriced relative to
> settlement probability, and if so, what is the cheapest defensibly executable
> way to acquire that exposure?

**On the discovery sample: no, and there is no cheaper way.**

1. The calibration residual **shrinks monotonically as distance grows** — the
   opposite of what the hypothesis needs. It is +0.086 at `|z|` 1.0–1.5 and
   **+0.010 at `|z| ≥ 2.0`**, where the hypothesis most wants to trade.
2. At `|z| ≥ 2.0` the favorite is priced at the **venue's 0.99 maximum**, so the
   entire available gross edge is **1.0 cent** and the taker breakeven true hold
   rate is **99.07%**. The trade is a bet on the third-to-fourth significant
   figure of a tail probability.
3. The "opposite-longshot maker expression" is **not a distinct expression**. Its
   payoff vector, its fee and its queue are all identical to the maker-favorite
   order. There is no cheaper route to the same exposure.
4. L5's own canonical history **does the opposite** of the late-favorite story,
   and his economics are a maker-rebate business, not a directional one.

The one thing today's data genuinely cannot settle is whether the tail
probability clears 99.07%. Phase 3 prices that answer at **~2,400 clean markets
(~114 days)** if the true rate is 99.5%.

---

## Phase 1 — calibration (DISCOVERY)

55 usable states over **19 independent markets** (of 90 built states; 35 had no
executable favorite price). Markets, not ticks, are the unit.

### The simpler question first: is settlement probability monotone in `|z|`?

**Yes — and so is the price, which is the problem.**

| `|z|` bucket | markets | obs | favorite settles | mean executable `fav_ask` | residual | clustered SE | t |
|---|---|---|---|---|---|---|---|
| <0.5 | 4 | 4 | 0.750 | 0.777 | **−0.027** | 0.289 | −0.10 |
| 0.5–1.0 | 6 | 10 | 0.700 | 0.703 | **−0.003** | 0.173 | −0.02 |
| 1.0–1.5 | 6 | 7 | 1.000 | 0.914 | **+0.086** | 0.029 | 2.91 |
| 1.5–2.0 | 6 | 8 | 1.000 | 0.978 | **+0.022** | 0.007 | 3.37 |
| ≥2.0 | 12 | 26 | 1.000 | 0.990 | **+0.010** | 0.001 | 12.46 |

Settlement rate rises 0.75 → 1.00 and the executable price rises 0.777 → 0.990.
**The venue tracks the state.** The residual does not grow with distance; it
*collapses* toward the 1-cent tick cap.

### The t-statistics in that table are artefacts, and must not be quoted

At `|z| ≥ 2.0` the outcome is 26/26 and the price is 0.990 in nearly every
observation, so the clustered SE collapses to 0.001 and t to 12.46. **That is
zero in-sample variance, which is the weakest possible evidence about a tail —
not the strongest.** The honest statement is the market-level Wilson interval:
26/26 over 12 markets is **[0.74, 1.00]**. Breakeven is 0.9907. The data cannot
distinguish a 74% hold rate (catastrophic) from 99.9% (good). Reporting
`t = 12.46` as significance would be exactly the failure mode the
preregistration exists to prevent.

### Full grid, time remaining × `|z|`

Every cell has 1–6 markets. The grid is reported for completeness and
**no cell carries inferential weight**.

| T− | `|z|` | mkts | obs | rate | `fav_ask` | residual | Wilson95 (markets) |
|---|---|---|---|---|---|---|---|
| 300 | <0.5 | 2 | 2 | 0.50 | 0.640 | −0.140 | [0.09,0.91] |
| 300 | 0.5–1.0 | 5 | 5 | 0.80 | 0.686 | +0.114 | [0.38,0.96] |
| 300 | 1.0–1.5 | 2 | 2 | 1.00 | 0.960 | +0.040 | [0.34,1.00] |
| 300 | 1.5–2.0 | 2 | 2 | 1.00 | 0.965 | +0.035 | [0.34,1.00] |
| 300 | ≥2.0 | 6 | 6 | 1.00 | 0.990 | +0.010 | [0.61,1.00] |
| 180 | <0.5 | 1 | 1 | 1.00 | 0.900 | +0.100 | [0.21,1.00] |
| 180 | 0.5–1.0 | 4 | 4 | 0.75 | 0.715 | +0.035 | [0.30,0.95] |
| 180 | 1.0–1.5 | 1 | 1 | 1.00 | 0.830 | +0.170 | [0.21,1.00] |
| 180 | 1.5–2.0 | 3 | 3 | 1.00 | 0.987 | +0.013 | [0.44,1.00] |
| 180 | ≥2.0 | 4 | 4 | 1.00 | 0.990 | +0.010 | [0.51,1.00] |
| 120 | 0.5–1.0 | 1 | 1 | 0.00 | 0.740 | −0.740 | [0.00,0.79] |
| 120 | 1.0–1.5 | 3 | 3 | 1.00 | 0.887 | +0.113 | [0.44,1.00] |
| 120 | 1.5–2.0 | 2 | 2 | 1.00 | 0.970 | +0.030 | [0.34,1.00] |
| 120 | ≥2.0 | 5 | 5 | 1.00 | 0.988 | +0.012 | [0.57,1.00] |
| 60 | <0.5 | 1 | 1 | 1.00 | 0.930 | +0.070 | [0.21,1.00] |
| 60 | 1.0–1.5 | 1 | 1 | 1.00 | 0.990 | +0.010 | [0.21,1.00] |
| 60 | ≥2.0 | 6 | 6 | 1.00 | 0.990 | +0.010 | [0.61,1.00] |
| 30 | 1.5–2.0 | 1 | 1 | 1.00 | 0.990 | +0.010 | [0.21,1.00] |
| 30 | ≥2.0 | 5 | 5 | 1.00 | 0.990 | +0.010 | [0.57,1.00] |

### Individual markets behind every cell with |residual| > 0.05

The largest apparent residuals are each **1 market**:

- **T-120, `|z|` 0.5–1.0, residual −0.740** — one market, `2026-09-27-2030z`,
  z=−0.78, DOWN favorite at 0.740, lost.
- **T-180, `|z|` 1.0–1.5, residual +0.170** — one market, `2026-09-27-2115z`,
  z=−1.11, DOWN at 0.830, won.
- **T-180, `|z|` <0.5, +0.100** — one market, `2026-09-28-0145z`.
- **T-60, `|z|` <0.5, +0.070** — one market, `2026-09-27-2030z`.

The only multi-market cells with a sizeable residual:

- **T-300, `|z|` 0.5–1.0, +0.114, 5 markets** — `1915z` won at 0.760,
  `2030z` **lost** at 0.550, `0115z` won at 0.680, `1215z` won at 0.700,
  `1315z` won at 0.740. Four of five won. One market flips the sign of this cell.
- **T-120, `|z|` 1.0–1.5, +0.113, 3 markets** — `0200z` (0.800), `1215z` (0.920),
  `1315z` (0.940), all won. Three markets, three wins.

**Verdict on breadth: nothing here is broad.** Every apparently large residual
is carried by one to five markets, and the single market `2026-09-27-2030z`
alone drives both the largest positive and the largest negative cell.

### Raw-bps diagnostic (not a strategy bucket)

| `|bps|` | mkts | obs | rate | `fav_ask` | residual |
|---|---|---|---|---|---|
| 0–2 | 2 | 2 | 1.00 | 0.915 | +0.085 |
| 2–5 | 4 | 6 | 0.50 | 0.740 | **−0.240** |
| 5–10 | 8 | 10 | 0.90 | 0.780 | +0.120 |
| 10–15 | 7 | 10 | 1.00 | 0.932 | +0.068 |
| 15–25 | 3 | 5 | 1.00 | 0.990 | +0.010 |
| 25–40 | 3 | 6 | 1.00 | 0.985 | +0.015 |
| 40+ | 4 | 16 | 1.00 | 0.990 | +0.010 |

The 2–5 bp row is the near-tie region where the **1-minute OHLC4 proxy is least
able to reproduce a 60-second settlement mean and the tie-goes-Up rule**. Its
−0.240 is as likely to be resolution error as a market fact and is not treated
as a finding.

### Concentration — day and data source are confounded

| day | mkts | obs | rate | `fav_ask` | residual | source |
|---|---|---|---|---|---|---|
| 2026-09-27 | 9 | 16 | 0.81 | 0.869 | −0.056 | public only |
| 2026-09-28 | 4 | 9 | 0.89 | 0.852 | +0.037 | public only |
| 2026-09-30 | 6 | 30 | **1.00** | 0.951 | +0.049 | authenticated only |

The authenticated markets are **all** on 2026-09-30 inside one 90-minute
stretch; the public markets are **all** 2026-09-27/28. **Regime and data source
cannot be separated.** The 09-30 day contributes every authenticated state and
has a 100% favorite rate, so it inflates every pooled residual.

### Two structural facts that bound the whole hypothesis

**(a) The 0.99 price cap.** 30 of 55 buyable states (55%), over 12 markets, sit
at `fav_ask ≥ 0.985`, mean 0.9900 — the venue maximum. The most you can gain is
1.0 cent; taker fee at 0.99 is 0.070 c. **Breakeven true hold rate: 99.07%
taker, 98.99% maker** (the rebate buys 0.010 c of room).

**(b) 39% of late states cannot be traded at all.** 35 of 90 states have **no
executable favorite price**: the offer side that would sell you the favorite is
empty, so "buy the favorite" does not exist at any price. These are the states
the hypothesis likes most — mean `|z|` **5.23** where unbuyable versus **2.53**
where buyable — and they concentrate at the latest checkpoints (T-30: 12,
T-60: 10, T-120: 8, T-180: 5). Expression A is unavailable exactly where
certainty is highest.

---

## Phase 2 — execution mechanics (DISCOVERY, 6 authenticated markets)

### Expression C is not a distinct expression — shown, not asserted

**Payoff vectors.** Buy favorite at `f`: `+(1−f)` if the favorite settles, `−f`
if not. Sell longshot at `q`: `+q` if the favorite settles, `q−1` if not. At
`q = 1−f` the two vectors are **identical** term by term. C is the same economic
position as A/B — **not an arbitrage against them.**

**Fees are identical too.** The venue fee is `θ·C·p·(1−p)`, and `p(1−p)` is
symmetric about 0.5, so the same position priced at `f` or at `q = 1−f` incurs
the same fee and the same rebate. Verified against `src/pmus_fees.py`:

| favorite price | taker | rebate | longshot price | taker | rebate | |
|---|---|---|---|---|---|---|
| 0.99 | 0.0700 c | 0.0100 c | 0.01 | 0.0700 c | 0.0100 c | MATCH |
| 0.95 | 0.3300 c | 0.0600 c | 0.05 | 0.3300 c | 0.0600 c | MATCH |
| 0.90 | 0.6300 c | 0.1100 c | 0.10 | 0.6300 c | 0.1100 c | MATCH |

**Queue is identical.** The tape serves one book per market, quoted in UP terms
(a single bid/ask and a single offer ladder per slug). Resting an offer on the
longshot at `q` is therefore **the same order, at the same price level, in the
same queue** as resting a bid to buy the favorite at `1−q`.

*What would change this:* evidence that PMUS maintains separate per-side books
with independent queues, as a mint-and-match CLOB can. The single
bid/ask/ladder in this tape does not support that. Recorded as
**unsupported-but-not-disproven**, and it is the one cheap check worth running
against the current full-L2 recorder.

**So there is no cheaper defensibly executable way to acquire the exposure.**
The only lever that is genuinely different is maker-versus-taker, worth
`fee + rebate` = 0.080 c/share at 0.99.

### A — taker favorite

| | |
|---|---|
| opportunities | 30 |
| A available | 30 |
| mean favorite price paid | 0.9507 |
| gross directional P&L | **+4.933 c/share** |
| taker fees | 0.274 c/share |
| net | **+4.659 c/share** |
| displayed touch depth | p50 1,727 shares (min 19, max 6,948) |

**This +4.66 c/share is not an edge estimate and must not be used as one.** All
six markets had the favorite win — 30/30 state-observations. The figure is
driven by the low-priced states (0.70, 0.73, 0.74, 0.76, 0.92, 0.94, 0.96) whose
true hold rates are roughly 70–90%, every one of which happened to win. It is
small-sample luck with zero in-sample variance.

### B — maker favorite (= C, same order)

No touch = fill. A fill required one of exactly two evidenced modes:
`STRICT_THROUGH` (a taker print strictly through our price) or `QUEUE_CONSUMED`
(same-price taker volume exceeding the depth displayed ahead of us when we
joined). A same-price print that did not exhaust the displayed queue was **not**
counted.

| | |
|---|---|
| placements | 30 |
| **defensible fills** | **9 (30.0%)** |
| nonfills | 21 |
| modes | STRICT_THROUGH 7, QUEUE_CONSUMED 2 |
| time to fill | p50 2.9 s (min 0.3 s, max 259.4 s) |
| maker rebate on fills, kept separate | +0.109 c/share |

**Queue-ahead at the cap is the capacity killer.** Displayed depth ahead of a
0.99 resting order ran **1,120 to 28,891 shares**. At the cap you are behind
thousands of shares that are already there for the same 1 cent.

### The adverse-selection measurement — UNANSWERED, not favourable

```
P(correct | all eligible opportunities) = 1.0000   (n = 30 states)
P(correct | defensible fill)            = 1.0000   (n =  9 fills)
ADVERSE_SELECTION_DELTA                 = +0.0000
```

**This zero is uninformative and must not be read as "no adverse selection."**
Every one of the 6 markets had the favorite win, so neither probability *could*
have been anything but 1.0, and their difference could not have been anything
but 0. The question you flagged — whether a 1-cent maker quote fills only when
the state is turning against it — **cannot be addressed by this sample at all**,
because the sample contains no losses.

EV figures, reported in both required forms and equally uninformative for the
same reason:

| | value |
|---|---|
| EV/share over all eligible (hypothetical always-fill) | +5.233 c |
| EV/share conditional on defensible fill | +11.667 c |
| EV adverse-selection delta | +6.433 c |
| **Unconditional EV per opportunity (nonfill = WAIT = 0)** | **+3.500 c** |

The +6.433 c "favourable" conditional delta reflects *which prices filled*
(cheap favorites at 0.69–0.75 filled; 0.99 quotes mostly did not), not any
information about correctness. With 30/30 wins it is a price-composition effect.

**No $3/window or $5/window claim is made from these six markets, and no
capacity claim either.**

---

## Phase 3 — forward sample required for a credible OOS verdict

### Cap economics, which set the precision target

Buying the favorite at 0.99: win +1.00 c, lose −99.00 c.

| true `p` | taker EV/share | maker EV/share | shares for $3 | shares for $5 |
|---|---|---|---|---|
| 0.9999 | +0.920 c | +1.000 c | 326 | 543 |
| 0.9990 | +0.830 c | +0.910 c | 361 | 602 |
| 0.9980 | +0.730 c | +0.810 c | 411 | 685 |
| 0.9960 | +0.530 c | +0.610 c | 566 | 943 |
| 0.9950 | +0.430 c | +0.510 c | 698 | 1,163 |
| 0.9930 | +0.230 c | +0.310 c | 1,304 | 2,174 |
| **0.9907** | **0.000 c** | +0.080 c | — *(breakeven singularity)* | — |
| 0.9900 | −0.070 c | +0.010 c | impossible | impossible |
| 0.9850 | −0.570 c | −0.490 c | impossible | impossible |

Observed displayed touch depth in cap states: min 140, p25 201, p50 3,129,
max 8,200 shares. So **$3/window is arithmetically reachable only if the true
hold rate is ≈99.5% or better and the window carries ≥700 shares of depth** —
inside the median but outside the thinnest quartile. $5 needs ≈1,160 shares.
These are displayed quantities, never filled, cleared or settled ones.

### Sample size

One-sided test that the true hold rate in the `|z| ≥ 2.0` / cap state exceeds
taker breakeven 0.9907, α=0.05, power=0.80, **markets as the independent unit**:

| H1 true `p` | independent markets needed |
|---|---|
| 0.9995 | 389 |
| 0.9990 | 459 |
| 0.9980 | 651 |
| 0.9960 | 1,452 |
| **0.9950** | **2,371** |
| 0.9940 | 4,305 |
| 0.9930 | 9,436 |

Accrual: 96 windows/day × 39.3% that trade at all × ~55% reaching a cap state
≈ **21 eligible markets/day**.

- p = 0.998 (strong edge): 651 markets ≈ **31 calendar days**
- p = 0.995 (modest edge): 2,371 markets ≈ **114 calendar days**

### Requirements for those markets to count

- **Full L2 ladder + authenticated trade tape + reference, synchronized, per
  window.** The current post-`ddc2195c` recorder emits full L2 at ~2 s cadence
  (observed live 2026-10-02T17:26–17:27Z, 39–43 price levels per book row), so
  this is now feasible — a capability the *historical* tape lacks. These must
  not be conflated.
- **Sub-minute BTC reference.** `btc-ref-collector` already runs and is the
  right source. A 1-minute proxy carries up to 60 s of staleness, so it
  **cannot** measure distance at T-60 or T-30 — the two checkpoints the
  hypothesis cares most about.
- **Settlement from BRTI or the venue's own resolution record**, not an OHLC4
  proxy.
- **≥20 distinct days across calm and volatile regimes**, to break the
  day/data-source confound.
- **≥150 markets per (checkpoint × `|z|`) cell** for the 25-cell grid.
- **Adverse selection needs losses.** At a 0.5% loss rate, ~600 defensible
  maker fills yield only ~3 losing fills; estimating `P(correct | fill)` to
  ±0.3% needs ~2,000+ fills, i.e. **≥2,000 placements** at the observed 30%
  fill rate.
- **Zero use of the active experiment's reserved windows** to reach any of it.

### Accrual design, without touching the live recorder

The lane should accrue **only windows released from other experiments'
reservations**. `windows.json` already carries a `reserved_denylist` enforced
**by name as well as by tape membership**, so a reserved window stays excluded
even if it later appears in a harvest. Accrual reuses the existing read-only
path (`scripts/pmus_railway_log_to_tape.py`, dedupe on
`(slug, t, px/bid, qty/ask)`) and **adds no service, no variable, no deployment
and no subscription**. The recorder is not modified, restarted or reconfigured
by this lane.

---

## Phase 4 — L5 behaviour (separate from PMUS economics)

Answered from the **existing canonical corpus already in this repo** — 35,578
heavy-side EXTENDs in 1,779 settled windows, 63,562 fills, under a
preregistration frozen in `4c94d53` before its P&L was computed. That corpus is
orders of magnitude better powered than anything this lane could build, so it is
reused rather than re-derived (`analysis/l5/RUN_LENGTH_FINDINGS.md`,
`analysis/l5/tte/btc15m_segments.json`).

### The key question: does L5 increase directional residual as the outcome hardens?

**No. The evidence points the other way, on three independent measurements.**

**1. L5 is not a late-window trader at all.** Fills by time-to-expiry at first
fill:

| TTE bucket | windows | fills | fills/window |
|---|---|---|---|
| >600 s | 495 | 63,140 | 127.6 |
| 300–600 s | 24 | 352 | 14.7 |
| 120–300 s | 7 | 76 | 10.9 |
| 60–120 s | 1 | 1 | 1.0 |
| **<60 s** | **0** | **0** | — |

Median fill TTE **431 s**; median *first* fill TTE **887 s** — i.e. he is in
within ~13 s of the window opening. **Zero fills in the last 60 seconds, one
fill inside the last two minutes, across the whole corpus.** The late-window
state that this PMUS hypothesis is about is a state in which L5 does not trade.

**2. His same-side additions go to the side getting CHEAPER and LESS likely.**
Across 35,578 heavy-side EXTENDs, raw win rate falls **48.8% → 26.5%** from run 0
to run 5+, while mean price falls **0.4984 → 0.2934** over the same span. He adds
to the side the market is marking *down*. That is dip-buying / longshot
accumulation, **not** conviction in a hardening favorite.

**3. He completes pairs; he does not run residual.** In the dominant >600 s
bucket, complement rate is **98.4%** (487 of 495 windows two-sided), matched over
gross 0.4573, median pair basis **0.9637**. The behaviour is pair completion at a
cheap basis.

### The alternative explanation wins on observable next-action behaviour

The competing hypothesis — that L5 simply buys whichever side became temporarily
cheap, with apparent late directionality emerging mechanically from price
movement — is the one the data supports. The decisive test is already in the
repo: **~92% of the apparent run-length signal is the price itself**, and
`RUN_LENGTH_SIGNAL_INCREMENTAL = NO`. Edge intervals cover zero at every run
length, and the sign does not survive chronologically — four chronological
quarters give **+0.0304, −0.0589, −0.0034, +0.0303**, with the OOS half
*positive* (+0.0074) where the preregistered rule required negative in both
halves.

So L5's directional residual is a **by-product of repeatedly buying the
cheapening side**, not evidence of state-dependent directional conviction. Per
your instruction this is behavioural evidence only and the PMUS rule is not bent
to match it — but it removes the L5-behaviour motivation for the late-favorite
hypothesis rather than supporting it.

### And L5's economics are a rebate business

Rated at the PMUS fee schedule now in force, L5's own tape is **−$1,020 net
across 2,216 windows** (gross +$2,740, fees $3,760). It is positive only once
the maker rebate is credited (+$2,257 accrued → **+$1,237**). Pre-rebate, the
behaviour does not transplant profitably. This is the single most
decision-relevant fact in Phase 4: **the money in the L5 pattern is in maker
rebate capture, not in directional conviction about late favorites.**

---

## What falsifies the late-favorite hypothesis

Collected, strongest first.

1. **The residual shrinks as distance grows.** +0.086 at `|z|` 1.0–1.5 →
   **+0.010** at `|z| ≥ 2.0`. The hypothesis predicts the opposite. The venue
   prices the tail efficiently right up to its tick cap.
2. **The 0.99 cap makes the maximum gross edge 1.0 cent and the breakeven true
   hold rate 99.07%.** Any edge must live in the third-to-fourth significant
   figure of a tail probability, where a 0.5-point error flips the sign. 26/26
   over 12 markets gives a Wilson interval of [0.74, 1.00].
3. **39% of late states have an unbuyable favorite**, concentrated at the highest
   `|z|` (mean 5.23 vs 2.53) and the latest checkpoints. Expression A does not
   exist where the hypothesis is most confident.
4. **There is no cheaper expression.** C's payoff vector, fee (`p(1−p)`
   symmetry) and queue are all identical to B. The entire maker-vs-taker lever
   is 0.080 c/share at 0.99.
5. **Queue-ahead at the cap is 1,120–28,891 shares.** Resting at the cap puts you
   behind thousands of shares competing for the same cent.
6. **L5 does the opposite** — nothing in the last 60 s, additions to the
   cheapening side, 98.4% pair completion, and `RUN_LENGTH_SIGNAL_INCREMENTAL =
   NO` with a chronologically unstable sign.
7. **An independent prior replay already failed.** `analysis/btc15m/BTC15M_VERDICT.md`
   (12,959 windows, lead-hold calibration replicating a published table to within
   1.3 points) found a best available edge of **+0.81 c against a required
   +3 c**, with **0 of 12,959 windows** qualifying.
8. **Phase 2's apparently strong numbers are zero-variance luck** — 30/30
   favorite wins across 6 markets on one day, which is also why the
   adverse-selection delta is structurally uninformative.

### What would revive it

Only one thing: evidence that the true hold rate in the cap state sits durably
above ~99.5%, measured on BRTI settlement with sub-minute reference across ≥20
days — plus evidence that ≥700 shares/window can actually be filled there, and
that maker fills at the cap are not adversely selected. That is the ~31–114 day
accrual in Phase 3. Given points 1–7, the prior on it clearing is low, and the
more promising reading of the same evidence is the rebate-capture channel that
L5's own economics point to.

**Nothing in this lane promotes a rule. No number here is realized, expected or
validated profit.**
