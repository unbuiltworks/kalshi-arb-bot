# EXPLORATORY_V2 — how much of L5's oscillation do we actually need?

> **FROZEN 2026-09-17 by owner direction — "Freeze EXPLORATORY_V2. Do not tune
> it further."** Hashes in `FROZEN.json`. These files are evidence; changing one
> after the freeze invalidates it and must be recorded as a NEW study, never as
> an edit to this one.
>
> **Disposition.** `V2E` matched-preservation is the **leading ADAPTED
> hypothesis for prospective testing — NOT promoted to live.** `V2A` and `V2F`
> are preserved as **secondary challengers**. `V2C` run-cutoff is **REJECTED as
> a control rule** despite the best OOS result, because its cutoff is
> temporally unstable (~9 TRAIN vs ~4 OOS) and every member fails TRAIN. It is
> retained as evidence, never as a gate.

`analysis/l5/exploratory_v2/` · BTC15m · 5,546 windows, 693,731 attributed
fills · chronological TRAIN (3,882 windows) / OOS (1,664 windows) ·
`btc15m_v2_sweep.json`, `btc15m_v2_studies.json`

Follows the FIDELITY conclusion recorded in `L5_OPERATING_MODEL.md` §2a:
**on BTC15m L5 is an inventory oscillator, not a flat-completion controller.**
FIDELITY reproduces that process; ADAPTED asks how much of it is needed to
harvest the matched economics without inheriting the residual economics.

Findings only. No policy, no risk-limit change, no executor. The frozen PMUS
sweep is untouched.

## What the counterfactual is — and is not

Each intervention is a **gate** applied to L5's observed fill sequence. A
suppressed fill is dropped and the inventory walk continues without it. Nothing
is added, no price is invented, no fill is moved.

**It assumes the surviving fills still happen, which is false in detail.** Had
L5 not bought, the book, his queue position and his counterparties would all
have differed, and there is no historical book in this dataset. Read every
number as *"the economics of the trades we would have kept"* — the standard
did-not-take-these-trades account — never as a simulation of a deployed policy.

Costs are `usdcSize` (fee-inclusive), so `trading_pnl_after_fees` is already
net of fees actually paid. **Incentives are never inside these numbers.**

### The benchmark that stops a trivial answer

A gate that merely shrinks the book by a factor *f* loses *f* × matched and
saves *f* × residual. Its ratio of matched-lost-per-residual-dollar-saved is
therefore fixed by the control:

| | control matched / \|residual\| | net of pure shrinkage |
|---|---|---|
| TRAIN | **1.342** | −$17,837 × *f* |
| OOS | **1.004** | −$355 × *f* |

**Only a gate with a ratio below that line is doing anything but shrinking.**
Every result below is read against it.

## 1. The headline: chronological consistency separates edge from regime fit

CONTROL (exact FIDELITY): TRAIN matched +$70,025 / residual −$52,188 /
**net +$17,837 (58 bp)**; OOS matched +$92,232 / residual −$91,877 /
**net +$355 (1.1 bp)**.

**Gates net-positive in BOTH periods** — ratio, matched P&L retained, fills
retained:

