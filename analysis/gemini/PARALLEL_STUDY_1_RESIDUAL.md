# Parallel study 1 — terminal residual, and what L5's own history says about it

**Status: ANALYSIS ONLY. COHORT-2 untouched.** Written on branch
`claude/l5-parallel-analysis-cohort2` while run 35721894867 was live. Nothing
here was pushed to the active branch, no workflow was dispatched, no account
was mutated, and no executor module was modified.

**Nothing here is REALIZED.** Every figure is settlement-implied, computed
from venue-confirmed fills and published resolutions.

---

## 1. The framework (`residual_analysis.py`)

A binary market pays $1 on a **matched pair** whatever happens, so matched
inventory is a locked, outcome-independent cashflow. Everything else is a
**directional bet** the operating model explicitly refuses to assume has
skill. One net number cannot tell them apart, and a good matched book can
hide a bad residual habit indefinitely.

```
m  = min(yes_qty, no_qty)
r  = |yes_qty - no_qty|
matched_pnl  = m x (1 - avg_yes - avg_no)        OUTCOME-INDEPENDENT
residual_pnl = r x [dominant won] - r x avg_dominant
-------------------------------------------------------------
matched_pnl + residual_pnl - fees == net         asserted per window
```

`check_identity` evaluates that equality on every row. A decomposition that
does not re-add to the number it decomposes is not a decomposition.

**The arithmetic is rational, not decimal, and that was a real bug.** EP016's
first window averaged $1.15 over 3 YES; Decimal carries it as `0.3833…33` and
multiplies back to `1.1499…99`, so the first version missed its own total by
1e-28. Harmless in dollars, fatal to the claim that the split is exact.
Per-leg cost now comes from **exact per-leg spend summed from the venue's own
fill events**, with average-times-quantity only as a declared fallback
(`cost_basis` says which was used).

Validated on the four EP016 windows: identity holds on all four, our per-leg
split agrees with the controller's own `total_spend_usd` and `matched_qty`,
and the total re-adds to the established **+$1.14**. 23 tests.

### EP016 re-read through the decomposition

| window | y/n | matched | pair cost | matched P&L | residual | won | residual P&L | net |
|---|---|--:|--:|--:|--:|---|--:|--:|
| …0230-UP | 3/4 | 3 | 0.7258 | **+0.8225** | 1 NO | no | −0.3425 | +0.48 |
| …0245-UP | 2/0 | 0 | — | 0 | 2 YES | yes | +1.29 | +1.29 |
| …0300-UP | 4/2 | 2 | 0.835 | **+0.33** | 2 YES | no | −0.73 | −0.40 |
| …0315-UP | 2/1 | 1 | 0.875 | **+0.125** | 1 YES | no | −0.355 | −0.23 |
| **total** | | | | **+1.2775** | | | **−0.1375** | **+1.14** |

**The entire positive result was matched economics. The directional residual
lost money.** That is invisible in the +$1.14 headline and is the single most
useful thing the split produces.

---

## 2. L5's own history, decomposed the same way

`analysis/l5/l5_windows_summary.csv` — **2,216 windows, 22 trading days,
2026-03-19 to 2026-08-31**, BTC and ETH, 5-minute and 15-minute.

**Two independent validations before any claim:**

1. L5 **sells in 2 of 2,216 windows (0.1%)** — effectively buy-and-hold to
   settlement, so the framework's hold-to-settlement assumption is sound.
2. My decomposition sums to **−$1,050.57**; L5's own `pnl_derived_resolution`
   column sums to **−$1,050.36**. Agreement to **$0.21 across 2,216 windows**,
   independently derived. The identity holds on all 2,216.

### Finding A — L5 almost never ends balanced

**23 of 2,216 windows (1.0%) end with zero residual.** Median terminal
residual is 55 shares against a median terminal inventory of 191. Carrying
directional residual into settlement is L5's *normal* case, not an error
state. Any model that treats residual as a defect to be eliminated is not
describing this trader.

### Finding B — matched is the edge; residual is where it goes

Consistent across two regimes that differ by a factor of ~23 in daily spend:

| regime | n | bought | matched | (% of spend) | residual | net |
|---|--:|--:|--:|--:|--:|--:|
| Mar–Apr (small) | 216 | $29,979 | **+$1,025.87** | **+3.42%** | −$623.46 | +$402.41 |
| Aug 28–31 (scaled) | 2,000 | $698,523 | **+$17,887.00** | **+2.56%** | −$19,339.97 | −$1,452.98 |

Matched economics run **+2.5% to +4.5% of spend in every regime and every
residual band**. That is the edge, and it is stable. The residual is what
turns it negative at scale.

### Finding C — partial completion is the worst place to end

By residual as a share of terminal inventory (Aug regime, where the volume is):

