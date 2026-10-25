# Ceiling x clip, whole-sequence: **FAIL** — not established, and not establishable as run

PMUS only. Fee schedule from `src/pmus_fees.py` exclusively; rebates only where the venue has
actually credited them (25 observed credits, $0.42). No Kalshi constant appears anywhere below.

## VERDICT: FAIL

Not "the strategy is bad". **The experiment does not establish positive net expectancy, and
at this signal-to-noise it cannot.** The reported win is dominated by residual settlement
variance at a magnitude ~56x the quantity being measured.

## 1. Pair economics at the authorized clip — PMUS schedule only

q = 1.56 per leg. Maker fee **zero**. Credited rebate **$0.00** at this clip (raw $0.0048,
cent-rounded away). No per-order fee exists on PMUS.

| ceiling | gross/pair | credited | **net/pair** |
|---|---|---|---|
| 0.96 | $0.0624 | $0.00 | **$0.0624** |
| 0.99 | $0.0156 | $0.00 | **$0.0156** |
| 1.00 | $0.0000 | $0.00 | **$0.0000** |

**A 0.96 completion is worth 4.0x a 0.99 completion.** The headline hides this: 0.99 wins only
on **frequency** (it accepts 100% of offered frames against 9.5% for 0.96), never on margin.

## 2. REQUIREMENT 6 — does 0.99 survive sequential fills? NO, the signal is not there

The sweep reported **+$0.9212 over 10 OOS windows** at ceiling 0.99, clip 1.56.

For that to be pair economics it needs **59.1 completed pairs — 5.9 per window**, which at
$1.56 of capital per pair is **$9.21 of exposure, 1.8x the authorized $5 aggregate** under
`cash_policy NO_RECYCLING`.

Bounding it the generous way instead — assume **every** window ran the maximum 3 concurrent
pairs that fit inside $5:

| | |
|---|---|
| max pair income per window | 3 x $0.0156 = **$0.0468** |
| max over 10 windows | **$0.4680** |
| reported headline | **$0.9212** |
| **therefore NOT pair economics** | **>= $0.4532, i.e. >= 49%** |

The remainder is **residual settlement**, which is EV-neutral at market prices and pure
variance. And the variance is enormous relative to the effect: a stranded window at q=1.56
costing $0.44/contract pays **+$0.87 if the residual wins and -$0.69 if it loses** — a swing
of **56x the $0.0156 a completed pair earns**.

That fully explains their own dispersion: **sd 0.528 on a mean of 0.9212 (57%)**, with a
**negative worst seed (-0.1560)**. The marginal claim fails the same test: one extra matched
window in ten can contribute at most $0.047, so the reported **+0.0897** for 0.96 -> 0.99 also
exceeds what pair economics can deliver.

**Conclusion: the sweep measured residual coin-flips, not completion economics.** The sign of
the marginal table was right — loosening into the offered range pays, past break-even costs,
beyond the offer is inert — but the magnitudes are noise.

## 3. What would establish it

| | |
|---|---|
| signal | $0.0468/window (3 pairs, the most $5 allows) |
| noise | $0.2733/window sd from residuals alone at an 11% strand rate |
| ratio | **5.8x** |
| n for 80% power | **267 windows (~2.8 days)** |
| n for 95% power | **442 windows (~4.6 days)** |
| their sample | n = 10 -> **27x short** |

**But more windows is the wrong fix.** The noise *is* the residual, and the residual is also
the loss. Eliminate strands and the effect becomes measurable at small n; carry them and no
feasible sample resolves a 1.56-cent quantity. **The measurement problem and the economic
problem are the same problem.**

## 4. Requirements I could not execute, stated plainly

Requirements 3, 4 and 5 need a PMUS sequential dataset I do not have and cannot build:

- my local PMUS data is **4 markets / 678 lines**; 42 two-sided bbo frames total
- the peer's `pmus_windows.json` (19 windows) is **loaded from disk and not committed** —
  derived data, gitignored, same as mine
- the 2.19M-row PMUS tape is on a **Railway volume this container cannot reach**
- and the PMUS tape carries **no print rows at all**, so queue position, fill-before-cancel
  races and sweep identification are **not derivable from PMUS data by anyone**, including the
  peer. Fills in any PMUS sweep are a **Bernoulli draw on a measured 0.4325 rate**, never an
  observed fill.

So per-window net, strand rate, completed-pair rate and worst drawdown exist only for n=10
from a single ~7-hour slice, which §2 shows is noise-dominated.

**Contamination labels, explicitly:**

- `analysis/adverse_entry/windows.json` (130 windows) — **CONTAMINATED and KALSHI.** I swept
  ceilings, clips and cancellation thresholds on its BTC15m half during this session. Mixes
  three venues (77 of 130 carry Gemini-sourced `ref_lag` labels). Not usable as a holdout and
  not usable for PMUS economics at all.
- the peer's 19 PMUS windows — **uncontaminated but n=10 OOS from one slice**, and fills are
  modelled rather than observed.
