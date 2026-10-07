# The residual-efficiency frontier — Lane B

**Protocol** `RESIDUAL-EFFICIENCY-FRONTIER-LANE-B`
**Preregistered fingerprint** `4b16c92bc658583265d014c196a3becf42540cad20c2e98999f954bea16e074d`
**Preregistration** `analysis/l5/residual_frontier/PREREGISTRATION.md` (written, fingerprinted and committed to **before** any P&L existed)
**Spec** `analysis/l5/residual_frontier/spec.py` → `assert_frozen()`, hard-coded literal
**Replay** `analysis/l5/residual_frontier/replay.py` · **Results** `FRONTIER.json` · **Tests** `tests/test_residual_frontier.py` (27 passing)
**Population** the frozen Q1 corpus of protocol `3fb8fccb`: 2,216 traded events, 75,862 fills, 22 observed days
**Cells** 30

> **LABEL — read this before any number below.** Every dollar in this document
> is **historical replay**: a counterfactual suppression applied to another
> trader's public Polymarket activity. It is not shadow, not a live probe, not
> submitted, not filled, not settled and **never realized**. L5's historical
> cash is not our profit. Rebates and rewards are **exactly $0.00**. Nothing
> here is a go decision. No live spec, frozen fingerprint, `deploy/**`,
> `scripts/l5_exec*` or anything under `analysis/l5/s3/**` was read, imported
> or modified; no order was placed and no git command was run.

---

## 0. HEADLINE — the answer to the question that was asked

**No preregistered rule turns covered ex-incentive cash positive without
gutting the matched-pair engine.**

Exactly one cell is both positive and clears all three preregistered
anti-gutting thresholds — `ABS200_EXTEND_ONLY`, at **+$29.84** over 22
observed days (its `ALL_FILLS` twin is numerically identical, see §5). That is
$1.36/day on $715k of retained turnover, 0.4 bp,
inside the noise of the accounting, and it is **negative (−$336) in the
incentive era that supplies 90% of the corpus**; its positive sign is produced
entirely by the 216-event March/April stratum. By the preregistration's own
rule ("the sign is not produced by a single stratum") it does **not** qualify.

Every cell that produces real dollars — `ABS25` +$1,941, `ABS50` +$1,605,
`ABS100` +$1,081, `TTE120x0.25` +$1,042 — reaches them by destroying
**63%–82% of matched-pair economics**. That is not residual efficiency. That
is turning the engine off a bit at a time and pocketing the fuel.

## 1. Why — the one number that explains the whole frontier

The grid's `avoid/sac` column is the exchange rate: dollars of directional
residual loss avoided per dollar of matched-pair economics sacrificed.

| cell | avoided $ | matched sacrificed $ | avoided per $1 sacrificed |
|---|---|---|---|
| `ABS800_EXTEND_ONLY` | 722 | 895 | **0.806** |
| `ABS25_ALL_FILLS` | 19,978 | 20,820 | **0.960** |
| `ABS25_EXTEND_ONLY` | 19,185 | 18,526 | 1.036 |
| `ABS50_EXTEND_ONLY` | 18,255 | 16,952 | 1.077 |
| `TTE60x0.25_EXTEND_ONLY` | 13,613 | 12,509 | 1.088 |
| `TTE60x0.5_EXTEND_ONLY` | 13,113 | 11,754 | 1.116 |
| `ABS100_EXTEND_ONLY` | 16,420 | 14,639 | 1.122 |
| `TTE120x0.25_EXTEND_ONLY` | 16,023 | 14,215 | 1.127 |
| `ABS200_EXTEND_ONLY` | 11,351 | 10,002 | 1.135 |
| **`TTE120x0.5_EXTEND_ONLY`** | 14,914 | 13,030 | **1.145** ← best in grid |

**The exchange rate never exceeds 1.15, and at the extremes it is below 1.0.**
In this corpus the matched-pair economics and the losing directional residual
are produced by **the same fills**. You do not get to keep one and refuse the
other; you buy about $1.13 of avoided residual loss for $1.00 of surrendered
matched economics, and the fee saving that comes with it is what makes the
net move at all.

That is the real finding and it is a *negative* one. The frozen audit's
decomposition (+$22,568 matched / −$25,314 residual) invites the reading that
there are two separable businesses, a good one and a bad one. At fill
granularity, **there is one business.** The residual is not a leak beside the
engine; within the bounds any contemporaneous cap can draw, it is the engine's
exhaust, and the two are nearly stoichiometric.

