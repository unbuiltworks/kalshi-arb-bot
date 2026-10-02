# RESOLVED: the 300 / 1,390 result was a methodology artifact, not a better market

**Status: NO LONGER UNRESOLVED.** The dataset is recovered, the historical code
runs on it, and the result does not reproduce. No live orders, no fitting, no
PMUS/SOL changes.

---

## 1. FIRST, A CORRECTION OF MINE

Through this whole lane I recorded that the 300/1,390 artifacts were stranded
on the Railway volume `/state/gemini-5m` and that the service was gone. **That
was wrong, and it was a research error, not a data-availability problem.** The
canonical dataset has been committed in git the entire time:

`analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz`

I anchored on `SECTION_D_E.md`'s account of a *different, later* 1 Hz
collector run and never checked whether the described cohort was the extract
sitting in the repo. It was.

## 2. COHORT IDENTITY — EXACT MATCH

| described | measured in `gemini_5m_canonical.jsonl.gz` | match |
|---|---|---|
| 182,119 rows | **182,119** | ✅ |
| 735 observed prints | **735** | ✅ |
| 98,202 contracts | **98,201.97** | ✅ |
| 103 windows | **103** (BTC windows with ≥1 print) | ✅ |
| `buyer_is_maker` available | present on every print | ✅ |

This is unambiguously the same cohort. Note the described figures **mix
denominators**: 98,202 contracts is BTC+ETH, while 103 windows is BTC-only
(BTC alone is 88,548.77 contracts over 103 windows; BTC+ETH is 139 windows).
That pooling is itself a small sign of loose bookkeeping in the original
summary.

## 3. THE HISTORICAL CODE, RUN UNMODIFIED, DOES NOT REPRODUCE 300

`analysis/l5/gemini_trajectory/continuous_inventory.py` — unmodified, same
model `bbb55bf90638d2bd`, same `pmus_l5_persistence_v2` policy — over four
cohort definitions:

| cohort | tier | usable | matched | **FILLS** | complement | orders | requotes |
|---|---|---|---|---|---|---|---|
| ALL (756) | STRICT | 741 | 0 | **20** | 0 | 3,649 | 2,774 |
| ALL | SUPPORTED | 741 | 2 | **32** | 2 | 3,646 | 2,760 |
| ALL | OPTIMISTIC | 741 | 15 | **125** | 21 | 3,829 | 2,850 |
| BTC (378) | STRICT | 372 | 0 | **18** | 0 | 1,681 | 1,252 |
| BTC | SUPPORTED | 372 | 2 | **30** | 2 | 1,678 | 1,238 |
| BTC | OPTIMISTIC | 372 | 15 | **112** | 20 | 1,833 | 1,309 |
| **BTC, ≥1 print (103)** | STRICT | 103 | 0 | **18** | 0 | **1,359** | 1,200 |
| **BTC, ≥1 print (103)** | SUPPORTED | 103 | 2 | **30** | 2 | **1,356** | 1,186 |
| **BTC, ≥1 print (103)** | OPTIMISTIC | 103 | 15 | **112** | 20 | **1,511** | 1,257 |

### "1,390" reproduces. "300" does not.

**1,390 ≈ the order count.** On the 103-window BTC cohort the module issues
**1,356 / 1,359 / 1,511** venue-admissible orders by tier — bracketing 1,390.
The denominator is right, and it confirms the cohort scale.

**300 is unreachable.** Every tier gives **18–125 fills**:
- **15–17× the STRICT tier** (the one needing no queue assumption),
- **10× SUPPORTED**,
- **2.4–2.7× OPTIMISTIC**, which the module's own docstring calls
  *"an explicit UPPER BOUND, not an estimate"* because it assumes front of
  queue.

**300 defensible fills is above the code's own physically-unreachable
ceiling.** It cannot be a defensible figure from this code on this data.

## 4. WHERE 300 MOST LIKELY CAME FROM

All three tiers share one stated invariant: *"All three require an actual
PRINT"* **at our price**. Drop the **price** match but keep the **side** match
and the arithmetic lands on 300:

