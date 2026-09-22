# P9 — can we tell, before opening, whether the market will give us the other side?

**Date:** 2026-09-22 · **Data:** Gemini public tape, corrected · **Live orders
placed: none. Nothing armed. No rebate term entered model selection.**

Split unchanged: **DEV 09-16..18**, **VALIDATION 09-19..20**. 09-21..22 is
previously exposed and was not read for this round. 09-23+ is sealed.

P8 isolated the failure mode: 53 of 115 BTC5m residual markets never offered a
complement at all, so the first leg bought there was a directional bet nobody
had decided to make. This round asks whether that is foreseeable at the moment
of opening.

**Reproduce:**

```
python3 analysis/gemini/p9a_targets.py    analysis/gemini/p7_bulk_tape.json
python3 analysis/gemini/p9b_predictors.py analysis/gemini/p7_bulk_tape.json
python3 analysis/gemini/p9c_gate.py       analysis/gemini/p7_bulk_tape.json
python3 analysis/gemini/p9d_families.py   <out.json>          # collect
python3 analysis/gemini/p9d_families.py   <out.json> report   # measure
```


> ### Corrections from the P8 audit (2026-09-22), applied after publication
>
> `analysis/gemini/P8_AUDIT.md` re-derived this round's inputs independently.
> Four statements below are corrected and one is withdrawn:
>
> 1. **"a quoted spread of 0.01-0.04" is wrong for the BTC lanes.** That figure
>    came from an all-families snapshot dominated by long-dated markets. The
>    recorded COHORT-2 book cycles put **BTC5m's own median spread at 0.280**
>    (p10 0.11, p90 0.33) and BTC15m's at 0.060. So the achieved pair margins
>    of 0.28-0.36 are *consistent with spread capture* and are **not** evidence
>    of directional selection. **The inference is withdrawn**; the identity
>    (pair margin = the price change between the two fills) still holds.
> 2. **P9-F was only tested up to a 0.20 spread, below the lane's real one.**
>    Re-run at 0.28/0.33/0.40 it stays negative in every cell (BTC5m DEV
>    -$0.047/quote at 0.28, VAL -$0.127), so the conclusion survives, but the
>    gap was real.
> 3. **"never offered a complement" is the wrong word.** Both sides were quoted
>    in **217 of 217** readable BTC5m cycles. What 53/115 markets lacked was a
>    complement *print* -- a maker-fill chance at our price. Completing as a
>    taker was always possible and, at a 0.28 spread, uneconomic.
> 4. **Every capture < 1.00 figure is one arbitrary sample.** `capture` is
>    implemented as "take every Nth print", which is phase-locked. Bernoulli
>    draws move BTC5m DEV @0.50 from -0.72 to a mean of +4.41. The capture-1.00
>    column is the only thinning-free comparison.
> 5. **The mirrored-twin deduplication is suspended** -- 1,474 of 1,476 pairs
>    are same-side and every re-pulled pair has distinct `tid`s. Base rates move
>    by at most 2.5pp without it (BTC5m DEV 44.9% -> 42.4%) and P9-F stays
>    negative, so the structural findings hold; P&L figures computed on the
>    deduped tape are provisional.

---

## Answer first

**Completion availability is predictable — and gating on it still does not
produce a positive pre-rebate validation result.**

* The signal is real: AUC **0.744 dev / 0.689 val** on BTC5m, and it survives
  removing the clock entirely (0.715 / 0.688 without `tte` or `elapsed`).
* The gate works beautifully on the data it was built on — BTC5m DEV net
  +$7.62 → **+$8.87**, ROI 8.1% → **13.8%**, residual 213 → 127 units, maxDD
  7.54 → 3.92, worst day +$2.07.
* On validation the same frozen gate returns **−$5.21**, and the reason is
  legible: the model's *ranking* transfers, its *calibration* does not.

So the answer to "can we identify markets likely to provide a fillable
opposite side?" is **partly yes, and not yet reliably enough to trade on.**

But the round turned up something that matters more than the gate, and it
changes what the matched leg of the strategy has been all along.

---

## The finding that reframes the programme: matched P&L is a price move, not a spread

On a mirrored one-ladder venue there is an exact identity. A YES bought when
the tape prints `p1`, paired with a NO bought when the tape prints `p2`, costs

```
    p1 + (1 - p2)  =  1 - (p2 - p1)
```

**The pair's margin IS the change in traded price between the two fills.**
There is no arbitrage term and no spread term: the spread only enters if both
legs are filled from quotes we were resting *simultaneously*, in which case
`p2 - p1` is the spread itself.

