# STATE_CONDITIONAL_PAIR_V1 — DEVELOPMENT result and FROZEN specification

Preregistered at **`c0ed7c0d`** before the state table existed. Venue semantics
at `e54c5198`. Code: `scripts/pmus_state_table.py`,
`scripts/pmus_state_conditional_v1.py`, `src/pmus_brti.py`. Data:
`analysis/l5/state_conditional/`.

**OOS has not been retrieved or inspected.** Everything below is DEVELOPMENT
(28 windows, 24,508 decision-seconds, 885 per window).

## Causality

All four preregistered rules enforced in code: a 1-second BRTI bucket is used
only when its start `<= t-1`; `priceToBeat` is withheld until `ws+15 s`; book
state is the latest update at or before `t`; the label is attached only after
every feature is computed and is asserted absent from the feature list.
Coinbase enters nothing. Windows chain as a free check — each window's
`settlementPrice` equals the next window's `priceToBeat` throughout.

## 1. The probability model has real skill

Logistic, three frozen features, **out-of-fold** predictions everywhere.

| | Brier | vs base rate | log loss |
|---|---|---|---|
| **leave-one-window-out** | **0.174742** | 0.249969 | 0.513754 |
| chronological (fit 09-30, test 10-02) | 0.201354 | 0.247934 | 0.684937 |

LOWO Brier is **30.1% below** the base-rate benchmark. Base rate 0.4945 of
rows, 0.50 of windows — the sample is balanced, so this is not a prior effect.
Reliability is reasonable: predicted 0.025 -> observed 0.012; 0.25 -> 0.27;
0.45 -> 0.51; 0.55 -> 0.55; 0.75 -> 0.75. The weakest bin is 0.1-0.2
(predicted 0.152, observed 0.272, n=1,944).

The chronological split is materially worse, but it trains on only the five
09-30 windows (5,038 rows) to predict 19,470 — it is a weak-training test, not
evidence of regime change.

`z` carries essentially all the signal (standardized coefficient ~108, stable
across folds at 97.5-129.7). `locked_frac` (−0.09 to 0.35) and `mom_60s`
(0.019 to 0.096) are small and the first changes sign across folds.

**Frozen coefficients** (full-sample, standardized):
intercept **1.057243**, `z` **108.293112**, `locked_frac` **0.241926**,
`mom_60s` **0.078187**; standardization mu `[1.083422, 0.033703, 0.098486]`,
sd `[84.648565, 0.145419, 8.580793]`.

## 2. The economics are breakeven — fees eat the entire gross

Unit size (1 share, depth-capped), 28/28 windows traded, 520 purchases
(18.57 per window).

| term | value |
|---|---|
| purchase cash | $245.7360 |
| settlement cash | $251.0600 |
| **gross before fees** | **+$5.3240** |
| fees, venue-cent | **$5.6500** |
| fees, modeled 4-dp | $5.3234 |
| **net, venue-cent** | **−$0.3260** |
| net, modeled 4-dp | +$0.0006 |
| net per traded window | −$0.0116 |
| median net | −$0.0600 |
| positive % | 50.0% |
| worst window | −$9.68 (10-02 1915z) |

Gross is positive and the strategy is, to three decimals, **exactly
breakeven**: $5.32 of gross against $5.32–$5.65 of fees.

## 3. Where the gross comes from — not from pairs

Exact reconciliation, residual **0.000000**:

```
matched-book economics (average cost)  +$0.0469
directional residual P&L               +$5.2771
                                       = gross +$5.3240
```

**99.1% of the gross is directional**, from the probability model. The matched
book contributes **+$0.047 across 28 windows** — indistinguishable from zero.

The first-leg/complement pair taken alone is **negative**: mean all-in pair
cost **$1.1357**, locked pair gain **−$3.80**, with only **11 of 28** pairs
under $1.00 (best $0.67).

So the complement, when it arrives, is on average acquired *above* $1 all-in
and destroys value rather than locking it. Reported completion rate of 1.0
(28/28, median 469 s, range 57–861 s) is therefore **not good news** and must
not be read as one: it reflects a permissive EV gate buying both sides across a
15-minute window, not a cheap-complement opportunity. Zero first legs went
uncomplemented, so items 7 and 8 are n=0 and $0.00 — a fact about this
controller's permissiveness, not evidence that unpaired risk is absent.

