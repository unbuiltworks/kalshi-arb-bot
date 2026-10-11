# BTC5m FAVORITE-FLIP CONTINUOUS-INVENTORY LANE — state + preregistration

**Step 0 deliverable. No implementation, no replay, no strategy P&L yet.**
No live orders. PMUS, Polymarket, SOL and unrelated services untouched.

## 0.1 CURRENT STATE

| item | value |
|---|---|
| branch | `research/gemini-btc5m-information-edge` |
| SHA at start | `c4e0090c4442e1bf144800b5d1e3b3bde380daf7` |
| working tree | clean |
| prior closed conclusion | `115de70b` — **preserved, not reopened or rewritten** |
| live collector | `ie_collector.py` running (Coinbase BTC-USD + Gemini BTC05M event-level + strike/settlement + clock probe) |

**The prior finding stands and is not under appeal here:** *generic passive
back-of-queue BTC5m L5 replication failed on capacity.* This lane tests a
different hypothesis (favorite accumulation, flip switching, oscillation-built
matched inventory, bounded exposure, +$5 net target). Its provenance is
`DECISION_GATE.md`, `L5_SETTINGS_AUDIT.md`, `RESOLVED_1390.md`, all untouched.

## 0.2 DATASETS AVAILABLE

| dataset | coverage | contract data | BTC underlying | settlement truth |
|---|---|---|---|---|
| **`data/gemini_hifi/`** (3 tapes) | ~187 min, **42 qualified BTC5m windows** | **event-level `@depth` diffs + `@trade`, `U`/`u` verified, 0% desync** | **none** | none |
| **`data/ref_lag/`** (41 files) | **24.6 h, 179 BTC05M windows** | `pbook` bookTicker (executable bid/ask+sizes), 303k rows | Gemini BTCUSD spot, **sparse: gaps p50 1.26 s** | **33,772 settlement rows** |
| **`data/gemini_infoedge/`** (running) | growing | event-level `@depth`+`@trade` | **Coinbase BTC-USD ~4.7 trades/s** | strike + settlement via REST |
| `data/policy_d_port/` | 46 tapes | ~5 s sampled (superseded) | — | — |

**Highest-fidelity contract dataset:** `gemini_hifi` (event-level, verified
sequence chain) — but it has **no synchronized BTC underlying**, so §4 BTC
state is **not computable on it**.

**Only datasets with BTC underlying:** `ref_lag` (sparse, 179 windows, and on
the **superseded** `GRR-KAIKO_RFR_BTCUSD_60S` settlement source) and the
**new `gemini_infoedge`** feed (high-frequency Coinbase, current
`LS-LRRR-XBTUSD` source). **The new collector is the only source that can do
§4 properly, and it is already running** — no new infrastructure needed.

## 0.3 REUSABLE CODE — DO NOT REBUILD

| module | provides | state |
|---|---|---|
| `analysis/l5/gemini_trajectory/continuous_inventory.py` | `L5Inventory`, EXTEND/COMPLETE/SWITCH relations, STRICT/SUPPORTED/OPTIMISTIC fill tiers, pair-cost accounting | reuse |
| `analysis/gemini/microstructure/p3_hifi_capture.py` | **corrected** event-level FIFO queue model, sticky vs reprice, attrition tier | reuse (this is the "corrected methodology" §10 asks for) |
| `analysis/gemini/microstructure/p1b_tiered_capture.py` | through-price vs same-price decomposition | reuse |
| `src/gemini_maker/fair_value.py` | analytic settlement-average fair value | reuse **with caveat** (built for 60 s RFR; live source changed) |
| `analysis/gemini/l5_audit/a3_semantics_test.py` | UP/DOWN identity + mutation tests, Decimal rebate | reuse |

## 0.4 REBATE AND FEE RULES — VERIFIED FROM OUR OWN EVIDENCE

From `GEMINI_ECON_VIABILITY_AUDIT_1.md` (seven credited `AdminCredit` rows,
total **$0.51**, effective ≈97.6 bp) and `rescue_frontier.py` /
`BURST_CENSUS_INTERIM.md`:

