# Bounded inventory controller: k sweep, band sweep, and L5's own sequential economics

Status: RESEARCH. Nothing here is authorized for live deployment. k stays 1,
the 0.96 pair ceiling stays, clip stays 1, HALT stays asserted.

## 0. Why this document exists

Campaign E (`cpc-btc-updown-15m-2026-11-05-0245z`) settled **-$0.2900** on
3 contracts and 1 completed pair. The question was whether a risk control
stopped accumulation, and whether a larger residual cap would produce
repeated profitable pairs.

## 1. Campaign E: no risk control blocked accumulation

501 `L5_MISSED_PAIR` records, every one at 02:53:45 or later:

| gate | count | first -> last |
|---|---|---|
| `DECISION/DECIDE_COOLDOWN` | 200 | 02:53:47 -> 02:59:56 |
| `OPENING/PAST_COMPLETE_CUTOFF` | 160 | 02:55:00 -> 02:58:56 |
| `OPENING/WORST_REACHABLE_PAIR_ABOVE_REDEMPTION` | 61 | 02:53:47 -> 02:54:59 |
| `DECISION/PAST_TERMINAL` | 48 | 02:55:02 -> 02:59:59 |
| `OPENING/PAIR_COST_ABOVE_CEILING` | 19 | 02:53:45 -> 02:54:25 |
| `DECISION/PAIR_ABOVE_REDEMPTION` | 13 | 02:53:50 -> 02:54:56 |

Zero `RESIDUAL_ABOVE_CAP`. Zero `MAX_LIVE_ORDERS`. Zero cash/exposure/budget
refusals. End-of-window budgets: OPEN 3/4, COMPLETE 1/6, REPLACE 2/6,
submissions 6/16, `completion_reserve.may_open: true`.

So **k=1, clip=1, the two-order limit and the cash reservation were not
binding.** The binding constraints were pair economics (93 refusals,
02:53:45-02:55:00) and then the 600s completion cutoff (208 refusals, from
exactly 02:55:00).

Honest gap: there are **no decision records between 02:45:00 and 02:53:45**.
`L5_MISSED_PAIR` emits only when a pair candidate is formed and refused, so a
tick that formed no candidate leaves no trace. That span is unobserved, not
proven empty, and needs per-tick opening-decision instrumentation.

## 2. Method

`inventory_k_replay.py` keeps the loader, fee schedule and conservative fill
proxy of `grok_inventory_policy_replay.py` and replaces only its
single-position state machine with a real two-sided inventory, so k>1 is
representable at all.

- Fill proxy: a resting buy on side s at p fills when `cross(s,bid,ask) <= p`,
  i.e. once the book has **traded through** our level. For UP, `cross` is `ask`
  and `touch` is `bid`, so a quote placed behind the touch can never fill on
  its placement tick. No queue model, no partial fills. **Nothing here is a
  fill or a realized profit.**
- Settlement: average cost basis per side. Validated against the venue's own
  `L5_WINDOW_ACCOUNTING` for 0245z: matched +$0.1450, residual -$0.4350,
  net -$0.2900 -- reproduced to the cent on all three lines.
- Rebates: `round2(theta*C*p*(1-p))` is $0 at C=1 for every price, which the
  venue's `actual_fees_usd $0` over 6 maker orders confirms. Rebates are a
  separate column and are never added to net.
