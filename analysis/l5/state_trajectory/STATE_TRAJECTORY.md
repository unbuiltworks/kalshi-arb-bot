# P0 ECONOMIC-STATE TRAJECTORY DECOMPOSITION

**Read-only.** No production change, no persistence deployment, no taker, no
cap/band/admission/sizing/concurrency change. `392b5c0e` remains deferred.
CEILING_PINNED_PERSIST is **parked**, not deployed.

---

## 0. The answer to the central question

> **If we gave PMUS perfect maker COMPLETE conversion at its existing prices,
> would its underlying inventory-state distribution still be materially worse
> than L5?**

**YES — and the part that remains is the larger part.**

Perfect conversion (every COMPLETE fills at the price it was actually posted,
capped by the residual standing in front of it) takes PMUS from 44 fills to
74 and from 12 switches to 35. It roughly doubles the cycling. It does not
close the gap:

| | L5 | PMUS | PMUS *perfect conversion* |
|---|---:|---:|---:|
| episodes | **2,216** | 9 | 9 |
| informative states (a computable gain) | **69,558** | 35 | **48** |
| **gain_per_unit exactly zero** | **4.0%** | 45.7% | **29.2%** |
| fills per episode (p50) | 6 | 4 | 8 |
| switches per episode (p50) | 2 | 1 | 5 |
| seconds between fills (p50) | **2.0** | 116.9 † | unchanged by conversion |

† upper bound — see §1.

Conversion halves PMUS's degenerate-state share, from 45.7% to 29.2%, and
leaves it at **7.3× L5's**. It leaves **48 informative states against 69,558**
and **9 episodes against 2,216**. And it cannot touch fill cadence at all:
conversion changes whether a posted order fills, not how often the engine is
in a position to post one.

**The remaining gap is habitat, not execution.** Stop optimising PMUS COMPLETE
microstructure; the binding constraint is how often we are in an L5-like state
at all.

---

## 1. Method, and what it costs

**One walker, both venues.** `src/l5_state_trajectory.walk` builds every state
from a common fill record, so the comparison cannot accidentally measure two
different reconstructions. Episodes are aligned at the **first fill**. Every
field is computed from fills **strictly before** it plus the action being
taken — nothing downstream is read.

**The identity this rests on**, verified here at **2.22e-16** across both
venues:

```
pair − 1.00 = (held_avg_cost − entry_price)  +  (complement_paid − (1 − entry_price))
            =        C_own_fills             +          B_market_move
```

Entry price **cancels out of the level**, which is a result rather than a
convenience: entering at any price gives a par pair if the complement is
available at one minus it. Entry price can therefore only matter through what
it makes *reachable*, and it is carried as a stratifier (component A), never
as a level contribution.

**PMUS fills are recovered from the engine's own inventory ledger.** Order
rows carry no timestamp, so a fill's moment is unrecoverable from them.
Consecutive `inventory_before` snapshots on timestamped decisions do carry it,
and the price falls out of the average-cost move exactly:
`px = (q₁·avg₁ − q₀·avg₀) / (q₁ − q₀)`. This recovers **44 fills against 45
order rows** with `filled_qty > 0`; the one difference is a fill with no
subsequent decision to observe it.

**PMUS cadence is an upper bound.** A fill is stamped with the decision that
*observed* it, not the moment it happened. The true cadence is faster than
116.9 s — by how much is UNKNOWN.

**Two L5 non-BUY prints (of 71,984) were dropped** rather than modelled.

---

## 2. The four-column comparison

| measure | L5 canonical | PMUS sports | Gemini 5m | Gemini 15m | class |
|---|---:|---:|---:|---:|---|
| episodes | 2,216 | 9 | 118 | 174 | — |
| fills | 71,982 | 44 | 27 ‡ | 6 | — |
| switches | 21,739 | 12 | — | — | — |
| **matched pairs formed** | 21,739 | 12 | **2** | **0** | — |
| pairs with a full decomposition | 13,048 | **9** | — | — | — |
| gain_per_unit p50 | −0.0113 | 0.0 § | n/a | n/a | UNKNOWN |
| **gain exactly zero** | **4.0%** | **45.7%** | n/a | n/a | **EXECUTION** |
| realised pair p50 | 0.997 | 0.96 § | 0.99 (n=2) | n/a | UNKNOWN |
| excess pair p50 | −0.003 | −0.04 § | n/a | n/a | UNKNOWN |
| C own fills p50 | 0.0 | 0.0 | n/a | n/a | SHARED_L5_BEHAVIOR |
| B market move p50 | −0.01 | −0.04 § | n/a | n/a | UNKNOWN |
| fills/episode p50 | 6 | 4 | — | — | EXECUTION |
| switches/episode p50 | 2 | 1 | — | — | EXECUTION |
| terminal matched fraction p50 | 0.3112 | 0.3751 | 0.0 | 0.0 | VENUE_DYNAMICS |
| terminal \|residual\| in clips p50 | **4.4** | 1.09 | — | — | SHARED_L5_BEHAVIOR? |
| sec between fills p50 | **2.0** | 116.9 † | — | — | **VENUE_DYNAMICS** |
| sec between switches p50 | **9.0** | 116.9 † | — | — | **VENUE_DYNAMICS** |
| median clip (contracts) | 12.5 | 1.72 | 1 | 1 | EXECUTION (by design) |

