# L5's sequential decisions vs ours, at the four junctures

Source: `analysis/l5/l5_all_trades.csv`, 63,569 L5 BTC 15-minute fills across
527 windows -- the same series our engine trades -- against our 10 settled
windows. Script: `analysis/l5/l5_vs_us_sequential.py`. Nothing deployed.

## Scale is the first and largest difference

| | L5 (p50) | ours |
|---|---:|---|
| fills per window | **122** | 2-3 |
| contracts per fill | **10.5** | 0.96-2.18 |
| gross contracts per window | **2,363** | ~4-6 |
| matched contracts per side | **1,040** | 1.72-2.18 |

L5 trades roughly 400x our volume per window, in clips 5-10x ours.

## Juncture 1 -- when the first pair completes

| | L5 | ours |
|---|---|---|
| windows reaching a matched pair | **506 of 527 (96.0%)** | 2 of 10 (20%) |
| seconds into window at first pair | p10 12, **p50 49**, p90 243 | 33.6s and 20.8s after first leg |

L5 gets paired early and almost always. We pair in a fifth of windows.

## Juncture 2 -- additional first-leg exposure after the pair

| | L5 | ours |
|---|---|---|
| further fills after the first pair | p50 **121**, max 457 | 1 per window |
| windows that STOP after the first pair | **3 of 506 (0.6%)** | n/a |
| second-round pairs completed | continuous | **0 of 3** |

**L5 does not stop after the first pair, and neither should we.** Continuous
sequential pairing is the correct behaviour; first-pair-only is not an L5
behaviour and should be dropped as a candidate policy.

## Juncture 3 -- stranded inventory at window end

| | L5 | ours |
|---|---|---|
| windows ending with imbalance | **527 of 527 (100%)** | 6 of 10 (60%) |
| terminal \|imbalance\| | p50 123 contracts | 0.96-2.09 contracts |
| imbalance as a fraction of GROSS volume | **p50 0.094** | ~0.45-1.00 |
| imbalance as a fraction of MATCHED volume | **p50 0.164** | **~1.00** |

This is the finding that matters. **L5 strands in every single window. It
does not avoid strands -- it dilutes them.** Its residual is a sixth of its
matched book; ours is the same size as our matched book, so our EV-neutral
tail dominates our P&L. That is exactly what the campaign showed: pairs
+$0.4186 against strands -$1.8326.

## Juncture 4 -- when a complementary price appears later

L5's imbalance-to-matched ratio through the window:

    0-120s   0.643
    120-300s 0.400
    300-540s 0.206
    540-720s 0.170
    720-900s 0.164

It falls monotonically. The tempting reading is that L5 completes late. **It
does not.** Of 26,462 L5 fills at or after T+540s, only **11,982 (45.3%)
reduced |imbalance|** -- below a 50% coin flip. L5 is following its own
signal, not chasing a complement.

The ratio falls because the DENOMINATOR grows: L5 keeps accumulating matched
volume all window. Dilution, not rescue.

**Our engine does the opposite on both counts.** It chases late completion --
156 `COMPLEMENT_PAIR_ABOVE_CAP` refusals, a behaviour L5 does not exhibit --
and fails to accumulate the matched volume that makes a strand harmless.
This independently corroborates the rescue analysis, which found rescue worse
than holding even with hindsight.

## Execution economics

| | L5 | ours |
|---|---|---|
| matched pair cost | p10 0.8984, **p50 0.9623**, p90 1.0369 | 0.82, 0.85, 0.93, 0.94, 0.95, 0.96 |
| windows with pair cost < 1.00 | 398 of 506 (78.7%) | 6 of 6 |
| windows with pair cost <= 0.96 | 245 of 506 (48.4%) | 6 of 6 |
| matched-pair P&L per window | p10 -$10.15, **p50 $20.67**, mean $38.09 | +$0.11 to +$0.31 |
| windows with positive matched-pair P&L | 398 of 506 (78.7%) | 6 of 6 |

