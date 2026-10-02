# Favorite-inventory lane: FROZEN implementation + DEV/VALIDATION findings

**Implementation frozen at this commit.** The prospective OOS cohort
(`data/gemini_infoedge`, still collecting) has **NOT been read**. No live
orders. The closed passive-maker conclusion at `115de70b` is untouched.

## 1. INVARIANTS — the Step-0 arithmetic is now enforced in the code path

`fi_core.py` raises if a matched pair's cost violates the identity, so the
controller **cannot** describe two taker fills as profitable matching.
`test_fi_invariants.py`: **all pass** (29 checks).

| identity | enforced |
|---|---|
| taker/taker pair cost = **1 + spread** | ✅ on real spreads 0.02–0.80 |
| maker/maker pair cost = **1 − spread** | ✅ |
| mixed pair cost = **1.000 exactly** | ✅ |
| taker/taker matched P&L = **−qty × spread**, negative for BOTH outcomes | ✅ (path F negative control) |
| `pnl == matched + residual` for both outcomes | ✅ |
| rebate **zero** above 0.80 / below 0.20 / at q1 | ✅ |
| rebate symmetric in P ↔ 1−P under Decimal | ✅ |
| fees **ceil** to the cent (0.0175 → 0.02), exact cents not inflated | ✅ |
| EXTEND refused on gross-cap or residual-cap breach (anti-martingale) | ✅ |
| midpoint never an acquisition price | ✅ |

**Path F (taker→taker) is therefore a proven negative control by
construction, not by simulation.** One fee-test failure was found during the
run — it was my *test* asserting `0.01` where the correct ceiling of `0.0175`
is `0.02`; the implementation was right and the test was corrected.

## 2. COHORT

`data/ref_lag/` — the only historical dataset with **all four** of executable
quotes + depth, the strike, BTC underlying, and **settlement ground truth**.

- 776 BTC05M markets seen; **176 with both settlement and book**
- resolution split 90 yes / 86 no (balanced)
- **Outcome mapping independently verified: `resolution_side == (settlement_value > strike)` in 38/38 windows where both fields exist.** `yes` = settlement above strike = UP wins.
- chronological split: **DEV 105 windows / VALIDATION 71 windows**
- **UNTOUCHED OOS:** `data/gemini_infoedge`, prospective, not read

**Stated limits:** `pbook` is bookTicker (**top of book only**), so
`queue_ahead` is the displayed BBO size and the deeper ladder is unobservable;
BTC spot is Gemini's own (gaps p50 1.26 s), so the 1 s momentum term is
marginal while 5/15/30/60 s are usable; and this cohort is on the **superseded
`GRR-KAIKO_RFR_BTCUSD_60S`** settlement source.

## 3. A METHODOLOGICAL CORRECTION I MADE TO MY OWN REPORTING

The 11 phases sampled per window **share one settlement**, so phase
observations are **not independent**. Reporting 742 "opportunities" overstates
the evidence; the effective sample is **windows**. Both views are reported
below, and the per-window view is the one that counts.

## 4. SIMPLE $5-TARGET DIRECTIONAL BASELINE — the user's original concept, not buried

Favorite identified from the executable book at each preregistered phase,
sized to earn ≈$5 on a winning settlement, **held to settlement**.

### Per-observation

| path | split | opportunities | filled | win% | mean break-even p | TOTAL NET | net/fill |
|---|---|---|---|---|---|---|---|
| **A taker** | DEV | 742 | 742 | 52.6% | **0.644** | **−$1,948.26** | −$2.63 |
| **A taker** | VALIDATION | 565 | 565 | 48.5% | **0.603** | **−$1,522.06** | −$2.69 |
| **B maker** | DEV | 742 | **93** (12.5%) | 43.0% | **0.569** | **−$203.64** | −$2.19 |
| **B maker** | VALIDATION | 565 | **38** (6.7%) | 31.6% | **0.571** | **−$121.22** | −$3.19 |

### Per-window (effective n, first fill per window)

| path | split | windows | win% ± se | net / window |
|---|---|---|---|---|
| A taker | DEV | 97 | 53.6% ± 5.1% | **−$1.12** |
| A taker | VALIDATION | 64 | 51.6% ± 6.2% | **−$0.61** |
| B maker | DEV | 25 | 52.0% ± 10.0% | **+$0.15** |
| B maker | VALIDATION | 20 | 40.0% ± 11.2% | **−$1.45** |

