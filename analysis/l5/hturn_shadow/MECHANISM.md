# H-TURN AS AN EXECUTABLE MECHANISM — SHADOW

**SHADOW.** Every number below describes the public fill tape of Polymarket
trader `l5zn1bwom8etsk` and a shadow mechanism replayed against it on archived
order books. Nothing here was **submitted, filled, settled or realized** for our
account. No order was placed, signed, previewed, amended or cancelled, and the
code is mutation-incapable by construction (§1). **This is not authorization to
trade and not a new strategy.**

Modules: `analysis/l5/hturn_shadow/{mechanism,shadow,runner}.py` →
`MECHANISM.json`. Tests: `tests/test_l5_hturn_shadow.py` (22 passing).
`src/polymarket_l5/action_model.py`, `analysis/l5/action_scorecard/`,
`analysis/l5/complete_vs_extend/`, `analysis/l5/residual_frontier/`,
`analysis/l5/hturn/` and `analysis/l5/book_shadow/` are **read, never written**.
`analysis/l5/historical_l2/bbo_offset.py` supplies the book reader, the 60 s
carry bound and `placement_consistency`; `src/l5_order_body.py` supplies the one
proven serializer. Nothing is duplicated.

---

## 0. The mechanism, as implemented

`FORENSIC.md` §3/§5 ends on a mechanism, not a feature: L5's `complete` is an
**arrival**. He runs a persistent same-side bid that fills as the market comes
to him — that is the run — and the turn fires when the **other** token's quote
crosses his resting complement bid. 94.3% of the clone's COMPLETE-called-EXTEND
error sits in the cell immediately after an `extend`; conditioned on that cell
not one of ten fill-history observables moves the mismatch rate off ~0.9,
standing imbalance is falsified at AUC 0.488, and every threshold override is
net-negative in rows.

So this does not predict the turn. It makes the turn impossible to miss:

> extend/rest one side, **simultaneously** maintain a fee-safe complement
> resting on the other token, and let **fills** determine the turn — updating
> inventory on **every partial fill**.

| component | implementation |
|---|---|
| the run | L5's **surplus-side** fills are mirrored as exogenous arrivals |
| L5's own turn | **not** mirrored. The shadow must find its turn or not have one |
| the complement | exactly **one** resting order, repriced when the surplus cash basis moves; never duplicated, never retried, never a second concurrent complement |
| complement price | `floor_tick(0.96 − surplus_cash_basis − fee_allowance)`, then clamped **strictly below** the complement's best ask |
| the turn | the first instant the complement's visible ask offers size at or below that resting price |
| partials | `Inventory.apply_fill` books **every** hit of any size; the remainder keeps resting |
| rebates / rewards | **$0**. There is no rebate or reward term in the module |

Economics are inherited, not invented. The pair ceiling is **imported** from
`src.l5_shadow.two_sided.ADAPTED_EXECUTABLE_PAIR_CEILING` (0.96) rather than
re-declared; near-$1 tolerance stays **LOCKED**. The surplus basis is **cash**
(`usdcSize / size`, so the fee L5 was actually charged is already inside it) and
our own leg carries an allowance of **$0.018/share** — the measured *maximum*
per-contract fee on the corpus (`POLYMARKET_FEE_SOURCES.json`), a bound read off
data, reported alongside a zero-allowance sensitivity arm and never tuned.

### Dependencies, stated as dependencies

1. a **two-token** per-second book — both outcomes of the same market;
2. the complement's **ask**, not a proxy for it;
3. a post-only placement that is **strictly below** that ask;
4. an assumption that our resting bid would have collected the same surplus-side
   arrivals L5 collected (§4 — this one is partly **false** and is measured).

---

## 1. The blocking gate

`mechanism.mutation_incapable_gate()` follows
`scripts/l5_exec1_boot_preflight.py` §2: it **FAILS** if
`src.l5_pmus_transport`, `src.pmus_q1_executor` or `src.pmus_micro_executor` is
importable. Those modules *are* on disk in this repository, so the gate is made
true by **construction**: `install_mutation_barrier()` puts a `sys.meta_path`
finder in front of the import system that refuses those names outright, and the
gate then verifies the refusal. `replay_window()` calls
`assert_mutation_incapable()` before touching data and raises `PermissionError`
if it does not hold.

`test_gate_fails_when_an_order_capable_module_is_importable` removes the barrier
and asserts the gate **fails**, and `test_replay_refuses_to_run_without_the_barrier`
asserts the replay refuses to run. "Cannot" outranks "will not".