| gate | net TRAIN | net OOS | eff TR | eff OOS | matched P&L kept TR/OOS | fills kept TR/OOS |
|---|---|---|---|---|---|---|
| V2A cap 100 | +$2,232 | +$8,119 | 0.939 | 0.900 | 0.508 / 0.208 | 0.870 / 0.805 |
| V2F k100 n4 v.05 t2 | +$2,384 | +$7,855 | 0.941 | 0.906 | 0.458 / 0.174 | 0.834 / 0.786 |
| V2A cap 200 | +$3,734 | +$5,147 | 0.854 | 0.922 | 0.689 / 0.344 | 0.959 / 0.918 |
| V2F k200 n6 v.10 t1 | +$2,334 | +$5,317 | 0.917 | 0.922 | 0.632 / 0.321 | 0.920 / 0.880 |
| V2D vol 0.20 | +$1,219 | +$3,721 | 0.945 | 0.917 | 0.698 / 0.553 | 0.884 / 0.890 |
| V2A cap 400 | +$2,727 | +$1,567 | 0.651 | 0.955 | 0.927 / 0.643 | 0.994 / 0.979 |
| **V2E near10 sub50** | **+$867** | **+$971** | **0.428** | **0.693** | **0.991 / 0.976** | **0.998 / 0.990** |
| **V2E near5 sub25** | **+$113** | **+$1,115** | **0.587** | **0.483** | **0.998 / 0.989** | **0.972 / 0.983** |
| V2E near25 sub100 | +$293 | +$149 | 0.496 | 0.716 | 0.996 / 0.996 | 1.000 / 1.000 |

**Gates positive OOS but NEGATIVE in training — regime fit, not edge.** Every
run-cutoff gate is here, and so is every tight residual cap:

| gate | TRAIN | OOS |
|---|---|---|
| V2C run 2 | **−$2,601** | +$11,208 |
| V2C run 3 | −$1,449 | +$9,614 |
| V2A cap 25 | −$4,840 | +$8,428 |
| V2A cap 10 | −$5,956 | +$8,325 |
| V2C run 4 / 6 / 8 / 12 | −$1,302 / −$825 / −$960 / −$1,249 | +$8,013 / +$6,983 / +$5,102 / +$4,389 |
| V2D vol 0.02 / 0.05 | −$3,384 / −$2,437 | +$7,629 / +$5,850 |
| V2B linear k25–k100 | −$3,385 … −$1,197 | +$7,530 … +$6,494 |

The biggest OOS winner, `V2C run 2` (+$11,208), would have **lost money in
training** and retains only 20% of matched P&L and 57% of fills. That is the
trap the owner named: it eliminates residual loss by eliminating the cycling.

## 2. The frontier

Two distinct regions, and they are not the same trade.

**High efficiency, small magnitude — V2E, matched-inventory preservation.**
Removes residual risk at **0.43–0.72 of a matched dollar**, far below the
1.34 / 1.00 shrinkage line, while keeping **97.6–99.9% of matched P&L** and
**99–100% of fills and turnover**. It is the only family net-positive in both
periods at essentially undisturbed matched economics. Its absolute size is
small (+$867 / +$971 for `near10 sub50`).

**High magnitude, near-parity efficiency — V2A caps and V2F hybrid.** Larger
net gains (up to +$8,119 OOS) at ratios of 0.85–0.96, i.e. barely better than
shrinkage, and they destroy **50–90% of matched P&L**.

**Matched *volume* retained is a misleading metric on its own, and the sweep
reports both for that reason.** `V2A cap 200` keeps 92% of matched *volume*
OOS but only **34% of matched P&L**: dropping fills raises the effective pair
cost, and matched P&L is `matched × (1 − pair_cost)`. V2E keeps 96.7% of volume
**and** 97.6% of P&L — it is the only family that leaves pair cost alone.

## 3. Run length: (3) both, with a cutoff — and the cutoff MOVED

Per-share attribution by position within the same-side run (proportional
attribution; it reconciles exactly to the window totals):

| run position | matched frac | matched/sh TR | residual/sh TR | **total/sh TR** | matched/sh OOS | residual/sh OOS | **total/sh OOS** |
|---|---|---|---|---|---|---|---|
| 1 | .928 / .933 | +.0102 | −.0058 | **+.0044** | +.0157 | −.0135 | **+.0021** |
| 2 | .924 / .929 | +.0099 | −.0061 | **+.0038** | +.0155 | −.0144 | **+.0010** |
| 3 | .919 / .924 | +.0096 | −.0070 | **+.0027** | +.0156 | −.0153 | **+.0003** |
| 4–5 | .911 / .917 | +.0101 | −.0080 | **+.0021** | +.0154 | −.0171 | **−.0017** |
| 6–8 | .900 / .908 | +.0107 | −.0104 | **+.0003** | +.0157 | −.0193 | **−.0035** |
| 9–12 | .886 / .897 | +.0119 | −.0132 | **−.0013** | +.0162 | −.0217 | **−.0055** |
| 13+ | .851 / .867 | +.0144 | −.0214 | **−.0069** | +.0173 | −.0300 | **−.0127** |