- Data: 19 usable windows (34 tape files), 2026-10-28 -> 2026-11-02, ~48
  two-sided BBO samples per window (one per ~19s, vs the engine's ~5s tick).
  Chronological split: 11 train / 8 holdout.

## 3. k sweep (band 0.30-0.60, clip 1, maker only, stable quoting)

| k | train | HOLDOUT | full | matched$ (full) | resid$ (full) | strand |
|---|---|---|---|---|---|---|
| 1 | -2.53 | -0.95 | **-3.48** | +0.73 | -4.21 | 33% |
| 2 | -4.22 | -0.27 | **-4.49** | +1.56 | -6.05 | 38% |
| 3 | -4.70 | -0.42 | **-5.12** | +2.06 | -7.18 | 40% |
| 5 | -5.96 | -0.77 | **-6.73** | +2.09 | -8.82 | 45% |

Matched economics are **positive in every single cell**. Residual settlement
losses swamp them, and grow faster than the matched gains: k=1 -> k=5 adds
+$1.36 of matched profit and -$4.61 of residual loss.

**Raising k does not improve profitability.** On the full sample it degrades
monotonically at every band tested. The holdout half points the other way, but
each half contains only ~5 windows that traded at all, so the k effect is not
established in either direction. What is established: k was never the
constraint.

## 4. The band floor is the dominant lever

Every band below is a **narrowing** of the authorized 0.30-0.60 band.

| band | k=1 full | k=1 HOLD | strand (full) | k=2 full | k=2 HOLD |
|---|---|---|---|---|---|
| 0.30-0.60 (live) | -3.48 | -0.95 | 33% | -4.49 | -0.27 |
| 0.35-0.60 | -3.54 | -1.15 | 31% | -3.35 | -0.09 |
| **0.40-0.60** | **-1.23** | **+0.10** | **13%** | -1.70 | +0.84 |
| **0.45-0.60** | **-1.06** | **+0.33** | **14%** | -1.62 | +1.23 |
| 0.40-0.50 | -2.76 | -1.76 | 37% | -3.67 | -1.51 |
| 0.30-0.45 (cheap only) | -3.50 | -1.50 | 52% | - | - |

Net P&L tracks the **strand rate**, and the strand rate tracks the **opening
band floor**. Raising the floor from 0.30 to 0.40/0.45 cuts stranding from
33-45% to 13-24% and improves net by >$2 at every k, in both halves.

Opening cheap is actively harmful: 0.30-0.45 strands 52%. A cheap leg is cheap
because it is losing, and a losing leg is the one that fails to pair.

**Caveat that limits all of the above:** in the 0.45-0.60 band only 8 of 19
windows trade at all, and the entire train/holdout difference is 5 traded
windows each way -- in train every loser carried residual, in holdout the
residual was 0 or happened to win. That is noise, not significance. Tightening
the band also cuts contracts from 30 to 21, so "repeated pairs" and
"profitable pairs" pull against each other under this controller.

## 5. L5's own sequential behaviour (556 BTC15m windows, 63,686 trades)

From `l5_canonical_sequence.csv.gz`, `duration_minutes == 15`:

| | L5 | us (Campaign E) |
|---|---|---|
| trades per window | median **114** (p90 255, max 461) | 6 submitted / 3 filled |
| peak \|residual\| in window | median **251** (p90 581, max 1180) | cap **1** |
| end-of-window \|residual\| | median **100**; **99% of windows end with residual** | 1 |
| matched pair unit cost | median **0.9649**, p10 0.8991, **p90 1.0456** | ceiling **0.96** |
| opening price | median **0.510**; 46% in 0.45-0.55; 72% in 0.40-0.60; 9.5% below 0.40 | band floor 0.30 |
| maker share | **87%** (95% in August) | 100% |

Two findings follow directly:

1. **L5 opens at the money.** Median first trade 0.510. This corroborates the
   band sweep from completely independent data: the replay says raise the
   floor to 0.40-0.45, and L5 itself puts 72% of openings in 0.40-0.60.
2. **Our 0.96 pair ceiling sits at roughly L5's 10th percentile.** L5's median
   completion is 0.9649 and 18-33% of its pairs cost **more than $1.00**. We
   are refusing the large majority of L5's own completions.

## 6. L5's settled economics, and the $480/day target

Per-window settlement computed from `cum_up_qty/cum_down_qty`,
`cum_up_spend/cum_down_spend` and `settlement_outcome`:

| | windows | days | trades/win | capital/win | **net per $ deployed** | **net/day** |
|---|---|---|---|---|---|---|
| **Mar-Apr (early era)** | 214 | 18 | 24 | $140 | **+1.342%** | **+$22.36** |
| **Aug (incentive era)** | 342 | 4 | 170 | $1,593 | **-0.277%** | **-$376.87** |
| all BTC15m | 556 | 22 | 115 | $920 | -0.209% | -$50.23 |

**On BTC15m over our entire captured sample, L5's trading P&L is
-$1,105.09.** The August incentive era loses money on trading alone
(-$1,507.49 over 4 days), consistent with reward farming we cannot verify and
do not receive. Its worst day is -$1,148 and its best +$1,207.

This is decisive support for the standing rule that near-$1 pair tolerance
stays locked: 18% of August pairs cost over $1.00, and August is the era that
loses.

**The era worth copying is March-April**: 24 trades/window, $140/window,
+1.342% net per dollar deployed, opening median 0.490, end residual median 26.

Sizing the target against that measured edge:

| configuration | per day |
|---|---|
| L5 early era as actually traded ($140/win, 12 win/day) | **+$22.55** |
| L5 early era extended to all 96 daily windows | +$180.36 |
| our authorized $12 cash, 2 windows/day | **+$0.32** |
| our authorized $12 cash, all 96 windows | +$15.46 |

**$480/day at +1.342% requires ~$373 per window across all 96 daily windows,
or ~$2,981 per window at L5's own 12 windows/day -- 31x and 248x our
authorized $12 cash limit.** The historical $480/day is not reachable from
early-era BTC15m behaviour at early-era size; it belongs to the August
incentive era, whose trading P&L is negative.

## 7. What this means for the controller

The binding problem is **adverse selection on the opening leg**, not inventory
capacity. The fill proxy encodes the mechanism: a resting buy fills only when
the book trades through it, so every fill arrives as the price moves against
us, and the pair only wins if the complement fills before settlement. At 3
contracts per window we carry all of that variance and none of L5's
diversification across ~114 trades per window and hundreds of windows.

Minimum changes implied by the evidence, in order, none authorized:

1. **Raise the opening band floor 0.30 -> 0.45** (a narrowing of the existing
   authorized band, requiring no new scope). Largest and most robust effect;
   independently corroborated by L5's own 0.510 median opening.
2. **Frequency, not size or k.** More windows at clip 1, which is already the
   documented promotion order (PATIENCE -> FREQUENCY -> SIZE -> CONCURRENCY).
3. **Per-tick opening-decision instrumentation** to close the 02:45-02:53
   blind spot before any controller redesign is justified by it.
4. **Do not raise k.** Not the constraint, and negative on the full sample.
5. **Do not raise the pair ceiling** toward L5's median 0.9649 on this
   evidence alone, despite it refusing most of L5's completions: the era whose
   pairs cost that much is the era that loses money.

## 8. Evidence required before any live promotion

- Per-tick opening-decision telemetry over >=20 live windows, to establish the
  real opening-attempt rate and close the blind spot.
- Tape at the engine's ~5s cadence, not ~19s: current sampling undercounts
  fill opportunities by roughly 4x.
- >=40 traded windows per arm before any band change is treated as
  established. 5 per half is noise.
- Settlement-confirmed realized net, never modelled net, as the acceptance
  measure.
