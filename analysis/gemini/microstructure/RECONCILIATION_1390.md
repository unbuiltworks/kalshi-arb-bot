# Reconciling the historical 300 / 1,390 defensible fills against the 0-of-92 result

**Neither cohort is averaged into the other and neither replaces the other.**
This document explains the mechanism of the difference and corrects an error
in my own earlier model.

---

## 1. THE RULE THAT PRODUCED 1,390 — FOUND

`analysis/l5/gemini_trajectory/continuous_inventory.py` (on this branch,
lines 36–60 and 181–236) defines three fill tiers. Verbatim:

> **STRICT** — a print strictly THROUGH our resting price. The book traded
> past our level, so we were reached whatever our queue position. *The only
> tier that needs no queue assumption.*
>
> **SUPPORTED** — a print AT our price, where the **cumulative** size traded
> at or through our price **since we joined** EXCEEDS the size displayed at
> that touch when we joined.
>
> **OPTIMISTIC** — any print AT our price. Assumes front of queue; an
> explicit UPPER BOUND.

All three require an actual print. `touch = fill` is excluded in every tier.
That model is sound, and on the through-price point it is **more** defensible
than the one I shipped.

## 2. MY MODEL WAS WRONG, AND THIS IS THE CORRECTION

`p1_queue_shadow.py` contained:

```python
if price != order["price"]:
    continue
```

This required a print's price to **equal** our resting price and therefore
**discarded every print that traded through our level.** That is not
conservatism — it is a defect. A print strictly through a resting order means
the book traded past it, so the order was reached *regardless of queue
position*. It is the one fill class needing no queue assumption at all.

From my own P0c classification, the discarded prints were **41 of 241**
(`BELOW_BID` 29, `ABOVE_ASK` 12) carrying **8,935 of 34,071 contracts
(26.2%)**.

Second difference: `SUPPORTED` accumulates traded size **cumulatively across
prints** since joining, whereas my model compared each print's qty against
the queue **individually**. Several prints that each fall short of the
displayed queue can jointly exhaust it. My per-print test can never see that.

**Both differences push in the same direction: my model under-counted
defensible fills. The "0 of 92" statement is still literally true — it was a
statement about *at-touch* prints only — but my headline "capture ≈ 0" did
not follow from it, and I withdraw that headline.**

## 3. THE CORRECTED MEASUREMENT ON THE SAME LEGACY COHORT

`p1b_tiered_capture.py`, 57 BTC5m windows, 241 prints / 34,071.18 contracts.
`DEFENSIBLE` splits its two components so the queue-free part is never hidden
inside the other.

| tier / clip | fills | contracts | % of flow | of which *through* (queue-free) | of which *depletion* | windows with any fill |
|---|---|---|---|---|---|---|
| DEFENSIBLE q1 | 26 | 26.00 | 0.08% | 23.00 | 3.00 | 15/57 |
| DEFENSIBLE q5 | 26 | 126.00 | 0.37% | 111.00 | 15.00 | 15/57 |
| DEFENSIBLE q10 | 28 | 251.00 | 0.74% | 221.00 | 30.00 | 15/57 |
| DEFENSIBLE q25 | 28 | 614.00 | 1.80% | 539.00 | 75.00 | 15/57 |
| PLAUSIBLE (pro-rata) q1 | 121 | 68.46 | 0.20% | 20.77 | 0.01 | 41/57 |
| PLAUSIBLE q25 | 123 | 1,466.41 | 4.30% | 493.32 | 5.01 | 41/57 |
| *ZERO_QUEUE* q1 | 112 | 110.22 | 0.32% | — | — | 41/57 |
| *ZERO_QUEUE* q25 | 113 | 2,071.01 | 6.08% | — | — | 41/57 |

**Defensible capture on the legacy cohort is small but NOT zero**, and it is
**dominated by through-prints** (23 of 26 contracts at q1) — the component
that needs no queue assumption whatsoever. `queue_ahead` at placement was
p50 100, p75 258, p90 454, max 15,000 contracts.

`PLAUSIBLE` is pro-rata allocation, `qty × clip/(queue_ahead+clip)`. A first
version defined it as "at-price once the level traded at all", which made it
mathematically identical to `ZERO_QUEUE` and therefore worthless as a
separate bound. Pro-rata is a **structural** allocation assumption — there is
no threshold in it to fit.

## 4. WHY 300 / 1,390 IS STILL NOT REPRODUCIBLE HERE

The tier rule is identified, but the *run* is not recoverable:

- `SECTION_D_E.md` records that the high-fidelity (1 Hz, obs/min 50–57)
  canonical rows were written to the **Railway volume `/state/gemini-5m`**,
  which *"only the collector container can read"* — deployment `9f11d488`
  serving `6a551dc9`, service `gemini-5m-collector`.
- `deploy/gemini_5m_collector/railway.json` confirms
  `--out-dir /state/gemini-5m`.
- **That service no longer exists.** No project in the workspace is named for
  it, and `crypto-basis-autonomous`, `kalshi-nested-arb-autonomous` and
  `sports-arb-autonomous` contain no such service. The repo's own
  `chore/l5-volume-export-workflow` commits are prior attempts to get this
  data out of the volume.

So the 300/1,390 cohort rests on data that was **never in git** and whose
host appears torn down. **I did not locate it and I am not claiming it is
permanently gone** — see unresolved uncertainties.

**Consequence:** the figure cannot be re-derived, re-audited, or compared
window-for-window. It is preserved as a prior result under its own queue
rule, not merged with anything here. Its ~9–11% "defensibly captured passive
exit rate" is of the same order as this cohort's `PLAUSIBLE`/`ZERO_QUEUE`
bounds at larger clips, which is consistent with it having been computed
under an at-price tier rather than a through-price-plus-FIFO tier — but that
is an inference, not a reproduction.

## 5. WHAT THIS CHANGES ABOUT THE VERDICT

| | before | after correction |
|---|---|---|
| defensible fills, legacy cohort | 0 (wrong) | **26–28** (15/57 windows) |
| defensible share of flow | 0.00% | **0.08%–1.80%** (q1→q25) |
| dominant fill mechanism | n/a | **through-prints**, needing no queue assumption |
| "0 of 92 at-touch prints cleared the queue" | correct | **still correct and unchanged** |
| conclusion on the legacy cohort | "capture ≈ 0" | **small but real; too small to be an economic case, and measured at 5 s** |

The direction of the verdict does not change on the legacy cohort. **Its
basis does**, and the earlier number was wrong. The decisive point stands
independently: this is all measured at ~5 s sampling, which is why the fresh
high-fidelity cohort is being collected rather than the lane being closed.