- **maker fee** `0.0175 · C · P · (1−P)`, **ceil** to cent per fill
- **taker fee** `0.07 · C · P · (1−P)`, **ceil** to cent per fill
- **maker rebate** `0.30 × 0.07 · C · P · (1−P)` = `0.021 · C · P · (1−P)`,
  **rounded DOWN**, **eligible band $0.20–0.80 only**, paid daily 17:00 ET
- Decimal-safe required: a float implementation with floor-to-cent **breaks
  the P ↔ 1−P symmetry** (confirmed: $4.40 vs $4.41 at C=1000)

**Two consequences for §3's proposed bands, measured not assumed:**

| band | mid | rebate @100 ct | eligible? |
|---|---|---|---|
| 51–55¢ | 0.530 | $0.52 | YES |
| 55–60¢ | 0.575 | $0.51 | YES |
| 60–65¢ | 0.625 | $0.49 | YES |
| 65–70¢ | 0.675 | $0.46 | YES |
| 70–75¢ | 0.725 | $0.41 | YES |
| 75–80¢ | 0.775 | $0.36 | YES |
| **80–85¢** | 0.825 | **$0.00** | **NO — outside band** |
| **85–90¢** | 0.875 | **$0.00** | **NO — outside band** |

**At q1 the rebate is $0.00 in every band** (cent floor). Rebates only exist
at size.

---

# 0.5 THREE ARITHMETIC CONSTRAINTS FOUND WHILE ESTABLISHING STATE

These are **identities and measurements from already-frozen data**, not new
strategy results. I am surfacing them now because the instruction is to try to
break the hypothesis, and they bear directly on whether +$5/window is
reachable. **They do not pre-empt the test — the test is still preregistered
below and will be run faithfully.**

## Constraint 1 — the matched-pair cost identity

Verified exactly on live venue data: `buy.no + sell.yes = 1.00` in every
sample. So on this single-instrument market:

```
acquire UP   = pay ask          acquire DOWN = pay (1 - bid)
pair cost, both TAKER = ask + (1 - bid) = 1 + spread
pair cost, both MAKER = bid + (1 - ask) = 1 - spread
pair cost, MIXED                        = 1.000 exactly
```

**"Exploit oscillation to reduce effective matched-pair cost" is arithmetically
impossible with taker fills.** Every taker-built matched pair costs
`1 + spread` — a *guaranteed* loss of exactly the spread, independent of BTC,
flips, timing or skill. Oscillation does not reduce it; it manufactures it
faster.

Matched inventory is only profitable if **both** legs are maker fills, at
`1 − spread`.

## Constraint 2 — the measured spread makes that loss large

Event-level, in-window, two-sided, 6,036 observations across 42 windows:

| phase | spread p25 | **p50** | p75 | depth p50 | taker pair cost p50 |
|---|---|---|---|---|---|
| 0–15 s | 0.270 | **0.290** | 0.310 | 25 | 1.290 |
| 15–30 s | 0.260 | **0.280** | 0.300 | 25 | 1.280 |
| 30–45 s | 0.190 | **0.250** | 0.280 | 25 | 1.250 |
| 45–60 s | 0.180 | **0.250** | 0.290 | 25 | 1.250 |
| 60–120 s | 0.180 | **0.240** | 0.280 | 25 | 1.240 |
| 120–180 s | 0.180 | **0.240** | 0.280 | 25 | 1.240 |
| 180–240 s | 0.190 | **0.270** | 0.310 | 25 | 1.270 |
| 240–270 s | 0.280 | **0.300** | 0.350 | 220 | 1.300 |
| 270–300 s | 0.010 | **0.010** | 0.010 | 3 | 1.010 |

Only **3.4%** of in-window observations have spread ≤2¢, and those sit almost
entirely in the last 30 s (n=6 observations — far too few to rely on).

**Taker directional entry therefore carries a ~+12¢ handicap at every price**
(ask ≈ mid + half-spread):

