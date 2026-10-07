# OUT-OF-SAMPLE CONFIRMATION — residual-efficiency frontier

**Protocol** `RESIDUAL-EFFICIENCY-FRONTIER-LANE-B`
**Spec fingerprint** `4b16c92bc658583265d014c196a3becf42540cad20c2e98999f954bea16e074d` — asserted via `spec.assert_frozen()` before any data was read, and re-verified byte-for-byte by `tests/test_residual_frontier_oos.py`.
**Machine-readable** `analysis/l5/residual_frontier/OOS_RESULT.json`
**Code** `analysis/l5/residual_frontier/oos.py` + `oos_adapter.py`; `replay.py` reused unchanged.

> **HISTORICAL REPLAY.** Every dollar in this document is a counterfactual
> modification of another trader's public Polymarket activity. It is not
> shadow, not a probe, not submitted, not filled, not settled, and **never
> realized**. L5's cash is not our profit. Rebates and rewards are exactly
> **$0.00** by construction, including the 107 `MAKER_REBATE`/`TAKER_REBATE`/
> `REWARD` records present in the holdout, which are excluded.

**This was a CONFIRMATION run, not a discovery run.** The existing 30 cells
were run unchanged. Nothing was tuned: no cell added, removed, re-capped or
rescued; no threshold re-chosen; no metric redefined. `spec.py`,
`PREREGISTRATION.md` and `replay.py` are byte-identical to their committed
state (sha256 pinned in the test file).

---

## 0. VERDICT, in one block

| pre-committed test | PRIMARY holdout | SECONDARY holdout |
|---|---|---|
| **T1 `ABS100_EXTEND_ONLY`** — sign survives? | **YES** (+1,080.71 → **+30,191.19**) | **NO** (+1,080.71 → **−299.78**) |
| **T1** — exchange rate survives (stays > 1.0)? | **YES** (1.1216 → **1.0127**) | **YES** (1.1216 → **1.8228**) |
| **T2 `TTE120x0.25_EXTEND_ONLY`** — sign survives? | **YES** (+1,042.12 → **+30,472.97**) | **NO** (+1,042.12 → **−379.76**) |
| **T2** — exchange rate survives (stays > 1.0)? | **YES** (1.1272 → **1.0397**) | **YES** (1.1272 → **1.8167**) |
| **T3 `ABS200_EXTEND_ONLY`** — does the E3-negative single-stratum failure repeat? | **NO** — E3 goes +25,685.12 (was −336.15). But a **new** single-stratum failure appears in its place: `REGIME_2026-08-19_ON` = **−788.25** | **YES** — the only stratum present is E3 and it is **−781.17** |
| **13 FRAC/COMBO cells dead by construction** — stay dead? | **YES**, 0 fills taken, identical set | **YES**, identical set |

**L5's own source-settled ex-incentive positive: REPRODUCES in sign on both
holdouts, but it is a SELF-SELECTED quantity and the confound is CONFIRMED,
not merely possible.** See §4.

**The frontier is not closed.** The in-sample report's structural claim — that
the exchange rate between avoided residual loss and sacrificed matched
economics "never exceeds 1.15", so no contemporaneous residual bound separates
the good component from the bad one — **reproduces and tightens** on the
107-day primary holdout: the maximum exchange rate across all 17 live cells (16 of which have a defined exchange rate) is
**1.1094** (`ABS400_EXTEND_ONLY`), and both pre-committed candidates sit at
**1.01–1.04**, i.e. essentially exactly 1.00. That is the frontier's own
negative finding confirmed out of sample on 6.7× the events. What did *not*
reproduce is the claim's companion framing: on the holdout `BASELINE` — L5
himself — is **+$18,677.78** ex incentives, not negative, so the caps are no
longer rescuing a losing engine; they are adding roughly 60 bp of turnover to
a winning one at an exchange rate of ~1.0.

---

## 1. WHAT WAS RUN, AND ON WHAT

