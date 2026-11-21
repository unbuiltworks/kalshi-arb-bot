# C · Passive dual-sided null, and D · quote skew

Both run on the 12 BTC15m windows where the Zenodo 1-second L2 overlaps L5.

## C — a dumb passive dual-sided maker does NOT reproduce L5

Rules: rest a BID on **both** tokens at (best bid − k ticks), re-quote when the
touch moves, never cross, never sell, **no next-side decision, no ≤0.96 pair
gate, no inventory logic.** Historical flow decides which side fills.

**Fill rule and its limitation.** The dataset is **book-only — it carries no
trade prints** — so `VOLUME_AHEAD_CLEARED` cannot be computed here. The
strictest criterion this data supports is that the observed best bid falls
**strictly below** our price: the whole level is gone from the book, so an
order resting at the back of it is gone too. It **cannot separate a level that
traded from one that was cancelled**. That ambiguity makes the null look
*better* than it is — the bias runs in the null's favour, which is the safe
direction for a test whose purpose is to refuse a controller.

| at the touch (0 ticks) | null | **L5, same 12 windows** |
|---|---|---|
| events per window (median) | 106 | **238** |
| **same-side persistence** | **0.530** | **0.750** |
| final pair cost (median) | 0.9859 | ~0.96 |
| final residual (rel) | 0.117 | 0.098 |

| deeper | −1 tick | −2 ticks |
|---|---|---|
| events/window | 71 | 54 |
| persistence | 0.552 | 0.527 |
| final pair cost | 1.0062 | 0.9685 |

**The null's side sequence is a coin flip — 0.53 — while L5 sits at 0.75 in the
same windows.** A passive two-sided quoter hit by real historical flow produces
essentially independent sides. L5 does not. Going deeper does not help: it
halves the fill count and leaves persistence at ~0.53.

**This refutes H3 (FLOW) on this sample.** If the side sequence were
manufactured by incoming takers against stable two-sided quotes, the null would
show L5's persistence. It shows chance.

The null also gets **fewer than half** his fills and **worse** pair economics
(0.986 vs ~0.96) — and that is with the fill rule biased in its favour.

## D — no inventory skew in his eventual fill offsets

For every L5 **maker** fill whose price is consistent with a resting bid
(mint fills excluded — they have no measurable depth), the offset from the BBO
observed 5 s earlier, against his signed inventory on that same side:

| own-side imbalance | n | median offset | mean offset |
|---|---|---|---|
| ≤ −0.20 (short this side) | 127 | 2.0 | 2.59 |
| −0.20 … −0.05 | 297 | 2.0 | 2.70 |
| −0.05 … 0.05 | 1,217 | 2.0 | 4.07 |
| 0.05 … 0.20 | 310 | 3.0 | 4.05 |
| ≥ 0.20 (long this side) | 203 | 3.0 | 3.07 |

**Correlation: 0.01.** n = 2,154.

H2 predicts that the longer he is of a side, the deeper below the touch his
further fills on that side should sit. The profile is flat — median 2–3 ticks
everywhere, correlation indistinguishable from zero. **No inventory skew is
detectable.**

**Stated strictly:** this measures *eventual fill price relative to a prior
BBO*. No anonymous book size is attributed to L5 or to anyone else. It is also
subject to selection — we observe the quotes that were hit, not the quotes he
posted — so it is evidence against a *large* skew, not proof of none.

## Where that leaves the three hypotheses

| | verdict on C/D evidence |
|---|---|
| **H3 FLOW** | **refuted here** — the passive null gives 0.53 persistence against L5's 0.75, on half the fills |
| **H2 SKEW** | **unsupported** — offset/inventory correlation 0.01, flat across every bucket |
| **H1 CHOICE** | **not established** — something makes his sides persist far beyond flow, but A/B show it is not predictable from the state vector |
