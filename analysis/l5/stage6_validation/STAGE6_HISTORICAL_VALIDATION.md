# STAGE-6 HISTORICAL VALIDATION — the exact matched-pair protocol against every evaluable historical state

**CLASSIFICATION: `HISTORICAL_REPLAY`.** Nothing in this document is shadow,
submitted, filled, settled or realized. No order was placed, no credential,
signing path, Railway service, `live_exec*` module, A6, S3 or the running
`l5-book-shadow` collector was touched. Lane 4's files under
`analysis/l5/stage6/` were **read only and never written**. Harness:
`analysis/l5/stage6_validation/harness.py`; machine output
`RUN_<gate>_<fee-basis>.json` in this directory.

**NO PARAMETER FITTING.** No grid search, no threshold optimization, no
re-run-until-it-looks-better. Every policy number is injected from Lane 4's
frozen modules (or the standing fallback) and is reported with its source. The
decision grid is a fixed 15 s cadence over every market's entire life; no
state was selected on its outcome. Two gates and two fee bases are reported
**side by side** because they are two counterparties' stated economics, not
two tries at a better answer — and they agree on the verdict.

---

## 0. The headline, before any number is quoted

**Under the Stage-6 protocol the first leg is a passive maker order, and in
95.58% of evaluable historical states whether it would have filled is
UNKNOWABLE from the data we hold.** The archive is a *book* archive: aligned
ladders written on change, **no trade prints, no sequence numbers**. A resting
bid at price `p` placed when size `Q` already sat at `p` joins *behind* `Q`,
and nothing in a book-only feed reveals whether our own quantity traded.

So the primary answer to questions 1 and 2 is **INCONCLUSIVE**, and it is
reported that way rather than as an optimistic point estimate.

The repository's own frozen fill standard
(`src/polymarket_l5/owned_order_shadow.py`, `EVIDENCED_LEVEL_CONSUMED`)
supplies a *proxy* that collapses that 95.58% to 8.49% — and
**`analysis/l5/august_replay/S2_FINDINGS.md` §2 already documents why that
proxy is a selection bias**, not a solution. Honoring that document is the
point of this lane, so both bucketings are carried, never merged, and the
proxy numbers are never presented as a representative sample.

**Within the proxy's own adverse-selected subset the economics fail
decisively**: the protocol needs a 78.73% matched fraction to break even and
achieves 68.83% (95% CI, market-clustered, 66.76%–70.94%), for
−$0.0372 per first leg over 24,439 states. On L5's own coins it is worse:
59.06% matched, −$0.0873 per first leg.

**Verdict: this evidence does NOT support authorizing a first matched-pair
cohort.** §9.

---

## 1. Parameters actually bound — read this before citing anything

Lane 4's `analysis/l5/stage6/STAGE6_PROTOCOL.md` **had not landed** when this
ran. The frozen *modules* had, and the run binds to them:

| bound from | sha256 | what it supplied |
|---|---|---|
| `analysis/l5/stage6/bounds.py` | `40fd4e0d4b95edf701240f1a252565be630b3f9684dc3ca21e01674c24974c2c` | qty, prices, ceiling, TTE, counts |
| `analysis/l5/stage6/pair_economics.py` | `1d354cc504ea78b3fdbb94c25378d1cd7349683de2ac5e64824310630cdc4c41` | the fee-safe complement ceiling |
| `analysis/l5/stage6/spec.py` | S6P fingerprint `32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8` | recorded, `assert_frozen()` passes |
| `src/pmus_fee_schedule.py` | SCHEDULE fingerprint `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629` | every fee, effective-dated |