| quantity | BTC | BTC+ETH |
|---|---|---|
| prints, passive-BUY side | **367** | 413 |
| prints, passive-SELL side | **292** | 322 |
| reproduced OPTIMISTIC (price match required) | 112 | 125 |
| reproduced STRICT | 18 | 20 |

**300 sits inside the 292–367 band of "every correct-side print while an order
rested, ignoring price".** A rule that matched side but not price would
produce ~300 on exactly this cohort. Nothing else in the data is near 300.

**Labelled as inference, not proof:** I cannot recover the original run, so I
cannot demonstrate which line of code differed. What I can demonstrate is
that 300 is incompatible with the price-match invariant and compatible with
its removal.

Secondary possibilities, neither supported: fills counted in **contracts**
rather than events (98,202 contracts makes 300 far too small), or a
**different, denser cohort** — ruled out in §5.

## 5. IT WAS NOT A BETTER MARKET REGIME

This is the discriminating test, and it is decisive.

If 300 had come from a genuinely richer regime or a denser data source, the
**denominators would be bigger**. They are not: the cohort reproduces at
**~100 windows, ~1,390 orders, 735 prints, 98,202 contracts** — the same scale
as the described result, matching to the row.

So the three candidate explanations resolve as:

| candidate | verdict |
|---|---|
| **the old methodology was optimistic** | **SUPPORTED** — 300 exceeds the code's own unreachable upper bound by 2.4–2.7× |
| the market regime was dramatically better | **REJECTED** — same dataset, same windows, same prints, same contracts |
| an execution mechanism / data source we are missing | **NO EVIDENCE** — the module's orders reproduce; only its fills do not |

## 6. THE DEPENDENT ECONOMICS ARE INVALIDATED TOO

The figures **net ≈ −$99.19 before compensation**, **≈ +$17.92 modeled
compensation**, **≈ $5.54 trading cost per $1 of compensation** and **57/98
positive windows** were all computed *on those 300 fills*. Since ~85–94% of
those fills are not supported by the code's own defensible tiers, **those
economics describe trades that would not have happened.**

In particular the **$5.54 per $1** ratio — which framed this entire lane as
"close-ish, needs a 5× execution improvement" — is not a measurement of
Gemini. It is an artifact of an inflated fill count. The real defensible
cohort is **18–30 fills across 103 windows**, which is the same order as this
lane's fresh event-level result (**33 defensible fills across 42 windows**).

**The two cohorts agree once both are measured defensibly.** There was never a
contradiction to explain — only an unreproduced number.

## 7. CONSEQUENCES

1. **The contradictory evidence is gone.** The audit's conclusion no longer
   has an outstanding rival result standing against it.
2. **The Railway volume is no longer a measurement debt.** Nothing needed from
   it; the cohort was in git. The `/state/gemini-5m` recovery item is closed
   as unnecessary, not as blocked.
3. **The historical result is preserved, not deleted** — as a **reproduced
   denominator with an unreproducible numerator**, with the specific
   incompatibility documented above.
4. **Nothing here reopens the frozen decision gate**, and nothing here is a
   new strategy experiment.

## 8. WHAT REMAINS GENUINELY UNRESOLVED

- **Which exact line differed** in the run that produced 300. The original
  run's artifacts are not in the repo, so the mechanism is inferred from
  arithmetic, not read from code or logs.
- The later **1 Hz collector cohort** described in `SECTION_D_E.md`
  (deployment `9f11d488`, obs/min 50–57) is a different and still-unexported
  dataset. It is **not** the 300/1,390 cohort, and this audit gives no reason
  to want it: the fresh event-level tape in this lane is strictly higher
  fidelity (p50 ~98–105 book events/min, verified sequence chain).
- Reproduction used the module's **default** residual cap, open-side rule and
  action ceiling. If the original run passed different `**kw`, fill counts
  would move somewhat — **but not by 2.4× past the OPTIMISTIC ceiling**, since
  that bound is set by the print-and-price invariant rather than by any
  configurable parameter.