## 2. A correction to the fact I was asked to explain

Before the frontier, the baseline itself needs fixing, and this materially
changes the headline −$2,745.53.

`BASELINE` is the full-take cell: it takes every L5 fill and so is L5. Its
matched component reconciles to the frozen audit **exactly**: **+$22,568.25**
against the audit's **+$22,568.25** (asserted in
`test_baseline_reconciles_to_the_frozen_norebate_matched_component`). Its net
does **not**: **−$1,526.81** here against **−$2,745.53** there, a **+$1,218.72**
difference. The difference is not an error in either; the two value settlement
differently, and the gap decomposes exactly:

| source of the gap | $ |
|---|---|
| **6 events whose guaranteed complete-set redemption is ABSENT from the feed** | **+1,829.86** |
| residual-valuation shortfall on the 2,000 REDEEM-backed events | −611.14 |
| **total** | **+1,218.72** |

The audit scores settlement as **observed REDEEM cash**; this protocol scores
it as **modelled payout at the resolved outcome**, with the winner taken from
the event's `REDEEM` `outcomeIndex` — a resolver validated before the grid was
frozen at median |redeem cash − modelled payout| = **$0.0000**, closer than
its complement on **98.2%** of 2,000 resolved events and exact on **89.2%**.

**The 6 events matter.** 218 of 2,218 events carry no `REDEEM` record at all,
so the audit books their settlement at **$0.00**. For 212 of them that is
*correct*: they are one-sided positions that lost, and a losing one-sided
position generates no redemption. But 6 of them hold **1,829.86 matched
shares**, and a held complete set redeems **$1.00 per share regardless of
outcome**. Booking $1,829.86 of arithmetically guaranteed value as zero is a
coverage hole, not a loss.

Five of those six events are on **2026-04-21** — the last day before the
May–July coverage hole — and they *are* era E2 in its entirety:

| slug | matched shares | audit net |
|---|---|---|
| `btc-updown-15m-1776754800` | 348.6 | −303.27 |
| `btc-updown-15m-1776755700` | 84.1 | −113.86 |
| `btc-updown-15m-1776756600` | 754.0 | −750.64 |
| `btc-updown-15m-1776757500` | 142.2 | −175.28 |
| `btc-updown-15m-1776758400` | 501.1 | −512.03 |
| **sum** | **1,830.0** | **−1,855.08** = the audit's entire E2 |

So the audit's **E2 = −$1,855.08 on 5 events with a 0.000 win rate** is an
artifact of the harvest boundary, not a trading result. Scored on modelled
settlement, E2 is **−$25.23**. The corrected corpus total is **−$1,526.81**,
and the corrected split is:

| stratum | events | net ex incentives |
|---|---|---|
| `SETTLEMENT_RESOLVED` (REDEEM-backed) | 1,998 | **+$2,016.81** |
| `SETTLEMENT_UNRESOLVED` (no REDEEM) | 218 | **−$3,543.62** |
| **FULL_CORPUS** | 2,216 | **−$1,526.81** |

**L5's ex-incentive cash on every event whose settlement is source-backed is
POSITIVE.** The whole negative sign lives in 218 events, 212 of them one-sided
bets that lost outright. That does not rescue the strategy — losing one-sided
bets are real losses and the exchange-rate finding in §1 still stands — but it
does mean the "−$2,746 ex-incentive loss" should not be quoted as a clean
measurement of the matched engine's economics. It is ~67% a boundary-day
accounting hole.

## 3. THE FRONTIER — all 30 cells, FULL_CORPUS

Column meanings are frozen in `spec.METRICS` and reproduced in the
preregistration. `avoid/sac` is derived, not preregistered, and is labelled as
such in `FRONTIER.json`. `POS` = net > 0 on FULL_CORPUS. `GUT` = trips a
preregistered anti-gutting threshold (turnover < 50%, actions suppressed
> 50%, or matched economics retained < 50%).