| mid | ask paid | win +/ct | lose −/ct | break-even p | handicap |
|---|---|---|---|---|---|
| 0.530 | 0.650 | +0.350 | −0.650 | **0.650** | +0.120 |
| 0.625 | 0.745 | +0.255 | −0.745 | **0.745** | +0.120 |
| 0.725 | 0.845 | +0.155 | −0.845 | **0.845** | +0.120 |
| 0.825 | 0.945 | +0.055 | −0.945 | **0.945** | +0.120 |

Buying a market-implied **53%** favorite as taker requires a **65%** true win
rate to break even. **That is the bar the BTC-state model must clear**, and
"the market price says it's the favorite" cannot clear it by construction —
the favorite is already priced.

## Constraint 3 — what +$5 net implies in maker fills

Matched-pair profit per contract (both maker) = spread. So:

| spread | gain/ct | matched contracts for $5 | maker fills needed (2 legs) | vs measured **0.79 defensible fills/window** |
|---|---|---|---|---|
| 0.10 | 0.10 | 50.0 | 100.0 | **127×** |
| 0.18 | 0.18 | 27.8 | 55.6 | **70×** |
| 0.24 | 0.24 | 20.8 | 41.7 | **53×** |
| 0.29 | 0.29 | 17.2 | 34.5 | **44×** |

The measured rate (33 defensible fills across 42 windows, frozen cohort) is
**0.79 fills/window**. **+$5/window from matched inventory requires 44–127×
that rate.** This is the same capacity wall the closed lane hit, reached by a
completely independent route.

**And rebates cannot bridge it:** the rebate is at most **$0.52 per 100
contracts** at P=0.50 and **$0.00 above 80¢**. To contribute $5 of rebate you
would need roughly **960 maker contracts per window**.

## 0.6 WHAT IS *NOT* FALSIFIED BY THE ABOVE — AND MUST STILL BE TESTED

I am not closing this on arithmetic. Three parts of the hypothesis survive it:

1. **Directional (unmatched) P&L is not bounded by the pair identity.** If BTC
   state genuinely predicts the outcome, a directional position can pay
   despite the 12¢ handicap. That is an empirical question (models B and D).
2. **Maker entry flips the handicap's sign.** A maker fill on the favorite
   enters at the *bid*, i.e. ~12¢ **better** than mid — the single most
   attractive combination in the whole design. It is capacity-limited, not
   economically dead.
3. **Spread regime is the biggest unknown.** The 24¢ p50 comes from one
   afternoon's 42 windows. Live polling has already shown 2¢ spreads. If a
   materially tighter regime exists for sustained periods, Constraints 2 and 3
   both soften substantially. **This is the single highest-value thing the
   running collector will establish.**

---

# PREREGISTRATION (frozen before any strategy P&L is computed)

## P1 Models, tested separately, all reported including failures

| id | model |
|---|---|
| **A** | MARKET-FAVORITE ONLY — contract price/flips, no BTC input |
| **B** | BTC-STATE FILTERED — favorite only when BTC distance/momentum/vol supports it |
| **C** | CONTINUOUS INVENTORY — bounded EXTEND/SWITCH/COMPLEMENT |
| **D** | HOLD-TO-SETTLEMENT BASELINE — enter and hold |
| **E** | EXIT BASELINE — enter, sell on favorable reprice |

A/D/E are baselines that must be beaten; if C does not beat D, the inventory
machinery is not the source of any edge.

## P2 Preregistered bands, phases, caps — fixed now

- **bands:** 51–55, 55–60, 60–65, 65–70, 70–75, 75–80, 80–85, 85–90¢, plus
  continuous price retained as a variable
- **phases:** 0–15, 15–30, 30–45, 45–60, 60–90, 90–120, 120–180, 180–240,
  240–270, 270–285, 285–300 s
- **exposure caps:** $10, $20, $30, $50, $75, $100 gross per window
- **targets:** +$1, +$2, +$3, +$5, +$7.50, +$10
- **activation time** is a *reported variable*, never fitted on OOS. The "~30 s
  after start" idea is tested as one preregistered phase among eleven.
- **No cap, band, threshold or target will be chosen by in-sample P&L.** All
  are reported as a full grid.

## P3 Economic rule for every EXTEND (the anti-martingale constraint)