| | in-sample (frozen) | **PRIMARY holdout** | SECONDARY holdout |
|---|---|---|---|
| source | `data/l5/raw/activity.jsonl` | `data/l5/raw/activity_gap.jsonl.gz` | `data/l5/raw/sep/l5_sep23_25.json` |
| payload records read | — | 706,500 (**706,016** after de-dup) | 6,160 |
| events admitted | 2,216 | **14,785** | 255 |
| fills (BUY) | 75,862 | **685,430** | 5,498 |
| observed days | 22 | **107** | 2 |
| span | 2026-03-19 … 2026-04-21 + 2026-08-30/31 | **2026-04-21 … 2026-08-28** | 2026-09-24 … 2026-09-25 |
| eras present | E1, E2, E3 | **E2, E3** | E3 only |
| regimes present | pre-04-25, 08-19-on | **all four** | 08-19-on only |
| SELL records | 2 (excluded by `replay`) | **0** | **0** |
| events with a `REDEEM` record | 2,000 | **13,472** | 201 |
| events with NO `REDEEM` | 218 | **1,313** | 54 |

### Leak guard

`analysis/l5/norebate/Q1_EVENTS.jsonl` holds the 2,218 in-sample slugs. **127
of them also appear in the PRIMARY harvest and were EXCLUDED**; 0 collide with
SECONDARY. The 1 slug shared between PRIMARY and SECONDARY was assigned to
PRIMARY and removed from SECONDARY, so the three populations are mutually
disjoint. `tests/test_residual_frontier_oos.py::test_holdout_populations_are_disjoint_from_in_sample`
and `::test_primary_and_secondary_are_disjoint_from_each_other` assert this on
the materialized population files, and
`::test_quoted_in_sample_figures_match_the_frozen_in_sample_result` asserts
that **every in-sample number quoted in this document is copied from the
committed `FRONTIER.json`, never recomputed** — which is what makes it
impossible for a holdout row to have entered an in-sample figure.

### Market-family scope (an exclusion, disclosed)

`replay._duration_and_epoch` derives `tte_s` by parsing the window-open epoch
out of an `<asset>-updown-<5m|15m>-<epoch>` slug and supports nothing else.
**45 PRIMARY event keys / 3,439 records (0.49%) and 1 SECONDARY key / 6 records
are bare `conditionId`s with no `eventSlug`** — mostly the slug-less
rebate/reward records the replay ignores anyway — and they were excluded rather
than given an invented `tte_s`, which would have been a change to the replay's
semantics. This is the only population narrowing beyond the leak guard.

### The adapter

`oos_adapter.py`. The holdout records carry **exactly** the field names
`q1.load()`/`replay.build_events` already read — `type`, `timestamp`, `side`,
`outcomeIndex`, `size`, `price`, `usdcSize`, `eventSlug`, `conditionId` — so
**the field mapping is the identity in every case**; the full mapping table is
in the module docstring. Only the container differs: PRIMARY is gzipped jsonl
of `{"payload":[rec,…]}` envelopes, SECONDARY is one JSON object keyed by
record type. The adapter therefore does exactly three things, all
container-level: gunzip / flatten, drop the harvest envelope keys, and
**de-duplicate exact-identity records** (the PRIMARY harvest paginates with
overlapping windows and carried **484 duplicate records out of 706,500**; a
duplicated fill would be double-counted). It then writes the holdout out in
`activity.jsonl` shape plus a population keep-file and calls
`replay.build_events` / `replay.run` **unchanged**, so the replay's semantics
could not bend to the data.
`tests/test_residual_frontier_oos.py` proves at the AST level that the mapping
functions contain no arithmetic and no subscript assignment, and proves
record-by-record that the adapter returns source records verbatim and that
de-duplication only removes.

---

## 2. THE FULL 30-CELL TABLE, HOLDOUT BESIDE IN-SAMPLE

`net` = `net_cash_ex_incentives_usd` on `FULL_CORPUS`.
`xr` = `DERIVED_avoided_per_matched_sacrificed`.
IS = in-sample (copied from `FRONTIER.json`), P-OOS = primary holdout, S-OOS = secondary holdout.

