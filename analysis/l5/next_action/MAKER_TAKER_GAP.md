# Why L5 is ~83% maker and the clone ~8% — read-only diagnosis

**READ-ONLY. No live order. The live executor, thresholds, inventory limits, λ,
sizing and deployment are UNCHANGED. No Gemini. The active PMUS experiment and
its completeness gate are untouched.**

I did not begin from the latency assumption, and the evidence refutes it as the
primary cause.

---

## 0. First: is L5's 83.4% maker share observed or inferred?

**INFERRED, by absence — not observed.** Exactly how, from
`src/polymarket_l5/canonical_sequence.py`:

> "Polymarket's public feeds do not label maker-ness on `/activity` rows. The
> takerOnly trades feed positively identifies TAKER fills; fills absent from it
> within the feed's covered span are labelled `maker`; fills outside the
> takerOnly coverage span are `unknown`. Never treat `unknown` as maker."

| property | value |
|---|---|
| taker identification | **positive evidence** — fingerprint match `(tx, ts, condition, token, side, price, shares)` against the `takerOnly` feed |
| maker identification | **absence of evidence** — not in the taker feed, inside the coverage span |
| taker feed shape | 27 JSON pages, but only **9 distinct offsets** (0…4000) walked **three times** → 13,500 items, **11,810 distinct fingerprints**, 11,068 matching corpus fills |
| exhaustion test | the deepest page returned a **full 500 rows**, so the walk stopped at offset 4000 **without exhausting the feed** |
| recorded coverage span | `takeronly_coverage_span_epoch` = **2026-08-28 00:07:57 → 2026-08-31 23:11:19**, i.e. **3.96 days** |
| scope check | **all 66,715 August fills fall inside that span** (0 outside), so the `maker` assertion is correctly scoped; March (1,931) and April (3,338) are entirely `unknown`, correctly |

### Two consequences that must travel with the number

1. **The error runs one way.** Any taker fill the walk missed — and the walk
   provably did not exhaust the feed — becomes a false `maker`. So **83.4% is an
   upper bound on L5's maker share**, not a point estimate.
2. **The maker/taker-classified corpus is FOUR DAYS**: 2026-08-28 (401 windows),
   08-29 (567), 08-30 (558), 08-31 (474). Everything in the previous
   STATE→NEXT ACTION study that depends on maker/taker rests on those four days.
   The registered DEV/OOS boundary of 2026-08-29 therefore splits DEV = March +
   April + 08-28 against OOS = 08-29…08-31, so "held out of sample" means
   **three consecutive days of the same week**, not a regime change.

**Breakeven maker share** (holding L5's own prices and behaviour fixed, modeled
PMUS economics): net crosses zero at roughly **71–72%**.

| maker share | 0% | 20% | 40% | 60% | **70%** | 75% | 83.4% | 90% |
|---|---|---|---|---|---|---|---|---|
| modeled net | −$13,566 | −$9,856 | −$5,956 | −$2,019 | **−$166** | +$806 | +$2,474 | +$3,781 |

This is the operationally important figure: the clone does not need to reach
83%. It needs **~72%**. At 8.4% it is not close, and even 60% is not enough.

---

## 1. What the timeline evidence actually permits — audit before attribution

The requested ten-step reconstruction needs per-action timestamps for L5's
trade, our earliest observable state, our decision, and submission-ready.
**I checked every committed artifact. Those fields do not exist.**

| artifact | what it holds | per-action timeline? |
|---|---|---|
| `analysis/pmus/p36_faithful_fidelity.json` | 5 windows × 12 aggregate metrics (`action_reproduction`, `maker_taker_mix`, `irreducible_failures`, …) | **NO** — window aggregates only |
| `analysis/pmus/P36_FAITHFUL_FIDELITY.md` | the narrative and the mechanism statement | **NO** |
| `analysis/l5/l5_canonical_sequence.csv.gz` | L5's fills with his timestamps | L5 side only; **no clone-side timestamps** |
| `data/pmus_ws_tape/…auth_tape.jsonl.gz` | PMUS book + trades, touch bound at each trade | venue side only; **no clone actions** |