An EXTEND is admissible only if **all** hold at decision time:
1. gross committed dollars after the fill ≤ exposure cap;
2. directional residual `R` after the fill ≤ hard residual cap;
3. the fill improves expected economics **on its own terms** — for a matched
   leg, pair cost after the fill < 1.00; for a directional leg, modelled win
   probability > the executable break-even price;
4. the window has not hit STOP.

**Explicitly forbidden and asserted in tests:** martingale/doubling,
unconditional averaging down, unlimited rescue trades, and any assumption
that a window must become profitable.

## P4 Accounting, maintained continuously and never netted-into-one

`Q_UP, Q_DOWN, C_UP, C_DOWN`, avg prices, `M = min(Q_UP,Q_DOWN)`,
`R = |Q_UP − Q_DOWN|`, `C = C_UP + C_DOWN`,
`PNL_UP = Q_UP − C`, `PNL_DOWN = Q_DOWN − C`.
Reported **separately**: matched-pair economics · directional residual P&L ·
spread capture · maker rebates · fees · settlement P&L.
**Directional losses are never hidden inside matched-pair profit.**
Profit labels stay distinct: **MARKED** vs **LOCKED** vs **SETTLED**; marked
is never called realized.

## P5 Execution — defensible only

- **No touch=fill, no invented queue, no midpoint fills, no future knowledge.**
- **TAKER:** fills against contemporaneous displayed depth at the executable
  price, capped by that depth, with the latency grid applied.
- **MAKER:** the corrected event-level FIFO model from `p3_hifi_capture.py` —
  queue ahead from displayed size, trades consume queue first, through-prints
  fill regardless, cancels credited only under the separately-reported
  attrition tier. **Sticky orders retain priority; the chasing-touch mistake
  (measured 102 reprices/fill) is not repeated.** Reported per maker order:
  time resting, `q_ahead`, through-price vs same-price fills,
  cancellations/reprices, attributable rebates.
- **Fees/rebates:** Decimal-safe, ceil-per-fill fees, floor rebate, band
  enforced.

## P6 Validation

Chronological **DEVELOPMENT / VALIDATION / UNTOUCHED OOS**. Rule frozen
before OOS; OOS read once. **Not optimized on OOS:** price band, start time,
extension amount, BTC-distance threshold, momentum threshold, exposure cap,
profit target. If many combinations are explored, **all are reported, not only
the winner.**

## P7 The statistic that decides it

Not win rate. **Total positive-window profit minus total failed-window losses
after all costs**, with the left tail reported explicitly: worst window, max
drawdown, average failed-window loss, residual exposure distribution. A rule
that wins $5 in 95% of windows and loses $100 in 5% is **rejected**.

## P8 Daily economics

Report observed **qualifying** windows/day — never assume 288 — then NET per
qualifying window, NET per available window, capital turnover, concurrent
capital requirement, and implied daily NET **only at observed frequency**.
Benchmarks ($288/$480/$576/$1,440 per day) are targets, not assumptions.
**$1,000 bankroll deployment is not recommended at this stage.**

## P9 Decision gate — chosen once against untouched OOS

**A** REJECT · **B** SIGNAL EXISTS, EXECUTION FAILS · **C** POSITIVE BUT
INSUFFICIENT CAPACITY · **D** REALTIME SHADOW CANDIDATE. **Only D advances,
and only to shadow. No live money.**

## P10 Stopping rules declared in advance

- If BTC state cannot be reconstructed leakage-free for a cohort, §4 is
  reported **not computable** on it rather than approximated.
- If the matched mechanism requires maker fills at rates the measured capture
  cannot supply, that is reported as the **capacity** finding (gate C), not
  patched with theoretical fills.
- Theoretical rebates are **never** counted as realized cash; A/B/C rebate
  reporting (before / credited-defensible / theoretical bound) is mandatory.
- OOS wins any contradiction with DEV/VALIDATION.

---

## WHAT HAS NOT BEEN DONE

No replay built or run. No strategy P&L computed. No parameter fitted. No
band, cap, threshold or activation time selected. No live orders. The closed
maker lane and its provenance are untouched.
