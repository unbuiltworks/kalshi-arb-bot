# Fixed-side directional baselines — 178-window OOS

Diagnostic only. One unit, one predeclared causal entry, hold to settlement. No
EXTEND, complement, SELL, hedge, resize, skipped window, model or threshold.
Run `37247157709`, artifact `fixed-side-baseline-37247157709`.

## The entry point applied cleanly — no selection was introduced

The entry rule is inherited, not chosen: the first second at which
`pmus_path_economics.eligible` passes, the same predicate the completed
sequential study used. It resolved to:

| entry `seconds_remaining` | min | max | median | distinct values |
|---|---|---|---|---|
| | **885** | **885** | **885** | **1** |

**All 178 windows entered at exactly 885 seconds remaining.** One distinct value,
so the timestamp is literally identical across the sample. No entry time was
searched, and none could have been — the rule is a single pre-existing predicate
evaluated forward in time. This is the clean case the instruction asked about, so
there was no need to stop.

Fees: `pmus_fees.taker_fee`, venue cent, 1 unit. Settlement base rate **UP in
53.93%** of the 178 windows.

## Economics

| | ALWAYS_UP | ALWAYS_DOWN | ALTERNATE (anchored UP) |
|---|---|---|---|
| windows | 178 | 178 | 178 |
| wins / losses | 96 / 82 | 82 / 96 | 81 / 97 |
| settlement win rate | 53.93% | 46.07% | 45.51% |
| mean executable purchase price | 0.5107 | 0.4994 | 0.4942 |
| gross P&L | **+5.09** | −6.90 | −6.96 |
| fees | 3.51 | 3.51 | 3.51 |
| **net P&L** | **+1.58** | **−10.41** | **−10.47** |
| net / window | **+0.00888** | −0.05848 | −0.05882 |
| median / window | +0.35 | −0.40 | −0.40 |
| positive-window % | 53.93 | 46.07 | 45.51 |
| worst window | −0.70 (`10-03-0600z`) | −0.74 (`10-03-1730z`) | −0.71 (`10-03-0715z`) |
| longest losing streak | 6 | 6 | **8** |
| max cumulative drawdown (unit size) | 6.26 | 11.51 | **16.89** |

Fees are identical at 3.51 across all three (0.0197/window, ~3.9% of a ~$0.50
stake) because the fee is convex and near-symmetric about $0.50.

## Transition matrix — descriptive only

| | next UP | next DOWN | row n |
|---|---|---|---|
| **prev UP** | 47 | 49 | 96 |
| **prev DOWN** | 48 | 33 | 81 |

- **P(next UP \| prev UP) = 0.4896**
- **P(next UP \| prev DOWN) = 0.5926**
- difference **+10.3 pp**, in the **reversal** (anti-persistent) direction
- χ² = 1.875, df = 1, z = 1.369, **two-sided p = 0.171**

No continuation or reversal strategy is constructed or optimised from this.

---

## The five answers

### 1. Is always-UP positive expectancy after executable price + fees?

**Its net is positive, but no — this is not evidence of positive expectancy.**

Net +1.58 over 178 windows is **+0.00888/window** against a per-window standard
deviation of 0.4985, so the standard error of the mean is 0.0374 and
**t = 0.238**. That is indistinguishable from zero.

The whole result is one number: the realized **53.93% UP rate**, which is itself
only **z = 1.05** from 50% (p ≈ 0.29). Break-even is mean price plus mean fee =
**53.04%**, so the entire "edge" is **0.89 percentage points** of win rate, against
a 3.75 pp standard error — **0.24 SE**.

Mechanically, always-UP is a **levered long BTC position** held across 46
contiguous hours in which BTC happened to finish above its 15-minute strike 53.93%
of the time. It is a directional accident of this sample, not an edge.

### 2. Is always-DOWN?

**No.** −10.41 net, **−0.0585/window**, 46.07% win rate, max drawdown 11.51. It is
the mirror of always-UP *plus* the same 3.51 of fees, which is why both sides
cannot be positive: the fee is paid either way.

