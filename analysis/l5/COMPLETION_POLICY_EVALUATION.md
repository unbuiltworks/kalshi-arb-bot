# Inventory completion — policy comparison, and the constraint that actually binds

> ## SUPERSEDED FOR PMUS DECISIONS — see `PMUS_COMPLETION_FINDINGS.md`
>
> The headline sample below is **Gemini BTC05M**, a different venue and
> duration. Re-run on **26 out-of-sample PMUS windows** it is wrong on two of
> its four conclusions, and the error was not small:
>
> | | Gemini said | PMUS says |
> |---|---|---|
> | completion fill rate | 10.5% | **46.9%** |
> | mean/window | −0.4281 [−0.70, −0.12] | **−0.1011 [−0.27, +0.07]** |
> | significantly negative? | yes | **no — the CI spans zero** |
> | does residual REDUCE help? | yes, +$1.25 | **no — $0.52 WORSE** |
>
> What *does* replicate on PMUS: the ceiling is inert, and two resting legs
> are much worse. What does not: the sign, the fill rate, and the one policy
> recommendation. **Do not use the Gemini panel to decide anything about
> PMUS.** The sections below are retained only as structural background.

**2026-10-26.** No live gate changed, nothing armed, no capital change, no new
service. `max_live_orders` is still 1, the live ceiling is still 0.96, and
`L5_MAKER_PAIR_GATE` is still unset.

---

## Verdict first

| question | answer |
|---|---|
| Does a better completion-order policy fix this? | **No.** Nine policies, two datasets; every one is significantly negative. |
| Which knob helps at all? | **Only residual reduction**, consistently, on every panel — and it only shrinks a loss. |
| Does repricing / improving / tracking help? | **No.** The complement almost never fills, so its price barely matters. |
| Is one outstanding order the constraint? | **It is a real structural constraint, and lifting it makes things 5× worse.** |
| What actually binds? | **Adverse selection on any resting order.** A quote in a short-dated binary fills precisely when it should not. |
| Is bounded live validation justified? | **No.** Nothing here is positive. |

---

## 1. The campaign-accounting fix (deliverable 1) — shipped

**The defect.** The submission claim is consumed before the wire and never
returned. That rule is correct — a claim released on a crash could buy a
second order into a venue that already holds the first — but a **disarmed**
transport is not an ambiguous outcome: it raises `NotArmed` out of `submit()`
without attempting a POST.

Campaign `camp1.87265e609a92b5a2889e5392` closed having spent **1 of 21
submissions and 1 of 6 OPENs** on a well-formed MAKER intent (1715z DOWN
0.4500 × 1.54) that was refused `NOT_ARMED` *after* its claim was durable.
`submitted:false`, `venue_order_id:null` — and the budget counted a
submission the venue never saw.

**The fix is ordering, not releasing.** `FaithfulExecutor.disarmed_refusal()`
declines to *take* a claim when the transport is provably disarmed. Nothing
releases a claim; nothing about the never-reset rule changes.

Properties, each pinned by a test:
- It acts **only** on a positive `armed is False`. An absent attribute, a
  raising attribute, `0`, `""`, `None` or no transport all **fail open** into
  the existing path, so a telemetry fault cannot halt trading.
- It runs **after** `presend_cross_refusal` and **before** both
  `claim_submission` and `claim_accumulation_leg` — the last point before a
  claim becomes durable, which is exactly where its precedent sits.
- It **cannot authorize anything**: a structural AST scan pins that the only
  calls it makes are `getattr`, `str` and `type`. `NullTransport.submit()`
  still raises `NotArmed`, and the existing `NotArmed` handler still
  terminates the store reservation.
- Window expiry, the exhausted campaign, and every risk limit are untouched.
  The campaign is **not** reset or extended.

---

## 2. The completion-order policy, reconstructed

`src/l5_maker_placement_replay.py`. Causal: one book snapshot, own inventory,
the clock. No L5 fill is ever read.

**Resting price, own terms.** Buy UP as maker → rest a bid → own cost `bid`.
Buy DOWN as maker → rest an offer on YES → own cost `1 - ask`.

**Fill test.** `own_taker_cost(side, bid, ask) <= resting_price`; `STRICT`
requires it to come strictly *through*, the conservative reading of queue
priority at observed depths of 870–3,872 contracts.

**The knobs** (all default to the baseline, so baseline parity is exact):
`complete_improve_ticks` rests the complement below its touch;
`escalate_after_s` gives that up near the deadline; `complete_drift` sets the
reprice tolerance; `reduce_after_s` sells the first leg back as a taker
rather than carrying full notional into settlement.

---

## 3. Datasets, and what each can and cannot establish

| dataset | windows | rows | what it establishes |
|---|---|---|---|
| **Gemini BTC05M** `gemini_bbo_btc05m.jsonl.gz` | **205** | **12,673** | Structural book dynamics of a short-dated binary. **NOT a PMUS result** — different venue, different duration. |
| **PMUS observer 2026-09-23** `observer_rows_settled_windows.jsonl` | 5 | 249 | Authenticated PMUS BBO, settled windows, **untouched** by every earlier analysis here (all of which used 2026-10-25/26). |
| 2026-10-26 holdout | 5 | 314 | Already used for the ceiling work; now *touched*. |