| parameter | value | source |
|---|---|---|
| `FIRST_LEG_QTY` | **1.00 share** | `bounds.py` — **not** the venue minimum 0.01 the mandate named as fallback |
| `MAX_FIRST_LEG_PRICE` | 0.60 | `bounds.py` |
| `PAIR_COST_CEILING` | 0.96, fee-inclusive | `bounds.py` ← CLAUDE.md spring discipline |
| venue tick / min qty / price domain | 0.01 / 0.01 / 0.01–0.99 | `bounds.py` |
| `TTE_CUTOFF_S` | 120 s, no placement inside it | `bounds.py` |
| complement re-quotes | **0** (one attempt, patience = waiting at one price) | `bounds.py MAX_COMPLEMENT_SUBMISSIONS = 1` |
| first-leg offset | 0 ticks, join the bid (maker-first) | not in `bounds.py`; S1's frozen offset 0 |
| maker rebate | **exactly $0.00** | PMUS cent floor: `round_half_even(0.0125·C·p(1−p), $0.01)` is $0.00 below 1.62 shares. At 1.00 share it is structurally zero. No gate, bound or economic test here depends on it. |

Two gates are reported because Lane 4's is stricter than a role-aware one:

* **`LANE4_TAKER_BOTH_LEGS`** — Lane 4's own `max_complement_price()`, which
  charges the **taker** coefficient on **both** legs. Strictly more expensive
  than anything the venue can charge a post-only pair. `0.96` becomes roughly
  `p₁+p₂ ≤ 0.925` raw. **This is the authoritative binding.**
* **`LANE5_ROLE_AWARE`** — leg 1 maker (no charge), taker fee only if the
  complement crosses. Looser, and reported only to show the conservatism's cost.

Two fee bases, because the evaluable states predate the schedule change:

* `STATE_TIMESTAMP` — 2026-08 states, so **taker θ = 0.06**, which is what the
  effective-dated schedule charges at those instants.
* `FORWARD_2026-10-07` — **taker θ = 0.0695**, what a probe authorized *today*
  pays. **This is the one citable for authorization.** The stale flat 6%
  constant is not used anywhere.

> **RE-BINDING IS REQUIRED BEFORE AUTHORIZATION.** `STAGE6_PROTOCOL.md` was
> absent and Lane 4's modules were mid-flight (`spec.py`'s pin moved during
> this session). These results are bound to the three sha256s above. If any
> moves, this validation must be re-run against the final fingerprint before
> it may be cited for authorization.

---

## 2. Inventory of evaluable historical states

Reusing, not redoing, the prior audits: `analysis/l5/hturn/L2_INVENTORY.md`
(22 candidate sources graded) and `analysis/l5/BOOK_ACQUISITION.md` (the
exhaustive on-disk and git-history search). Their conclusion holds and is
re-measured here: **two sources only.**

| | measured |
|---|---:|
| `data/zenodo_22084977/updown_15m.parquet` | 265 markets |
| `data/zenodo_22084977/updown_5m.parquet` | 471 markets |
| markets with **both** outcome tokens present | **736 / 736 (100%)** |
| markets excluded for a single token | 0 |
| snapshot rows, two-token markets | **407,251** |
| span (measured, UTC) | **2026-08-21T00:14:09Z → 2026-08-24T12:29:59Z** |
| coins | bnb, btc, doge, eth, hype, sol, xrp, zec |
| candidate decision states at a 15 s cadence | **42,654** |
| **evaluated states** | **28,062** |

`updown_4h.parquet` is absent from disk and irrelevant (L5 never traded 4h).
Every other candidate source in `L2_INVENTORY.md` is fills-only, the wrong
venue, one-token, or after L5's period; `clob.polymarket.com/orderbook-history`
stopped producing snapshots **2026-02-20**, before L5's span begins, and every
bulk-egress host answers 403 to CONNECT from this container. **So the
evaluable universe is 3.5 of L5's 166 span days — 2.1% — and that is a
ceiling, not a sampling choice.**

### Temporal resolution and known gaps — carried, not assumed away

| inter-snapshot gap (per token) | measured |
|---|---:|
| n | 405,779 |
| p50 / p90 / p99 | **1 s / 4 s / 13 s** |
| max | **361 s** |
| gaps longer than the 60 s carry limit | **291** |

A row is written only **when the book changes**, so an old row is current
state. It is carried at most **60 s** — the collector's own REST resync
interval — and **refused** beyond that, exactly as `hturn.py` refuses it. The
feed runs ~24k events/s with **no sequence numbers**, so a dropped delta is
undetectable and every read carries up to one resync of possible drift.