Our pair costs are BETTER than L5's median (0.9623) because our 0.96 cap
enforces it; 21.3% of L5's windows pair above par. **Our pricing is not the
problem and the cap does not need raising.** Our volume is the problem.

## The $5/window target, measured against L5 rather than against our ceiling

| basis | matched contracts/side for $5 | % of L5's p50 matched volume |
|---|---:|---:|
| our observed mean $0.0874/contract | **57.2** | **5.5%** |
| our worst observed $0.04 (at the 0.96 cap) | 125.0 | 12.0% |
| L5's own p50 pair 0.9623 -> $0.0377 | 132.6 | 12.7% |

**$5/window is roughly an eighth of what a median L5 window already
produces.** I previously called it "6-21x outside authorization"; that was a
statement about our current $20 ceiling, not about feasibility, and it was
the wrong frame. L5's median window yields $20.67 of matched-pair P&L.

Liquidity supports it: L5 moves 2,363 gross contracts per window at p50,
roughly 20x the target, and observed displayed touch depth was 199-2,659
contracts. 57-133 contracts is a small fraction of that, though queue
position (760-7,778 ahead, `our_position_within_level` UNKNOWN) remains the
execution risk and is unmeasured at that size.

## Single highest-impact change: FREQUENCY, inside the existing ceiling

**Raise the number of sequential pairing rounds per window. Do not raise the
clip, the pair cap, the loss limits, or the $20 hard exposure ceiling.**

Why frequency and not size: a window ends with at most ONE unfinished round,
so the strand is bounded at roughly one leg while matched volume grows with
every round that completes. That is mechanically L5's dilution, reached from
our side. Raising the clip instead scales the strand in lockstep with the
pair and buys no dilution at all.

From our own measured per-round economics -- $0.134 net per completed pair
(6 pairs, +$0.8051) and -$0.260 per strand (6 strands, -$1.5610):

    net(window) ~ P x 0.134 - 0.260          P = completed pairs

    P=1   -$0.126   (observed: -0.1506, -0.1766)
    P=2   +$0.008   break-even
    P=3   +$0.142
    P=5   +$0.410

**Expected improvement at P=5: from the observed -$0.15/window to about
+$0.41/window, a swing of roughly +$0.56 per window.**

### It fits inside the existing hard ceiling

Matching does NOT release capital -- only settlement does
(`cash_committed()` skips only legs marked `settled`, written solely by
`retire_settled_market`). So R sequential rounds need R x the per-round
obligation, which measured **$2.99** for a 1.77-contract pair.

    envelope $6.00  -> 2 rounds   (what we ran; matches observed behaviour)
    envelope $9.00  -> 3 rounds
    envelope $15.00 -> 5 rounds   -- still under the $20 hard code ceiling

So the change is `L5_MAX_CASH_DEPLOYED_USD` and `L5_TS_ROUND_ALLOCATIONS`
raised together, with `MAX_TOTAL_EXPOSURE_USD = 20`,
`OPENING_LEG_MAX_CONTRACTS = 3`, the 0.96 cap, the 0.20 floor, maker-only,
the daily and cumulative loss halts and every other gate **unchanged**.

### Capital for the full $5/window, stated separately

57-133 matched contracts/side at ~$1/share per resting leg needs roughly
**$150-300 of exposure headroom**, i.e. 7.5-15x the current $20 ceiling.
That is a capital authorization decision, not an engineering one, and it
should not be taken before the frequency mechanism is demonstrated at the
current clip.

## Honest limits

- The "strand bounded at one leg per window" assumption rests on **n=2**
  windows (2100z, 2115z each stranded exactly one leg across two rounds).
  It is directionally supported by L5's falling ratio but is the weakest
  link in the argument and is what the next campaign should test.
- L5's $20.67 median is its matched-pair line only. We cannot see L5's
  settlement outcomes, so its residual P&L is unknown; residual is
  EV-neutral in expectation, which is why the matched line is the right
  comparison, but it means L5's realized net is not established here.
- 10 settled windows on our side, 6 pairs, 6 strands, 3 second-round
  openings.
