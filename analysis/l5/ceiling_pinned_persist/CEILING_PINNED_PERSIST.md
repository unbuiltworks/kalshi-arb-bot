# CEILING_PINNED_PERSIST — bounded SHADOW experiment

**No production change.** The arm is not imported by any production module, is
in neither the probe image nor the recorder's watch set, and nothing here is
proposed for deployment. Probe still serving `75edc4bd`; `392b5c0e` deferred;
`PMUS_WS_STATE_MAX_MARKETS` 1500; residual cap 6; maker-only; no taker.

---

## 1. The one changed behaviour, as a diff against the live ladder

CONTROL is `pmus_spring_l5_session._still_wanted.check()`, in its live order:

| # | branch | arm |
|---:|---|---|
| 1 | `bbo_unreadable` | unchanged |
| 2 | `book_one_sided` | unchanged |
| 3 | `price_bound_left` | unchanged (open/extend only; never COMPLETE) |
| 4 | `band_left` | unchanged (open only; never COMPLETE) |
| 5 | `would_cross` | **unchanged — and it still runs BEFORE 6** |
| 6 | `touch_moved` | **the only branch touched** |
| 7 | `l5_action_changed` | unchanged |

Branch 6 is suppressed **only** when all three hold:

1. the relation is COMPLETE (open and extend untouched);
2. the resting price was **pinned by the pair ceiling**, not chosen freely;
3. re-running the pricing function against the refreshed book returns the
   **same legal maker price**.

Anything else — a different legal price, a refusal, a free price, a different
relation — is CONTROL, unchanged.

**Unchanged and still able to end the order:** price, pair ceiling, size, band,
cap, admission, concurrency, settlement horizon, post-only status, campaign
deadline, risk breakers, maker-only execution. `would_cross` is evaluated
before the modified branch and is never suspended, so **holding cannot breach
post-only**. An unreadable or one-sided book cancels immediately under both
arms.

### Why the predicate is arithmetic, not a prediction

Under the `l5_touch` regime the ceiling-pinned price is

```
px = floor((ceiling − avg_cost) / tick) × tick
```

which **does not involve the touch at all** once the cap binds, and does not
involve the opposite ask unless the post-only trim binds. So "the re-quote
would recompute to the same price" is decidable from values already held. When
`avg_cost` moves — an EXTEND fills under a resting COMPLETE — the recomputed
price moves with it and the arm falls back to CONTROL with no special case.

`ceiling_pinned_price` **mirrors** `spring_complement_price` rather than
reimplementing it, and a test calls the real function on a 120-cell grid and
asserts agreement, so a drift in production turns the suite red instead of
quietly invalidating this study.

---

## 2. Results

Ceiling-pinning is decided the **exact** way on both corpora, from the held
average cost (`economic_max_px = ceiling − avg_cost`). The weaker
post-time-touch route was cross-checked against it and agreed on **259 of
260** orders; the exact route is used because it cannot be confused by a
post-only trim, whose price signature appears on 79 of the 260.

| | pre-cutover live sessions | post-cutover cohort |
|---|---:|---:|
| COMPLETE attempts | 260 (8 markets) | 193 (4 markets) |
| ceiling-pinned | **136 (52.3%)** | **51 (26.4%)** |
| chains the arm merges | 20 | 33 |
| **identical-price reposts eliminated** | **80** | 18 † |
| orders: CONTROL → ARM | **260 → 180 (−30.8%)** | 193 → 175 (−9.3%) |
| merged lifetime, observed (s) | p25 2.37 / **p50 5.94** / p75 15.36 / max 50.74 | not recorded |

† The cohort records no `rest_s`, so the deadline segmentation below cannot
bite and 18 degenerates to the naive count. **Treat it as an upper bound.** On
the corpus that does record rest time the naive count overstated the real one
by 31%.

### The correction that matters: the deadline still fires

A naive merge would say 116 reposts eliminated. That is wrong. **The arm
suppresses `touch_moved`; it does not change the rest bound.** Under R5FT
`rest_policy` is `model` with `rest_floor_s = 5`, and the observed median
`deadline` rest is **6.44 s**, so a held order still dies at the model's own
next re-evaluation and a fresh order is posted after it — which is CONTROL
behaviour, not something the arm removes.

Segmenting each chain's observed rest at that bound:

**116 naive → 80 real. The naive figure overstates the arm by 36 reposts
(31%).**

The four largest chains make the point: `lad-mia` ran four runs of 22–23
attempts at `0.575`, each with 47–51 s of cumulative rest. Merged naively each
is one order; segmented at the real bound each is ~8. Per market:

| market | attempts | pinned | chains | reposts cut | orders CONTROL → ARM |
|---|---:|---:|---:|---:|---|
| `aec-mlb-lad-mia` | 125 | 106 | 7 | **68** | 125 → 57 |
| `asc-lal-lev-fcb` | 19 | 8 | 1 | 5 | 19 → 14 |
| `aec-cs2-mzp-ezt` | 26 | 11 | 7 | 3 | 26 → 23 |
| `aec-setkameua-sapole-salvas` | 9 | 4 | 1 | 2 | 9 → 7 |
| `atc-epl-cov-bha` | 32 | 4 | 1 | 2 | 32 → 30 |
| `aec-nfl-buf-hou` | 31 | 2 | 2 | 0 | 31 → 31 |
| `aec-atp-jayfri-maxbas` | 10 | 1 | 1 | 0 | 10 → 10 |