### Refusals, by cause (of the 42,654 candidates)

| cause | n |
|---|---:|
| `REFUSED_FIRST_LEG_PRICE_CEILING` (bid above `MAX_FIRST_LEG_PRICE` 0.60) | 11,268 |
| `REFUSED_CROSSED_BOOK` (the archive's documented crossed-book defect) | 1,908 |
| `REFUSED_STALE_BOOK` (no snapshot within 60 s) | 658 |
| `REFUSED_NO_BID` | 506 |
| `REFUSED_BELOW_MIN_PRICE` | 252 |
| **evaluated** | **28,062** |

Both outcome tokens of every market are evaluated as independent candidate
first legs. **No side-selection rule was applied** — a selection rule is a
parameter, and this lane does not choose one.

---

## 3. The three buckets — reported separately, never merged

### 3A. STRICT bucketing — queue-independence only. This is the primary result.

A verdict is admitted here only if it is **independent of queue position**:

* a passive bid is **provably not filled** if the touch never reached our
  price at any observed instant — no queue position could have filled us;
* nothing else about a passive bid is provable without trade prints;
* a **taker** cross is provable either way, because a taker consumes
  *displayed* depth and queue position is irrelevant to it.

| first leg (passive, maker-first) | n | fraction |
|---|---:|---:|
| `WOULD_FILL` | **0** | **0.0000** |
| `WOULD_NOT_FILL` (touch never reached our price) | 1,240 | 0.0442 |
| **`UNKNOWABLE` (queue-dependent)** | **26,822** | **0.9558** |

On L5's own coins (btc, eth; n = 6,872) the unknowable fraction is **0.9540**.

**The UNKNOWABLE bucket dominates at 95.58%. Under the strict standard this
validation is INCONCLUSIVE on questions 1, 2 and 3.** It is stated here rather
than buried, as the mandate requires.

The one channel the archive *can* prove, over all 28,062 evaluated states,
is the complement taken against displayed depth:

| complement by taker cross (queue-independent) | n | fraction |
|---|---:|---:|
| `WOULD_FILL` | 16,425 | 0.5853 |
| `WOULD_NOT_FILL` | 11,152 | 0.3974 |
| `UNKNOWABLE` (no snapshot in horizon) | 485 | 0.0173 |

That is a real, unbiased measurement — **but it is the second leg.** It cannot
rescue the first, and a taker completion is not what the protocol specifies.

### 3B. FROZEN-PROXY bucketing — the repo's own `EVIDENCED_LEVEL_CONSUMED`

The frozen standard calls a passive leg filled when the best bid falls
**strictly below** our price: the whole level left the book and the touch is
through it. Applied with the same three-bucket discipline:

| first leg | n | fraction | 95% CI (market-clustered) |
|---|---:|---:|---|
| `WOULD_FILL` (level consumed) | 24,439 | **0.8709** | — |
| `WOULD_NOT_FILL` | 1,240 | 0.0442 | — |
| `UNKNOWABLE` | 2,383 | **0.0849** | **[0.0724, 0.0993]** |

CIs are **market-clustered** bootstraps (2,000 resamples): overlapping
decision states inside one market are not independent observations.

### 3C. Why 3B's 87% is not good news — the LEVEL_CONSUMED bias, honored

`analysis/l5/august_replay/S2_FINDINGS.md` §2 states it exactly:
`EVIDENCED_LEVEL_CONSUMED` fires **iff the market has crossed our bid**, so it
"mechanically selects the subset of hypothetical maker fills whose
contemporaneous book movement went **against us**, and systematically excludes
benign fills." This run reproduces that condition verbatim, so **`WOULD_FILL`
here *is* that biased subset.** Three facts make the bias visible rather than
asserted:

1. **Time-to-level-consumed** (n = 24,439): p50 **9 s**, p75 29 s, p90 77 s,
   p99 **390 s**, max 767 s. The long tail shows `WOULD_FILL` is substantially
   *earned by horizon length* — i.e. by the market eventually moving against
   us — which is the bias itself, not a benign maker fill.
2. **A vanished level is also consistent with mass cancellation.** The proxy
   cannot distinguish "everything at `p` traded" from "everything at `p` was
   pulled."
3. **Snapshot granularity loosens `WOULD_NOT_FILL` optimistically**: a sweep
   through `p` that recovered between two snapshots is undetectable, so 4.42%
   is an upper bound on provable non-fills, not an exact one.

**Therefore nothing in §4–§7 may be read as an unbiased estimate of what a
real resting Stage-6 order would have experienced.** It is the arithmetic of a
known-adverse subset, and it is quoted only because it is the one place the
data permits arithmetic at all.

---

## 4. The four questions, answered

### Q1 — How often would a first leg become MATCHED at fee-inclusive pair cost ≤ $0.96?

| standard | answer | UNKNOWABLE fraction |
|---|---|---:|
| **STRICT (primary)** | **INCONCLUSIVE — not estimable** | **95.58%** of first legs |
| FROZEN PROXY, Lane 4 gate | **68.83%** of provable first legs, 95% CI **[66.76%, 70.94%]** (16,822 / 24,439) | 3.16% of that subset; 11.14% of all evaluated states are in some UNKNOWABLE bucket |
| FROZEN PROXY, role-aware gate | 74.07%, CI [72.13%, 76.00%] (18,102 / 24,439) | 3.23% |
| FROZEN PROXY, Lane 4 gate, **L5 coins only** | **59.06%** | 2.86% |

Every matched pair cleared the ceiling by construction: under Lane 4's gate the
realized pair cost is p05 **0.93**, p50 **0.93**, p95 0.95, max **0.95** — i.e.
the fee-safe gate costs ~3 ticks of headroom against the nominal 0.96, exactly
as `pair_economics.py` says it would. Fee basis makes no difference to the
matched fraction, because the matched legs are maker fills and carry no venue
charge; the forward θ = 0.0695 rating is what tightens the gate.

### Q2 — How often would it instead REMAIN RESIDUAL?

| standard | answer |
|---|---|
| **STRICT (primary)** | **INCONCLUSIVE** |
| FROZEN PROXY, Lane 4 gate | **28.01%** `RESIDUAL_PROVABLE` (6,845) + **3.16%** `UNKNOWABLE_COMPLEMENT` (772) |
| FROZEN PROXY, role-aware gate | 22.70% + 3.23% |
| FROZEN PROXY, **L5 coins only** | **38.08%** + 2.86% |

Plus, over all 28,062 evaluated states: 4.42% `NO_FIRST_LEG_PROVABLE` (no
position taken, no exposure) and 8.49% `UNKNOWABLE_FIRST_LEG`.

### Q3 — How long would residuals PERSIST? (full distribution)

Time from the first leg's fill instant to a provable complement fill, Lane 4
gate, maker complement (n = 16,822):

| p50 | p75 | p90 | p95 | p99 | max |
|---:|---:|---:|---:|---:|---:|
| **19 s** | 54 s | **150 s** | 249 s | **451 s** | **699 s** |

Taker complement (n = 14,106): p50 **42 s**, p75 99 s, p90 214 s, p95 312 s,
p99 530 s, max **758 s**.

**Right-censoring is 31.17%** (6,845 of 21,951 provable-first-leg pairs never
provably matched inside the horizon). Censored observations are **reported,
never imputed**: the true persistence distribution has ~31% of its mass at
"never, within one market's life," and the percentiles above describe only the
pairs that did complete. For a 15m contract the practical ceiling is the
window itself minus the 120 s TTE cutoff — a residual that has not matched by
then is carried to settlement, which is what Q4 prices.

### Q4 — WORST EXPOSURE

**Worst-case residual dollars.** Arithmetic, and it matches Lane 4's bound:
`FIRST_LEG_QTY × MAX_FIRST_LEG_PRICE = 1.00 × $0.60 = $0.60`. The measured
worst single residual notional is **$0.600000** and the measured worst single
residual loss at settlement is **−$0.600000**. Lane 4's `MAX_POSSIBLE_LOSS_USD`
is therefore **confirmed empirically, not merely asserted** — no evaluable
state produced a residual outside it.

**Residual loss distribution** (provable residuals, n = 7,148, valued at the
archive's settled `winner`): p01 −$0.59, p50 **−$0.28**, p99 −$0.01, mean
**−$0.2774**, and **the loss rate is 1.000 — every single provable residual
lost money.** That is the LEVEL_CONSUMED bias in its purest form: the first leg
"fills" *because* the price crossed down through us, so the token we bought is
the one that fell, and it settles worthless far more often than chance. It is
**not** a forecast of a real cohort's loss rate; it is a demonstration that the
proxy's fill rule and its outcome rule are biased in the *same* direction.

**Worst-case drawdown path.** Constructed to match the protocol's own
discipline — one market at a time, one probe per market, the first evaluable
state in each, chronological, n = 727:

| | USD |
|---|---:|
| final cumulative | **−5.71** |
| **maximum drawdown** | **13.22** |
| curve minimum / maximum | −12.00 / +1.22 |
| longest consecutive losing-probe streak | **3** |
| deepest-drawdown state | `eth-updown-5m-1787489100`, side Up, p₁ 0.49, `RESIDUAL_PROVABLE` |

So a 727-probe serial cohort at 1.00 share would have drawn down **$13.22**
against Lane 4's **$0.60** per-session bound. The per-session bound holds; the
*cumulative* bound does not exist yet. `MAX_DAILY_REALIZED_LOSS_USD = $0.60`
is justified in `bounds.py` by "one probe per authorization, no automatic
repeat" — true for probe #1, and **the first thing that stops being true when
a cohort is authorized.** A cohort needs a cumulative loss bound, and this
measurement sizes it.

---

## 5. The economic decomposition — why the verdict is what it is

Lane 4 gate, forward fee basis, provable-first-leg subset (n = 24,439):

| | n | total USD | mean USD |
|---|---:|---:|---:|
| `MATCHED_PROVABLE` | 16,549 | **+1,240.57** | **+0.07496** |
| `RESIDUAL_PROVABLE` | 7,148 | **−1,982.98** | **−0.27742** |
| `UNKNOWABLE_COMPLEMENT` *(not evidence; shown for completeness)* | 742 | −167.19 | −0.22532 |

A matched pair locks about **+$0.075**. An unmatched residual costs about
**−$0.277** — **3.7×** the locked gain. So:

> **Break-even matched fraction required: 78.73%.
> Observed: 68.83%, 95% CI [66.76%, 70.94%].
> The confidence interval excludes break-even.**

On L5's own coins (btc, eth) the observed matched fraction is **59.06%** and
the replay expectancy **−$0.0873 per first leg** — materially worse than the
all-coin pool, which matters because btc/eth is the forward-relevant category.

Replay expectancy per provable first leg, every binding, for the record:

| gate | fee basis | matched frac | mean USD/leg | total USD |
|---|---|---:|---:|---:|
| LANE4_TAKER_BOTH_LEGS | FORWARD θ=0.0695 | 0.6883 | **−0.0372** | −908.14 |
| LANE4_TAKER_BOTH_LEGS | state θ=0.06 | 0.6883 | −0.0372 | −908.14 |
| LANE5_ROLE_AWARE | FORWARD θ=0.0695 | 0.7407 | −0.0374 | −914.23 |
| LANE5_ROLE_AWARE | state θ=0.06 | 0.7407 | −0.0374 | −914.23 |

All four agree. **Loosening the gate does not help**: it buys a higher matched
fraction at a thinner locked gain, and the two cancel. Neither does the fee
basis, because the maker rebate is structurally $0.00 and the matched legs
carry no venue charge. **No rebate assumption was permitted to rescue these
economics, and none could have: $0.00 is $0.00.**

`LABEL: HISTORICAL_REPLAY_ONLY — NOT SHADOW, NOT SUBMITTED, NOT FILLED, NOT
SETTLED, NOT REALIZED.`

---

## 6. Regime split

Canonical change points: **2026-04-25, 2026-06-23, 2026-08-19**. The split was
computed from each state's own timestamp rather than asserted, so the result is
a measured fact:

| regime | candidate states | evaluated states |
|---|---:|---:|
| `REGIME_PRE_2026-04-25` | **0** | **0** |
| `REGIME_2026-04-25` | **0** | **0** |
| `REGIME_2026-06-23` | **0** | **0** |
| **`REGIME_2026-08-19_ON`** | **42,654** | **28,062** |

**Every evaluable historical state we possess falls on 2026-08-21..24, i.e.
entirely inside `REGIME_2026-08-19_ON`.** The "pooled" block and the
`REGIME_2026-08-19_ON` block are therefore **numerically identical** — stated
rather than duplicated as though it were a second population, the same
treatment `L2_INVENTORY.md` §0 gives the same fact.

Consequences, both load-bearing:

* **Nothing here is advertised as pooled expectancy**, because there is no
  pool. Every number above is already regime-current.
* **The three earlier regimes cannot be validated at all.** Not "were not" —
  *cannot be*, with the data we hold. Any claim about pre-08-19 Stage-6
  behavior has zero evaluable states behind it.
* `analysis/l5/residual_frontier/OOS_RESULT.md` §3 finds L5's own
  ex-incentive baseline **negative** in exactly this regime (−$5,851,
  −30.4 bp). This lane's negative result is **consistent** with that, which is
  worth noting and is not independent confirmation of it.

---

## 7. Leak and hygiene controls

* **No hindsight settlement in any fill decision.** `winner` is read only to
  *value* a residual, after every fill verdict is already fixed, and is never
  passed to a classifier. The classifiers (`passive_bid_verdict`,
  `taker_cross_verdict`) take a book and a price and have no settlement
  argument available to them by construction.
* **Strictly forward reads.** Every verdict reads only snapshots with
  `captured_at` strictly greater than the placement instant and ≤ the horizon.
* **Strict temporal ordering across legs.** The complement is evaluated from
  the instant the first leg could have filled, never from the decision instant,
  whenever the first leg has a fill instant.
* **Stale reads refused, not carried.** 60 s limit; 658 refusals.
* **Crossed books refused.** 1,908 refusals, per
  `analysis/l5/data_quality/CROSSED_BOOK_DEFECT.md`.
* **Clustered inference.** Overlapping states in one market are not
  independent; all CIs are market-clustered bootstraps.
* **No fitting.** No parameter was chosen to improve a result. The two gates
  and two fee bases are two counterparties' stated economics and two schedule
  dates, all four reported, all four agreeing.

---

## 8. Limits — what this does NOT establish

1. **It does not establish a Stage-6 fill rate.** 95.58% of first legs are
   queue-unknowable. The 87% proxy rate is adverse-selected by construction
   (§3C) and its 9 s median time-to-consumption shows the mechanism.
2. **It does not establish an expectancy.** −$0.0372/leg is the arithmetic of a
   known-biased subset. Its sign is informative — the bias runs *against* the
   policy, so an unbiased sample would plausibly be less bad — but its
   magnitude is not an estimate of anything.
3. **A vanished level is not a proven trade.** Mass cancellation is an
   untestable alternative here.
4. **2.1% of L5's span, 3.5 days, one regime, one venue.** The archive is
   Polymarket **international**; the fee schedule, the rebate cent floor and
   the tick/qty grid are **Polymarket US**. The two are different venues with
   different liquidity, different incentives and different queue dynamics.
   This is a **cross-venue** validation, and that is a first-order limitation,
   not a footnote.
5. **No L5 behavioral comparison.** This lane asks what *our* protocol would
   have done, not whether it matches L5 action-by-action. The CLAUDE.md
   validation standard ("at this exact market state, what did L5 do next, and
   what would our clone have done?") is **not** met by this lane and is not
   claimed to be.
6. **Partial fills are not modelled.** Legs are all-or-nothing at 1.00 share.
7. **Censoring, not imputation.** 31.17% of residual durations are right-
   censored; no distribution was fitted through them.
8. **The 15 s cadence is a cadence, not a claim.** Overlapping states are
   correlated; clustering handles inference, not the fact that 28,062 states
   come from 736 markets.
9. **`STAGE6_PROTOCOL.md` was absent.** Bound to three module sha256s (§1);
   re-binding is required before authorization.
10. **One probe per market in the drawdown path** is the protocol's own
    discipline, not the only possible cohort shape. A denser cohort would have
    a different path.

---

## 9. Verdict

**This evidence does NOT support authorizing a first matched-pair cohort.**
Three independent reasons, each sufficient on its own:

1. **Evidentiary.** The protocol's first leg is a passive maker order and the
   data cannot determine whether it fills in **95.58%** of evaluable states.
   The archive has no trade prints and no sequence numbers. On the question the
   authorization actually turns on, the validation is **INCONCLUSIVE**, and the
   repo's own frozen proxy is a documented selection bias rather than a fix.
2. **Economic.** Where arithmetic is possible at all, it fails: break-even
   needs **78.73%** matched, the observed fraction is **68.83%** with a 95% CI
   of **[66.76%, 70.94%]** that excludes break-even, and on L5's own coins it
   falls to **59.06%** (−$0.0873/leg). The cause is structural, not marginal:
   a matched pair locks ~**$0.075** while an unmatched residual costs
   ~**$0.277** — **3.7×**. The $0.96 ceiling, once made fee-safe, simply does
   not buy enough upside to pay for the residuals it leaves behind. No gate
   loosening helps, and the rebate is $0.00 by cent floor.
3. **Risk-sizing.** The per-session bound of **$0.60** is confirmed
   empirically. The *cumulative* bound does not exist: a 727-probe serial
   cohort draws down **$13.22**. `MAX_DAILY_REALIZED_LOSS_USD = $0.60` is
   derived from "no automatic repeat," which is precisely the assumption a
   cohort retires.

**What this does NOT say.** It does not reject the L5 program, and it does not
prove the Stage-6 protocol is unprofitable — claim 1 forbids claim 2 from being
that strong, and the bias runs against the policy. What it says is that **the
historical L2 we hold is structurally incapable of authorizing a passive
maker-first protocol**, and that the one economic signal it can produce is
negative in the forward-relevant regime and worse on the forward-relevant
coins.

**The gating constraint is evidence about queue position, and only two things
produce it:** owning the orders (the zero-capital `owned_order_shadow` /
`l5-book-shadow` path, which is already running and is the correct next rung),
or a trade-print-bearing archive (every route to which is 403 from this
container — `BOOK_ACQUISITION.md` §4). **Historical L2 replay has been taken as
far as it goes. More of it will not change this answer.**

---

### Provenance

| artifact | path |
|---|---|
| harness | `analysis/l5/stage6_validation/harness.py` |
| machine output, authoritative binding | `analysis/l5/stage6_validation/RUN_LANE4_TAKER_BOTH_LEGS_FORWARD_2026-10-07.json` |
| other bindings | `RUN_LANE4_TAKER_BOTH_LEGS_STATE_TIMESTAMP.json`, `RUN_LANE5_ROLE_AWARE_{FORWARD_2026-10-07,STATE_TIMESTAMP}.json` |
| bias document honored | `analysis/l5/august_replay/S2_FINDINGS.md` §2 |
| frozen fill standard reproduced | `src/polymarket_l5/owned_order_shadow.py :: classify_fill` |
| inventory audits reused | `analysis/l5/hturn/L2_INVENTORY.md`, `analysis/l5/BOOK_ACQUISITION.md` |
| fee schedule | `src/pmus_fee_schedule.py`, fingerprint `ecc240bd…91629` |
| Lane 4 modules bound (read-only) | `analysis/l5/stage6/{bounds,pair_economics,spec}.py` |