| cell | matched $ | residual $ | avoided $ | fees $ | **NET $** | bp | turn.frac | matched.frac | ev/day | worst DD $ | supp.all | supp.EXT | supp.CMP | avoid/sac | POS | GUT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `BASELINE` | 22,568 | -20,102 | 0 | 3,993 | **-1,527** | -19.9 | 1.000 | 1.000 | 100.7 | -4,784 | 0.000 | 0.000 | 0.000 | — | no | — |
| `ABS25_EXTEND_ONLY` | 4,043 | -916 | 19,185 | 1,185 | **+1,941** | 60.4 | 0.419 | 0.179 | 82.9 | -139 | 0.468 | 0.732 | 0.000 | 1.036 | **YES** | **GUTTED** |
| `ABS50_EXTEND_ONLY` | 5,617 | -1,847 | 18,255 | 2,165 | **+1,605** | 37.6 | 0.557 | 0.249 | 99.1 | -328 | 0.312 | 0.506 | 0.000 | 1.077 | **YES** | **GUTTED** |
| `ABS100_EXTEND_ONLY` | 7,929 | -3,681 | 16,420 | 3,167 | **+1,081** | 17.8 | 0.792 | 0.351 | 100.7 | -1,218 | 0.160 | 0.282 | 0.000 | 1.122 | **YES** | **GUTTED** |
| `ABS200_EXTEND_ONLY` | 12,566 | -8,750 | 11,351 | 3,786 | **+30** | 0.4 | 0.932 | 0.557 | 100.7 | -2,830 | 0.065 | 0.120 | 0.000 | 1.135 | **YES** | — |
| `ABS400_EXTEND_ONLY` | 17,967 | -15,177 | 4,925 | 3,967 | **-1,177** | -15.5 | 0.990 | 0.796 | 100.7 | -4,466 | 0.018 | 0.035 | 0.000 | 1.070 | no | — |
| `ABS800_EXTEND_ONLY` | 21,673 | -19,380 | 722 | 3,992 | **-1,698** | -22.2 | 0.999 | 0.960 | 100.7 | -4,981 | 0.004 | 0.007 | 0.000 | 0.806 | no | — |
| `ABS25_ALL_FILLS` | 1,749 | -124 | 19,978 | 545 | **+1,080** | 68.9 | 0.204 | 0.077 | 82.9 | -208 | 0.549 | 0.678 | 0.304 | 0.960 | **YES** | **GUTTED** |
| `ABS50_ALL_FILLS` | 4,885 | -1,399 | 18,703 | 2,111 | **+1,375** | 32.4 | 0.554 | 0.216 | 99.1 | -323 | 0.314 | 0.506 | 0.005 | 1.058 | **YES** | **GUTTED** |
| `ABS100_ALL_FILLS` | 7,929 | -3,681 | 16,420 | 3,167 | **+1,081** | 17.8 | 0.792 | 0.351 | 100.7 | -1,218 | 0.160 | 0.282 | 0.000 | 1.122 | **YES** | **GUTTED** |
| `ABS200_ALL_FILLS` | 12,566 | -8,750 | 11,351 | 3,786 | **+30** | 0.4 | 0.932 | 0.557 | 100.7 | -2,830 | 0.065 | 0.120 | 0.000 | 1.135 | **YES** | — |
| `ABS400_ALL_FILLS` | 17,967 | -15,177 | 4,925 | 3,967 | **-1,177** | -15.5 | 0.990 | 0.796 | 100.7 | -4,466 | 0.018 | 0.035 | 0.000 | 1.070 | no | — |
| `ABS800_ALL_FILLS` | 21,673 | -19,380 | 722 | 3,992 | **-1,698** | -22.2 | 0.999 | 0.960 | 100.7 | -4,981 | 0.004 | 0.007 | 0.000 | 0.806 | no | — |
| `FRAC0.1_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `FRAC0.2_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `FRAC0.35_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `FRAC0.5_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `FRAC0.75_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `TTE120x0.5_EXTEND_ONLY` | 9,538 | -5,188 | 14,914 | 3,517 | **+833** | 12.1 | 0.901 | 0.423 | 100.7 | -2,106 | 0.088 | 0.162 | 0.000 | 1.145 | **YES** | **GUTTED** |
| `TTE120x0.25_EXTEND_ONLY` | 8,353 | -4,079 | 16,023 | 3,233 | **+1,042** | 15.7 | 0.864 | 0.370 | 100.2 | -1,449 | 0.112 | 0.204 | 0.000 | 1.127 | **YES** | **GUTTED** |
| `TTE60x0.5_EXTEND_ONLY` | 10,814 | -6,989 | 13,113 | 3,688 | **+137** | 1.9 | 0.922 | 0.479 | 100.7 | -2,714 | 0.075 | 0.138 | 0.000 | 1.116 | **YES** | **GUTTED** |
| `TTE60x0.25_EXTEND_ONLY` | 10,059 | -6,489 | 13,613 | 3,597 | **-27** | -0.4 | 0.912 | 0.446 | 100.5 | -2,642 | 0.084 | 0.154 | 0.000 | 1.088 | no | — |
| `ABS50+FRAC0.2_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS50+FRAC0.35_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS100+FRAC0.2_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS100+FRAC0.35_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS200+FRAC0.2_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS200+FRAC0.35_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS100+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |
| `ABS200+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | 0 | 0 | 20,102 | 0 | **+0** | — | 0.000 | 0.000 | 0.0 | 0 | 1.000 | 1.000 | — | 0.891 | no | — |

### Era / regime / settlement sensitivity — net cash ex incentives ($)

| cell | E1 pre-rebate | E2 early (n=5) | E3 incentive | regime <04-25 | regime 08-19→ | settl. RESOLVED | settl. UNRESOLVED | E1/E2/E3 sign |
|---|---|---|---|---|---|---|---|---|
| `BASELINE` | +391 | -25 | -1,893 | +366 | -1,893 | +2,017 | -3,544 | **flips** |
| `ABS25_EXTEND_ONLY` | +273 | -7 | +1,676 | +265 | +1,676 | +2,565 | -623 | **flips** |
| `ABS50_EXTEND_ONLY` | +349 | +8 | +1,247 | +357 | +1,247 | +3,333 | -1,728 | stable |
| `ABS100_EXTEND_ONLY` | +348 | +27 | +706 | +375 | +706 | +3,862 | -2,781 | stable |
| `ABS200_EXTEND_ONLY` | +391 | -25 | -336 | +366 | -336 | +3,424 | -3,394 | **flips** |
| `ABS400_EXTEND_ONLY` | +391 | -25 | -1,543 | +366 | -1,543 | +2,366 | -3,543 | **flips** |
| `ABS800_EXTEND_ONLY` | +391 | -25 | -2,064 | +366 | -2,064 | +1,845 | -3,544 | **flips** |
| `ABS25_ALL_FILLS` | +273 | -21 | +827 | +252 | +827 | +1,716 | -636 | **flips** |
| `ABS50_ALL_FILLS` | +349 | +8 | +1,017 | +358 | +1,017 | +3,103 | -1,728 | stable |
| `ABS100_ALL_FILLS` | +348 | +27 | +706 | +375 | +706 | +3,862 | -2,781 | stable |
| `ABS200_ALL_FILLS` | +391 | -25 | -336 | +366 | -336 | +3,424 | -3,394 | **flips** |
| `ABS400_ALL_FILLS` | +391 | -25 | -1,543 | +366 | -1,543 | +2,366 | -3,543 | **flips** |
| `ABS800_ALL_FILLS` | +391 | -25 | -2,064 | +366 | -2,064 | +1,845 | -3,544 | **flips** |
| `FRAC0.1_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `FRAC0.2_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `FRAC0.35_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `FRAC0.5_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `FRAC0.75_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `TTE120x0.5_EXTEND_ONLY` | +474 | -20 | +380 | +453 | +380 | +3,843 | -3,010 | **flips** |
| `TTE120x0.25_EXTEND_ONLY` | +416 | -26 | +652 | +390 | +652 | +3,751 | -2,709 | **flips** |
| `TTE60x0.5_EXTEND_ONLY` | +435 | -21 | -277 | +414 | -277 | +3,330 | -3,193 | **flips** |
| `TTE60x0.25_EXTEND_ONLY` | +383 | -26 | -383 | +356 | -383 | +2,990 | -3,017 | **flips** |
| `ABS50+FRAC0.2_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS50+FRAC0.35_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS100+FRAC0.2_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS100+FRAC0.35_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS200+FRAC0.2_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS200+FRAC0.35_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS100+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |
| `ABS200+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | +0 | +0 | +0 | +0 | +0 | +0 | +0 | stable |