### Fill classification — no fill is invented anywhere

| class | pre-cutover chains | post-cutover chains |
|---|---:|---:|
| OBSERVED_FILL | 1 | 1 |
| OBSERVED_NO_FILL | 7 | 19 |
| **COUNTERFACTUAL_FILL_UNKNOWN** | **12** | **13** |

`OBSERVED_FILL` means an attempt inside the chain actually filled — the merged
order was live at that instant with no worse queue position, so the fill is
observed, not imputed. `OBSERVED_NO_FILL` covers **only** the union of the
observed rest windows. Everything else is `COUNTERFACTUAL_FILL_UNKNOWN`.

### Termination boundary the merged order reached

| boundary | pre-cutover | post-cutover |
|---|---:|---:|
| deadline | 9 | 0 |
| filled | 1 | 1 |
| would_cross | 0 | 0 |
| **UNKNOWN** | **10** | **32** |

### Touch and book evolution while resting, where observable

Distinct touch values seen inside a chain (pre-cutover): 1 in 7 chains, 2 in 5,
3–4 in 3, 7 in 1, **10–11 in 4**. The touch moved up to eleven times under a
single price we never changed.

**The complement ask reached our price in 0 of 20 chains, and 0 of 33 in the
cohort — 0 of 53 in total.** In the four biggest `lad-mia` chains the closest
the complement ever came was **0.67 against our 0.575** — 19 ticks away at a
0.005 tick, across ~50 s of cumulative observation each.

---

## 3. What cannot be inferred, and why

Stated plainly because it is the load-bearing limitation of this experiment.

- **Would the held order have filled? UNKNOWN, and no replay can answer it.**
  The historical order was cancelled, so the book path after the cancel was
  never recorded against a live order. The extra time a merged order would
  have had carries *no observation at all* — not a fill, not a no-fill.
- **Queue position: UNKNOWN on both arms.** The arm would preserve queue
  position across what are currently separate posts, which can only help, but
  nothing we record measures queue depth. The direction is arguable; the
  magnitude is not measurable.
- **Trade flow against a resting order: NOT RECORDED.** The order tape carries
  book extrema (`min_opp_ask`, `max_touch`) but no trade prints per rest
  window.
- **The gaps between cancel and repost: NOT OBSERVED.** Nothing was resting
  there.
- **Order lifetime on the post-cutover cohort: NOT RECORDED** (`rest_s` absent).

### Matched inventory, residual, pair economics

| | delta |
|---|---|
| matched inventory | **0.00** |
| residual | **0.00** |
| pair economics | **0.0000** |

**Zero by construction, not a measured null result.** The arm invents no fill,
so it adds no matched contract and removes no residual in replay. These zeros
say nothing about whether the arm would help; they say the replay cannot
answer that question. They must not be read as "the arm has no economic
effect".

---

## 4. Persistence is not profit

Held separate, as instructed, and not blurred.

What this experiment establishes is **mechanical**: 52.3% of COMPLETE attempts
are ceiling-pinned, and 80 of them are cancels whose re-quote provably
recomputes to the same price, so the order is destroyed and rebuilt at the
same price against the same book for no reason. Removing that is a fidelity
and conversion change, and its effect on order count is measured.

What it does **not** establish is any profit. The prior forensics bound the
entire prize: the marginal pair on attempts that do not fill is **0.9952**,
total residual available to match is **32.77 contracts**, and even perfect
conversion yields **$0.1563** against a settled residual loss of $1.3408.
Covering that loss needs a pair of ≤0.9591; covering the account settlement
line needs ≤0.6183.

**Improved persistence is not expected profit and is not described as such
here.** It would become an economic claim only on filled-and-settled evidence,
and the evidence that would produce it does not exist yet because the orders
that would have generated it were cancelled.

One observation cuts against optimism and is reported rather than buried: in
**0 of 53 chains** did the complement ever reach our price over the observed
windows. On this corpus there is no instance of a fill that persistence would
have captured. That is not proof the arm is worthless — the observed windows
are short and the unobserved time is exactly where the question lives — but it
is the direction the available evidence points, and nothing here should be
read as expecting otherwise.

---

## 5. What a next step would need

To answer the question the replay cannot, an arm must actually rest. That is a
live change and is **not proposed here**. If it is ever authorised, the
pre-registered primary outcomes should be observational, not economic:

1. book polls per resting order (CONTROL median 1 on the pinned half);
2. whether the complement ask reaches the resting price within the model's own
   re-evaluation window;
3. COMPLETE fill rate on the ceiling-pinned half (CONTROL 0.74%);
4. and **only then**, matched contracts and pair cost at completion.

Reproduce: `python3 analysis/l5/ceiling_pinned_persist/run.py`
