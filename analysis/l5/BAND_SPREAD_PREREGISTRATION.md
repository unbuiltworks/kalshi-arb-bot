# Band × spread two-sided quoting: pre-registration

**Frozen before the grid was computed.** Third pre-registration in this
programme, for the same reason as the first two.

Owner, 2026-09-11: *"We aren't looking for 'which band has the most trades'; we're
looking for states where the spread itself is large enough to pay for the
observed one-sided-fill risk... If there is no meaningful cell where the
economics clear, then we should stop trying to force this exact venue/market
class and use the L5 engine elsewhere."*

---

## 1. What is observable, and the one thing that is not

**Observable, with no model.** The selector logged, on every cycle, every
candidate that passed the strict evidence gate: **5,381 admitted observations
across 376 distinct markets**, each carrying `bid`, `ask`, `bid_touch_qty`,
`state`, `state_n`, `p_buy`, `pair_at_touch` — 100% coverage on all of them.
That is the live universe as the engine actually saw it.

**NOT observable: the both-fill rate.** We have never once quoted both sides
simultaneously. There is no tape of it. Any both-fill number would come from
multiplying two one-sided fill rates, and independence is precisely the
assumption the hypothesis disputes — two-sided flow is claimed to make the
legs *dependent*. So a modelled both-fill rate would assume away the entire
question.

**This is declared now, before the result, because it bounds what this study
can conclude.** It can answer whether any cell's economics *could* clear. It
cannot, on our own evidence, answer whether simultaneous quoting achieves
them. The owner's instruction — *"Do not use modeled future fills to
manufacture authoritative P&L"* — is what forces this split.

## 2. The grid

**Rows — probability band**, on the market mid `(bid+ask)/2`:
45–55, 40–60, 35–65, 30–70. Nested by construction; reported nested, not
disjoint, because the owner named them that way.

**Columns — spread bucket:** ≤0.01, (0.01, 0.02], (0.02, 0.03], (0.03, 0.05],
> 0.05.

**Per cell:** observations; distinct markets; median spread; median
`bid_touch_qty`; best attainable pair cost (`1 − spread`); attainable gross
edge at a stated size; and the decisive column below.

## 3. The decisive column, fixed now

> **Implied break-even residual incidence** = `edge / (edge + 1.9120)`
> where `edge = spread × q`, and −$1.9120 is the measured cost of one stranded
> market.

A cell **clears** iff its implied break-even incidence is **at or above the
observed residual incidence of 28.2%** — i.e. the spread pays for the observed
one-sided-fill risk. Stated the other way, the cell's required spread at size
`q` is `0.2821/0.7179 × 1.9120 / q = 0.7511 / q`.

**Size, fixed before looking:** `q = 10.3`, the measured median matched
quantity on paired episodes. Sensitivity is reported at `q = 5.75` (the median
*planned* contracts at entry) and `q = 20`.

## 4. What would make this stop the programme

Pre-registered, so it cannot be softened afterwards:

- **No cell clears at any of the three sizes** → the honest reading is that
  this venue and market class cannot support the strategy, and the engine
  should be taken elsewhere. That is the owner's own stated conclusion
  condition and it is recorded here before the numbers.
- **A cell clears but is nearly empty** → a cell holding a handful of
  observations across one or two markets is not an opportunity set. Distinct
  **markets**, not observations, is the count that governs, and any clearing
  cell is reported with both.

## 5. Banned

Settlement outcomes, resolution sides and `realized_usd` are not inputs to any
cell, threshold or rule. −$1.9120 and 28.2% enter as **population constants**
measured previously, not as per-observation labels; they scale the bar, they
do not fit it. No parameter is fitted in this study at all — the grid is
descriptive and the bar was set above before it was computed.

## 6. The L5 comparison, and its honest limit

The owner asks whether L5 buys both sides more often near 50/50, sizes
differently, or turns directional outside the band. Existing measured evidence
(`L5_COMPLETION_BEHAVIOR` / `l5_residual_state_replay`, 2,216 L5 windows) is
cited rather than recomputed, and any new cut is reported with its `state_n`.
L5's behaviour is **evidence about a two-sided trader in these markets**; it is
not evidence that our engine would achieve his fills, and it will not be
presented as such.

---

**Committed before the grid was computed.** Deviations recorded as deviations.
