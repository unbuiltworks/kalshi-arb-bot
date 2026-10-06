# Preregistration — terminal completion economics

**Fixed before any result was computed.** S3 unchanged and frozen at
`8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`. No
strategy fitting. No P&L-selected threshold. No live orders.

## 0. Hypothesis

A2 of the allocation study strongly refutes per-action balance seeking: at
clip resolution L5 moves *away* from a balanced target more often than a
lighter-side baseline (margins −0.48 / −0.49). The hypothesis here is
different and is **not** a restatement of it:

> Does L5 opportunistically accumulate one outcome when its acquisition
> economics are attractive, tolerate large intermediate imbalance, and
> subsequently acquire the complement such that terminal matched inventory
> approaches equal shares at a bounded cumulative pair cost?

This is an *opportunistic accumulation with deferred completion* hypothesis.
It predicts exactly the A2 result as a consequence rather than a
contradiction: a trader buying the cheap side while it is cheap will
routinely extend imbalance.

## 1. Degeneracy among the candidate objectives, established first

Of the four named terminal descriptions, **two are the same objective.**
Since `matched_fraction = 2·min(w, 1−w)`, which is maximised *exactly* at
`w = 0.5`:

| named candidate | status |
| --- | --- |
| equal shares | **O1** |
| maximum matched fraction | **≡ O1** — same argmax, not a distinct objective |
| pair-cost constraint | **O2** |
| joint matched-fraction + pair-cost | **O3** |

So the model comparison has **three** competitors, and a win by O1 may never
be reported as two independent confirmations. (This is the second study in a
row where named candidates collapsed; the check is now routine.)

- **O1** terminal `w → 0.5`, indifferent to pair cost
- **O2** terminal cumulative pair cost `≤ C`, indifferent to matching
- **O3** maximise matched shares **subject to** pair cost `≤ C`

## 2. Reconstruction

All 12 BTC15m windows. BTC5m excluded (identification-only). At every
inferred clip and at every checkpoint, the full trajectory:
UP shares · DOWN shares · matched · residual · UP VWAP · DOWN VWAP ·
effective cumulative matched pair cost · **marginal complement price to
match the next 20 / 40 / 80 shares** (walking the complement's live ask
ladder) · cumulative dollars invested · locked gross value of matched
inventory · unmatched capital at risk.

Inferred ≤80 clips only, via the frozen break rules. Observed fill quantity
is never treated as a sizing decision.

## 3. Clip classification

Each inferred clip is **EXTENDS** / **COMPRESSES** / **NEUTRAL** by its
effect on `residual = |up − down|`:

- EXTENDS: residual increases by > 1 share
- COMPRESSES: residual decreases by > 1 share
- NEUTRAL: |change| ≤ 1 share

The ±1 share dead-band is fixed now and is not tuned.

## 4. Endpoints and bars, all fixed now

**T1 — EXTEND vs COMPRESS economics.** Cliff's delta on the six named
variables measured immediately *before* each clip: acquisition price,
marginal eventual pair cost, current cumulative pair cost, probability,
inventory imbalance, TTE.
*Bar:* |delta| ≥ 0.33 (medium) on at least one variable, with the sign
reproducing in ≥ 9/12 windows.

**T2 — do EXTEND decisions buy cheap completion?** Matched shares are
attributed to their source clips **FIFO** on each side. Each matched share
therefore has a realized pair cost and an EXTEND/COMPRESS-sourced origin.
*Bar:* matched shares sourced from EXTEND clips carry a realized pair cost
lower than COMPRESS-sourced matched shares by ≥ 0.01, in ≥ 9/12 windows.

**T3 — is there a stable pair-cost ceiling?** **No value is assumed.**
Strict discovery/confirmation separation, following the earlier phases:

- **Discovery window:** `btc-updown-15m-1787300100` (the already-inspected
  one) **only**. The ceiling `C` is estimated as the p95 of realized
  matched pair cost there, and is then **frozen**.
- **Confirmation:** the other **11** windows, never used to choose `C`.
- *Bar:* ≥ 90 % of matched shares fall below the discovery-derived `C` in
  ≥ 9 of 11 confirmation windows.

No threshold anywhere in this study is selected by P&L.

**T4 — which objective?** The discriminating test is the cross-window
relationship between terminal matched fraction and terminal pair cost:

- **O1** predicts no relationship — matching is always maximised.
- **O3** predicts a **negative** relationship — in windows where cheap
  completion was unavailable, L5 should end with *less* matching, having
  declined to complete at a bad price.

*Bar:* Spearman rho ≤ −0.50 across the 12 windows supports **O3** over O1.
O2 is supported if terminal `w` is dispersed (IQR ≥ 0.15) while pair cost is
tight (IQR ≤ 0.05).

**T5 — time-space vs inventory-space.** All 12 windows aligned two ways:
six clock checkpoints (T+30/60/120/300/600/shutdown) and six inventory
checkpoints (**10 / 25 / 50 / 75 / 90 / 100 %** of final acquired
inventory). Reproducibility is the cross-window IQR of matched fraction and
of cumulative pair cost.
*Bar:* median IQR in inventory space < 0.5 × median IQR in time space.

**Caveat fixed in advance, because it limits what T5 can ever mean:**
inventory-space alignment normalises by the window's *final* inventory and
therefore **uses future information**. It is retrospective description of
what L5 optimised, and **cannot be used by any live policy**, which does not
know its own final inventory. A win for inventory-space is a statement about
the controlling variable, not an implementable signal.

## 5. Verdict classes, defined before results

- **OPPORTUNISTIC DEFERRED COMPLETION** — T1 and T2 pass, and T3 shows a
  stable ceiling
- **BOUNDED COMPLETION, NO OPPORTUNISM** — T3 passes but T2 fails
- **OPPORTUNISM WITHOUT A CEILING** — T2 passes but T3 fails
- **REFUTED** — neither T1 nor T2 passes

Deliverable: the **smallest objective function** consistent with L5's
behaviour over the whole window, or an explicit statement that the data do
not identify one. A null result is a complete answer.