| cell | IS net $ | **P-OOS net $** | S-OOS net $ | IS xr | **P-OOS xr** | S-OOS xr | P turn frac | P supp act | P events |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `BASELINE` | -1526.81 | **18677.78** | -1823.70 | — | **—** | — | 1.0000 | 0.0000 | 14785 |
| `ABS25_EXTEND_ONLY` | 1941.43 | **20918.32** | 248.12 | 1.0356 | **0.7748** | 1.9271 | 0.4094 | 0.4861 | 11942 |
| `ABS50_EXTEND_ONLY` | 1604.86 | **28012.53** | -74.81 | 1.0769 | **0.8976** | 1.7634 | 0.5700 | 0.3177 | 14451 |
| `ABS100_EXTEND_ONLY` | 1080.71 | **30191.19** | -299.78 | 1.1216 | **1.0127** | 1.8228 | 0.7794 | 0.1639 | 14785 |
| `ABS200_EXTEND_ONLY` | 29.84 | **28898.04** | -781.17 | 1.1349 | **1.0970** | 1.9206 | 0.9354 | 0.0599 | 14785 |
| `ABS400_EXTEND_ONLY` | -1176.91 | **22785.11** | -1491.48 | 1.0704 | **1.1094** | 1.6524 | 0.9935 | 0.0122 | 14785 |
| `ABS800_EXTEND_ONLY` | -1698.50 | **18150.66** | -1828.59 | 0.8061 | **0.8788** | 0.9582 | 0.9996 | 0.0014 | 14785 |
| `ABS25_ALL_FILLS` | 1079.52 | **14101.63** | 28.52 | 0.9596 | **0.7234** | 1.7165 | 0.2561 | 0.5257 | 11942 |
| `ABS50_ALL_FILLS` | 1375.00 | **27421.59** | -122.50 | 1.0577 | **0.8896** | 1.7257 | 0.5378 | 0.3206 | 14451 |
| `ABS100_ALL_FILLS` | 1080.71 | **30193.96** | -299.78 | 1.1216 | **1.0127** | 1.8228 | 0.7794 | 0.1639 | 14785 |
| `ABS200_ALL_FILLS` | 29.84 | **28898.04** | -781.17 | 1.1349 | **1.0970** | 1.9206 | 0.9354 | 0.0599 | 14785 |
| `ABS400_ALL_FILLS` | -1176.91 | **22785.11** | -1491.48 | 1.0704 | **1.1094** | 1.6524 | 0.9935 | 0.0122 | 14785 |
| `ABS800_ALL_FILLS` | -1698.50 | **18150.66** | -1828.59 | 0.8061 | **0.8788** | 0.9582 | 0.9996 | 0.0014 | 14785 |
| `FRAC0.1_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `FRAC0.2_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `FRAC0.35_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `FRAC0.5_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `FRAC0.75_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `TTE120x0.5_EXTEND_ONLY` | 833.04 | **30718.29** | -522.98 | 1.1445 | **1.0735** | 1.8747 | 0.9017 | 0.0850 | 14785 |
| `TTE120x0.25_EXTEND_ONLY` | 1042.12 | **30472.97** | -379.76 | 1.1272 | **1.0397** | 1.8167 | 0.8658 | 0.1122 | 14695 |
| `TTE60x0.5_EXTEND_ONLY` | 137.01 | **31657.97** | -660.30 | 1.1156 | **1.1051** | 1.8175 | 0.9208 | 0.0729 | 14785 |
| `TTE60x0.25_EXTEND_ONLY` | -27.29 | **32634.71** | -527.58 | 1.0882 | **1.1002** | 1.8408 | 0.9067 | 0.0846 | 14740 |
| `ABS50+FRAC0.2_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS50+FRAC0.35_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS100+FRAC0.2_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS100+FRAC0.35_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS200+FRAC0.2_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS200+FRAC0.35_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS100+FRAC0.35+TTE60x0.5_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |
| `ABS200+FRAC0.35+TTE60x0.5_EXTEND_ONLY` **DEAD** | 0.00 | **0.00** | 0.00 | 0.8907 | **0.6201** | 1.6527 | 0.0000 | 1.0000 | 0 |