---

## 4. A PREREGISTERED DESIGN ERROR, DISCLOSED NOT PATCHED

**11 of the 30 cells are degenerate by construction and take zero fills.**
Every `FRAC*` cell and every `COMBO` cell containing a `FRAC` bound is dead.

The reason is arithmetic. With an empty book, the first fill of any event makes
`|residual| / gross == 1.0` exactly, because the entire book *is* that one
fill. No fractional cap below 1.0 can ever admit an OPEN leg, so the event
never starts, so no second fill can ever reduce the fraction. The `FRAC`
family as frozen cannot trade.

**The grid is frozen and I have not changed it.** Re-freezing the spec to
rescue a family after seeing it fail is exactly the post-hoc search the
preregistration exists to prevent, and the broken cells are left in the grid
and in `FRONTIER.json` as evidence that the discipline held. The correct fix —
exempting the OPEN leg, or applying the fraction only once gross exceeds some
observed floor — is a **different protocol** with a **different fingerprint**,
and it is not run here.

The degenerate cells do say one accidental thing worth reading. Their
`net_improvement_vs_baseline` is **+$1,526.81**: in this corpus, **taking no
position at all beats copying L5's own fill sequence by $1,527**. $0 is not a
strategy, and the preregistered test (`net > 0`) correctly refuses it, but it
calibrates what the frontier is competing against.

