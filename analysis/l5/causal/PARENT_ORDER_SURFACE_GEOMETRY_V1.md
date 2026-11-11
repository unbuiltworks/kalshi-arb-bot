# PARENT_ORDER_SURFACE_GEOMETRY_V1 — the shape of the latent quote surface

`analysis/l5/causal/parent_geometry.py` · `parent_geometry_v1.json`
**1,130 chain-proven parent orders** from 3,000 public fills · 1,600 receipts,
**0 missing**

The unit is the chain-proven parent order, never the fill fragment. The
question: **can proven parents reveal the size and price geometry of the latent
surface even though we never see an unfilled order?** They can, and the answer
is unusually concrete.

**Two limits that are never blurred.** `executed_qty` is what MATCHED — an
order for 200 that filled 100 and was cancelled is indistinguishable from an
order for 100, so every quantity is a **lower bound on order size**. And
first → last execution is an **execution-lifetime lower bound**, saying nothing
about submission or cancellation.

## 1. 100 is a real base lot — present everywhere, used most where shares are cheap

`executed_qty` modes: **100.0 (×225)**, 5.0 (×34), 20.0, 10.0, 80.0 (×25 each),
30.0, 99.99, 15.0, 25.0, 35.0. **18.94% of all parents executed exactly 100.**

Share exactly 100, **by probability** — the test that separates a base lot from
a regime artifact:

| p_up | share = 100 | median executed qty |
|---|---|---|
| ≤0.10 | **0.2511** | **59.0** |
| 0.10–0.30 | 0.1593 | 30.5 |
| 0.30–0.50 | 0.1864 | 25.0 |
| 0.50–0.70 | **0.1357** | **23.3** |
| 0.70–0.90 | 0.1269 | 26.7 |
| >0.90 | **0.3404** | **67.0** |

**It appears in every regime (0.127–0.340), so it is a base lot, not an
artifact** — but it is used **~2.5× more often at decided probabilities than in
the middle**, and median size follows the same U: 23–30 shares mid-book against
**59–67 at the extremes**.

That is the signature of **dollar-aware sizing**. A 100-share lot costs ~$5 at
p = 0.05 and ~$50 at p = 0.50. He reaches for the big round lot where shares
are cheap and works in ~25s where they are not.

**There is no single smaller base unit.** Multiples: 1 → 50.8%, 5 → 37.1%,
10 → 29.1%, 25 → 20.8%, 100 → 18.9%. Half the executed quantities are not even
integers, which is exactly what partial execution of round orders produces.

## 2. He builds price ladders, 1–3 ticks deep

**290** cases of distinct parents on the **same outcome within 5 s at different
prices**. Tick gaps: **1 tick ×113, 2 ×57, 3 ×37**, 0 ×16, 4 ×16, 6 ×8. Median
separation **3 seconds**.

A multi-level ladder is therefore **directly observed in parent orders**, not
inferred. It also retires the one test that appeared to contradict
`PASSIVE_QUOTE_SURFACE`: `L5_CAUSAL_MECHANISM_V1`'s P2 found only 30.5% of
same-side continuations at an identical price, which rules out a *single*
resting level — and the ladder is what stands in its place.

## 3. He quotes both outcomes at once, at a sub-$1 pair

**271** cases of **opposite-outcome** parents within 5 s. Median implied pair
cost **0.99**, and **67.9% of them are below 1.00**.

This is the most direct evidence yet for the two-sided surface: not "he buys
one side then later the other", but **two parent orders on opposite outcomes
inside five seconds, priced so the pair costs less than a dollar two times in
three.**

## 4. Cadence: a new parent every ~8 seconds

Gap between successive distinct parents in the same market: **median 8 s**
(p25 3 s, p75 21 s); **16.0% within 1 s, 38.4% within 5 s**.

**This is what remains of run persistence after fill fragmentation is removed.**
Continuation hazard on the collapsed unit runs 0.63 → 0.78, and parents arrive
every ~8 s, so a same-side run of four spans roughly half a minute of
re-quoting — a surface being refreshed, not a decision being repeated.

Execution-lifetime lower bound: **11.15%** of parents span more than one
timestamp, p50 **2 s**.

## The specification this implies

Stated as what the evidence describes, **not** as a policy and **not** as a
gate:

| element | observed |
|---|---|
| base lot | **100 shares**, plus a working range whose median is 23–30 mid-book and 59–67 at extremes |
| size rule | **dollar-aware** — the big lot appears where shares are cheap |
| depth | **ladder 1–3 ticks** behind the touch, built within ~3 s |
| sidedness | **two-sided**, opposite-outcome parents within 5 s, pair < 1.00 about 68% of the time |
| refresh | a new parent roughly every **8 s** |
| persistence | at least 2 s of execution life on the 11% that fill more than once — **a lower bound, not the order's life** |

## What is still missing

Unfilled and cancelled orders remain **`UNOBSERVABLE_OR_UNRESOLVED`**: every
parent here is one that *executed*. The surface's full width, the levels that
never traded, and how long any order actually rested are outside this dataset.
Prospective `L5_BOOK_CONTEXT_V1` is the cross-check — whether the public book
around L5's fills shows 1–3-tick structure and two-sided presence consistent
with this geometry — and that comparison is next.