‡ best of four arms. § **survivorship — see the warning below.**

Sizes are normalised by each venue's own median clip wherever a quantity
scales with size (residual, matched).

### The warning that has to travel with the pair numbers

**PMUS's realised pair (0.96) looks better than L5's (0.997). It is not a real
advantage and must not be reported as one.** PMUS formed only **12** pairs (9
with a full decomposition), and
they are exactly the COMPLETEs that *filled* — and the prior forensics
established that our fills land at pair p50 0.949 while our no-fills sit at
0.9952. The realised-pair distribution is the cheap tail of the attempted one.
Classified **UNKNOWN**, not VENUE_DYNAMICS and not an edge.

The same caveat kills the naive reading of the observed gap in §3.

### And the warning that has to travel with gain_per_unit

`gain_per_unit` is the held side marked against its **own running average**, so
a position built from a single fill has a gain of **exactly zero by
arithmetic** — not because the market sat still. PMUS's gain is exactly zero in
**45.7%** of its 35 states (L5: 4.0% of 69,558), and 23.5% of its held-side
states have exactly one fill on the held side (L5: 3.3%).

**PMUS's tight, healthy-looking gain distribution centred on zero is a symptom
of not cycling inventory, not of well-behaved positions.** Any cross-venue
comparison of gain or pair *levels* is meaningless without that number beside
it, which is why it is in the table.

---

## 3. OBSERVED decomposition — exact, and not causal

The identity is exact, so the gap in mean excess pair is exactly the sum of
the gaps in its two terms:

| | L5 | PMUS | Δ (PMUS − L5) |
|---|---:|---:|---:|
| mean excess pair | +0.0045 | −0.1352 | **−0.1397** |
| C own fills | −0.0059 | −0.0036 | +0.0023 |
| B market move | +0.0103 | −0.1317 | **−0.1420** |
| **n (pairs with a full decomposition)** | **13,048** | **9** | — |

`ΔC + ΔB = −0.1397` — exact, max identity residual **2.22e-16**.

Read literally this says PMUS's pairs are 14 cents *better* than L5's, and
**that reading is wrong**: **n = 9 against 13,048**, and those 9 are the fills
that survived. Nine observations cannot carry a 14-cent claim in either
direction. The decomposition is reported because it is exact and because
it localises where any real difference would appear — **in B, the market-move
term, not in C, what our own fills do to the average**. C is essentially
identical across venues (Δ +0.0023), which is a genuine
**SHARED_L5_BEHAVIOR** finding: our averaging behaviour is not the problem.

No causal claim is made. C and B are two halves of an accounting identity;
which of them a trader controls is a different question this decomposition
does not answer.

---

## 4. COUNTERFACTUAL decomposition — kept separate, associational only

Each reweighting asks "what would PMUS's mean excess pair be if its strata
occurred at L5's frequencies?" It holds each stratum's behaviour fixed and
changes only how often the strata are visited. **It is a composition question,
not a causal one, and the components are not additive.**

