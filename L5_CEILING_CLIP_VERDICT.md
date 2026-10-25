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