Measured on the P8-A frozen candidate's own completed pairs:

| lane | split | pairs | median margin | mean | p10 | p90 | positive | venue's quoted spread |
|---|---|---|---|---|---|---|---|---|
| 5m | dev | 48 | **0.3600** | 0.3505 | 0.1400 | 0.5625 | **100%** | 0.01–0.04 |
| 5m | val | 20 | 0.2800 | 0.3450 | 0.1383 | 0.6500 | 100% | 0.01–0.04 |
| 15m | dev | 38 | 0.2750 | 0.3260 | 0.1350 | 0.5467 | 100% | 0.01–0.04 |
| 15m | val | 31 | 0.2817 | 0.3088 | 0.1100 | 0.4820 | 100% | 0.01–0.04 |

(Quoted spread is measured live across 1,780 quoted Gemini contracts in every
family: median 0.01 to 0.04 depending on family. See P9-D.)

**A median margin of 0.28–0.36 against a quoted spread of 0.02 is not spread
capture — it is ten times the spread, and it is positive 100% of the time.**
That is the signature of a selection rule, not of an edge: a completion ceiling
of 0.92 *defines* a margin of at least 0.08, so the policy books a pair only
when the price has already moved at least 8 cents its way, and carries the
cases where it moved the other way to settlement as residual.

Matched P&L and residual P&L are therefore not two independent lines. **They
are the two halves of the same coin flip**, split by a threshold. That is why
matched is always positive, residual is always negative, the net is near zero,
and the sign will not hold still between DEV and validation.

It also prices the honest version of the strategy. Earning the *spread* means
resting on both sides at once and being hit on both; the margin is then the
quoted spread, **1–4%**, not 30%. At BTC5m's 48 DEV pairs that is about $0.96
of repeatable matched value against $18.78 of residual loss. A two-cent spread
cannot pay for a habitat that completes 44.9% of the time.

---

## P9-A — the target, and the base rates that make it urgent

`src/gemini_execution/completion_labels.py` treats **every print** as a
candidate first leg — not only the ones some policy chose to open, which is a
population already selected by that policy. For each candidate it records what
the tape did next, and never what the market settled at.

The causal seam is the module's whole point: features are built from
`prints[0..i]` and labels from `prints[i+1..]`, by two functions that cannot
see each other's inputs. 12 tests assert it, including that changing a later
print cannot change an earlier feature and that no label key derives from
settlement.

| lane | split | opens | complement ever | **pairs ≤1.00** | ≤0.98 | ≤0.95 | ≥3 chances @1.00 | median latency |
|---|---|---|---|---|---|---|---|---|
| 5m | dev | 1,686 | 59.9% | **44.9%** | 42.4% | 31.6% | **13.3%** | 26s |
| 5m | val | 911 | 53.8% | **35.9%** | 33.2% | 27.4% | 12.1% | 35s |
| 15m | dev | 2,565 | 71.4% | 46.3% | 40.5% | 35.6% | 17.8% | 102s |
| 15m | val | 1,999 | 76.2% | 46.3% | 41.5% | 37.1% | 17.8% | 111s |

**Fewer than half of all candidate opens ever see a pairable complement**, and
the share that sees three or more chances — what a capture of 1/3 needs to
expect one fill — is **13.3%**. That is the size of the problem, stated before
any policy is involved.

It is also strongly price-dependent, and worst at both ends:

| BTC5m DEV, first-leg price | opens | complement ever | pairs ≤1.00 |
|---|---|---|---|
| <0.20 | 165 | 34.5% | **27.3%** |
| 0.20–0.35 | 209 | 58.4% | 46.4% |
| 0.35–0.50 | 438 | 71.2% | **56.2%** |
| 0.50–0.65 | 494 | 66.2% | 52.4% |
| 0.65–0.80 | 179 | 59.8% | 34.6% |
| >0.80 | 201 | 42.3% | **23.9%** |

This is the P8 residual finding seen from the other end: the cheap legs that
lost 34–59% of cost are the legs least likely to have been pairable in the
first place.

---

## P9-B — what predicts it

Univariate first, then one small logistic on standardized inputs so the
coefficients can be read. Everything fitted on DEV; validation scored once.

**BTC5m DEV, target "a complement that pairs at ≤1.00 appears":**

