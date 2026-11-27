# Directional inventory allocation — design, evidence, release candidate

27 Nov 2026. Branch `dev/pmus-directional-allocation`, built on the deployed
release `a51cfdbd2`. **Disarmed:** flag `PMUS_RN1_DIRECTIONAL` defaults to
`0`. Nothing is deployed, nothing live is changed, and no limit is
relaxed. Live trading needs separate owner authorization.

## 1. What was already there (reused, not rebuilt)

The unified sports controller already provides:
- multi-level maker ladder (`RN1_LADDER_LEVELS`);
- asymmetric inventory and same-side EXTEND (`pmus_rn1_replica.quotes`);
- independent side evaluation;
- marginal reward-adjusted EV, including the no-fair form;
- reward calibration;
- live-first selection and replenishment after a cool-down;
- the international guard: no opening or extension above the international
  fair minus 1 tick;
- per-market, campaign and account caps, loss stops, exact-order
  cancellation, reconciliation and the independent watchdog.

This work adds one thing: **allocation between the two sides driven by an
outside probability.** It is a pure module, `src/pmus_directional_alloc.py`,
with no venue imports (a test enforces this). It is called from two places
in `Engine.step_market`, behind the flag. It can only remove, shrink or
deepen an opening or extension bid. It never adds a bid, raises a price or
size, or touches a completion.

## 2. Evidence from our own live fills (no new campaign)

Script: `analysis/l5/sports/adverse/directional_accumulation.py`, on the
research branch. Data: all 110 live fills from 22–27 Nov, rebuilt from
production logs only (FILL, FILL_MARKOUT, RN1_QUOTE, jump_pause,
RN1_JOIN_TOUCH, SETTLEMENT_RECORDED). That gives 66 openings or same-side
additions, 22 of them in Stage 1. Results are clustered by market.

| Group (openings and additions) | 300 s markout per contract | Settlement return per contract |
|---|---|---|
| All 66 (34 markets) | **−4.5c ± 1.5** | −11.6c ± 8.4 |
| Favourite at fill (side mid > 0.5) | −4.0c ± 2.8 | −0.3c ± 14 |
| Underdog at fill | −3.8c ± 1.2 | −15.6c ± 9.6 |
| Side weakening over the prior 30/60/120 s | **−5.7c ± 1.5** | −14.3c ± 17 |
| Side flat | −0.4c ± 1.6 | −4.9c ± 13 |
| Side strengthening | −2.5c ± 3.0 | −9.8c ± 20 |

- **Adverse selection is systematic and the same for both sides.** Passive
  execution does not specifically collect deteriorating underdogs. It
  collects whichever side the price is running through, favourite or not.
  Entry was 2.1c below the side's own mid, and the price then moved about
  4.5c against us.
- **Momentum separates the outcome; favourite/underdog does not.** Fills on
  a side already weakening keep losing. That is about 2.4 standard errors on
  11 against 13 markets: suggestive, not established. Coverage of the
  look-back windows is 38–39 of 66 fills; a book older than 60 s is
  treated as unknown.
- Settlement returns per leg are binary and too noisy at this sample size to
  separate groups.
- **Conclusion for the design:**
  - A favourite inferred from our own venue's price is not a usable signal.
    This agrees with the withdrawn favourite-band result in
    `bonereaper_recent/FINDINGS.md`, 20 Nov.
  - A probability from the leading market is. The two-venue recordings of
    21 Nov showed international mid changes precede 91–93% of PMUS mid
    drops of 1c or more (`sports_wallets/FINDINGS.md`).
  - The allocation is therefore driven only by the international fair
    value. With no fair value it changes nothing.

## 3. Mechanism

For each opening or same-side addition the engine wants, and each ladder
level behind it, with the international band (lo ≤ p ≤ hi) once settled:

```
fair_s  = lo (YES) | 1 − hi (NO)                       conservative
edge    = fair_s − px − fill_loss(competition, placement) − fee
N       = pos[s] − pos[other]                            signed imbalance
risk(q) = λ · p_s(1 − p_s) · ((N + q)² − N²) / q          concentration charge
ev(q)   = edge + reward − risk(q)
```

- **Size** is the largest q, at most the engine's own, with ev(q) > `min_ev`.
  Otherwise the bid is refused (`directional:marginal_ev_not_positive`).
- **Flat or heavier side with a negative edge:** refused
  (`trade_edge_negative`). A reward can never carry it.
- **Lighter side:** risk(q) < 0, so cheaper opposite-side inventory is
  bought when its edge plus the variance reduction pays.