**A correction found while loading the Gemini tape.** Every row precedes its
symbol's nominal time by 31–4,125 s, so the timestamp in
`BTC05M2608302330` is the contract's **expiry**, not a window start, and the
tape is a forward curve sampling many upcoming contracts. My first loader read
it as a start and selected **zero rows**. The decision window is now the last
900 s before expiry, which also maps onto the replay's native 600/720 s
cutoffs with no scaling.

**The opening rule is held fixed** across every policy, so the comparison is
about completion only: open the cheaper side by own maker cost — causal,
symmetric, and the side L5 actually rests on (his maker price median 0.38,
p25 0.18).

---

## 4. Nine completion policies, same opportunities

Ceiling 0.96 throughout. `mean/window` carries a 2,000-sample bootstrap 95% CI.

### Gemini BTC05M — 205 windows

| policy | cmp | str | red | net | pairs | strand | fill% | mean/window [95% CI] |
|---|---|---|---|---|---|---|---|---|
| A JOIN (baseline) | 2 | 17 | 0 | −8.13 | +0.43 | −8.57 | 10.5 | −0.4281 [−0.6972, −0.1162] |
| B HOLD (no reprice) | 4 | 15 | 0 | −8.02 | +0.61 | −8.63 | 21.1 | −0.4220 [−0.6570, −0.1468] |
| C TRACK (drift 0.01) | 2 | 17 | 0 | −8.13 | +0.43 | −8.57 | 10.5 | −0.4281 [−0.6972, −0.1162] |
| D IMPROVE 1 tick | 2 | 17 | 0 | −8.10 | +0.46 | −8.57 | 10.5 | −0.4264 [−0.6955, −0.1136] |
| E IMPROVE 2 ticks | 1 | 18 | 0 | −9.18 | +0.18 | −9.36 | 5.3 | −0.4831 [−0.7094, −0.1852] |
| F ESCALATE 1→0 | 2 | 17 | 0 | −8.13 | +0.43 | −8.57 | 10.5 | −0.4281 [−0.6972, −0.1162] |
| G ESCALATE 2→0 | 2 | 17 | 0 | −8.13 | +0.43 | −8.57 | 10.5 | −0.4281 [−0.6972, −0.1162] |
| **H JOIN + REDUCE@660** | 2 | 3 | 14 | **−6.88** | +0.43 | **−2.39** | 10.5 | **−0.3623 [−0.5199, −0.1963]** |
| I ESCALATE + REDUCE | 2 | 3 | 14 | −6.88 | +0.43 | −2.39 | 10.5 | −0.3623 [−0.5199, −0.1963] |

### PMUS BTC15M 2026-09-23 observer — 5 untouched windows

| policy | cmp | str | red | net | strand | fill% | mean/window [95% CI] |
|---|---|---|---|---|---|---|---|
| A JOIN (baseline) | 2 | 3 | 0 | −1.64 | −2.03 | 40.0 | −0.4096 [−0.7068, −0.0324] |
| D IMPROVE 1 tick | 2 | 3 | 0 | −1.60 | −2.03 | 40.0 | −0.3997 [−0.7068, −0.0128] |
| E IMPROVE 2 ticks | 2 | 3 | 0 | −1.56 | −2.03 | 40.0 | −0.3898 [−0.7068, +0.0069] |
| **H JOIN + REDUCE@660** | 2 | 0 | 3 | **−1.49** | **0.00** | 40.0 | −0.3714 [−0.6011, −0.0331] |
| I ESCALATE + REDUCE | 2 | 0 | 3 | −1.47 | 0.00 | 40.0 | −0.3664 [−0.6001, −0.0281]

**Readings.**

1. **The completion fill rate is the whole story: 10.5% (Gemini), 40% (PMUS).**
   The strand term is −$8.57 of a −$8.13 net. Completions contribute +$0.43.
2. **Price knobs are nearly irrelevant.** A/C/F/G are identical to four
   decimal places, because a complement that never fills does not care what
   price it never filled at. Improving *two* ticks is actively worse (fill
   rate halves to 5.3%).
3. **Reduction is the only robust improvement**, on every panel: it converts
   14 of 17 Gemini strands and 3 of 3 PMUS strands, cutting the strand term
   from −$8.57 to −$2.39 and tightening the CI (−0.52…−0.20 against
   −0.70…−0.12). It also only ever *shrinks a loss*.
4. **Every policy's CI excludes zero on the negative side** (bar E on the
   5-window PMUS panel, which is not a sample). Nothing here is promotable.

> **A reporting defect I found and fixed.** My first per-window series omitted
> **reduced** legs, so the reduce policies appeared to score
> **+0.1983 [+0.1365, +0.2600]** on the PMUS panel — purely by *deleting* the
> loss-making windows from the sample, since reduction turns a strand into a
> reduction. Corrected to **−0.3714**. The positive figure was an artifact and
> is withdrawn.