| feature | AUC (dev) | AUC (val) | quintile completion rate, low → high |
|---|---|---|---|
| `tte_s` | **0.724** | 0.666 | 25.1% · 28.4% · 40.2% · 50.2% · **72.9%** |
| `elapsed_s` | 0.301 | 0.389 | **81.5%** · 44.0% · 44.9% · 32.1% · 24.8% |
| `trades_per_min` | 0.646 | 0.566 | 29.4% · 34.9% · 40.4% · 36.9% · 68.1% |
| `dist_50` | 0.325 | 0.364 | 68.8% · 57.3% · 38.0% · 38.1% · 25.0% |
| `n_prints` so far | 0.333 | 0.364 | 84.8% · 47.8% · 39.9% · 37.6% · 30.6% |
| `abs_move_all` | 0.318 | 0.370 | 78.8% · 43.6% · 35.6% · 41.8% · 26.7% |

(An AUC below 0.5 means the feature predicts *negatively* and is exactly as
usable as one above.)

**Is it just the clock?** Partly, and not only. Time remaining mechanically
bounds how many chances are left, so `tte_s` leading is unsurprising. But:

* a model with **`tte_s` and `elapsed_s` removed** scores **0.715 dev / 0.688
  val** on BTC5m — as good out of sample as the full model;
* within `tte` quartiles the same model keeps AUC 0.55–0.79 on validation.

So market state carries information about completion beyond time remaining.

| model, BTC5m | AUC dev | AUC val |
|---|---|---|
| full logistic (17 features) | 0.744 | **0.689** |
| without `tte_s` / `elapsed_s` | 0.715 | 0.688 |
| `tte_s` alone | 0.724 | 0.666 |
| 4 features (`tte`, `dist_50`, `trades/min`, `$/min`) | 0.728 | 0.678 |
| BTC15m full logistic | 0.671 | 0.636 |

The harder target — **≥3 chances**, the one a realistic capture needs — is
predicted much worse: 0.705 dev / **0.576 val** on BTC5m.

---

## P9-C — the gate, and why it fails out of sample

Four arms with the **completion rule held constant** at the P8-A ceiling
(0.92), so the only thing varying is what we agree to open. The gate's model is
DEV-fitted and frozen; its threshold is chosen on DEV; validation is scored
once.

**BTC5m, capture 1.00:**

| arm | DEV mkts | DEV net | DEV ROI | DEV residQ | **VAL mkts** | **VAL net** | VAL ROI |
|---|---|---|---|---|---|---|---|
| 1 trade all | 215 | −3.67 | −1.7% | 348 | 101 | **+3.44** | +4.1% |
| 2 pair-cost only | 132 | +7.62 | 8.1% | 213 | 68 | −2.13 | −3.9% |
| 3 opportunity gate only | 205 | +1.62 | 0.9% | 262 | 48 | −3.07 | −9.3% |
| 4 pair-cost + gate | 90 | **+8.87** | **13.8%** | 127 | 21 | **−5.21** | −51.0% |

**BTC15m, capture 1.00:** arm 4 is DEV +4.55 and VAL −0.07 — the only arm that
is not deeply negative on validation at any capture, but it is not positive.

**The gate's cost, priced honestly.** On DEV it refuses 42 markets that arm 2
traded, worth **−$4.34** under arm 2, of which **12 were profitable**; it keeps
90 worth +$11.96. So on DEV the gate is discarding a net loser. On validation
that stops being true.

**Why it breaks.** Not ranking — AUC only falls 0.744 → 0.689. **Calibration.**
The validation base rate is 35.9% against DEV's 44.9%, so a fixed *probability*
threshold selects a much smaller and different slice: arm 3 goes from 205
markets on DEV to 48 on validation, and arm 4 to 21, whose realized completion
rate is **0.10** — worse than the ungated 0.30. A gate on a drifting
probability inherits the drift.

**A calibration-free version transfers better on the thing it targets.** A
clock gate ("open only while at least T seconds remain") raises validation
completion from 0.30 to 0.37 on BTC5m and 0.33 to 0.36 on BTC15m. But the
DEV-chosen threshold (60s) still loses on validation (−$5.34), and the
threshold that would have worked (120s: +$0.86) was chosen with hindsight and
is reported as hindsight, not as a result.

---

## P9-E — the q_hat defect, corrected as a separate version

`ev_policy_v2` is V1's decision with the orientation fixed and **nothing else**:
the residual table, `p_win`, `MarketState`, `complete_value` and
`acquire_value` are V1's own objects, asserted by test, so the two policies can
differ only in which side the complement is looked up on. V1 is untouched, so
every P7/P8 number remains reproducible. `complement_of` is a named function
purely so the orientation is testable on its own.

Run as a comparator, never as a continuation of tuning:

| lane | policy | dev @1.00 | val @1.00 | dev completion | val completion |
|---|---|---|---|---|---|
| 5m | **ev (V1, defective)** | **+17.24** | +0.67 | 0.60 | 0.56 |
| 5m | ev2 (corrected) | **−7.87** | −10.08 | 0.30 | 0.25 |
| 15m | ev (V1, defective) | +3.94 | +6.89 | 0.69 | 0.71 |
| 15m | ev2 (corrected) | **−32.88** | −36.91 | 0.18 | 0.22 |

**The corrected version is far worse, and that is the finding.** Because the
complement's mean price on a mirrored one-ladder venue is close to `1 − p`,
V2's projected pair sits near 1.00 almost everywhere and the acquisition test
stops binding. V1's projected pair was `p + mean(p)` ≈ `2p`, so its threshold
acted as **a cap on the leg price** — roughly "never pay more than half the
ceiling". That cap is where P7/P8's EV results came from. The economically
coherent test has no edge at all.

This closes the question the owner raised: P7/P8's EV policy was never evidence
of a predictive model. It was a price cap with a probability model bolted on
that moved ~10% of decisions.

---

## P9-F — the decisive test: quote both sides and see who hits you

The identity above says the only repeatable version of this trade is to rest a
bid and an ask **at the same time** and be hit on both, earning the spread with
no view on direction. So that is measured directly: quote a YES bid at
`m − s/2` and a YES ask at `m + s/2` around the last traded price, leave both
resting, and let the rest of the tape decide.

* both hit → a matched pair at cost `1 − s`, margin `s`, outcome-free
* one hit → an unpaired leg carried to settlement and **paid in full**
* neither → nothing

**One quote per contract, placed at its first print:**

| lane | split | capture | spread | quotes | both | one leg | pair $ | orphan $ | **NET $** | per quote |
|---|---|---|---|---|---|---|---|---|---|---|
| 5m | dev | 1.00 | 0.02 | 245 | 98 | 138 | +1.96 | −25.06 | **−23.10** | −0.094 |
| 5m | dev | 1.00 | 0.20 | 244 | 33 | 123 | +6.60 | −19.64 | **−13.04** | −0.053 |
| 5m | val | 1.00 | 0.02 | 150 | 36 | 91 | +0.72 | −20.50 | **−19.78** | −0.132 |
| 15m | dev | 1.00 | 0.02 | 220 | 76 | 122 | +1.52 | −26.97 | **−25.45** | −0.116 |
| 15m | val | 1.00 | 0.20 | 176 | 28 | 111 | +5.60 | −21.14 | **−15.54** | −0.088 |

**Quoting at every print instant instead — an upper bound on a maker that
re-quotes continuously:**

| lane | split | capture | spread | quotes | both | one leg | pair $ | orphan $ | **NET per quote** |
|---|---|---|---|---|---|---|---|---|---|
| 5m | dev | 1.00 | 0.02 | 1,413 | 23.7% | 54.9% | +6.70 | −155.19 | **−0.105** |
| 5m | dev | 1.00 | 0.20 | 1,322 | 11.5% | 51.9% | +30.40 | −110.57 | **−0.061** |
| 5m | val | 1.00 | 0.02 | 701 | 18.7% | 59.3% | +2.62 | −134.58 | **−0.188** |
| 15m | dev | 1.00 | 0.02 | 2,327 | 26.3% | 54.4% | +12.24 | −321.76 | **−0.133** |
| 15m | val | 1.00 | 0.20 | 1,721 | 9.6% | 57.1% | +33.20 | −209.97 | **−0.103** |

**Every cell is negative** — both lanes, both splits, captures 1.00 and 0.33,
spreads from 2 to 20 cents, one quote or continuous re-quoting. Orphan losses
run **10× to 30×** the pair income, because one-sided fills are adversely
selected by construction: the side that gets hit is the side the price is
moving toward.

Two things this is *not*. It is not a pessimistic treatment of the orphan: it
holds the unpaired leg to settlement, which P8-B measured as **better** than
completing it at the prices complements are actually offered. And it is not a
statement about every possible maker — there is no inventory management, no
skewing, no cancel-on-adverse-move. But the gap is an order of magnitude, not a
few percent.

**Pre-rebate, Gemini BTC5m and BTC15m do not support two-sided quoting.** That
is a venue property, measured in the venue's own tape, and no threshold in any
of our policies changes it.

---

## Held, unchanged

No live orders. Nothing armed. `bundle_sha` unchanged. No rebate term entered
model selection. No Gemini probability refit. 09-23+ collector untouched and
unread — it fires at 01:20Z daily and its `LAST_EXPOSED` guard still refuses
anything at or before 09-22.