- **Ladder levels** are judged on the inventory ahead of them. This gives
  decreasing size and an earlier stop on the heavier side.
- **Favoured side:** the larger edge, with `hysteresis` so it does not flip
  on noise. The unfavoured side rests one tick further back when its edge is
  under `touch_edge`. Replenishment order is by ev per contract.
- **Re-assessment** happens every pass. When the fair value crosses, the old
  side's additions stop and the new side is bought. Nothing is sold; held
  inventory is carried to settlement.
- **Openings on a flat side are not judged** by default
  (`judge_openings=0`), so both sides still open and pair. Judging them made
  inventory one-sided in the backtest (§5).
- **Completions:** never refused, resized or re-priced. Pair-cost limits
  are unchanged.

### Mapping to the observed wallets

| Observed behaviour | Where it lives |
|---|---|
| RN1: resting maker bids only, both sides, multi-level ladder | existing `quotes` + ladder (unchanged) |
| RN1: loose pairs, no forced completion, same-side additions allowed | existing EXTEND; additions now priced by ev(q) |
| RN1: continuous replenishment | existing cool-down; order by ev per contract |
| RN1: residual held to resolution | unchanged (buy-only; nothing sold) |
| RN1: fair anchor from the leading market, pull on adverse moves | existing international guard and jump/volatility pulls; the band now also sizes additions |
| RN1: reward-aware | existing marginal reward EV; `reward` input to `assess` |
| Bonereaper: later accumulation on the higher-conviction side; moves to the new favourite | favoured side by fair-value edge, hysteresis, re-assessed each pass |
| Bonereaper: dynamic imbalance | N enters risk(q): imbalance allowed while edge pays for it |
| Bonereaper: early cheap-side accumulation | **not adopted**; cheap is not an edge without a fair value (§2) |
| Bonereaper: dominance ratio | **not adopted** as a fixed ratio; imbalance falls out of ev(q) |

## 4. Configuration (sports service; all default as shown)

| Variable | Default | Meaning |
|---|---|---|
| `PMUS_RN1_DIRECTIONAL` | `0` | master switch |
| `PMUS_RN1_DIR_RISK_AVERSION` | `0.005` | λ, $ per contract² of variance |
| `PMUS_RN1_DIR_MIN_EV` | `0` | marginal EV per contract must exceed this |
| `PMUS_RN1_DIR_HYSTERESIS` | `0.01` | favoured side flips only past this edge gap |
| `PMUS_RN1_DIR_DEPTH_UNFAVOURED_TICKS` | `1` | extra ticks back for the unfavoured side |
| `PMUS_RN1_DIR_TOUCH_EDGE` | `0.02` | the unfavoured side keeps its place above this edge |
| `PMUS_RN1_DIR_JUDGE_OPENINGS` | `0` | 1 = judge openings on a flat side too |

Fill loss comes from the existing `PMUS_RN1_FILL_LOSS_TOUCH` and
`PMUS_RN1_FILL_LOSS_BEHIND`. The probability comes from the existing
`intl_fair_band` (`PMUS_RN1_FAIR_STABLE_S`, `PMUS_RN1_FAIR_MAX_RANGE_USD`).

**Where it acts today:** only markets with a settled international fair
value, meaning ATP/WTA names the guard matches and three-way soccer. Setka
table tennis and other no-fair markets are unaffected; they stay on the
no-fair reward EV rule.

**Crypto reuse:** `allocate` and `assess` take any probability band and any
fill loss. The crypto controller can pass a spot-model band without
changing the inventory engine. Crypto is not modified here.

## 5. Backtest on recorded books

- **Harness:** `src/pmus_directional_backtest.py`; driver
  `scripts/pmus_directional_backtest_run.py`. It replays recorded books
  through the production `quotes`, guard and ladder rules. Only the order
  book is simulated.
- **Data:**
  - two-venue recordings, 21 Nov 2026, 20:27–00:15 UTC, every 3 s;
  - PMUS top-of-book (sizes in the second file) and the international book;
  - 65 CFB, NBA and NHL markets;
  - settlement from the PMUS public market record.
- **Fill model:**
  - "touch": optimistic, queue-blind;
  - "through": strict, the ask must trade through our bid;
  - fills capped by displayed size;
  - 3 s latency on placements and cancels, so stale bids get run over.
- **Fees:** maker 0.
- **Rewards:** not modelled; no programme is recorded for these markets.

**Every figure is THEORETICAL.**