| residual share | n | matched | residual | net |
|---|--:|--:|--:|--:|
| 0% balanced | 31 | +$430.85 | −$0.50 | **+$430.35** |
| 0–10% | 366 | +$10,382.71 | −$6,435.46 | **+$3,947.26** |
| 10–25% | 344 | +$6,203.64 | −$10,493.82 | **−$4,290.18** |
| 25–50% | 377 | +$934.75 | −$4,365.43 | **−$3,430.67** |
| 50–90% | 279 | −$71.25 | +$589.36 | +$518.10 |
| 90–100% one-sided | 603 | +$6.30 | +$1,365.86 | +$1,372.16 |

The Mar–Apr regime reproduces the same sign pattern on 1/23 the money
(10–25%: −$99; 25–50%: −$312). **The 10–50% band is where the losses are** —
worse than balanced *and* worse than fully one-sided. A half-completed window
carries the residual's full variance without the matched book to pay for it.

### Finding D — the $480/day target is not reproduced by this corpus

| | |
|---|---|
| days observed | 22 |
| total settlement P&L | **−$1,050.36** |
| positive days | 16 of 22 |
| best / worst day | +$1,898.17 / −$2,242.44 |
| **days reaching +$480** | **2 of 22** |
| mean per day | **−$48** |

The four August days carry 90% of the windows and ~$700k of the spend; their
mean is **−$363/day**. I am not claiming L5 is unprofitable — this corpus is
22 days, the reconstruction is settlement-only, and Polymarket incentive
income is **not** in it (and would be a separate line in any case, never
trading P&L). What I can say is that **$480/day is not visible in the data we
hold**, and the operating model already calls it "a target to measure
against, never an assumption." This is the measurement.

---

## 3. TTE and the late-EXTEND question — correlation, not a cutoff

Trades classified OPEN / EXTEND / COMPLETE by their effect on running
imbalance, over 71,984 rows:

```
OPEN      2,216    median TTE 261s
EXTEND   35,786    median TTE 368s
COMPLETE 33,982    median TTE 401s
```

**EXTEND and COMPLETE are near 1:1 and interleave throughout the window.**
L5 has no distinct "extend early, complete late" phase structure.

Grouping windows by the TTE of their **last** EXTEND gives opposite signs for
5m and 15m:

- **5-minute**: last EXTEND inside 30s → **−$1,644** net (matched itself goes
  negative, −$162). Windows whose extending stopped early (120–300s) → +$1,139.
- **15-minute**: last EXTEND inside 30s → **+$1,656**; stopping at 60–300s → −$1,999.

**I am not proposing a cutoff from this.** The grouping is confounded: 15m
windows with a very late last-EXTEND also have the *lowest* residual share
(12.2%), i.e. they are the actively-worked windows, while 5m windows with a
late last-EXTEND have 45.8% residual share. TTE-of-last-EXTEND is a proxy for
how hard the window was worked, not an independent variable. Separating them
needs a controlled comparison at matched residual share, which is the next
step and is **not** done here.

---

## 4. WS vs polled — the latency audit

| input | mechanism | cadence |
|---|---|---|
| decision loop | `asyncio.sleep(interval_s)` | **fixed 5.0 s** |
| outcome book | `MD.outcome_book()` **HTTP poll** inside `state_from_venue` | once per decide |
| authoritative state | `authoritative_state()` **REST poll** via `refresh_fn` | pre-decide, and again pre-place |
| order events (`m`, `n`, fills) | **WebSocket** `_drain_events` | drained *at the top of the 5 s tick* |

**No meaningful WS event triggers an immediate re-evaluation.** Fills arrive
over the socket but are consumed on the next scheduled tick, so a fill landing
just after a cycle waits up to ~5 s plus refresh round-trips before the
controller reacts to it. Under a 30-second retention floor that is up to ~17%
of an order's life spent on stale inventory. This is a **finding, not a
recommendation** — changing it touches the frozen controller.

---

## What is NOT done

- **Aggressive-maker offline study (+1¢/+2¢/spread-dependent).** Not started.
  The tape exists (`tape_replay.json`, `queue_replay.json`, `large_prints.json`)
  and a queue-position model already exists (`completion_model_b.QUEUE_BIASED`).
- **Sports/PMUS portability audit.** Not started. `analysis/pmus/` has
  `sports_habitat_discovery.json`, `live_slate_habitat.json`, `chain_and_flow.json`.
- **Rebate payout reconciliation.** Reader built and armed for after 21:00Z
  (the payout has not happened yet); expectation committed in advance at `d28f289`.
- **`n=0.0000` maker-fee discrepancy.** Not resolved. Published schedule says
  maker rate 0.0175 with fees rounding **up** to the cent, implying ~$0.01 on a
  C=1 P=0.45 fill, yet all 18 fills report zero. Needs settled-day account
  records to establish what `n` represents.
- **Any strategy recommendation.** Deliberately withheld until COHORT-2
  evidence exists, per instruction.