## 5. What the EXTEND-only semantic is worth

The preregistered primary semantic refuses **additional same-side exposure**
once the cap binds while still allowing a `COMPLETE` (a fill on the lighter
side, which reduces |residual|). The `ALL_FILLS` contrast arm prices it:

| abs cap | NET EXTEND_ONLY | NET ALL_FILLS | matched retained EXTEND_ONLY | matched retained ALL_FILLS |
|---|---|---|---|---|
| 25 | **+1,941** | +1,080 | **0.179** | 0.078 |
| 50 | **+1,605** | +1,375 | **0.249** | 0.216 |
| 100 | +1,081 | +1,081 | 0.351 | 0.351 |
| 200 | +30 | +30 | 0.557 | 0.557 |
| 400 | −1,177 | −1,177 | 0.796 | 0.796 |
| 800 | −1,698 | −1,698 | 0.960 | 0.960 |

**At caps of 100 shares and above the two semantics are byte-identical**: no
`COMPLETE` fill in the entire corpus ever pushes |residual| past 100 shares,
which is itself a finding about L5 — his completing legs are small relative to
his imbalance. The semantic only pays at tight caps, and there it pays a lot:
at cap 25 it is worth **+$861 (+80%) of net and 2.3× the matched economics**.

This corroborates the live shell's existing choice (`residual_cap_units` gates
EXTEND, not COMPLETE) from a second, independent direction. It is the one
preregistered family whose result is unambiguously supportive.

## 6. Era, regime and settlement sensitivity

The second table above gives every cell's net in every stratum. Three things
to read out of it:

1. **`ABS100_EXTEND_ONLY` is the only cell positive in all three eras**
   (E1 +$348, E2 +$27, E3 +$706) and in both occupied regime segments. It is
   also the only cell that turns E2 — the 5 boundary-day events — positive,
   because capping residual at 100 shares refuses most of the one-sided
   inventory those events accumulated. That sign stability is the strongest
   evidence in the grid, and it costs **65% of matched economics**.
2. **The regime partition degenerates.** `analysis/l5/CANON_REGIMES.md`'s
   primary change points are `2026-04-25`, `2026-06-23`, `2026-08-19`. The
   frozen Q1 corpus spans 2026-03-19→04-21 and 08-28→08-31, so only two
   segments are occupied and they coincide with E1∪E2 and E3. The regime split
   therefore adds **no independent information** here. This is reported as a
   degeneracy; no new regime was invented to manufacture a third arm.
3. **Every cell stays deeply negative on `SETTLEMENT_UNRESOLVED`.** No cap
   fixes a one-sided losing bet, because the fills that create it are the only
   fills in the event: there is no matched leg to protect and nothing to
   suppress except the position itself. Only `ABS25` reduces that stratum
   materially (−$3,544 → −$623), and it does so by declining to participate in
   81 of the 218 events at all.

## 7. THE THREE BEST CANDIDATES — full metric rows

Ranked by the preregistered criteria in order: sign stability across strata,
then matched-engine preservation, then net dollars.

### #1 `ABS100_EXTEND_ONLY` — the only cell positive in every era