### 3. Is mechanical alternation?

**No.** −10.47 net, **−0.0588/window** — and it is the *worst* of the three on risk:
longest losing streak **8** and max drawdown **16.89**.

This is expected and not informative. Alternation is outcome-independent, so it
simply samples roughly half UP bets and half DOWN bets and converges to their
average (≈ −0.025/window), paying full fees throughout. Its realized 81 wins
against ~89 expected is within noise (SE ≈ 6.6 wins). **Alternation cannot create
an edge from a reversal tendency**, because it alternates *our side* regardless of
what actually settled — it is not conditioned on the previous outcome at all.

### 4. Is there meaningful descriptive next-window dependence?

**There is a visible reversal tilt, and it is not statistically significant.**

0.5926 after a DOWN against 0.4896 after an UP is a 10.3 pp anti-persistent gap —
the largest single signal in anything measured in this whole programme. But
χ² = 1.875 on 1 df, p = 0.171. With 96 prior-UP and 81 prior-DOWN windows, a 10 pp
effect simply cannot be separated from zero.

Treat it as a **hypothesis generated, not a finding**. Note also it is *opposite* to
the direction most people assume (momentum), which makes it more interesting and
also more deserving of suspicion.

### 5. Does any baseline warrant a separately preregistered fresh-data test?

**The three fixed baselines: no.** Always-DOWN and alternation are clearly negative
with no mechanism to appeal to. Always-UP is not an edge, it is BTC drift over 46
hours, and re-testing it would be re-testing "did BTC go up".

**The next-window reversal dependence: yes — one test, and it is unusually cheap.**

It is worth preregistering for reasons that have nothing to do with its P&L here:

- **It has no free parameters.** "Buy the side opposite the previous window's
  settlement, 1 unit, at 885 seconds remaining, hold to settlement" is fully
  specified. There is nothing to sweep, so there is nothing to overfit.
- **It needs no new execution capability.** A single taker buy held to settlement
  is exactly what the already-proven live path does — the one that produced a real
  filled, settled, +$0.06 realized probe. It requires no SELL path, which is the
  blocker that killed the residual-exit idea.
- **It is falsifiable fast.** To detect this same 10.3 pp effect at 80% power and
  5% two-sided needs **≈363 windows per prior-outcome group, ≈727 windows total** —
  about **7.6 days** of continuous 15-minute recording. The recorder already runs
  24/7 and would collect it passively.

**Required before it is run, declared now:**

1. **Sample size fixed in advance at ~727 windows** (or ~364 per prior-outcome
   group), and the test evaluated **once**, on windows that start *after* the
   preregistration timestamp.
2. **These 178 windows are retired from this question.** They generated the
   hypothesis and cannot also test it.
3. **The decision rule and the success criterion written down first**: the
   criterion should be the P(UP\|prev DOWN) − P(UP\|prev UP) difference with its
   confidence interval, *not* P&L, because P&L on a near-50/50 binary is enormously
   levered and will look impressive from noise alone.
4. **An economic bar separate from the statistical one.** Break-even is ~53% win
   rate; the target is ≥$5 net per traded window within $200. Both must clear, and
   the statistical test must clear first.

**One honest flag.** This is a **directional** hypothesis, which sits against the
project's own operating thesis — `CLAUDE.md` states the strategy is not directional
and that "historical evidence does not support" residual directional skill. So this
recommendation is a *departure* from the L5 replication programme, not part of it.
I am surfacing it because you asked which baseline warrants a test and this is the
only one that does; whether a cheap directional side-experiment is worth the
attention is your call, not mine.

## Limits

- 178 windows, 46 contiguous hours, one venue regime. Everything above is
  conditional on that stretch of BTC behaviour.
- Unit size is 1 contract; touch depth was not binding at that size but was not
  re-verified here.
- Entry is a single timestamp (885 s). No other entry was examined, by design.
- No live order was placed and nothing was deployed.