### Reading the table

- **The 13 dead cells are still dead.** Every `FRAC*` cell and every
  `ABS*+FRAC*` / `ABS*+FRAC*+TTE*` combo takes **zero fills** on both
  holdouts, exactly as in-sample. The mechanism is unchanged and structural:
  `FRAC` bounds `|residual| <= FRAC * post_gross`, and on the **first** fill of
  an event the whole book *is* the residual, so `|residual| = gross` and every
  `FRAC < 1.0` refuses the opening fill — after which no fill is ever a
  `COMPLETE` and the cell never starts. They are reported dead and **not
  rescued**. (Note: the task brief says 11 such cells; the frozen grid
  actually contains **13** — 5 `FRAC` + 6 `ABS×FRAC` + 2 `ABS×FRAC×TTE`. The
  set is identical in-sample and out, listed in `OOS_RESULT.json`
  `DEAD_CELLS`.)
- **`ALL_FILLS` collapses onto `EXTEND_ONLY` for caps ≥ 100** on the holdout,
  as in-sample: at those caps no fill the cap would block is ever a
  `COMPLETE`, so the two semantics coincide. The contrast arm is still only
  informative at caps 25 and 50, and there it is still worse.
- **Rank order is not preserved.** In-sample, net fell monotonically as the
  cap loosened (25 → 800). On the holdout net is **hump-shaped**, peaking at
  `TTE60x0.25` (+32,634.71) and `ABS100`/`ABS200`, with the tight `ABS25` cell
  far down the list. The in-sample ordering was an artifact of the baseline
  being negative there.
- **`net_bp_of_turnover` does its job again.** The tightest cell
  (`ABS25_ALL_FILLS`) is once more the one that trades least — 25.6% of
  turnover, 52.6% of actions refused — and is now also the **worst** cell by
  net on the primary holdout. On the holdout the gutting flags fire for
  `ABS25`/`ABS50`/`ABS100` and both `TTE120` cells
  (`matched_retained<50%_of_baseline`), so the three cells with the best
  headline net all carry a gutting flag.

---

## 3. THE PRE-COMMITTED TESTS, JUDGED IN THEIR OWN TERMS

The pre-commitment (FRONTIER.md §9) is explicit: *"Net dollars are not the
test; the exchange rate is."*

### T1 `ABS100_EXTEND_ONLY`

| | in-sample | PRIMARY | SECONDARY |
|---|---:|---:|---:|
| net ex incentives | +1,080.71 | **+30,191.19** | −299.78 |
| sign | + | **+** | **−** |
| exchange rate | 1.1216 | **1.0127** | 1.8228 |
| exchange rate > 1.0 | yes | **yes** | yes |
| net improvement vs `BASELINE` | +2,607.52 | **+11,513.41** | +1,523.92 |

**SIGN: survives on PRIMARY, FAILS on SECONDARY. EXCHANGE RATE: survives on
both (> 1.0 everywhere).** The exchange rate *compresses toward 1.0* on the
large holdout — 1.1216 → 1.0127 — meaning the cell now buys **$1.01** of
avoided residual loss for **$1.00** of surrendered matched economics. That is
the in-sample structural finding reproduced more starkly, not refuted: the two
components are even closer to stoichiometric over 107 days than over 22. The
net improvement is real but is almost entirely the **fee saving** that rides
along with refusing fills, not a favourable exchange.

### T2 `TTE120x0.25_EXTEND_ONLY`

| | in-sample | PRIMARY | SECONDARY |
|---|---:|---:|---:|
| net ex incentives | +1,042.12 | **+30,472.97** | −379.76 |
| sign | + | **+** | **−** |
| exchange rate | 1.1272 | **1.0397** | 1.8167 |
| exchange rate > 1.0 | yes | **yes** | yes |
| net improvement vs `BASELINE` | +2,568.93 | **+11,795.19** | +1,443.94 |