| metric | FULL_CORPUS |
|---|---|
| matched_retained_usd | +7,928.80 (**35.1%** of baseline) |
| residual_pnl_usd | −3,681.29 |
| residual_loss_avoided_usd | +16,420.34 |
| fees_usd | 3,166.79 |
| **net_cash_ex_incentives_usd** | **+1,080.71** |
| net_bp_of_turnover | +17.8 |
| turnover_retained_usd / frac | 607,382 / **0.792** |
| events_touched / per observed day | 2,216 / **100.7** (unchanged) |
| worst_drawdown_usd | −1,217.60 (baseline −4,784.09) |
| suppressed_action_frac | **0.160** |
| suppressed_extend_frac | 0.282 |
| suppressed_complete_frac | 0.000 |
| avoided per $1 matched sacrificed | 1.122 |
| era nets (E1 / E2 / E3) | +348 / +27 / **+706** |
| regime nets (<04-25 / 08-19→) | +375 / +706 |
| settlement (RESOLVED / UNRESOLVED) | **+3,862** / −2,781 |
| **verdict** | **GUTTED_ENGINE** — matched retained 35.1% < 50% |

The tension this cell exposes is worth stating precisely: it suppresses only
**16% of L5's actions** and keeps **79% of his turnover** and **100% of his
event count** — by action count it is barely an intervention — yet it destroys
**65% of matched-pair dollars**. Matched economics are concentrated in the
late, large-inventory phase of big events, and a 100-share residual ceiling
prevents the book from ever growing into it. "Suppress few actions" and
"preserve the engine" are **not** the same constraint, and this cell is the
proof.

### #2 `TTE120x0.25_EXTEND_ONLY` — the lightest touch that still pays

| metric | FULL_CORPUS |
|---|---|
| matched_retained_usd | +8,353.48 (**37.0%** of baseline) |
| residual_pnl_usd | −4,078.51 |
| residual_loss_avoided_usd | +16,023.12 |
| fees_usd | 3,232.85 |
| **net_cash_ex_incentives_usd** | **+1,042.12** |
| net_bp_of_turnover | +15.7 |
| turnover_retained_usd / frac | 662,760 / **0.864** |
| events_touched / per observed day | 2,205 / **100.2** |
| worst_drawdown_usd | −1,448.88 |
| suppressed_action_frac | **0.112** (the lowest of any cell netting > $1,000) |
| suppressed_extend_frac | 0.204 |
| suppressed_complete_frac | 0.000 |
| avoided per $1 matched sacrificed | 1.127 |
| era nets (E1 / E2 / E3) | +416 / **−26** / +652 |
| settlement (RESOLVED / UNRESOLVED) | **+3,751** / −2,709 |
| **verdict** | **GUTTED_ENGINE** — matched retained 37.0% < 50% |

A 200-share cap that tightens to 50 shares inside the last 120 seconds. It
buys 96% of `ABS100`'s net while suppressing 30% fewer actions, and it is the
family with the **best exchange rate in the grid** (its ×0.5 sibling,
`TTE120x0.5`, hits 1.145). It flips sign on E2 — but E2 is **5 events on one
day**, and §2 shows those 5 events are the boundary-day coverage hole, so that
flip carries no weight either way. Its mechanism is also the one with the
clearest independent L5 evidence behind it: `EXTEND_AND_SWITCH.md` already
shows L5's own `P(STOP)` jumping an order of magnitude inside the last 30
seconds, so a clock-tightened cap sharpens observed behaviour rather than
inventing a new one.

### #3 `ABS200_EXTEND_ONLY` — the only non-gutted positive, and it is nothing

| metric | FULL_CORPUS |
|---|---|
| matched_retained_usd | +12,565.95 (**55.7%** of baseline) |
| residual_pnl_usd | −8,750.16 |
| residual_loss_avoided_usd | +11,351.47 |
| fees_usd | 3,785.95 |
| **net_cash_ex_incentives_usd** | **+29.84** |
| net_bp_of_turnover | **+0.4** |
| turnover_retained_usd / frac | 715,253 / **0.933** |
| events_touched / per observed day | 2,216 / **100.7** (unchanged) |
| worst_drawdown_usd | −2,829.62 |
| suppressed_action_frac | **0.065** |
| suppressed_extend_frac | 0.120 |
| suppressed_complete_frac | 0.000 |
| avoided per $1 matched sacrificed | **1.135** |
| era nets (E1 / E2 / E3) | +391 / −25 / **−336** |
| settlement (RESOLVED / UNRESOLVED) | **+3,424** / −3,394 |
| **verdict** | passes all three anti-gutting thresholds; **FAILS** the single-stratum test |