EXTENDs: 464 purchases, $214.77 cash, $4.82 fees, mean predicted EV $0.0558.

## 4. The sub-$1 pair state essentially does not exist

The question asked directly, over all 24,508 decision-seconds:

| | |
|---|---|
| rows where `up + down + both cent fees < $1.00` | **3 of 24,508** |
| share of all rows | **0.0122%** |
| share of rows after a qualifying first leg | 0.0123% (3 of 24,480) |
| distinct occurrences | **1 run, 3 seconds long** |
| best all-in pair cost observed | **$0.99** (1 cent of gross) |
| size available at that state | 512.99 shares on the thinner side |

**The 30c + 25c illustration does not occur in this data.** The one qualifying
state in over six hours of 1-second observation was worth one cent per pair and
lasted three seconds. Size was ample — the constraint is frequency and
duration, not depth. Any strategy premised on acquiring complements
substantially below $1 is premised on a state this sample does not contain.

## 5. First-leg edge prediction is NOT validated

| predicted edge | n | mean predicted EV | mean realized P&L | win rate |
|---|---|---|---|---|
| [0.01, 0.02) | 1 | $0.0140 | +$0.4400 | 1.00 |
| [0.02, 0.05) | 9 | $0.0333 | +$0.1422 | 0.67 |
| **[0.05, 1.00)** | **18** | **$0.0771** | **−$0.1278** | **0.39** |

Total first-leg predicted EV **+$1.7018** against realized **−$0.5800**. The
relationship is **inverted**: the highest-predicted-edge bucket did worst.

With n=28 first legs this is noise-dominated and I will not over-read it. But
it is the single most important thing to watch on OOS, and it is a reason to
expect the OOS economics to be no better than breakeven. Mean `q` at entry is
0.4185 and mean seconds-remaining at entry is 875.3 of 900 — i.e. the
controller almost always fires at its first legal opportunity, on the cheap
side. **The EV gate is permissive, not selective.**

## 6. Verdict against the preregistered success criterion

```
completed-pair gains + directional gains - unpaired losses - fees > 0
```

On DEVELOPMENT: `+$0.047 + $5.277 - $0.000 - $5.650 = -$0.326`. **Fails, but
only just**, and it fails for a different reason than Cell C did.

This is a materially better position than Cell C despite a similar net. Cell C
had *negative raw edge* (paying $386.67 for inventory settling at $359.61).
This controller has **positive gross from a calibrated probability model** and
loses it entirely to fees. The problem has moved from "no edge" to "edge
approximately equal to transaction cost".

What it does **not** establish: that the pair/complement architecture adds
anything. On this sample it adds $0.047 of $5.324. The thing that works here is
the state-conditional directional model, not the matched-inventory structure.
Sizing cannot fix a breakeven per-share result — and sizing was deliberately
not used to manufacture one.

## 7. FROZEN — specification closed before OOS

Frozen as of this commit and unchanged for the OOS evaluation:

- **Features**: `z`, `locked_frac`, `mom_60s` (definitions in
  `scripts/pmus_state_table.py`), standardized by the development mu/sd above.
- **Model**: logistic, ridge 1e-6, Newton; coefficients above.
- **EV**: `EV_UP = q − up_ask − fee_cent(up_ask, size)/size`;
  `EV_DOWN = (1−q) − (1−bid) − fee_cent(1−bid, size)/size`; `EV_WAIT = 0`.
- **Controller**: BUY UP / BUY DOWN / WAIT / SETTLE, no SELL; first leg and
  every later leg require independently `EV > 0`; one purchase per side per
  60 s period; unit size capped by displayed depth; nothing before `ws+15 s`.
- **Fees**: venue-cent banker-rounded for all cashflow; 4-dp reported beside.

OOS will be evaluated **exactly once**, with the development-fitted
coefficients applied unchanged (not refitted). No tuning. No live orders. No
maker assumptions.

Caveat carried forward: the **Sep 17 vs Oct 1 taker-coefficient effective date
remains unresolved**, and every development window lies in the disputed range.
At 4-dp fees this result is +$0.0006; at venue-cent fees −$0.3260. A fee-date
correction is therefore large relative to the entire result, which is itself
the finding — this strategy's P&L is a fee-rounding artifact at unit size.