### The deciding statistic — total positive minus total negative

| path | split | total positive | total negative | **NET** |
|---|---|---|---|---|
| A taker | DEV | +$1,839.45 | −$3,787.72 | **−$1,948.26** |
| A taker | VALIDATION | +$1,306.61 | −$2,828.67 | **−$1,522.06** |
| B maker | DEV | +$177.87 | −$381.52 | **−$203.64** |
| B maker | VALIDATION | +$55.51 | −$176.73 | **−$121.22** |

**All four negative.** Worst single outcomes: taker **−$162.01**, maker
−$24.49. Taker losses over $50 occurred 13 times. **Max cash at risk for a $5
target reached $245** — on a $1,000 bankroll that is 24.5% of capital risked
for $5 of upside.

## 5. WHY IT LOSES — two distinct mechanisms, both measured

**Path A (taker): the spread handicap, exactly as Step 0 predicted.**
Win rate is ~50% (53.6% / 51.6% per window, within 1 se of a coin flip) while
**break-even is 0.603–0.644**. A ~14-point structural gap. The favorite is
already priced, so "the market says it is the favorite" cannot clear a bar the
market itself sets. Every band is negative, every phase is negative, and every
spread bucket is negative except n=3 noise cells.

**Path B (maker): better price, adversely selected fills.**
Maker entry does what Step 0 predicted — it improves break-even from 0.644 to
**0.569**. But the **win rate falls to 43%/31.6%, below that better
break-even**, and the fill rate collapses to 12.5%/6.7%. The reason is
structural: a resting buy fills precisely when the market trades *down*
through it, so the fills obtained are selected for being wrong. **This is the
closed lane's adverse-selection finding reappearing in a directional
setting.**

Note the one apparently-positive cell — maker 51–55¢ in DEV at +$0.279/fill
(n=29) — **does not replicate**: VALIDATION gives −$0.243 (n=10). It is
reported because all cells are reported, not because it is a finding.

## 6. BANDS AND SPREAD REGIME

The high bands are the worst, which the $5 sizing explains: at 80–85¢ the
break-even is ~0.91, so ≈58 contracts and ~$53 of cash are needed for $5 of
upside, and a loss takes all of it. DEV taker 80–85¢ = **−$11.28/fill**;
VALIDATION 85–90¢ = **−$45.81/fill** (n=2).

Spread regime did **not** rescue anything in this cohort: the `≤2¢` bucket is
the largest (380/369 observations) and still loses (−$1.49 / −$1.06 per fill),
because a tight spread lowers the handicap but does not create prediction.
**This partially answers the Step-0 open question — a tighter spread regime
alone is not sufficient.**

## 7. FROZEN RULES (no changes permitted before OOS)

Frozen: the six execution paths; the eight bands; the eleven phases; the five
spread buckets; the $5 target; the $10/$20/$30/$50/$75/$100 exposure caps;
the EXTEND admissibility rule; the fee/rebate arithmetic; the FIFO maker rule
(through-price fills regardless of queue; same-price only once observed traded
volume exceeds the displayed queue joined behind; cancels do not advance us;
sticky priority, no touch-chasing); and the leakage discipline (book and BTC
state at `t` use only data with timestamp ≤ `t`; settlement scores only,
never decides).

**Nothing above was selected by in-sample P&L.** All bands, phases and buckets
are reported in full, including the losing ones.

## 8. STILL TO RUN ON THE FROZEN CODE

Models **C (continuous inventory)**, **D (hold baseline — partially covered,
since the simple baseline *is* hold-to-settlement)**, **E (exit baseline)**,
and **BTC-state filtering** of paths A/B. The bar BTC state must clear is now
quantified: it must identify situations with **true probability above
0.603–0.644 (taker)** or **0.569 (maker)**, against a measured unconditional
win rate of ~50%.

Then the prospective OOS read, once.

## 9. INTERIM POSITION — NOT the final gate

On DEV and VALIDATION the simple favorite-accumulation concept is **rejected
in both execution modes**, for two independently measured reasons (spread
handicap; adverse fill selection). The final gate (A/B/C/D) will be chosen
**after** models C/E and BTC-state filtering are run on this frozen code and
the untouched OOS is read once.

I will not claim the hypothesis is dead before those run — but the two
mechanisms above are structural, apply to every band and phase, and are the
same wall the maker lane hit. **Current expectation is gate A or B.**