---

## 2. Where the book comes from, and the honest N

The decisive artifact `FORENSIC.md` §4 asks for — both tokens' top-of-book over
the canonical scorecard's own windows (2026-03-27..04-21, 2026-08-28..31) —
**does not exist**. The only two-token L2 on disk is Zenodo 22084977
(`data/zenodo_22084977/`, 1 s full ladders, 2026-08-21..24, CC-BY-4.0) and it
overlaps the canonical sequence in **zero** windows. The realtime two-token
collector being built separately (`analysis/l5/book_shadow/`) was not touched.

The mechanism was therefore run on **every** window where both tokens' books and
L5's own fills exist at once, across all four families the archive covers,
because `CLAUDE.md` requires the engine to stay market-agnostic:

| family | windows | L5 fills | L5 turns |
|---|---|---|---|
| BTC 15m | 12 | 2,961 | 397 |
| BTC 5m | 23 | 132 | 26 |
| ETH 15m | 15 | 112 | 11 |
| ETH 5m | 15 | 52 | 6 |
| **total** | **65** | **3,257** | **440** |

A *turn* is the forensic's own unit: L5's first `complete` ending a same-side
run, labelled by `action_model._relation` imported unmodified. 440 turns is the
entire evaluable universe — against the 2,976-row turn cell the forensic
measured on the fill tape alone.

**Corroboration and its gap.** The chain backfill in
`ZENODO_OVERLAP_FINDINGS.md` found the same 12 BTC15m windows and **3,166** L5
fills; the activity corpus used here yields **2,961** for those windows, i.e.
**93.5%**. The missing 6.5% are fills the replay never sees, which biases
inventory and relation labelling by an unmeasured amount. Fill timestamps are
also **1-second**, so fills inside the same second are ordered arbitrarily and
a short run's `extend`/`complete` labelling can flip.

### REGIME — and this is not a footnote

Every evaluable window falls on **2026-08-21..22**, entirely inside
**R4 = REGIME_2026-08-19_ON** (`canon_regimes.json`). There are **zero**
evaluable rows in R1, R2 or R3.

> The pooled figure **is** the R4 figure. R4 is the regime where L5's
> ex-incentive baseline is **negative** (−$5,851, −30.4 bp), while the three
> earlier regimes were positive. So the only cohort in which this mechanism can
> be evaluated at all is the cohort in which the behaviour being copied did not
> pay — and no number in this document is current expectancy for anything else.

---

## 3. The unevaluable fraction

**27.7% of turns (122 of 440) are UNEVALUABLE.** An absent complement quote is
never a default.

| status | turns | share |
|---|---|---|
| `EVALUABLE` | 318 | 0.723 |
| `UNEVALUABLE_MIRRORED_SURPLUS_FILL_NOT_POST_ONLY_ACHIEVABLE` | 102 | 0.232 |
| `UNEVALUABLE_NO_FEE_SAFE_POSTABLE_COMPLEMENT_PRICE` | 15 | 0.034 |
| `UNEVALUABLE_COMPLEMENT_HAS_NO_ASK` | 3 | 0.007 |
| `UNEVALUABLE_COMPLEMENT_BOOK_BEYOND_CARRY` | 2 | 0.005 |

The book itself is barely the problem — these books are quoted continuously
across the whole 1 ¢ grid (`pre_fill_complement.json`: liquidity at 1,175 of
1,176 prices), so only 5 turns of 440 lack a usable complement quote. The
dominant exclusion is the **mirror**, not the book, and the cheap reading
"we cannot evaluate because we have no book" is **wrong here**: we cannot
evaluate because *we could not have had the inventory*.

---

## 4. The behavioural comparison, action by action

All figures: authorized arm (ceiling 0.96, fee allowance $0.018), 318 evaluable
turns. **P&L appears nowhere in this section by choice** — `CLAUDE.md`'s
validation standard says positive aggregate replay P&L with materially different
behaviour is not evidence of replication, and these legs are not filled, not
settled and not realized.

### 4.1 Side

| | n |
|---|---|
| shadow's complement side == L5's turn side | 217 |
| **disagrees** | **101 (31.8%)** |

The side is not free. The shadow rests the complement of **its own** surplus,
and because it does not take L5's completes its surplus drifts from his, so a
third of the time the mechanism is quoting the *other* token at L5's turn. That
is a real behavioural divergence, not a construction artifact.

