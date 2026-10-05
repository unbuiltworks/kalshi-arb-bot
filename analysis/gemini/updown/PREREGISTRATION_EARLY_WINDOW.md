# PREREGISTRATION — BTC5m early-window analysis

**Frozen before any profitability number from the new recorder was inspected.**

| | |
|---|---|
| analysis script | `scripts/gemini_early_window_analysis.py` |
| **fingerprint (sha256)** | **`f2800e3e55672485708a7e0ebd167d92d69bb160f7091ccd8b43f6a6c960e90b`** |
| collection baseline | `ce68fa49` |
| recorder run | `37321367522` (stage 2, 32,400s) — **not interrupted** |

The fingerprint is the point. The script is run **unchanged** on each tranche; if it is
edited after a tranche's profitability is seen, the fingerprint moves and that is
detectable. Rerunning an edited analysis is the single thing this document exists to
prevent.

## Primary question (fixed)

> During **T+15 … T+120**, can the causal observed state `D_t`, its trajectory, and the
> contemporaneous executable book state identify positive expectancy **before the winning
> side becomes unavailable**?

The objective is **not win rate**. It is whether any causally identifiable state has
settlement probability sufficiently above executable acquisition cost to give positive
expectancy at useful frequency and capacity. Losing trades are acceptable. NO_TRADE
windows are acceptable.

## The ten commitments, each enforced in code

**1 · ECONOMIC_TWO_SIDED ≠ structural TWO_SIDED.** Structural = both sides carry ≥1
level (what the recorder labels). **Economic(S)** = **both** sides can actually fill **S**
contracts. Reported at every predeclared level — **S ∈ {1, 5, 10, 25, 50, 100}** — under
**both** depth variants, `TOUCH` (best level's own size) and `CUMULATIVE` (walking the
ladder). **All 12 combinations are reported; none is selected.** The validated tape
already shows why this matters: a book with bid `0.02 ×1` against ask `0.25 ×200` is
structurally two-sided and economically one-sided at any S > 1.

**2 · `D_t` = published `LRRR_XBT` reference − strike.** Causal by construction: the
latest published value whose venue event time `E` ≤ the checkpoint's own wall clock
(bisect on an ascending series). Reported in **dollars and basis points**. If the newest
such value is older than 10 s the state is marked `STALE` and the age travels with it; it
is never interpolated forward. **It is not called Z.**

**3 · Settlement probability** by checkpoint × sign/magnitude of `D_t` × executable-ask
bucket, **separately for UP and DOWN**. Fixed bins: `D_BINS_USD` = ±(5, 20, 50, 100, 200);
`D_BINS_BPS` = ±(2, 6, 12, 25); `ASK_BINS` = 0.10 steps.

**4 · Every probability is reported against the actual executable VWAP plus the published
taker fee** (7%·q·p·(1−p)), as an edge in percentage points and as EV per share, with the
zero-fee breakeven beside it because that schedule is **not** reconciled against our own
fills. A win rate is never reported without its breakeven.

**5 · Per-window and per-day economics divide by ALL observed windows**, NO_TRADE
included. Every such figure carries `denominator_note` naming the count. `$/day` projects
at 288 BTC5m windows.

**6 · No cutoff is optimised.** The bins are module constants. The script contains no
search, no argmax over thresholds, and no "best cell" output. **No threshold will be
promoted from the same tranche that discovered it.**

**7 · OOS is sealed.** Windows are ordered chronologically; the last **40%** are sealed
and `analyse()` **refuses to read them** without an explicit `unseal_oos=True`, recording
the refusal in the report. Unsealing happens once, to freeze a candidate.

**8 · Trajectory features are causal and leakage is asserted, not promised.** `D_t`
momentum (since first observation, and step-over-step), absolute distance, book state,
displayed depth per side, level counts and quote age — all computed from the checkpoint
and earlier. The eventual economic transition is retained as a **descriptive label in its
own quarantined block**, and `assert_no_leakage()` raises if any feature name contains
`won`, `resolution`, `settlement`, `eventual`, `winner`, `disappear` or `future`.

**9 · Z remains NOT IDENTIFIED.** `D_t` is an observed difference, not a normalised
state. My own N=60 mean hypothesis for `LRRR_XBT` was tested against all 478 published
values and **refuted** (exact in 34.5%; 313 values have no minimal denominator ≤240). No
alternative statistic is assumed.

**10 · No BTC5m live orders** are placed by this analysis. It has no order path.

## Tranche schedule (fixed now)

| tranche | trigger | status |
|---|---|---|
| **T1** | ~12 completed new BTC5m windows (~1 h) | **exploratory / mechanics evidence only** |
| **T2** | ~24–36 completed windows (~2–3 h) | **same frozen script, thresholds unchanged by T1** |
| **T3** | toward ~108 windows | confirmation; sealed OOS partition available to freeze a candidate |

Each tranche is **preserved before analysis** — the tapes are gzipped and committed, and
the report names the tape files it read. T1 is explicitly not evidence of expectancy; it
is evidence about mechanics and about whether the dataset can answer the question at all.

## Exclusion, declared in advance

Any BTC5m window in which the live execution probe submits an order is **excluded from
this analysis dataset**. A crossing order consumes displayed depth and so changes the
book the recorder observes; tagging is not sufficient, exclusion is. See
`BTC5M_PROBE_SPEC.md` §6.
