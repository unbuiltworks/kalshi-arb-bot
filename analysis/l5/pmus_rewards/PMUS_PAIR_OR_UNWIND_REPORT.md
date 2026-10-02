# PMUS PAIR_OR_UNWIND shadow — resolves inventory, economics indistinguishable from zero

2026-10-02. No live orders. Nothing deployed, restarted or re-capped. The
pending Railway `EnvironmentPatch` was not applied. No parameter fitted;
ceilings are the pre-registered .99/.98/.97/.95.

## Provenance

`data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl(.gz)`, harvested from the
existing `pmus-ws-evidence-recorder` (project `arb-ev-wallet-shadow` /
`production`). 8 BTC15m windows: seven ~900 s windows on 2026-09-30
(1215z–1345z) and one 50 s partial on 2026-10-02 (0400z).

**11,624 SCORABLE trades used.** Excluded and held UNKNOWN, never filling
anything: `stale_book` 4,341, `no_prior_book` 402, `unknown_dir` 253.

## Primary question

> Out of 100 defensible first-leg fills, how many obtain either a qualifying
> pair or an economically acceptable unwind before the risk deadline?

| ceiling | qualifying exits captured | unresolved |
|---|---|---|
| **.99** | **88** | **2.2%** |
| .98 | 85 | 2.1% |
| .97 | 80 | 2.6% |
| .95 | 67 | 3.6% |

**67–89 of 100, with only 2–4% unresolved.** Against Gemini's 9–11% captured
and 53% unresolved, this is a decisive structural improvement: on PMUS,
PAIR_OR_UNWIND actually resolves inventory.

The mechanism is the **immediately executable** path, which the Gemini run did
not have. At q25/.99, 440 of 482 captured exits were `PAIR_TAKER` — displayed
depth already gave profitable economics after fees — and only 42 were passive
`PAIR_MAKER`. Waiting for a passive complement was the Gemini bottleneck; here
it is mostly unnecessary.

## Execution-path breakdown (q25)

| config | first legs | qual. seen | captured | PAIR_TAKER | PAIR_MAKER | UNWIND_MAKER | FORCED | unresolved |
|---|---|---|---|---|---|---|---|---|
| c.99 | 547 | 457 | 482 | **440** | 42 | 0 | 53 | 2.2% |
| c.98 | 561 | 455 | 478 | 455 | 23 | 0 | 71 | 2.1% |
| c.97 | 558 | 420 | 446 | 420 | 26 | 0 | 96 | 2.9% |
| c.95 | 575 | 360 | 387 | 358 | 29 | 0 | 166 | 3.8% |
| two-sided | 349 | 265 | 265 | 0 | 0 | 265 | 75 | 2.9% |
| forced | 609 | 20 | 0 | 0 | 0 | 0 | 609 | 0% |

Median time to exit **15–60 s** (c.99 → c.95); two-sided 97 s; forced 0 s.

## Economics (q25, 8 windows)

| config | matched | gross pair | rebate | taker fees | net before comp | net after comp | net/leg | cap-hours | worst window |
|---|---|---|---|---|---|---|---|---|---|
| c.99 | 7,941 | 172.39 | 19.33 | 78.54 | −53.88 | −34.55 | −0.0632 | 116.5 | −33.41 |
| c.98 | 8,133 | 193.08 | 18.99 | 82.65 | −68.67 | −49.68 | −0.0885 | 141.5 | −33.87 |
| **c.97** | 8,002 | 247.43 | 19.25 | 76.56 | **+15.75** | **+35.00** | **+0.0627** | 182.6 | −30.20 |
| c.95 | 8,204 | 334.64 | 19.19 | 70.89 | −49.85 | −30.66 | −0.0533 | 278.6 | −143.15 |
| two-sided | 5,016 | — | 19.75 | 0.93 | −180.90 | −161.15 | −0.4618 | 133.2 | −93.12 |
| forced | 9,054 | — | 19.22 | 107.56 | −130.39 | −111.17 | −0.1825 | 0.3 | −25.42 |

### The c.97 positive result is one window, not an edge

I checked the ceiling non-monotonicity before reporting it, because a sign flip
between adjacent pre-registered thresholds is not how a real effect behaves.

Per-window net after compensation (q25):

| window | .99 | .98 | .97 | .95 |
|---|---|---|---|---|
| 1215z | −23.71 | −26.66 | −30.20 | −17.63 |
| 1230z | −33.41 | −28.15 | −27.40 | −22.55 |
| 1245z | −26.70 | −33.87 | −25.68 | −12.41 |
| 1300z | −15.60 | −17.00 | −5.17 | −8.80 |
| 1315z | −16.94 | −29.39 | −16.09 | −18.83 |
| 1330z | −19.76 | −17.28 | −6.29 | −143.15 |
| **1345z** | **+100.56** | **+101.66** | **+144.19** | **+176.80** |
| 0400z | +1.01 | +1.01 | +1.65 | +15.91 |

| ceiling | mean | SE | sigma | median | positive | mean excl. 1345z |
|---|---|---|---|---|---|---|
| .99 | −4.32 | 15.40 | −0.28 | −18.35 | 2/8 | −19.30 |
| .98 | −6.21 | 15.88 | −0.39 | −21.97 | 2/8 | −21.62 |
| .97 | +4.38 | 20.39 | **+0.21** | **−11.19** | 2/8 | **−15.60** |
| .95 | −3.83 | 30.85 | −0.12 | −15.02 | 2/8 | −29.64 |

**Every ceiling is within 0.4 sigma of zero, median-negative, and positive in
only 2 of 8 windows.** Removing 1345z turns c.97 from +$35.00 to **−$109.18**
(−$0.2421/leg). The non-monotonicity was noise, and **no ceiling has
demonstrably positive economics.**

## Cost per $1 of modeled compensation (q25)

| config | cost per $1 |
|---|---|
| **PAIR_OR_UNWIND c.95** | **$2.60** |
| PAIR_OR_UNWIND c.99 | $2.79 |
| forced completion | $6.78 |
| persistent two-sided | $9.16 |
| *Gemini PAIR_OR_UNWIND (for reference)* | *$6.32* |

PAIR_OR_UNWIND is **2.4–3.5× more capital-efficient per dollar of compensation
than its comparators**, and ~2.4× better than the same strategy on Gemini.

## Verdict

**Two findings, and they point in opposite directions.**

1. **The inventory problem is solved.** 67–89% qualifying exits and 2–4%
   unresolved, against 9–11% and 53% on Gemini. The immediately-executable
   complement path is what does it — 440 of 482 captured exits at c.99. This is
   robust across all four ceilings and all three sizes, and it is the question
   PAIR_OR_UNWIND was designed to answer.
2. **The economics are not positive.** Every ceiling sits within 0.4 sigma of
   zero on 8 windows, is median-negative, and is positive in 2 of 8 windows.
   The one apparently-positive configuration collapses when a single window is
   removed.

So PAIR_OR_UNWIND **beats both comparators decisively** (−$0.05 to −$0.09 per
leg against −$0.18 forced and −$0.46 two-sided) and **still does not make
money** on this sample.

No annualization, no extrapolation to $3/$5, and no threshold selected. Eight
windows cannot settle the sign; the honest next step is more windows under the
identical rules, with the ceilings staying pre-registered.
