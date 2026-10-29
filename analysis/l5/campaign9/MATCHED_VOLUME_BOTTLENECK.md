# The largest avoidable loss of profitable completed pairs — BTC15m

Read-only, from the C6/C7 durable tapes and the C8 joined order record.
Reproduce with `analysis/l5/campaign9/stranding_cause.py` and
`analysis/l5/campaign9/order_slot_coverage.py`.

## 1. It is not stranding, and it is not completion pricing

Both candidates are now closed by measurement rather than argument.

**Stranded legs were quoted.** Across C6–C8, 16 windows, 7 ended with
unmatched inventory. Of those 7, **6 had submitted completion quotes while
the inventory was outstanding** (1, 1, 3, 9, 1, 4 quotes). Only one — C8
`0245z`, the single-order window — stranded with zero completion quotes.

This **corrects an earlier claim of mine** that 2 of 3 C8 strandings
submitted no completion quote. Under the classifier here, which counts a
submission as a completion quote when its side opposes net inventory at the
moment it was sent, the C8 count is 1 of 3, and 1 of 7 across all three
campaigns. The one genuine starvation is real but it is a single window.

**Completion pricing is a dead heat.** Settled break-even completion pair
cost is **0.9532**; achieved quantity-weighted was **0.9530**. The
counterfactual is already on record: at c=0.9532 the delta is −$0.0005, at
0.96 it is −$0.0877, at 1.00 it is −$0.6005. Quoting completions dearer to
win the race loses money at every price the book would actually have
reached. So the remaining completion quotes that did not fill were not
mispriced — paying more to fill them is negative.

## 2. It is order-slot idle time

| window | orders | covered | of clock | gap median | **submit cadence** |
|---|---|---|---|---|---|
| 0200z | 9 | 111.6 s | 12.4 % | 29.7 s | **34.1 s** |
| 0215z | 5 | 180.1 s | 20.0 % | 46.7 s | 101.1 s |
| 0230z | 7 | 178.2 s | 19.8 % | 30.2 s | **34.5 s** |
| 0245z | 1 | 17.7 s | 2.0 % | — | — |
| 0300z | 5 | 48.9 s | 5.4 % | 118.9 s | 97.4 s |
| 0315z | 5 | 112.4 s | 12.5 % | 29.8 s | 73.6 s |
| **total** | **32** | **649.0 s** | **12.0 %** | **30.2 s** | — |

**We had an order in the market for 12.0 % of the clock** — 649 s of 5,400 s
— and 32.7 % even of the span between the first submission and the last
terminal.

The constant is the **submission cadence, not the gap after a terminal**.
In the two windows that ran uninterrupted, submit-to-submit is 34.1 s and
34.5 s with sub-second dispersion, while dwell ranges 3.7–45 s. The engine
is not waiting for its order to resolve and then pausing; it is submitting
on a roughly fixed ~34 s beat and the order happens to resolve inside it.
That rules out dwell, fill latency and cancellation timing as the cause, and
it rules out the three loop periods already measured (rates 3.36 s, admitted
actions 14.08 s, shadow decisions 3.36 s) as the direct cause, since none of
them is 34 s or a divisor that would produce it.

**I have not identified the mechanism that imposes ~34 s, and I am not going
to keep hunting it from these tapes.** The C6/C7/C8 records hold transmitted
orders and terminals, not the per-evaluation refusal reasons, so the cause is
not recoverable from them. It is recoverable from the Campaign 9 decision
tape, which classifies every evaluation — and that is blocked (§4).

## 3. What the measurement says about the $5/window target

Taking the settled C8 economics and holding fill quality constant:

| | |
|---|---|
| matched surplus | **+$1.0184** over 11 pairs / 21.68 contracts / 6 windows |
| per pair | $0.0926 |
| per contract | $0.04697 |
| per window achieved | **+$0.1697** at 12.0 % coverage |
| per window at 100 % coverage, same clip | **+$1.4121** |
| multiple still needed for $5/window | **3.54×** |