| Metric (THEORETICAL) | touch: baseline | touch: additions-only | through: baseline | through: additions-only |
|---|---|---|---|---|
| 1. Settled trading P&L after fees | −$12.67 | −$8.60 | −$14.64 | **−$3.89** |
| 2. Confirmed maker rebates | $0 | $0 | $0 | $0 |
| 3. Confirmed liquidity rewards | $0 | $0 | $0 | $0 |
| 4. Total realized net | none (backtest) | none | none | none |
| 5. Modelled incentives | not modelled | not modelled | not modelled | not modelled |
| 6. Matched / residual | +$6.84 / −$19.51 | +$5.19 / −$13.79 | +$5.52 / −$20.16 | +$5.24 / −$9.13 |
| 7. Peak capital / return on it | $131.10 / −9.7% | $77.47 / −11.1% | $82.81 / −17.7% | $60.66 / −6.4% |
| 7. Turnover | 1.00 | 1.00 | 1.00 | 1.00 |
| Markets traded / fills | 22 / 159 | 22 / 100 | 17 / 105 | 17 / 80 |
| Per market | −$0.58 ± 0.30 | −$0.39 ± 0.23 | −$0.86 ± 0.32 | −$0.23 ± 0.26 |

Turnover is 1.00 because no market settled inside the 4-hour recording, so
capital is never released. It does not measure live recycling.

Paired on the same markets (`PAIRED_COMPARISON_20261121.txt`), additions-only
at λ = 0.005 against the baseline:

| Fill model | Gain per market | t | Markets better / worse | Source of the gain |
|---|---|---|---|---|
| through | **+$0.63 ± 0.29** | 2.2 | 8 / 4 | residual +$11.04, matched −$0.28 |
| touch | +$0.19 ± 0.20 | 0.9 | 5 / 10 | residual +$5.73, matched −$1.66 |

Judging openings as well (`directional_l*`) trades half the markets and is
no better per market (touch −$0.91, through −$0.83). λ between 0 and 0.01
changes little, because the edge test binds before the concentration charge.

**What this shows:**
- The correction cuts the residual losses that come from adding to a side
  the price is running through. It keeps matched-pair income.
- **Neither policy is profitable on these books before incentives.** That
  is consistent with RN1's own 30-day reconstruction: matched +$869k,
  residual −$388k, one-sided −$49k.
- The result is one evening, mostly CFB, not our allowlisted competitions.
- The ROC row is a diagnostic, not a forecast.

## 6. Validation

- **New tests:**
  - module: 14 (`tests/test_pmus_directional_alloc.py`);
  - engine in enabled mode with a fair value: 7
    (`tests/test_pmus_directional_engine.py`): without a fair value nothing
    changes; openings pass and additions on the overpriced side are refused;
    when the favoured outcome flips, additions stop and the completion is
    kept; caps hold through fills; an allocation error changes nothing; the
    campaign loss stop still pulls openings;
  - harness: 7 (`tests/test_pmus_directional_backtest.py`).
- **Enabled-mode safety module** with `PMUS_RN1_DIRECTIONAL=1`: 57 + 7 pass.
- **Image guard** (`tests/test_pmus_spring_image_modules.py`): it failed
  until `pmus_directional_alloc.py` was added to the Dockerfile, then
  passed.
- **Docker build (local, from this branch):**
  - all 62 `src` modules import inside the image with the flags on;
  - with `PMUS_TWO_SIDED_RN1=1`, `PMUS_RN1_DIRECTIONAL=1` and the
    activation flags, `python3 -m src.pmus_sports_two_sided` boots with the
    lock at `1`;
  - it ran 75 s with no loop error, latch or market error, failed closed on
    the unreachable account read, and cancelled on SIGTERM with nothing
    open.
- **Full regression, flags off:** see `REGRESSION_20261127.txt`.

## 7. Activation and rollback (not authorized by this work)

- **Activation:** `PMUS_RN1_DIRECTIONAL=1` on a release containing this
  branch, with the lock held through deploy verification
  (RELEASE_CHECKLIST.md). It acts only where a settled international fair
  value exists.
- **Rollback:** unset the flag. Every decision path returns to the deployed
  behaviour, and an error inside the allocation already falls back to it.

## 8. Not established

- Profitability: no variant here is positive before incentives.
- Live fills in markets with an international fair value. Most Stage 1
  fills were Setka, with no fair value.
- Whether the momentum effect (§2) is real at a larger sample. A
  weakening-side extension guard for no-fair markets is a candidate, but is
  not implemented: the evidence is about 2.4 standard errors on 11 markets.