- **there is no untouched chronological PMUS validation period in existence yet.** One cannot
  be frozen from data we hold.

## 5. Requirement 7 — against L5 and against the current engine

| | L5 (PMUS, venue records) | current engine | the 0.99 candidate |
|---|---|---|---|
| fills/window | **32.48** | 0.25-0.50 (`max_live_orders=1`) | unchanged, 1 order at a time |
| income mix | **83% incentives**, trading edge +0.18% of turnover | rebate $0.00 at authorized clip | rebate $0.00 at authorized clip |
| per completed pair | n/a (continuous turnover) | refuses ~90-100% of offers | $0.0156 |
| turnover/day | ~$44,848 | ~$1-20 | ~$1-20 |

The candidate does not move the engine toward L5 on the axis that mattered for L5: **he earned
83% of his money from incentives at $45k/day of turnover, and at our authorized clip the
credited rebate rounds to exactly zero.** Raising the ceiling changes how often we complete; it
does not change clip, concurrency, or incentive capture. Capacity at the best cell is
~$28/day on one series against a $480/day target — **~17x short**.

## 6. Smallest justified next action

**Not the cap change.** Not more sweeping. Not a live probe.

The one action that is both necessary for the economics and necessary for the measurement is
the same: **stop carrying residuals.** Everything in §2 and §3 says the residual is
simultaneously the entire loss, the entire noise, and the reason no feasible sample can
resolve the effect.

Concretely, and in this order:

1. **Measure the completion rate achievable with a complement posted from the moment the first
   leg fills**, on the peer's 19 PMUS windows, with fills still drawn from the measured 0.4325
   rate. This is a strand-rate measurement, not a P&L measurement, so it is **not** swamped by
   residual variance — the quantity is a proportion, not a cent. It needs no new data, no
   capital and no orders.
2. If achievable strand rate falls below ~**3%**, the pair signal clears the residual noise at
   n in the low hundreds and the ceiling question becomes answerable. If it does not, the
   completion thesis fails on PMUS BTC15m regardless of the ceiling, and the next question is
   market selection, not configuration.

Requirement 8 is satisfied by doing nothing to production: `armed=false`, `shadow_lock=true`,
`campaign_id=null`, cap unchanged at 0.96.

---

# ADDENDUM — the peer widened n and the result REVERSED. FAIL is now empirical.

Commit `9ce6dd0b`. Harvested four more hours from the running collector: 19 -> 27 windows,
10 -> 14 OOS. **The central finding reversed sign at every clip.**

| marginal effect of 0.96 -> 0.99 | clip 1.56 | clip 2.50 | clip 5.00 |
|---|---|---|---|
| n=10 OOS | +0.0897 | +0.1438 | +0.2875 |
| **n=14 OOS** | **−0.1576** | **−0.2525** | **−0.5050** |

Worst seed negative on both runs and worse at n=14: −1.0920, −1.7500, −3.5000.

**This is the outcome §2 predicted.** I argued the +$0.9212 was ≥49% residual settlement
variance rather than completion economics, and that re-running would therefore land
differently. It did, and it landed on the other side. **The FAIL verdict is no longer an
argument from arithmetic; it is an observed reversal.**

## It inverts the shared thesis, and that has to be recorded

**At n=14, more completion produced LESS money.** Clip 5: the 0.96 arm matched 10.6 windows
for **+2.8237**; the 0.99 arm matched 11.3 windows for **+2.3188**. If completion were the
money, that could not happen.

Both this session and the peer independently converged on "the money is in completion, not
capture", and I reported it upward as the better-supported reading. **It is withdrawn as
established.** It remains the best available hypothesis and the residual decomposition still
implies it, but the first real test did not cooperate and at n=14 it points mildly the wrong
way. The peer's own caution is worth preserving verbatim in substance: *agreement between two
sessions is not evidence; we were agreeing about a hypothesis.*

## Power: their number supersedes mine

| | quantity | sd | n required |
|---|---|---|---|
| mine (§3) | total pair income/window vs zero, 0.0468 | 0.2733 | 267 at 80% |
| **theirs** | **marginal difference between arms, 0.0113** | **0.1604** | **~812 at 2 sigma** |

Checked: `(2 x 0.1604 / 0.0113)^2 = 806`, and 806/96 = 8.4 days — matches their ~812 / ~8.5
days, and all three clips independently agree at ~811. **Theirs is the correct quantity**: the
experiment asks whether two ceilings differ from each other, not whether pair income differs
from zero. ~812 windows is **~8.5 days** of continuous single-series collection.

## A known gap worth recording: every Kalshi number here rests on an unsourced fee rate

Verified independently at the peer's prompting, and the asymmetry is real:

| | Kalshi | PMUS |
|---|---|---|
| venue capture | **NONE** | `analysis/pmus/pmus_published_fee_schedule.json`, **99 rows** with source/effective/formula/thetas |
| validation | none | **20 tests** in `tests/test_pmus_fees.py` |
| what exists | `config/settings.py:63 TAKER_FEE_RATE = 0.07` and a docstring in `src/fee_calc.py` — repo assertions only | row-by-row validated |

