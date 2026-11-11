# L5 — four-policy timestamp-causal replay of the eight-window campaign

`src/l5_strand_policy_replay.py` · `tests/test_l5_strand_policy_replay.py` (11
tests) · frames in `data/l5_strand_replay/residual_books_20261025.jsonl`.

**Nothing deployed, rearmed, or enabled. No taker execution path was written.**
The pre-send crossing guard is unchanged and still undeployed.

## Anchor

The replay's no-policy baseline reproduces the **realized** campaign net of
**+$0.1256** exactly — the same delta as `realized_today` moving
−11.6213 → −11.49570000. This is asserted as a test. A replay that cannot
reproduce the past is not evidence about the future.

## Evidence base

290 `L5_RESIDUAL_BOOK_SAMPLE` frames the live engine emitted while inventory was
unmatched — full authenticated ladders plus a `taker_walk` the engine sized to
the exact residual. Coverage: 0030z 58, 0100z 7 (two episodes), 0115z 43 (two),
0130z 73, 0145z 47. **0045z has zero frames: both legs filled in the same
instant, so no residual ever existed and no completion decision can arise.**

My ladder walk reproduces the engine's own `taker_walk` VWAP to **six decimal
places across 100+ frames** (asserted as a test). That cross-check caught a real
error in my first pass: the touch is *not* the price. Several frames do not hold
the whole residual on the top rung — one reports a VWAP of 0.513789 against a
touch of 0.51 — so crossing is priced on a walk sized to the full residual, not
on the touch.

### What is modelled, and which way each assumption cuts

- **Latency.** A decision at frame *t* executes at the first frame ≥ *t* + λ,
  against *that* frame's ladder. λ swept including the **8.818 s this window
  actually logged**.
- **The order is an IOC limit, not a market order.** If the book has left the
  limit it fills partially or not at all. **It does not chase.**
- **Partial fills carry.** Whatever does not fill stays residual and settles as
  such. No policy is credited with a fill it could not get.
- **Maker completion is never simulated.** Where a window actually completed as
  a maker, its realized fill and pair cost are used as-is. So the replay only
  ever decides whether to *pre-empt a known-good fill* — which makes it **harsh
  on taker completion in the windows that completed**, and scores 0130z against
  its realized total loss. Both are the honest directions.

---

## Result 1 — Selective taker completion: **REJECT**

This is the well-powered finding.

| latency λ | net | vs baseline | per-window outcome |
|---|---|---|---|
| 0.000 s *(physically impossible)* | +$1.1190 | **+$0.9934** | all five FILLED |
| **0.500 s** | **−$0.1570** | **−$0.2826** | 0115z, 0130z `BOOK_LEFT_THE_LIMIT` |
| 1.000 s | −$0.1570 | −$0.2826 | same |
| 2.000 s | −$0.1570 | −$0.2826 | same |
| 5.000 s | −$0.1570 | −$0.2826 | same |
| **8.818 s** *(observed)* | −$0.0445 | −$0.1701 | 0145z also misses |

**The policy fills where it destroys value and misses where it would create
value.** That is not a tuning problem; it is the shape of the opportunity.

### 0130z — the window the policy exists to rescue is unreachable

```
lam=0.000  decide 01:32:56.651  limit own 0.3300 -> exec 01:32:56.651  FILLED               total +0.0635
lam=0.500  decide 01:32:56.651  limit own 0.3300 -> exec 01:33:03.007  BOOK_LEFT_THE_LIMIT  total -1.1594
lam=1.000  decide 01:32:56.651  limit own 0.3300 -> exec 01:33:03.007  BOOK_LEFT_THE_LIMIT  total -1.1594
lam=8.818  decide 01:32:56.651  limit own 0.3300 -> exec 01:33:10.595  BOOK_LEFT_THE_LIMIT  total -1.1594
```

The next observable frame after the decision is **6.36 seconds later**, by which
time the ask had moved 0.33 → 0.34. A limit at 0.33 cannot fill against an ask
of 0.34. **At every latency above half a second, 0130z still strands for the
full −$1.1594.**

This retires my own earlier "+$1.2242 swing": it required **zero** latency.

### 0145z — the mandatory counterexample is pre-empted

Realized: maker completion at pair 0.87 after 5m23s, **+$0.2028**.