**Same verdict, same direction, same magnitude of compression** (1.1272 →
1.0397). The clock-tightening family holds up slightly better than the plain
`ABS` family on the holdout, and `TTE60x0.25` — which was the *worst* TTE cell
in-sample at −27.29 — is the **best cell in the grid** on the primary holdout
(+32,634.71, xr 1.1002). A cell's in-sample rank carried no information about
its holdout rank.

### T3 `ABS200_EXTEND_ONLY` — the pre-committed failure

In-sample this cell was positive overall (+29.84) but **negative in exactly
one stratum**, `E3_INCENTIVE_ERA` = **−336.15**, and that single-stratum
failure was pre-committed as a thing to re-check.

- **On PRIMARY it does NOT repeat:** `E3_INCENTIVE_ERA` = **+25,685.12**. The
  holdout's E3 is 14,365 events against in-sample E3's handful, so the
  in-sample E3 figure was essentially the two August days.
- **But the failure MOVES rather than disappearing.** On PRIMARY the cell is
  negative in exactly one stratum again — the newest one,
  **`REGIME_2026-08-19_ON` = −788.25** — while being +28,898.04 overall. So
  "positive overall, negative in one stratum, and the negative stratum is the
  most recent one" is the pattern that reproduces, with a different label.
- **On SECONDARY it repeats directly:** the only stratum present is
  `E3_INCENTIVE_ERA` and it is **−781.17**.

### The newest regime is where the engine is negative

This is the single most decision-relevant row in the run and it was not asked
for, so it is reported plainly:

| `BASELINE` (= L5, ex incentives) by regime, PRIMARY holdout | events | net $ | bp of turnover |
|---|---:|---:|---:|
| `REGIME_PRE_2026-04-25` | 170 | +1,649.55 | 179.2 |
| `REGIME_2026-04-25_TO_2026-06-22` | 1,641 | +8,333.74 | 63.3 |
| `REGIME_2026-06-23_TO_2026-08-18` | 8,235 | +14,545.54 | 41.4 |
| **`REGIME_2026-08-19_ON`** | **4,739** | **−5,851.05** | **−30.4** |

L5's ex-incentive cash decays monotonically across the four regimes and goes
**negative in the most recent one** — which is also the only regime the
SECONDARY holdout samples, and SECONDARY `BASELINE` is likewise negative
(−1,823.70). Both candidate cells do turn that regime positive
(`ABS100` +1,468.84, `TTE120x0.25` +1,402.63), which is the most favourable
reading available for them, but 10.5 bp and 8.6 bp of turnover is not a
margin, and it comes with rebates set to $0 in an era when L5 was demonstrably
collecting them.

---

## 4. L5's EX-INCENTIVE ECONOMICS WITH SETTLEMENT ACCOUNTED PROPERLY

Settlement is modelled as: **a held complete set redeems $1.00/share
regardless of outcome** (so matched shares are never booked at $0 merely for
want of a `REDEEM` record — the error that produced the in-sample
+$1,829.86 correction), and **the residual pays $1.00/share iff its side is
the resolved winner**, with the winner taken from the event's `REDEEM`
`outcomeIndex`.

### 4a. The REDEEM winner-resolver, validated on the holdout before being trusted

| | in-sample | **PRIMARY** | SECONDARY |
|---|---:|---:|---:|
| events with a `REDEEM` | 2,000 | **13,472** | 201 |
| median \|redeem cash − modelled payout\| | $0.0000 | **$0.0000** | $0.0000 |
| p90 \|difference\| | — | **$0.0000** | $0.0000 |
| mean \|difference\| | — | **$0.2995** | $17.6974 |
| exactness rate | 89.2% | **97.5%** | 99.5% |
| closer than its complement | 98.2% | **99.9%** | 100.0% |