### 4.2 Price — and this is the decisive number

| | value |
|---|---|
| median (shadow complement limit − L5's own complete price) | **−0.08** |
| p10 / p90 | −0.34 / +0.44 |
| shadow strictly cheaper | 242 / 318 |
| **L5's own complete price ABOVE our fee-safe target** | **240 / 318 = 75.5%** |
| L5's running pair cost at his turn, median | **0.9950** |
| … fraction ≤ 0.96 | **0.186** |
| … fraction < 1.00 | 0.576 |
| shadow pair cost if hit, median / max | 0.936 / **0.942** |
| shadow pair cost ≤ 0.96 | **318/318 — a construction invariant** |

> **Three quarters of L5's turns happen at a price our frozen fee-safe
> discipline forbids.** His turns cluster at a pair cost of ≈0.995; ours cannot
> exceed 0.96. The mechanism and L5 are not doing the same thing at the turn,
> and the gap is the *discipline*, not the model.

### 4.3 Fill timing

| | n |
|---|---|
| shadow's complement hit **inside** the run | 52 |
| hit only after the next run had begun | 19 |
| **never hit** | **247** |
| hit rate given evaluable | **0.164** |

Of the 52 hits, the timing delta (shadow minus L5, seconds):

| p10 | median | p90 | ≤ 0 | > 0 | \|Δ\| ≤ 1 s | \|Δ\| ≤ 5 s |
|---|---|---|---|---|---|---|
| −98 | **−12.5** | −1 | **52 / 52** | 0 | 7 | 10 |

**Every single hit is earlier than L5's own turn, none later.** Median run
duration is 9 s, so −12.5 s means the complement typically became fee-safe
obtainable *before the run even reached the point where L5 turned*. Under the
pure-arrival reading (A) — L5 turns when his resting complement gets hit — he
should have turned then too. He did not; he turned later and at ≈0.995.

> So on the only two-token book we have, H-TURN **as a fee-safe mechanism does
> not reproduce L5's turn timing**: when it fires it fires too early and too
> cheap, and 84% of the time it does not fire at all. That is not a refutation
> of H-TURN as a *description* of L5 — it is evidence that if L5's turn is an
> arrival, it is an arrival against a **near-par** complement bid, not a 0.96
> one.

### 4.4 Is the miss the book or the discipline? — the diagnostic arm

A third arm relaxes the ceiling to par. **It is labelled
`diagnostic_ceiling_1_00_NOT_AUTHORIZED` and `authorized: false` in the JSON;
near-par tolerance remains LOCKED for every deployable path.** It exists so the
attribution is measured rather than argued.

| arm | ceiling | fee allow. | evaluable | hit inside run | hit rate | median Δs | pair cost if hit |
|---|---|---|---|---|---|---|---|
| authorized | 0.96 | 0.018 | 318 | 52 | **0.164** | −12 | 0.936 |
| sensitivity | 0.96 | 0 | 328 | 63 | 0.192 | −13 | 0.955 |
| **diagnostic (NOT authorized)** | 1.00 | 0 | 334 | **107** | **0.320** | −12 | 0.993 |

Relaxing the ceiling to par **roughly doubles** the hit rate — so about half the
miss is the frozen discipline. The other half is real: even at par the mechanism
never gets hit on **two thirds** of L5's turns, and the timing of the hits it
does get does not improve (median still −12 s). **Unlocking near-par would buy
turn frequency and give back the entire matched-leg margin, and it still would
not reproduce his timing.** That is an argument for keeping the lock, not
against it.

### 4.5 Inventory trajectory and residual

| | L5 | shadow |
|---|---|---|
| time-weighted mean \|residual\| (median over windows) | 69.0 | 35.8 |
| peak \|residual\| (median over windows) | 105.5 | 100.0 |
| residual at window end (median) | +6.0 | −10.0 |
| matched quantity, total | 37,966 | **24,968 (65.8%)** |
| complement fills booked | — | **151, of which 34 partial (22.5%)** |

**The residual comparison is scale-confounded and must not be read as "the
mechanism carries less risk".** The shadow declines L5's completes and so books
only 65.8% of his matched quantity; a smaller book carries a smaller residual
for free. What *is* meaningful: 34 of 151 complement fills were partials, the
remainder stayed resting, and inventory stepped on every one of them — an
implementation booking only complete fills would have misstated residual on
22.5% of its own completion events.

### 4.6 The mirror, measured

| | turns |
|---|---|
| runs with **no** post-only achievable arrival (→ UNEVALUABLE) | 102 |
| turns with **at least one** non-post-only arrival | **248 / 440 = 56.4%** |

This is the §2 finding of `ZENODO_OVERLAP_FINDINGS.md` biting the mechanism:
26.4% of L5's BTC15m maker fills price at or **above** the prevailing ask — a
complete-set mint, which needs no offer on his own book and which a post-only
resting bid **cannot** do. More than half of L5's turns sit in runs that a
post-only mechanism could not have fully assembled. **The persistent-bid picture
is not the whole of how he acquires inventory**, and a replication that is
post-only everywhere is reproducing part of him by construction.

---

## 5. The fill model, stated not hidden

A resting bid is credited with the size offered **at or below** its own price in
the complement's visible ladder, at the first snapshot where that is positive.
This is **generous in our favour**: it ignores queue priority, assumes we are
first in line at our price, and assumes the offered size would have traded
against us rather than against someone else's equal or better bid. The depth is
**anonymous** — size at a price, never attributed to L5 or to any trader.

Consequences, in the direction they cut:

* the 0.164 hit rate is an **upper bound**;
* "every hit was earlier than L5's turn" is an upper bound on earliness;
* the shadow's 0.936 pair cost is the best case, not an expectation.

A pessimistic fill model would make the mechanism look worse, never better. The
headline conclusions therefore survive it.

---

## 6. What the missing book makes unanswerable

**Bluntly: the question `FORENSIC.md` §4 was written to settle is still open,
and nothing here closes it.**

* (A) exogenous arrival, (B) book-triggered decision, (C) latent private state
  remain **undistinguished**. This run adds one real constraint — if (A) holds,
  L5's resting complement sits near **par**, not at 0.96 — but a resting order
  that was not hit is still unobservable, so (A) and (B) are still the same row.
* **Zero overlap with the canonical scorecard windows.** None of the 2,976 turn
  rows the forensic measured is evaluable here. These 440 turns are a *different
  cohort*, two days wide, in one regime.
* **Zero evaluable rows in R1/R2/R3.** The eras where L5's economics stood alone
  — including `E1_PRE_REBATE`/`R1`, the designated confirmation cohort — have no
  two-token book at all. The mechanism cannot be tested where the behaviour paid.
* **No maker/taker flags and no order placement/cancel events.** `maker_taker`
  is `"unknown"` on the corpus and the venue does not serve L5's placements, so
  whether his turn fill was passive or aggressive is still unknown on every row.
* **1-second fill timestamps** against a book that changes ~24k events/s, with
  up to one 60 s resync interval of undetectable delta drift. A −12 s median
  delta is well outside that; a −1 s one is not.

What would change the answer is unchanged from the forensic: a per-second
two-token top-of-book series over the canonical sequence's **own** windows, or
maker/taker flags on those fills. The realtime collector in
`analysis/l5/book_shadow/` is the prospective version of the first and is the
right place to settle it; this document is what the archive can say in the
meantime.

---

## 7. Verdict

1. **The mechanism is implementable and structurally safe.** One maintained
   post-only complement, fee-safe by construction (318/318 ≤ 0.96, 0 crosses),
   inventory booked on every partial, and refusal rather than a default when the
   complement quote is absent.
2. **It does not reproduce L5's turn timing where it can be evaluated.** Hit
   rate 0.164 of 318 evaluable turns; of the hits, 52/52 fired *before* L5
   turned, median −12.5 s.
3. **The reason is price, not prediction.** 75.5% of L5's turns price above our
   fee-safe target; his pair cost at the turn is ≈0.995 against our hard 0.96.
   H-TURN may well describe him — but as a near-par mechanism, which is exactly
   the behaviour `CLAUDE.md` keeps locked.
4. **Relaxing the lock is measured and still fails.** Par doubles the hit rate
   to 0.320, surrenders the whole matched-leg margin, and does not improve
   timing. Keep the lock.
5. **56.4% of L5's turns sit in runs a post-only bid could not have assembled.**
   The persistent-bid story is incomplete regardless of the ceiling.
6. **N is 440 turns over 65 windows on two days, all of it R4**, the regime
   where L5's ex-incentive baseline is negative. 27.7% unevaluable. No row
   overlaps the canonical scorecard, and the three positive regimes contribute
   nothing.

Nothing here was submitted, filled, settled or realized. No rebate or reward was
credited. This is not authorization to trade.