**Therefore steps 3, 4 and 6 are not reconstructable, and causes A (observation
latency), B (decision latency) and C (submission latency) cannot be separated
from each other on the existing sample.** Reporting per-category millisecond
percentiles would mean inventing them. I have not.

What I did instead: measure the **venue mechanism** on 16,620 authenticated
trade prints, which settles the question without needing clone timestamps.

---

## 2. The mechanism, measured independently of the clone

For every authenticated trade print, treat it as a target action — "buy this
side at the price that just printed" — and classify it against the touch **bound
by the recorder at that same instant** (no merge, no clock skew):

| classification at the receipt instant | n | share |
|---|---|---|
| **MARKETABLE — must cross** | 8,117 | **48.8%** |
| **AT TOUCH — queue ahead unknown** | 7,597 | **45.7%** |
| PASSIVE, behind touch | 653 | 3.9% |
| PASSIVE, improves inside spread | 253 | 1.5% |

Under the standing no-touch=fill rule, an at-touch order has unknown queue-ahead
and **cannot be credited as a maker fill**. So **94.5% of print-triggered target
prices cannot become a defensible maker post**, and only **5.4%** rest cleanly.

That independently brackets P36's 120/131 = 91.6% taker, from a different
dataset and a different venue instance. **The clone's taker share is a property
of the trigger, not of our speed.**

### The decisive test against the latency hypothesis

For the 8,117 marketable prints, how long until that price becomes passive —
i.e. until the market moves away from it?

| | value |
|---|---|
| became passive later in the window | 6,478 |
| **never became passive in-window** | **1,639 (20.2%)** |
| wait to postability | **p50 4.20 s**, p90 56.3 s, max 365 s |
| postable within 1 s | 20.9% |
| postable within 5 s | 56.5% |

**A printed price becomes postable only when the market moves away from it, and
that takes seconds.** Latency is measured in milliseconds. Shaving milliseconds
converts essentially none of these crossings into posts. One fifth never become
postable at all.

### And the two timescales are in direct conflict

| quantity | value |
|---|---|
| L5's own gap to his next action (median) | **2.00 s** (p25 0.0 s, p75 8.0 s) |
| median wait for a printed price to become postable | **4.20 s** |
| share of L5's next actions arriving **within** that 4.20 s | **65.6%** |

**L5 re-acts faster than his own printed prices become postable.** So a
faithful follower cannot wait for postability without already being behind his
next action. **Faithfulness to L5's actions and maker-ness are mutually
exclusive for a follower** — at any latency.

L5 escapes the conflict because he is not a follower. His order **rests before
the print exists**; the print is other people hitting him. His maker-ness is
upstream of the event our clone uses as its trigger.

---

## 3. Mutually exclusive primary-cause classification

Assigned at the level the evidence supports, with evidence quality stated. The
284 P36 actions divide into 131 reproduced and 153 not reproduced.

| cause | actions | share of 284 | timing evidence | maker-share ceiling if ONLY this were fixed | evidence quality |
|---|---|---|---|---|---|
| **D PRICE-POLICY MISMATCH** | **120** | 42.3% | not separable; mechanism independently measured | **~5–8%** — the rule targets L5's price, which is marketable or at-touch 94.5% of the time | **STRONG**: P36's rule is parameter-free and mandates "closest executable means at the receipt instant"; 16,620 prints agree |
| **E ACTION-POLICY MISMATCH** | 118 | 41.5% | n/a | unquantifiable | MODERATE: `NO_SUBSEQUENT_INTERACTION` 98 + `QUEUE_AMBIGUOUS` 20 — the clone needs an interaction L5's state never produced |
| **F REPLAY/ALIGNMENT ARTIFACT** | 35 | 12.3% | n/a | n/a | STRONG: `NO_BOOK` 35 — no book existed; causal timing cannot be established |
| **A / B / C LATENCY (obs/decision/submission)** | **0 attributable** | 0% | **not reconstructable** | **~5%** even if all three were zero (see CF1/CF2) | **ABSENT** — no clone-side timestamps exist in any committed artifact |
| G OTHER / UNKNOWN | 11 | 3.9% | — | — | the 11 actions that did rest as maker |