| component | result | class |
|---|---|---|
| **A** initial entry state/price | reweighting PMUS to L5's price-band mix shifts excess pair by **+0.0178** — but **51.6% of L5's mass falls in bands PMUS never visited** | **UNKNOWN** (half of L5's habitat is unobserved) |
| **B** market movement after entry | −0.1420 (observed, §3) | UNKNOWN — survivorship |
| **C** our own fills' effect on average cost | +0.0023 (observed, §3) | **SHARED_L5_BEHAVIOR** |
| **D** sampled→acted shell divergence | **n = 1** fill state where sampled ≠ acted (43 where equal). Not informative. L5 **has no shell**, so this component exists on one side only and can never be a like-for-like contribution | **UNKNOWN** |
| **E** number/cadence of inventory cycles | fills/episode 4 vs 6; switches/episode 1 vs 2. Too few PMUS episodes with >1 switch to reweight without single episodes driving it | **EXECUTION** |
| **F** venue/category/TTE regime | **UNAVAILABLE** — PMUS fills carry no per-fill TTE, and no gain-bucket stratum is shared | **UNKNOWN** |
| **G** remainder | everything outside the exact B/C split: the 51.6% of L5's price-band mass PMUS never enters, the 2s-vs-117s cadence, and 2,216 episodes against 9 | **UNKNOWN / habitat** |

**A, D and F are dominated by the fact that PMUS does not visit most of L5's
state space at all.** That is not a measurement failure to be fixed with more
careful statistics; it is the finding.

---

## 5. Gemini — an availability finding, not a performance comparison

Gemini data **is** in this worktree. It cannot answer this question, and the
reason is countable rather than a judgement.

**Gemini 5m** (`gemini_l5_shadow_v2.json`, BTC 5m, 118 campaigns, four arms):

| arm | first-leg fills | complement orders | complement fills | matched campaigns | median pair |
|---|---:|---:|---:|---:|---:|
| modal:optimistic | 27 | **0** | **0** | **0** | null |
| modal:strict | 8 | 0 | 0 | 0 | null |
| stochastic:optimistic | 23 | 2 | **2** | **2** | 0.99 |
| stochastic:strict | 6 | 1 | 1 | 1 | 0.99 |

**Gemini 15m** (`gemini_counterfactual_15m_rows.jsonl`): 174 rows, 170
`NO_FILL` and 4 `RESIDUAL_HOLD`, **6 fills in total**, **zero matched
inventory** — no pair was ever formed.

`gain_per_unit` needs a running average over repeated same-side fills; a pair
needs a completion. The best Gemini arm has **2**. Three of four arms have
**zero**.

**Gemini does NOT exhibit more L5-like gain/pair/cycling distributions. It has
essentially no pair formation at all.** Nothing here is called profitable, and
nothing here is called unprofitable either — it is an availability finding.

### The exact schema the Gemini workstream must return

Summaries cannot substitute: every distribution in this study is rebuilt from
the fill stream. One row **per fill**, chronological within an episode:

```
episode_id      str    one market window / campaign
venue_duration  str    '5m' | '15m'
seq             int    1-based order within the episode
t               float  epoch seconds of the FILL (not the order)
side            str    'up' | 'dn'  -- the OUTCOME bought, not BUY/SELL
price           float  in (0,1)
qty             float  contracts filled
tte_s           float  seconds to settlement AT THE FILL
maker_taker     str    'maker' | 'taker' | 'unknown'
sampled         str    the action the model drew, before any shell gate
acted           str    the action actually taken
refusal         str    the gate that changed it, or null
```

per episode: `settlement_outcome`, `terminal_up`, `terminal_dn`,
`realized_usd` (venue-confirmed, or null — **never a proxy**).

```sql
SELECT episode_id, venue_duration, seq, t, side, price, qty, tte_s,
       maker_taker, sampled, acted, refusal
FROM fills WHERE filled = true ORDER BY episode_id, seq
```

**Must not be sent:** assumed or imputed fills of any kind; mid-proxy
settlements presented as realized; aggregates in place of the fill stream.

**Minimum to be usable:** ≥30 matched campaigns and ≥30 complement fills.
Below that the gain and pair distributions cannot be compared to L5's 21,739
switches at any useful resolution.

---

## 6. The perfect-conversion counterfactual, and its guardrail

Fills **are** imputed in this column — only here, only for COMPLETE, only at
the price actually posted, and only to answer a state-distribution question.
No P&L, realized figure or fill-rate claim may be taken from it.

**Each imputed fill is capped by the residual standing in front of it.** A
COMPLETE exists to close inventory we own; once the position is flat the
engine posts no further COMPLETE, so imputing every attempt in full would run
the position through flat and out the other side — a different and much worse
strategy, not "perfect conversion". Of 195 attempts: **29 imputed** (38.8
contracts), **12 trimmed**, and **166 dropped because no residual stood in
front of them** — which is the churn finding arriving from a third direction:
85% of our COMPLETE attempts are retries against a residual the first fill
would already have closed.

Result: matched fraction 0.3751 → 0.4081, switches 12 → 35, degenerate gain
states 45.7% → 29.2%. **Better, and still 7.3× L5.**

---

## 7. Where this leaves the program

1. **Execution is not the binding constraint.** C — what our own fills do to
   average cost — is statistically indistinguishable from L5's. Perfect
   COMPLETE conversion improves cycling and still leaves a 7.3× degeneracy
   gap, 48 informative states against 69,558, and 9 episodes against 2,216.
2. **Habitat is.** L5 fills every 2 seconds across 2,216 windows; we fill
   every ~117 seconds across 9 markets and never enter 51.6% of his price
   bands. Nothing in the COMPLETE microstructure changes any of that.
3. **Gemini is not the answer as it stands** — 2 matched pairs across 118
   campaigns — but it is also not refuted, because the workstream has never
   returned a fill stream that could be tested. The schema in §5 is the
   cheapest way to find out.
4. **Nothing here is profit.** No figure in this study is realized. The pair
   numbers that favour PMUS are survivorship artifacts and are classified
   UNKNOWN.

Reproduce: `python3 analysis/l5/state_trajectory/run.py`