The resolver is **better** behaved on the primary holdout than in-sample:
exact on 97.5% of 13,472 events, median and p90 difference both $0.0000, and it
beats the flipped-outcome hypothesis on 99.9%. It is trusted. The SECONDARY
mean of $17.70 is driven by harvest-window truncation, not by resolver error
(exactness is 99.5%).

### 4b. The two strata, kept apart

**PRIMARY holdout (14,785 events, 107 days):**

| stratum | events | matched $ | residual $ | fees $ | **net ex incentives $** | median event $ | event win rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SETTLEMENT_RESOLVED` (REDEEM-backed) | **13,472** | +179,437.65 | −80,027.47 | 48,428.75 | **+50,981.43** | +6.28 | 61.5% |
| `SETTLEMENT_UNRESOLVED` (no REDEEM) | **1,313** | −0.12 | −31,247.66 | 1,055.87 | **−32,303.65** | −11.35 | **0.0%** |
| **FULL_POPULATION (pooled)** | **14,785** | +179,437.54 | −111,275.13 | 49,484.62 | **+18,677.78** | +3.52 | 56.0% |

**SECONDARY holdout (255 events, 2 days):**

| stratum | events | **net ex incentives $** | median event $ | event win rate |
|---|---:|---:|---:|---:|
| `SETTLEMENT_RESOLVED` | 201 | **−640.66** | +4.44 | 71.1% |
| `SETTLEMENT_UNRESOLVED` | 54 | **−1,183.04** | −13.23 | **0.0%** |
| **FULL_POPULATION (pooled)** | 255 | **−1,823.70** | +1.13 | 56.1% |

### 4c. Does the +$2,016.81 reproduce?

**In SIGN, on the primary holdout: YES, and far larger —
+$50,981.43 on 13,472 source-settled events** (in-sample: +$2,016.81 on
1,998). Normalized, in-sample was +$1.01 per source-settled event; the primary
holdout is **+$3.78 per source-settled event**. On the secondary holdout it
does **NOT** reproduce: −$640.66 on 201 events.

**The in-sample coverage-hole artifact does NOT recur.** The six in-sample
events holding 1,829.86 matched shares with no `REDEEM` record were a harvest
boundary. On the primary holdout the equivalent quantity —
`SETTLEMENT_UNRESOLVED` events still holding matched shares — is **13 events
holding 6.22 matched shares, $6.22 of guaranteed-but-absent redemption**, and
on SECONDARY it is 2 events / $497.87. The primary holdout's no-REDEEM stratum
is therefore **not** a coverage hole: 1,300 of its 1,313 events are one-sided
positions, which is what a lost one-sided bet looks like.

### 4d. THE SELF-SELECTION CONFOUND — tested, and CONFIRMED

The owner's question: *is +$2,016.81 itself a selection artifact of which
events happened to have `REDEEM` records?*

**The mechanism is logical, not statistical.** On Polymarket a `REDEEM` exists
only if there was something redeemable: a winning share or a complete set. For
a **one-sided** position, having a `REDEEM` record is therefore *logically
equivalent to having won*. `SETTLEMENT_RESOLVED` conditions on that, so it
cannot be a random sample of events.

Tested directly with an **independent** resolver — market `outcomePrices` from
`data/l5/raw/markets.jsonl`, which does not look at L5's holdings at all:

| PRIMARY holdout | value |
|---|---:|
| events with independent resolution | 1,310 (8.9% of 14,785) |
| …of which REDEEM-backed | **0** |
| …of which no-REDEEM | **1,310** |
| one-sided no-REDEEM events covered | 1,297 |
| **fraction of those whose held side actually LOST** | **99.61%** (1,292 / 1,297) |
| one-sided no-REDEEM events that WON anyway | 5 |
| understatement from booking those 5 at $0 | **$3.29** |

**CONFIRMED: `SETTLEMENT_RESOLVED` is self-selecting.** 99.61% of one-sided
no-REDEEM events are events L5 **lost**. Absence of a `REDEEM` record is
near-perfectly predictive of a losing outcome, so the REDEEM-backed stratum is
the subset of events in which L5 *did not lose outright*. Saying it plainly:

> **+$50,981.43 (and the in-sample +$2,016.81 before it) is a CONDITIONAL
> quantity — L5's cash on the events where he had something to redeem. It is
> NOT an estimate of L5's unconditional ex-incentive economics and must never
> be quoted as one. The only unbiased figure is the pooled
> `FULL_POPULATION`.**

Two honest caveats on this test, in both directions:

1. The independent resolver's **coverage is itself non-random** — it covers
   1,310 no-REDEEM events and **zero** REDEEM-backed ones, because
   `markets.jsonl` was evidently harvested for exactly the events lacking a
   `REDEEM`. So `P(REDEEM | won)` cannot be estimated on this data at all;
   only the no-REDEEM direction can be, and it is the decisive one. The
   SECONDARY holdout has **0%** independent coverage, so the confound is
   **untested** there.
2. The confound does **not** overturn the pooled result. The pooled
   `FULL_POPULATION` figure already contains both strata, and on the primary
   holdout it is **+$18,677.78 over 107 days ≈ +$174.56 per observed day**,
   ex incentives, ex rebates. That is positive, out of sample, with the losers
   fully included — and it is the number that answers "was L5's engine
   profitable before incentives" without conditioning on anything.
   It is also **less than 40% of the $480/day historical target**, and the
   target was never claimed to be ex-incentive.

### 4e. Modelled settlement vs the audit's observed-redeem-cash valuation

| | PRIMARY | SECONDARY |
|---|---:|---:|
| modelled-settlement net | +18,677.78 | −1,823.70 |
| audit-style net (settlement = observed REDEEM cash only) | +20,350.53 | +1,235.60 |
| modelled − audit | **−1,672.74** | **−3,059.31** |

On the holdout the sign of this gap **reverses** relative to in-sample
(in-sample: modelled was *higher* by +$1,218.72 because of the six
unredeemed complete sets). On the holdout the audit-style valuation is
*higher*, because both harvests are windowed: they contain `REDEEM` records
for events whose buy-side fills fall outside the window, which credits cash
against no cost. This is a **harvest-boundary artifact of both valuations**,
it affects the pooled number by low single-digit percent on PRIMARY and
materially on SECONDARY, and it is the main reason the SECONDARY population
should not be used for a dollar claim at all.

---

## 5. WHAT THIS RUN SETTLES, AND WHAT IT DOES NOT

**Settled.**

1. The two candidates' **exchange rates survive** out of sample in the sense
   pre-committed — they stay above 1.0 — but they **compress toward 1.00**
   (1.12 → 1.01, 1.13 → 1.04) on 6.7× the events and 4.9× the observed days.
   The in-sample ceiling claim ("never exceeds 1.15") holds: the primary
   holdout's maximum across all 17 live cells (16 of which have a defined exchange rate) is **1.1094**.
2. The **signs survive on the primary holdout and fail on the secondary**. The
   secondary failure is not a surprise and not independent of (3).
3. **No contemporaneous residual bound in this grid separates matched
   economics from residual loss.** This is the in-sample finding, and it is
   the finding that reproduced most cleanly. The caps' net improvement is
   dominated by avoided **fees**, not by a favourable exchange.
4. **L5's source-settled positive reproduces in sign and is self-selected.**
   Both halves of that sentence are now evidence-backed, the second at 99.61%.
5. The **13 dead FRAC/COMBO cells are dead for a structural reason**, on every
   population, and will be dead on any population.

**Not settled, and not closed.**

6. **The frontier is NOT closed.** What is closed is the *question the
   frontier grid was built to answer*: within this grid of contemporaneous
   residual bounds, there is no cell that separates the good component from
   the bad one, and that negative result now holds out of sample. What remains
   open — and what §5/§10 of the in-sample report already named — is
   everything the grid cannot reach: order-level gating rather than
   fill-level suppression, maker/taker role, queue position, and the rebates
   that are $0 here by construction and were not $0 for L5.
7. The most recent regime, `REGIME_2026-08-19_ON`, is where L5's ex-incentive
   engine is **negative** (−$5,851.05 over 4,739 events), confirmed
   independently by the forward-in-time SECONDARY holdout (−$1,823.70). Any
   forward-looking claim has to be made against *that* regime, not against the
   pooled 107 days.

---

## 6. LIMITATIONS

1. **Suppression is still not counterfactual execution.** A cell can only
   refuse a fill L5 actually took. Nothing here models what the book would
   have done without L5's flow, what price the refused size would have
   reprinted at, or what L5 would have done next. Every number is an
   **accounting** bound on an observed sequence, not a backtest.
2. **Maker/taker role is still UNAVAILABLE**, so no cell can claim a rebate, a
   queue position, or that a refused fill would have been a maker fill. The
   holdout *does* carry 107 `MAKER_REBATE` / 70 `TAKER_REBATE` / 17 `REWARD`
   records, which proves the incentive income existed and is **excluded here**
   — so every holdout figure understates L5's actual cash by an amount this
   protocol deliberately refuses to estimate.
3. **Order placements and cancels are still UNAVAILABLE.** A residual cap in a
   live system gates *orders*; this study gates *fills*. That gap is not small
   and nothing here closes it.
4. **Both holdouts are harvest-windowed at both ends**, which contaminates any
   dollar total: `REDEEM` records appear for events whose fills precede the
   window and vice versa (§4e). On SECONDARY this is severe — 710 slugs were
   kept but only **255 events** could be built, the other 455 being
   REDEEM-only — and SECONDARY should be treated as a **2-day, 255-event sign
   check, not a dollar measurement**.
5. **`events_per_observed_day` in the frontier blocks is on the WRONG
   denominator.** `replay.OBSERVED_DAYS` is frozen at 22 and was deliberately
   **not** changed, so that metric is uninterpretable on the holdout. The
   holdout's own day counts (107 / 2) are reported separately in
   `populations.*.observed_days`.
6. **PRIMARY contains no E1 era** (it starts 2026-04-21), so the in-sample
   E1 figures have no out-of-sample counterpart at all.
7. **The self-selection test's resolver coverage is non-random** (8.9% of
   PRIMARY, 0% of SECONDARY) and covers only the no-REDEEM side, so
   `P(REDEEM | won)` is unmeasured. See §4d caveat 1.
8. **`FULL_POPULATION` pooling assumes a no-REDEEM one-sided residual paid
   $0.** That is correct 99.61% of the time on the measured subset; the 0.39%
   that won cost $3.29 to mis-book, which is immaterial, but it is an
   assumption on the 91.1% of events with no independent resolution.
9. **One trader, one venue, two market families (btc/eth updown 5m/15m).**
   Nothing here speaks to market-agnostic transfer.
10. **The 127 excluded colliding slugs** mean PRIMARY's April coverage is
    slightly thinner than the raw harvest suggests, and the April 2–20 records
    in the harvest produced no admitted events (they carry no in-family BUY
    fills), which is why PRIMARY's 107 observed days are fewer than the
    harvest's 130 record-bearing days.

---

## 7. PROVENANCE

- Frozen and untouched: `spec.py` (sha256 `f9730231…e948`),
  `PREREGISTRATION.md` (`958446eb…ea0e`), `replay.py` (`8a720945…6d0a`),
  `FRONTIER.md`, `FRONTIER.json`, `tests/test_residual_frontier.py` (27 tests,
  still passing).
- Added by this run: `oos.py`, `oos_adapter.py`, `OOS_RESULT.md`,
  `OOS_RESULT.json`, `tests/test_residual_frontier_oos.py` (19 tests).
  Combined suite: **46 passing**.
- Not read, not imported, not modified: `analysis/l5/s3/**`, any
  `live_exec*` spec, anything under `deploy/` or `scripts/l5_exec*`, any
  Railway variable or service. No git command was run. No order, no signing,
  no credential.