**No action can be attributed to latency on the available evidence, and the
venue mechanism says latency is not where the loss is.** A/B/C are not ruled out
as contributors — they are unmeasured — but CF1 below bounds how much they
could possibly be worth.

---

## 4. Counterfactuals — POSTABILITY ONLY, never fills

No counterfactual below is converted into a fill. Queue position and fill
probability remain a separate, unsolved problem.

**CF1 — ZERO OBSERVATION DELAY.** At the earliest reconstructable state, would
L5's target price have been passive? **Mostly no, and this is the bound on
A/B/C.** The earliest state we could possibly observe is the print itself; the
print is the first moment L5's action exists as information. At that instant the
price is marketable 48.8% and at-touch 45.7%. **Ceiling with all latency
eliminated: ~5.4% postable.** Latency cannot be the primary cause because its
perfect elimination leaves the clone at roughly the maker share it already has.

**CF2 — ZERO DECISION DELAY.** Given the state we actually received, would
immediate submission have been passive? **No — same 5.4% ceiling.** Decision
delay is bounded above by CF1: it cannot do better than acting at the receipt
instant, which CF1 already prices.

**CF3 — L5 PRICE POLICY.** At the clone's decision time, would using L5's price
instead have been passive? **This is the clone's actual policy**, and it is the
problem rather than the fix: L5's price *is* the print. P36 records favourable
slippage of **−$4.88** precisely because "we cross only when L5's price is at or
through our touch" — the clone crosses *because the target price is already
executable*, and it gets a weakly better price for doing so. Targeting L5's
price is what guarantees taker status.

**CF4 — L5 STATE/POLICY (H1/H3).** Would a state-driven policy have acted
earlier or chosen a materially different price? **Yes, and this is the only
counterfactual that changes the ceiling.** From the verified policy: L5 extends
when price sits **below his own running basis** (largest coefficient −0.293) and
when the complement is expensive (`opp_px` +0.233, `pair_cost` −0.265). None of
those inputs is an L5 fill. A policy quoting from **book state plus its own
basis** posts *before* flow arrives, which is how L5 gets 83%. The 5.4% ceiling
applies only to print-triggered pricing; it does not apply to state-triggered
quoting.

---

## 5. Decomposition of the execution-style counterfactual

**MODELED COUNTERFACTUAL throughout. L5 traded on Polymarket, never on PMUS. He
never paid these fees and never received these rebates. `+$1,237` is NOT
realized L5 profit under PMUS economics.**

| line | L5 observed | clone-like (8.4% maker) | class |
|---|---|---|---|
| gross directional + pair P&L | **+$2,740.16** | **+$2,740.16** | **ACTUAL**, identical in both arms |
| modeled taker fees | −$3,759.87 | −$14,933.99 | MODELED |
| modeled maker rebate | +$2,256.34 | +$246.85 | MODELED, **not cash-confirmed** |
| net | +$1,236.63 | −$11,946.98 | MODELED COUNTERFACTUAL |

**Swing = −$13,183.61**, attributed:

| component | amount | share |
|---|---|---|
| extra modeled taker fees | **−$11,174.12** | **84.8%** |
| lost modeled maker rebate | −$2,009.49 | 15.2% |
| changed execution price | $0.00 | by construction — both arms use L5's own fill prices |
| other | $0.00 | — |

Corrections to my previous statement of this figure:

- I quoted **$13,905**. The consistent figure under the registered convention
  (the 5,269 `unknown` fills excluded from both fees and rebates in *both* arms)
  is **$13,184**. The earlier number rated unknowns as taker in the clone arm
  only, which is an inconsistent comparison.
- The swing is **entirely a fee/rebate sign-flip on unchanged prices**, so it is
  an **upper bound on the execution-style cost, not a forecast**. A real
  taker-heavy clone also gets different prices, and P36 measured that difference
  as **favourable** (−$4.88 slippage, crossing was cheap). The arithmetic does
  not model that offset.