---

## 5. The structural identity, and why lifting the one-order limit is worse

Resting both legs at their own touch costs

```
own_maker(UP) + own_maker(DOWN) = bid + (1 - ask) = 1 - spread
```

**So a maker-maker pair earns exactly the spread**, and a ceiling `C` is
reachable only when `spread >= 1 - C`:

| ceiling | spread required | Gemini | PMUS observer |
|---|---|---|---|
| 0.96 | ≥ 0.04 | 22.3% of snapshots | 10.0% |
| 0.99 | ≥ 0.01 | **100%** | **100%** |

**This materially corrects my previous turn's reasoning.** I reported the 0.99
ceiling as inert. It *is* inert **given one outstanding order**, where it only
governs chasing a complement. With two resting legs it is the difference
between 10% and 100% eligibility — so "inert" was conditional on a constraint
I had not identified as the binding one.

So I tested the strongest form of the hypothesis. `replay_two_sided` rests
both legs and **counts** the both-fill rate rather than assuming it:

| ceiling / residual | placed | both | one-sided | **both-fill rate** | net | strand |
|---|---|---|---|---|---|---|
| 0.96 / carry | 3 | 0 | 3 | 0.0% | −2.39 | −2.39 |
| 0.98 / carry | 172 | 17 | 66 | **20.5%** | **−41.56** | −42.11 |
| 0.99 / carry | 172 | 17 | 66 | **20.5%** | **−41.56** | −42.11 |
| 0.99 / reduce | 172 | 13 | 10 | 56.5% | −37.75 | −7.84 |

**Two resting legs are 5× worse than one** (−$41.56 against −$8.13). The
spread identity is sound arithmetic; the conditional probability destroys it.
172 placements yield 17 locked pairs and **66 one-sided strands**, because a
trending market sweeps one side of the book and leaves the other. Collecting a
2-cent spread 20% of the time does not pay for an adversely-selected strand
80% of the time.

**Therefore the one-order limit is not what is costing us money, and raising
it is not the next step.** I am not requesting that change.

---

## 6. What actually binds

Adverse selection on **any** resting order. Independently measured three ways,
all agreeing:

| measurement | result |
|---|---|
| Deployed shadow controller, 956 modelled fills | **−$0.0827 per filled contract, 91.63% of fills adverse**; WAIT dominates by +$52.25 over 355 windows |
| This replay, single order, 205 Gemini windows | completion fill rate **10.5%**, strand term −$8.57 of −$8.13 net |
| This replay, two resting legs, 205 Gemini windows | both-fill rate **20.5%**, 66 one-sided strands of 83 fills |

A resting quote in a short-dated binary fills **precisely when it should not**:
the only path to a fill is the market moving through your price. That is a
property of the instrument, not of our ceiling, our reprice cadence, our order
limit, or our fee model — all four of which I have now tested and none of which
changes the sign.

---

## 7. Deliverables and provenance

**Production-bound (deliverable 7):** the `NOT_ARMED` claim-accounting guard
and its 19 tests only. Cherry-picked onto the narrow lineage after baseline
parity. The research modules are **not** promoted.

**Research-only, not promoted:** the completion-policy knobs,
`replay_two_sided`, and `analysis/l5/completion_policy_eval.py`.

**Baseline parity:** full suite shows the same 34 pre-existing failures and
errors, byte-identical by name, with these files set aside.

**Evidence grades.** Venue-confirmed: the fee schedule, the $0.42 credited
rebate, L5's on-chain maker classification. **Modelled, not venue-confirmed:**
every fill and every settlement in §4 and §5 (`TERMINAL_BOOK_INFERRED`).
Nothing here is realized profit. The Gemini panel is **structural evidence
from another venue and duration** and no figure from it is a PMUS result.

**Sample sizes and uncertainty, stated plainly.** Gemini: 205 windows, 19
sequences single-sided, 83 two-sided fills — enough to sign the result.
PMUS observer: **5 windows, 5 sequences** — not enough to promote anything,
and reported only because it agrees with the larger panel. Every mean carries
a bootstrap CI. **No policy is promoted on either.**

---

## 8. Biggest remaining blocker, and whether to go live

**Blocker: there is no demonstrated positive-expectancy entry.** Every
execution-side question is now answered and none of them was the problem —
the ceiling (inert), the completion price (irrelevant), the reprice cadence
(irrelevant), the order limit (worse when lifted), the fee model (decomposed),
the rebate (zero at our size). What remains unanswered is the one upstream
question: **is there any observable state in which opening a leg has positive
expectancy at all?** Three independent measurements say WAIT currently
dominates.

**Bounded live validation is not justified.** Nothing measured here is
positive, and a live probe cannot discover an edge that the replay says is
absent. The next genuinely informative work is **entry selection** — finding
a conditioning state where the opening leg is not adversely selected — done
read-only against existing recordings, with no capital at risk.