This is the cell the preregistration was designed to catch. It clears every
mechanical gutting threshold — 93% of turnover, 94% of actions, 56% of matched
economics — and its net is **+$29.84**, which is **$1.36/day and 0.4 bp**, far
inside the ±$611 settlement-valuation uncertainty quantified in §2. Its
positive sign comes from +$391 in the 216-event March/April stratum while the
2,000-event incentive era stays at **−$336**. Under the preregistered rule
that a positive sign must not be produced by a single stratum, it does not
qualify, and under any reading it is not a result.

## 8. The explicit answer to the brief's question 4

> **Does any preregistered rule turn covered ex-incentive cash positive
> without destroying the matched-pair engine?**

**No.**

- 11 of 30 cells are positive on FULL_CORPUS. **9 of those 11 are
  GUTTED_ENGINE.** The other two are `ABS200_EXTEND_ONLY` and
  `ABS200_ALL_FILLS`, which are the *same* cell numerically (§5: no COMPLETE
  fill in the corpus ever breaches a 200-share cap), at +$29.84 and
  incentive-era-negative. Of the remaining 19 cells, 8 are negative and 11 are
  the dead `FRAC`/`COMBO` cells of §4.
- The cost of every real positive is stated above: `ABS25` +$1,941 costs
  **82% of matched economics, 58% of turnover and 47% of all L5 actions**;
  `ABS50` +$1,605 costs 75% of matched and 44% of turnover; `ABS100` +$1,081
  costs 65% of matched for only 16% of actions; `TTE120x0.25` +$1,042 costs
  63% of matched for 11% of actions.
- A rule that gets there by suppressing almost everything is not a win, and
  three of those four qualify: `ABS25_ALL_FILLS` retains **20% of turnover**
  and suppresses **55% of actions** to make $1,080. Saying it louder: the
  highest-bp cell in the grid (`ABS25_ALL_FILLS`, 68.9 bp) is the one that
  trades least, which is why `net_bp_of_turnover` was preregistered — on its
  own it would have ranked the grid upside down.
- And the structural reason, from §1: the exchange rate between avoided
  residual loss and sacrificed matched economics **never exceeds 1.15**.
  There is no contemporaneous residual bound in this grid that separates the
  good component from the bad one, because at fill granularity they are not
  two components.

## 9. OUT OF SAMPLE — named and LEFT UNRUN

No out-of-sample test was run. Nothing here is validated and nothing here is a
go decision. The split I would use, and why:

**PRIMARY OOS (unrun): `data/l5/raw/activity_gap.jsonl.gz`.** 706,500 payload
records, ~14,800 additional window-events, spanning the May–July period that
is **entirely absent** from the frozen Q1 corpus plus the rest of August. It
is the right holdout for three reasons: (i) it was never read by protocol
`3fb8fccb` and has never been read by this protocol, so it is genuinely
unseen; (ii) it is the same trader, same venue, same two market families, so
the transfer assumption is minimal; (iii) it fills the exact coverage hole
that caps every conclusion above — 22 observed days in two disjoint clusters
becomes ~130 days of continuous coverage, which is the only way the era and
regime sensitivities here can be given any power. The pre-committed OOS test
is: re-run this frozen spec unchanged on that population and check whether
`ABS100_EXTEND_ONLY`'s and `TTE120x0.25_EXTEND_ONLY`'s **signs and exchange
rates** survive. Net dollars are not the test; the exchange rate is, because
that is the quantity §1 identifies as decisive.

**SECONDARY OOS (unrun): `data/l5/raw/sep/l5_sep23_25.json`.** 2026-09-23→25,
strictly forward in time from the entire corpus. Smaller and later, so it
tests temporal rather than coverage generalization, and it is the only
population that speaks to whether these bounds survive into the mature
incentive era.

**What I would NOT use, and why:** a random event-level or fill-level split of
the frozen corpus. Fills inside one event share one book, and events inside
one day share one market regime and one volatility state, so a random split
leaks and would report an inflated agreement between arms. A within-corpus
holdout would also have to be a **day** holdout, and with 22 observed days —
18 of them carrying 216 of 2,216 events — any single-day holdout is either
trivially small or trivially dominant. If a within-corpus split is demanded
anyway, the only defensible one is **hold out 2026-08-31 entirely** (474
events, the last observed day, inside the post-08-19 regime so it is at least
exchangeable with its training days). It is named here and also unrun.