* **(1) is true.** Matched P&L per share does **not** decay with run length —
  it is flat and if anything *rises*. Long runs are genuinely productive of
  matched inventory.
* **(2) is true.** The matched fraction falls monotonically (.93 → .85) and
  residual P&L per share deteriorates by **3.7× TRAIN and 2.2× OOS**.
* **(3) is the answer, and the cutoff is not stable.** Total per share crosses
  zero at run position **≈ 9 in TRAIN** but **≈ 4 in OOS**.

That instability is exactly why every V2C gate fails the both-periods test: a
cutoff fitted at 8 would have been far too loose out of sample. **Run length is
the strongest *predictor* of L5's EXTEND and a poor *control* variable — those
are different things and this is the case that separates them.**

Note the counterfactual and the attribution answer different questions and only
agree OOS: attribution says fills at run ≥ 4 contributed −$30 TRAIN / −$7,777
OOS; the counterfactual for `V2C run 4` says −$1,302 TRAIN / +$8,013 OOS.
Attribution decomposes the realized outcome holding everything else fixed; the
counterfactual recomputes pair cost and matched quantity after the removal.

## 4. Volatility: |Δp| buys more opportunity at the same price, and more risk

| \|Δp\| 60s | P(opposite fill ≤60s) TR/OOS | median pair with next opposite | total/sh TR | total/sh OOS |
|---|---|---|---|---|
| ≤0.01 | .821 / .916 | **0.9900** | +.0065 (118 bp) | +.0015 (30 bp) |
| 0.01–0.02 | .832 / .920 | **0.9900** | +.0055 (102 bp) | +.0013 (25 bp) |
| 0.02–0.05 | .840 / .911 | **0.9900** | +.0037 (76 bp) | −.0004 (−9 bp) |
| 0.05–0.10 | .857 / .931 | **0.9900** | +.0026 (56 bp) | −.0014 (−30 bp) |
| >0.10 | **.880 / .947** | **0.9900** | +.0003 (7 bp) | **−.0018 (−40 bp)** |

L5's higher EXTEND rate in volatility is **not irrational**: matching
opportunity really does rise, monotonically, in both periods. But **the price
does not improve — the attainable pair with the next opposite fill is 0.9900 in
every single bin** — while residual P&L per share deteriorates monotonically.

**Verdict: more opportunity, unchanged pair economics, more residual risk. Net
value-destroying, crossing zero out of sample at |Δp| ≈ 0.02.** Magnitude only;
`sign(Δp)` was falsified OOS in RESIDUAL_EXIT_FORENSICS_V1 and is used nowhere.

## 5. The >$1 COMPLETE, re-read under the oscillator model — the owner is right

Fills scored by what their shares **actually earned** (proportional
attribution) rather than by the pair implied at the instant of the fill:

