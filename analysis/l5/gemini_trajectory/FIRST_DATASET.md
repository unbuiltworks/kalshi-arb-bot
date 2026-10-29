# Gemini 5m: first canonical trajectory dataset, and the answer it gives

Read-only replay. No strategy change, no Gemini deployment, no capital.
**The L5 model was NOT modified to fit Gemini** — the replay loads the same
`pv2.load_v2()` model and the same `L5Inventory` the PMUS side uses.

---

## THE QUESTION

> Does Gemini 5m actually allow the unchanged canonical L5 engine to cycle
> inventory the way L5 does?

## **ON THIS EVIDENCE: NO.**

120 windows, **29 campaigns**, days 260830-260831, BTC only, across all four
policy x fill-mode combinations:

| policy:mode | camps | orders | 1st-leg fills | complement orders | complement fills | **fully matched** | touch_moved cancels |
|---|---|---|---|---|---|---|---|
| modal:optimistic | 29 | 145 | 11 | 0 | 0 | **0** | 129 |
| modal:strict | 29 | 141 | 2 | 0 | 0 | **0** | 132 |
| stochastic:optimistic | 29 | 139 | 10 | 12 | 1 | **1** | 113 |
| stochastic:strict | 29 | 139 | 2 | 1 | 1 | **1** | 129 |

**The best any configuration achieves is ONE fully-matched campaign out of 29.**
Complement fills: 0, 0, 1, 1. First-leg fill rate under the honest `strict`
counterfactual: **1.4% per order** (2 of 141).

`strict` is the mode to quote: it does not manufacture a fill because the
market later touched a price. `optimistic` is reported beside it precisely so
the gap between them is visible rather than chosen between.

---

## THIS INVERTS THE WORKING HYPOTHESIS

The habitat work established that Gemini 5m is a **materially better habitat**
than PMUS sports — p50 0.3944 against 0.108, its worst quartile above PMUS's
best market, at full data coverage. The obvious inference was that a better
habitat would let the engine cycle inventory.

**It does not.** Against the owner's own taxonomy:

> good habitat + poor capture = **EXECUTION PROBLEM**

Gemini 5m lands squarely in that box. And the mechanism is not new:
**113-132 of ~140 orders die as `touch_moved` cancels** — the identical churn
the PMUS corpus showed (47.4% of sessions ceiling-pinned below touch), and the
identical mechanism the nicpar-gabpen forensics found killing two COMPLETEs at
1.2 s and 1.8 s.

**That is now three independent confirmations of the same execution failure —
PMUS corpus, PMUS single-market forensics, and a different venue entirely.**
The churn transfers across venues even though the habitat does not. The
binding constraint is microstructure capture, not habitat quality.

---

## AGAINST THE PRE-REGISTERED FLOOR

| requirement | floor | observed | |
|---|---|---|---|
| matched campaigns | **>= 30** | **1** | NOT MET |
| complement fills | **>= 30** | **1** | NOT MET |

**No distributional verdict on Gemini's inventory trajectory may be claimed,
and none is claimed here.** The floor is not close: it is short by a factor of
thirty on both axes. This dataset establishes that the engine does not cycle
inventory on Gemini 5m as configured; it does not establish what Gemini's
inventory trajectory looks like, because there is almost no trajectory to
measure.

---

## WHAT THIS DATASET DOES AND DOES NOT CONTAIN

Present, from the canonical replay: per-window decisions with the model's
OPEN / EXTEND / COMPLETE / HOLD relation, refusal reasons, orders with rest
and cancel cause, fills, `L5Inventory` snapshot (up/dn/matched/residual and
running average costs), `pair_unit_cost`, `pair_cost_after_fees`, theoretical
fees, window flow, TTE and the outcome proxy. Observed market state, shadow
model action and counterfactual fill are separate fields and are never merged.

**UNKNOWN and not inferred:** queue position (nothing in a public tape
establishes it), and any fill not supported by the strict counterfactual.

**Missing for the full comparison the owner asked for:**

- **`gain_per_unit` distribution, exactly-zero-gain frequency, attainable
  pair-cost distribution and matched/residual evolution are all UNMEASURABLE
  HERE** — they are properties of a position, and with 1 matched campaign of
  29 there is no position series to take a distribution over.
  `median_pair_cost_after_fees` comes back `null` for exactly this reason.
- **Asset segmentation is not possible from this tape.** `gemini_bbo_btc05m`
  is BTC only. ETH 5m rows exist in the `data/policy_d_port` collector tapes
  (`GEMI-ETH05M...`) and would need extracting into the same schema first.
- **Time-of-day segmentation is not supportable at n=29 campaigns over 2
  days.** Splitting that sample would produce cells of single digits.

---

## SECONDS BETWEEN ACTIONS, AGAINST THE CANONICAL REFERENCES

L5's canonical references, unchanged and not re-tuned: **~2 s median between
consecutive fills**, **~171 s median TTE at fills**, order-episode gaps p50
3 s (extend) / 4 s (complete).

Gemini 5m under the unchanged engine produces **6 decisions per campaign
(p50)** across a 300 s window. That is roughly one action per 50 s against
L5's 2-4 s episode cadence — **an order of magnitude slower**, and slower for
the same reason PMUS was: the engine is not being filled, so it is not
progressing through states.

**The habitat score said the MARKET moves like L5's. The trajectory replay
says OUR ENGINE does not move through it like L5 does.** Those are different
claims and this dataset is the first to separate them.