So closing the idle time entirely — a perfect result, not a plausible one —
gets to about **$1.41/window**, under a third of the target. The remaining
3.54× has to come from size: clip 1.8 → **6.37 contracts**.

That does not fit the approved envelope. C8's worst simultaneous committed
cash was **$9.0833 of the $12 envelope** at a 1.8-contract clip and 12 %
coverage. Scaling the clip 3.54× takes that to roughly **$32** before any
coverage increase at all, against a $12 cash envelope and $20 exposure cap.

**$5 per BTC15m window is not reachable inside the currently approved risk
envelope**, at any coverage, with the fill economics we have measured. The
honest paths are a larger envelope, a materially better per-contract edge
than $0.047, or more markets — and the first two are owner decisions, not
engineering ones. I am not proposing any of them here.

## 4. The smallest justified improvement — and why it is not implemented yet

The candidate is: **close the ~34 s submission cadence** so order-slot
coverage rises toward the 32.7 % the active span already demonstrates, with
no change to clip size, pricing, concurrency or any risk limit. Coverage is
the only lever measured to be large (8.3× headroom) that costs nothing in
pair economics — unlike completion pricing, which is negative, and size,
which needs an envelope decision.

**It is not implemented because the mechanism is unidentified.** Changing a
timing constant I cannot name is not the smallest justified improvement; it
is a guess that spends a live campaign. The one input that resolves it is the
Campaign 9 decision tape — every evaluation with its refusal class — which
needs the C9 campaign document installed.

**Minimum safe way to unblock it:** the one-time owner mandate setup (§
`analysis/l5/authorization/MANDATE_AS_SOURCE_DELIVERY.md`). Generate the
Ed25519 keypair offline, register the public key, set
`L5_MANDATE_AS_AUTHORIZATION=1`. After that no campaign ever again needs a
nonce-bearing file hand-carried into Railway, and the 7-day standing mandate
covers the decision-tape campaign and its successors inside the same limits.
Nothing about this widens risk: every limit is compared against the running
configuration and arming fails closed if any is wider.

## 5. The ~34 s cadence: a code-grounded hypothesis, not a measurement

Stated so the Campaign 9 tape has something specific to confirm or kill.
The engine's submit path is **serial**, and three of its latencies are
constants in the deployed code or already measured:

| stage | period | source |
|---|---|---|
| main loop granularity | 3.0 s | `L5_LIVE_POLL_S` default, `l5_pmus_live_runner.py:6920` |
| order-state poll — how late a terminal is *noticed* | 6.0 s | `ORDER_POLL_S`, `:217` |
| admitted L5 action arrival | 14.08 s | measured, `GAP_MECHANISM_NARROWED.md` |
| measured order dwell (C8 median) | ~11 s | `c8_joined.json` |

Summed serially: 11 + 6 + 14 + 3 ≈ **34 s**, which is the observed cadence.
Under one-order concurrency each stage has to complete before the next
submission can be built, so they add rather than overlap.

**This is arithmetic that fits, not a demonstration.** I have not shown that
the slot actually waits on each stage in turn, and the fit would survive a
quite different mechanism with a similar total. Treat it as the hypothesis to
test, not a finding.

What makes it worth testing: if it holds, **two of the three waits are plain
configuration** — the order poll and the loop granularity — with no bearing
on clip size, pricing, concurrency or any risk limit. Lowering
`ORDER_POLL_S` and `L5_LIVE_POLL_S` would be the smallest possible
improvement to matched volume: a constant change, no new code path, no risk
envelope implication, and the coverage gain is directly measurable against
this 12.0 % baseline.

The confirming measurement is the Campaign 9 decision tape, which records
every evaluation with its refusal class, so the wait each submission actually
sat in is read off the record instead of inferred. That is the one thing
still blocked.