| instantaneous pair | median window pair cost | matched/sh | residual/sh | **total/sh** | bp/$ |
|---|---|---|---|---|---|
| **TRAIN** | | | | | |
| ≤0.96 | 0.9709 | +.0137 | **−.0148** | **−.0011** | **−28** |
| 0.96–1.00 | 0.9796 | +.0099 | −.0061 | +.0038 | +77 |
| **1.00–1.02** | 0.9831 | +.0086 | −.0030 | **+.0056** | **+98** |
| 1.02–1.05 | 0.9832 | +.0081 | −.0038 | +.0043 | +75 |
| **>1.05** | 0.9888 | +.0043 | −.0004 | **+.0039** | **+63** |
| **OOS** | | | | | |
| ≤0.96 | 0.9640 | +.0175 | **−.0222** | **−.0046** | **−119** |
| 0.96–1.00 | 0.9687 | +.0148 | −.0147 | +.0001 | +2 |
| **1.00–1.02** | 0.9687 | +.0150 | −.0115 | **+.0035** | **+61** |
| 1.02–1.05 | 0.9680 | +.0150 | −.0118 | +.0032 | +57 |
| >1.05 | 0.9697 | +.0143 | −.0113 | +.0030 | +51 |

**An over-par instantaneous pair is not a locked loss.** The median *window*
pair cost is essentially identical across every bin (0.964–0.989), so the
instantaneous pair carries almost no information about the pair the shares end
up in. The best bin in both periods is **1.00–1.02**, and even **>1.05 is
clearly positive**, while **≤0.96 is negative in both periods**.

The mechanism is visible in the split: a cheap instantaneous pair coincides with
the two sides' prints being far apart — the market has moved and the fill is
accumulating the side that is losing. Its matched P&L per share is the highest
of any bin and its residual P&L is catastrophic.

**Two things this does NOT say.** It is attribution, not causation, and it is
no licence to seek over-par completes. And `instantaneous pair at one fill` is
a different object from `window effective pair cost`: the window-level band
analysis in `BTC15M_EXCEPTION.md` — windows attaining an effective pair ≤0.96
earn +600 matched bp/$ OOS — still stands and is not contradicted here.

**Authoritative cash accounting is unchanged.** Costs remain `usdcSize`,
settlement remains the redemption-row rule, and the totals remain the ones
`analysis/l5/btc15m/decompose.py` reports.

## 6. Incentives, kept strictly separate

Account-level, never attributed to a market or an episode — these credits cover
every cohort L5 traded, and the venue publishes no per-market reward row for
another account.

| month | BTC15m matched | BTC15m residual | **BTC15m trading net** | spend | bp/$ | **ACCOUNT incentives** |
|---|---|---|---|---|---|---|
| 2026-04 | +$12,965 | −$8,131 | +$4,834 | $312,606 | 155 | $918 (maker) |
| 2026-05 | +$17,366 | −$15,852 | +$1,514 | $712,236 | 21 | $2,004 (maker) |
| 2026-06 | +$16,437 | −$12,653 | +$3,784 | $579,246 | 65 | $1,834 |
| 2026-07 | +$11,232 | −$4,672 | +$6,560 | $1,001,943 | 66 | $5,773 |
| **2026-08** | **+$104,065** | **−$102,687** | **+$1,378** | **$3,763,586** | **3.7** | **$61,022** (maker $27,861 · taker $4,364 · **REWARD $28,798**) |
| total | | | **+$18,067** | | | **$71,552 = 4.0×** |

**Yes — the oscillator is materially less attractive on trading economics alone
in the incentive-heavy period.** August carried **59% of all BTC15m spend** and
returned **3.7 bp**; matched (+$104,065) and residual (−$102,687) almost exactly
cancelled. It is also the first month with `REWARD` rows at all, and
account-level incentives that month were **44× the BTC15m trading P&L**.

Across the sample, account-level incentives are **4.0× total BTC15m trading
net**. Stated as the constraint it is for us: copying August-era L5 means
copying ~4 bp of trading economics on an account receiving none of that.

## What is not observable

The historical book at any instant; queue position; L5's unfilled, resting or
cancelled orders; maker vs taker per fill; per-market incentive credits. All
named, none imputed. A suppressed fill in this study is a fill *we would not
have taken*, not a fill that would not have occurred.

## Status

BTC15m only. Findings, not proposals. `analysis/l5/sweep/` remains frozen,
Lane B remains unarmed, no risk limit changes, no executor starts.