---

## 6. Timing waterfall

| stage | delay | basis |
|---|---|---|
| MARKET STATE → observable by us | **not measurable**; bounded by venue emission | the authenticated recorder's own book cadence is p95 **0.2–0.7 s**, max 5.2–10.5 s per window |
| observable → normalized state available | **not measurable** — no clone-side timestamps committed | — |
| normalized state → policy decision | **not measurable** | the frozen rule applies a **2.0 s** book-staleness bound and a **30 s** stale-action bound (P39) — these are *policy parameters*, not measured delays |
| decision → order constructed | **not measurable** | — |
| order constructed → submission possible | **not measurable** | — |
| **reference: window-open book availability** | **+0.2 s to +5.2 s** after open, before the first trade and before L5's first action every window | P39, builds `72ee0ad` / `8e4c8b7` |
| **the delay that actually matters** | **4.20 s median** for a printed price to become postable, vs **2.00 s** median to L5's next action | this study |

**The waterfall cannot be filled in, and filling it in would not answer the
question.** Every unmeasured stage lies inside the window that CF1 already
bounds: even at zero total delay the ceiling is ~5.4%. Instrumenting the
waterfall is worth doing for its own sake, but it is not on the critical path to
maker share.

---

## 7. The answer

> **WHAT PREVENTS AN L5-LIKE ACTION FROM BEING POSTED PASSIVELY BY OUR CLONE?**

**The trigger is the wrong object.** The clone's trigger is an L5 *fill*, and a
fill is positive proof that the passive opportunity at that price has just been
consumed. 94.5% of print-triggered target prices are marketable or at-touch at
the receipt instant; the price becomes postable only after the market moves away
(median 4.20 s, never for 20.2%), by which time L5 has already acted again
(median 2.00 s, 65.6% inside the postability wait).

L5's 83% maker share is not speed. It is **position in the causal order**: he
rests a quote derived from state, and the prints we react to are other
participants hitting *him*. A follower of his fills is structurally the taker of
the liquidity he provided.

### Where the next implementation change belongs

**Pricing policy and H1/H3 behavioural policy — jointly, and not latency.**

1. **Pricing policy (primary).** Stop pricing from L5's print. Price from our own
   book state plus our own running basis. This is the only change that moves the
   ceiling off ~5.4%.
2. **H1/H3 behavioural policy (primary, and inseparable from the above).** Quote
   from the verified drivers — price relative to our own basis, `opp_px`,
   `pair_cost` — so the order rests *before* flow arrives. Target ~**72%** maker
   share, the modeled breakeven, not 83%.
3. **Data transport / decision loop / submission path (not now).** CF1 bounds the
   total achievable gain from perfecting all three at ~5.4% maker share. Worth
   instrumenting so the waterfall stops being unmeasurable, but it is not the
   bottleneck and should not be funded as if it were.

### The cost of choosing this

It is a real trade-off, not a free win. Pricing from state **reduces
action-level fidelity to L5 by construction** — P36 already measured that a
maker-only translation reproduces **3.9%** of his actions. The programme has to
decide whether it is replicating L5's *actions* or his *economics*; on the
verified decomposition those are different objectives, and only the second is
profitable. That is a decision for you, not an inference I should make.

---

## 8. Sample-size cautions, stated plainly

- **The clone's 8.4% maker rate is 5 windows / 284 actions**, all in-sample by
  P36's own labelling. It is a **diagnostic signal requiring more shadow
  evidence**, not an established rate. The *mechanism* (94.5% of prints
  unpostable) rests on 16,620 prints and is far better supported than the rate.
- **83.4% is an upper bound** on L5's maker share, from an absence-based
  inference over a non-exhausted feed walk.
- **Everything maker/taker-dependent rests on four days** (2026-08-28…31).
- **Nothing here is realized.** Every fee and rebate figure is a modeled PMUS
  counterfactual on Polymarket behaviour.