| λ | horizon | outcome |
|---|---|---|
| 0.0 | none | CROSS 01:52:14.277 at pair 0.9300 → **+$0.0792** |
| 1.0 | none | CROSS 01:52:14.277 at pair 0.9156 → **+$0.1016** |
| 8.818 | none | misses — "saved" only by being too slow |
| any | **120 s** | **RETAINED, +$0.2028** |
| any | 60 s | RETAINED, +$0.2028 |

An unhorizoned rule crosses ten seconds before the better maker fill and
**costs $0.10–0.12**. A ≤120 s horizon protects it — but then the rule never
fires usefully anywhere (net exactly baseline). **Safe and useful are mutually
exclusive here.**

### The EV-of-waiting gate cannot rescue it either

| P(maker completes) | net | crossings |
|---|---|---|
| 0.50 – 0.90 | −$0.1570 | 5 |
| 0.95 | +$0.0283 | 2 |
| 0.96 | +$0.1127 | 1 |
| **≥ 0.98** | **+$0.1256 (= baseline)** | **0** |

The gate only begins refusing at **Pc ≥ 0.95** and is fully inert at **≥ 0.98**.
To make the policy safe you must assume maker completion is ≥98% likely — at
which point it never crosses. L5's own data puts completion at **76.2%** at this
opening price. **Harmful below 0.95, inert above 0.98, nothing in between.**

Fees were charged on the published formula throughout (`θ_taker = 0.0695`),
e.g. 0130z: gross +$0.0935, fee $0.03, net +$0.0635.

---

## Result 2 — Maximum opening price: **REJECT as overfit**

| threshold | net | vs baseline | refused | worst-case leg |
|---|---|---|---|---|
| 0.50 | +$0.9608 | +$0.8352 | 0030z@0.55, 0100z@0.75, 0130z@0.62 | $0.8300 |
| 0.55 | +$1.2506 | +$1.1250 | 0100z@0.75, 0130z@0.62 | $0.8855 |
| **0.60** | **+$1.2506** | **+$1.1250** | 0100z@0.75, 0130z@0.62 | $0.8855 |
| **0.65** | **+$0.0912** | **−$0.0344** | 0100z@0.75 | $1.1594 |
| 0.70 | +$0.0912 | −$0.0344 | 0100z@0.75 | $1.1594 |
| 0.80 | +$0.1256 | +$0.0000 | — | $1.1594 |

**Non-monotonic in its own threshold, and at 0.65 it is worse than doing
nothing.** Only **1 of 8 episodes** sits above 0.60, so a single observation
sets the parameter. That is the signature of overfitting.

*Correction to my previous note:* the figure is **+$1.2506**, not +$1.2850. The
replay found 0100z's second episode opened DOWN at own **0.75** and completed
profitably (+$0.0344); the cap refuses that too.

### Adverse scenarios

**A — had 0130z completed at 0.95 like its neighbours** (baseline becomes
+$1.3785): the cap returns +$1.2506, i.e. it **costs $0.1279**.

**B — had a second window stranded at own 0.48** (0115z-B; baseline −$1.1512):
the cap gives −$0.0262. It catches the 0.62 leg and provides **no protection at
all** against the 0.48 one.

### And the statistic behind it cannot be validated on PMUS

The owner's instruction was explicit, and the answer is that it **cannot be
done with available data**:

- `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl` — **2 windows** (1815z, 1830z
  on 2026-09-27), 132 bbo + 108 book rows, plus 59 `error` and 61 `book_error`.
- `data/policy_d_port_ext/tape_*.jsonl` — 17 files, 17 MB, but **Kalshi
  KXSOL15M / KXXRP15M**, a different venue and series.
- Our own PMUS evidence is **8 windows, 7 pairs, 1 strand.**

So the L5 opening-price completion curve (2,216 windows, BTC/ETH 5m+15m)
remains **unvalidated for PMUS and for our depth-2 maker ladder**. I am not
treating it as transferable, and that is itself the argument for preferring a
price-agnostic policy.

---

## Result 3 — Risk-adjusted opening size: **RECOMMEND**

| opening notional cap | net | vs baseline | worst-case leg |
|---|---|---|---|
| $0.50 | +$0.3929 | +$0.2673 | $0.50 |
| $0.60 | +$0.4286 | +$0.3030 | $0.60 |
| $0.70 | +$0.4471 | +$0.3215 | $0.70 |
| **$0.80** | **+$0.4503** | **+$0.3247** | **$0.80** |
| $0.90 | +$0.3850 | +$0.2594 | $0.90 |
| $1.00 | +$0.2850 | +$0.1594 | $1.00 |