## 10. LIMITATIONS — what this data cannot support

1. **Suppression is not counterfactual execution.** A cell can only refuse a
   fill L5 actually took. It does not model what the book would have done
   without L5's flow, what the counterparty would have done, what L5 would
   have done next, or what price the refused size would have reprinted at.
   Every number here is an **accounting** bound on an observed sequence. It is
   not a backtest of a strategy and must not be read as one.
2. **Maker/taker role is UNAVAILABLE.** `/activity` carries no liquidity flag
   (`CANON_REGIMES.md` §0 UNAVAILABLE). No cell can claim a rebate, a queue
   position, or that a refused fill would have been a maker fill. Rebates are
   $0 by construction here and that is the only honest treatment.
3. **Order placements and cancels are UNAVAILABLE.** Only fills are public.
   L5's quoting, his patience in book, and whether a "blocked EXTEND" would
   have been a resting order rather than a fill are all unobservable. A
   residual cap in a live system gates *orders*; this study can only gate
   *fills*. That gap is not small and nothing here closes it.
4. **22 observed days in two disjoint clusters.** 2026-03-19→04-21 (216
   events) and 08-28→08-31 (2,000 events), separated by the May–July coverage
   hole that is **a hole, not a zero**. 90% of the corpus is 4 calendar days.
   No per-day figure here generalizes and none was divided by calendar span.
5. **E2 is 5 events on one day, and they are a coverage artifact** (§2). Any
   "E2 flips sign" reading is noise. It is reported because the stratum was
   preregistered, not because it is informative.
6. **The regime split adds nothing** (§6.2). The repository's detected change
   points fall outside or at the edge of this corpus's coverage, so the
   partition degenerates to the era split. No new regime was invented to
   produce a third arm.
7. **Settlement valuation carries ±$611 of unexplained residual** across the
   2,000 REDEEM-backed events (§2). That is 40% of the baseline net and
   **20× the entire net of the best non-gutted cell**, which is the second
   reason `ABS200_EXTEND_ONLY`'s +$29.84 cannot be called a result.
8. **218 events have no source-backed settlement at all.** Scoring their
   residual payout at $0 is conservative and correct for the 212 one-sided
   losers, but it is an assumption, and for 6 events it is demonstrably wrong
   by $1,829.86.
9. **The FRAC family is dead on arrival** (§4). 11 of 30 cells produced no
   evidence. The grid delivered 19 informative cells, not 30, and I did not
   re-freeze to fix it.
10. **Fees are the charged wedge only.** `max(0, usdcSize − price·size)`,
    never the `0.07·p·(1−p)` formula. Fees appear only in the incentive era;
    March and April fills carry none, so every fee-mediated effect in this
    grid is an E3 effect and is confounded with everything else that changed
    on 2026-08-19.
11. **Nothing here is evidence of replication.** Per `CLAUDE.md`'s validation
    standard, positive aggregate replay cash with materially different
    behaviour is not sufficient. Every positive cell in this grid behaves
    *materially differently* from L5 — that is the whole mechanism — so none
    of them is a better model of L5. They are at most a better-behaved
    descendant, and that claim is unsupported until the OOS in §9 is run.

## 11. Provenance

- `FRONTIER_SUPERSEDED_20260914.md` is the earlier, unrelated residual/pair-cost
  study (BTC5m/ETH5m window corpus, behavioral units, already self-marked
  SUPERSEDED on 2026-09-14). It is preserved unchanged for provenance and its
  Result 4 is cited above for the live shell's EXTEND-vs-COMPLETE gating
  semantics. `frontier.py` from that study is likewise untouched.
- This protocol's code is `spec.py` + `replay.py`; it reads
  `data/l5/raw/activity.jsonl` and `analysis/l5/norebate/Q1_EVENTS.jsonl` and
  writes only `FRONTIER.json`.
- `analysis/l5/s3/**` was not read, imported or modified (asserted in
  `test_no_order_path_and_no_s3_import`). No `live_exec*` spec, frozen
  fingerprint, `deploy/**` or `scripts/l5_exec*` was touched. No git command
  was run. No order was placed, nothing was signed, Railway was not contacted.