So **every Kalshi economic figure in this repo rests on an unverified fee rate**, including the
130-window baselines and the peer's own Kalshi adverse-selection measurement. Not necessarily
wrong — **unsourced**. This is why the `PER_ORDER` relabel kept "reportedly" rather than
claiming a source.

## Revised next action — same action, different and stronger justification

§6 proposed measuring the achievable strand rate on the premise that completion is where the
money is. **That premise is now weakened, but the action survives and matters more**, for a
reason that does not depend on it:

**The residual variance is what blocks every P&L question, not just this one.** A completed
pair is worth ~1.5 cents; an unmatched residual carries ~$0.44 of zero-mean sd at clip 1.56 —
about thirty times the effect. So which arm "wins" is decided by residual luck. If the strand
rate can be driven near zero the variance collapses and the economics become measurable at
small n; if it cannot, **no P&L question on a single series is resolvable by analysis at all**,
whatever the ceiling.

Strand rate is a **proportion**, so it is measurable at n=27 where a cent is not.

That reframes the program's open question exactly as both sessions now independently reach it:
**the per-window edge is too small relative to residual variance for analysis to settle on one
series.** That is an argument for concurrency and capital — the owner's decision — and not for
more sweeping by either of us.

**Production untouched: `armed=false`, `shadow_lock=true`, `campaign_id=null`, cap 0.96.**

---

# ADDENDUM 2 — requirement 7, answered from L5's own data, and a confound in the drift claim

## The peer's residual-drift correction: arithmetic verified, mechanism not

They withdrew their own "zero-mean residual" premise. Every figure checks out exactly:

- `residual_net` **−$85.2296** over 355 settled windows; at a per-window sd of $0.44 the sd of
  that total is $8.29, so it sits **−10.3 sd** below zero, and stays past −7.5 sd at any
  plausible per-window sd (0.30 -> −15.1, 0.60 -> −7.5)
- stratified: `|resid| 1-2` **+$0.2112/window** (159 windows); `|resid| 3+`
  **−$0.6932/window** (167 windows) — **3.3x worse**
- the strata sum to −$82.19 over 326 windows against a −$85.2296 total over 355, so **29
  windows and $3.04 are unaccounted** — presumably `|resid| = 0` plus fractional `|resid| < 1`,
  which our 1.56-contract clips produce. Minor, but the strata are not exhaustive.

**The residual is not zero-mean noise. That part is established.** But their *mechanism* —
the cap refuses completion, forcing accumulation into the 3+ stratum where the drift lives —
is **not** established by the stratification, for two reasons:

1. **Confound.** A large adverse move causes *both* a large unmatched residual *and* a large
   loss. The stratification is observational, so "3+ residual windows lose more" is equally
   consistent with the move causing both. Nothing here controls for the move.
2. **Their own sweep is evidence against it.** If the cap caused the drift via accumulation,
   raising the ceiling should have reduced accumulation and improved net. It did the opposite
   at n=14, at every clip.

## And L5's own data is the control that breaks the causal story

From `analysis/l5/inventory_trajectory/trajectory.json` — **L5's own 527 windows, 62,020
fills, all buys** — at his second fill:

| | |
|---|---|
| windows two-sided by fill 2 | **165 of 512** |
| `inventory_imbalance_rel` median | **1.0000** (entirely one-sided) |
| `matched_qty` median | **0.0** — typically nothing matched yet |
| fills before his first pair | median **3**, p90 **9** |

**L5 carries one-sided inventory as a matter of course, and he is profitable.** So a negative
drift cannot be a property of accumulation as such. It is a property of **our** accumulation —
i.e. of what we are selected into — which is the adverse-selection finding, not a cap finding.

## REQUIREMENT 7, answered properly at last

L5's **actual pair cost at first formation**, 506 windows:

| p10 | median | mean | p90 |
|---|---|---|---|
| 0.9018 | **0.9900** | 0.9911 | **1.0821** |

His matched VWAP pair cost after two fills: median **0.99**, p90 **1.06**.

**Our cap is 0.9600 — below L5's median.** Interpolating p10 -> median (an estimate, not a
measurement) puts the share of his formations at or under 0.96 at **~36%**, so **our cap
refuses roughly 64% of what L5 himself did.**

That is a **replication divergence by construction**, and it is independent of whether
changing the cap pays. `CLAUDE.md` makes sequential behavioural replication the primary
strategy and sets the standard as "at this exact market state, what did L5 do next, and what
would our clone have done?" On this dimension the answer is: he formed at 0.99, and our clone
is configured to refuse it.

**Both of these are true and must travel together:**

- **0.99 is behaviourally faithful to L5** — established, from his own data.
- **0.99 is not shown to pay** — the sweep reversed at n=14, and ~812 windows are needed to
  separate the arms.

So the cap is a **confirmed replication divergence** and an **unresolved economic question**.
Those call for different justifications, and neither on its own justifies a live change.

**Production untouched: `armed=false`, `shadow_lock=true`, `campaign_id=null`, cap 0.96.**