**Improves at every budget tested. Smooth, broad optimum, no cliff, no
threshold to overfit.** Worst-case single-leg inventory loss equals the budget
*by construction* rather than by luck.

At $0.80 it trims only four legs, and only one materially:

```
0030z  own 0.55 q 1.61  notional $0.8855 -> x0.9034
0045z  own 0.50 q 1.66  notional $0.8300 -> x0.9639
0115z  own 0.48 q 1.68  notional $0.8064 -> x0.9921
0130z  own 0.62 q 1.87  notional $1.1594 -> x0.6900
```

### Adverse scenarios

**A — had 0130z completed**: costs **$0.0637** (4.6% of that scenario's P&L) —
a small, bounded insurance premium.

**B — had a second window stranded at own 0.48**: **+$0.3348.** It protects
**regardless of opening price**, which the price cap cannot.

---

## Result 4 — Combinations

| policy | net | vs baseline | worst-case leg |
|---|---|---|---|
| P1 0.60 + P2 $0.80 | +$1.2159 | +$1.0903 | $0.80 |
| P1 0.60 + P2 $0.60 | +$0.9942 | +$0.8686 | $0.60 |

Higher point estimates, but they inherit P1's cliff and its dependence on a
single observation. **Not recommended as the first change.** If the price cap is
ever validated on PMUS data, this combination is where to revisit it.

---

## Honest statement of power

The campaign contains **exactly one strand**. Any policy's measured gain against
stranding is therefore n = 1, and the +$1.1250 and +$0.3247 figures above are
single realizations, not expectations.

What **is** well-powered is the **rejection of taker completion**: it rests on
290 authoritative frames and five crossing decisions, and its failure mode — the
limit is unreachable once latency is real — does not depend on how many windows
stranded.

---

## The smallest evidence-supported change

**A worst-case opening-notional cap.** It only ever *reduces* size.

```
On any submission classified OPEN -- a leg that increases |prospective
imbalance| -- with own price p and intended quantity q:

    q_cap   = L5_OPENING_NOTIONAL_BUDGET_USD / p
    q_final = min(q, floor(q_cap, 0.01), OPENING_LEG_MAX_CONTRACTS)
    if q_final < venue minimum:  REFUSE the opening leg
    else:                        place q_final

    candidate  L5_OPENING_NOTIONAL_BUDGET_USD = 0.80
    (use 0.60 for a tighter worst case at slightly lower expected gain)
```

Properties that make it the smallest defensible step:

- **Never increases size or exposure**; unset or unreadable means inert.
- **Does not apply to COMPLETE or REPLACE legs** — those reduce residual and
  must never be throttled, which is what stranded 0130z in the first place.
- **No new action type.** No taker path, no sell path, no limit raised.
- **Worst-case single-leg strand loss ≤ the budget, deterministically** —
  $0.80 against the realized $1.1594.
- **Price-agnostic**, so it does not rest on the completion curve I could not
  validate on PMUS.

### Does it preserve profitable delayed completions?

**Yes, all seven.** It changes no completion logic, no TTL, no cutoff, no quote
depth, and no complement pricing. 0145z's five-minute maker completion is
retained in full and arrives at the same pair cost 0.87, on a clip of 1.56
(unchanged — its notional of $0.546 is already inside the budget). The only
effect anywhere is a smaller opening clip on four legs.

### Remaining implementation and authorization requirements

1. Implement as a sizing reduction in the two-sided probe's opening path, keyed
   on `submission_class() == "OPEN"`. Not written yet.
2. Tests mirroring the existing opening-size tests, plus a mutation check that
   removing the cap changes behaviour.
3. A graded row in `src/l5_runtime_config_verify.py` so a variable edit without
   a restart surfaces as a disagreement rather than silently not applying.
4. Add `L5_OPENING_NOTIONAL_BUDGET_USD` to `SNAPSHOT_ALLOWED`.
5. **Owner action:** set that one variable. No other variable changes.
6. For any next campaign: a **new experiment id** (the current OPEN budget is
   16/16 spent and must not be reused), a fresh approval JSON and deadline.
7. **No taker execution is to be written or enabled.** On this evidence it
   should not be authorized at all.

**Explicitly not proposed:** taker completion, residual sell exits, dynamic
order mirroring, larger submission budgets, any increase in capital or limits.
