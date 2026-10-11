# BTC5m bounded probe — spec frozen, **awaiting explicit live authorization**

**Nothing is armed. No order has been sent. I am stopping here for your authorization.**

This is an **EXECUTION CANARY**, labelled as such. We do **not** have a proven BTC5m
directional signal, and I am taking the option you offered rather than inventing one: the
preregistered analysis has not yet run on a single real tranche, and every BTC5m
directional study so far is negative by 13–40 points. **Its P&L will not be reported as
strategy evidence.** Its purpose is the execution/mechanics list in §7.

---

## 1 · The exact deterministic entry rule

Per BTC5m window, evaluate the recorder's own early checkpoints in ascending order and
take the **first** one satisfying **all** of:

| condition | value | why this and not something fitted |
|---|---|---|
| checkpoint | **≥ T+15s** | T+5s is excluded because the validated tape shows the book unpriced dust at both extremes there (`0.02 ×2501` / `0.98 ×2499`). That is a *mechanics* observation from the schema-validation tape, not a profitability selection. |
| structural state | `book_state == TWO_SIDED` | |
| economic state | **ECONOMIC_TWO_SIDED at q = 1, CUMULATIVE** — both sides can fill 1 contract | the preregistered definition, at the size being traded |
| price cap | **UP acquisition VWAP for q=1 ≤ 0.90** | the frozen policy's existing `HARD_MAX_PRICE`, reused rather than invented. This is also the binding cost control in §3. |
| book integrity | `book_integrity == OK` | never act on a book with a known sequence gap |
| staleness | `quote_age_s ≤ 2.0` | |

**Side: UP, always.** Deterministic and non-directional by construction — the
unconditional UP base rate is 55.64% with a 95% CI of **[47.2%, 64.1%]**, which straddles
50%, so "always UP" expresses no view. It is the **native** instrument (`outcome: yes`),
so it exercises the real order path without the derived-complement step, which keeps the
recorder comparison in §7 clean.

**Order:** capped marketable **LIMIT** at the recorded executable ask (Treatment T),
`makerOrCancel: false`, `timeInForce: good-til-cancel`, `quantity: 1`.
**One outstanding order at a time. One entry attempt per window. Hold to settlement. No
SELL. No averaging down. No size change of any kind — the size is fixed at q=1 for all
five attempts, preregistered and outcome-independent, so no martingale is expressible.**
Deadman/cancellation protection on. No discretionary selection: the first qualifying
checkpoint is taken or the window is NO_TRADE.

**If no checkpoint qualifies, the window is NO_TRADE** and does not consume an attempt.

## 2 · Minimum permitted quantity, and worst-case dollars per trade

From the venue's **own** contract metadata, recorded on 58 live BTC5m windows — not
assumed:

| field | value |
|---|---|
| `quantityMinimum` | **0.01** |
| `quantityIncrement` | **0.01** |
| `priceMinimum` | 0.01 |
| `priceIncrement` | 0.01 |
| `quoteAssetPrecision` | 2 |

So **q=1 is permitted and is 100× the venue minimum.** Worst case per trade at the 0.90
price cap:

| | q = 0.01 (venue min) | **q = 1 (proposed)** |
|---|---|---|
| principal at cap | $0.0090 | **$0.9000** |
| taker fee at cap `0.07·q·p·(1−p)` | $0.0000630 | $0.0063 |
| **worst case per trade** | **≈ $0.01** | **≈ $0.9063** |

**I propose q=1, not the venue minimum.** At q=0.01 the fee computes to $0.00006 and
rounds away at 2-decimal precision, so the probe could not test fee mechanics at all, and
a 0.01-lot order tests neither depth nor queue realistically. q=1 exercises fees, rounding
and depth while staying inside your envelope. I am flagging the trade-off rather than
choosing silently: q=0.01 would make the cohort cost ~$0.05 and answer less.

## 3 · Exact total cohort maximum loss and exposure

| | |
|---|---|
| attempts | **5 completed entries, maximum** |
| concurrent | **1** position/order at a time |
| per-trade worst case | **$0.9063** (price cap 0.90 + fee) |
| **cohort worst-case total principal + fees** | **5 × $0.9063 = $4.5315** |
| **cohort worst-case realized loss** | **$4.5315** — every entry settles worthless |
| your envelope cap | $5.00 principal, $5.00 realized-loss stop |
| **headroom** | **$0.47 under both** |
| realized-loss stop | **$5.00, immediate halt** — enforced, though unreachable before the 5-attempt cap |

The **0.90 price cap is what makes $5 binding-safe.** Without it a single entry near 1.00
would cost ~$1.00 and five would reach $5.00 with no margin.

## 4 · Execution SHA

`a3b1c9d4` — **placeholder; I will pin the real full 40-character SHA of this branch's
HEAD at the moment you authorize**, and report it before dispatch. A short ref is resolved
by `actions/checkout` as a branch and the probe step then skips silently; that has
happened once in this programme, so the pin is always the full 40 characters.

## 5 · Authorization ID required

Proposed: **`OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1`**

A **new** id is mandatory. `arming.EXPECTED_AUTH_ID` must equal the live grant for an
order to be constructible, and CI15M-1/2/3/4 are retired with the build asserting a
retired grant cannot be re-armed. Note this is a **different cohort from the PERSIST4 q1
BTC15m mechanics cohort**, which remains separately unarmed and still needs its own
authorization; authorizing one does not authorize the other.

## 6 · Recorder and executor concurrently — and the honest contamination answer

**They can run concurrently. The datasets are not automatically clean, and here is why.**

Separation that does hold:
- the recorder is **read-only public market data** with no credential, no signer and no
  order path — proved over the AST before every run;
- the executor is authenticated and writes to its own cohort ledger;
- separate processes, separate outputs, separate branches of the evidence tree.

**The contamination that is real:** a q=1 **crossing** order *consumes displayed depth*
and prints to the public trade stream. The recorder is watching that exact book. So our
own order appears in the L2 and trade rows the analysis reads. Tagging those rows is not
enough — the depth they show has already been altered by us.

**Declared mitigation, already written into the preregistration:** any BTC5m window in
which the probe submits an order is **excluded from the analysis dataset**. Five windows
out of a target ~108. The probe additionally preserves its own decision and submission
timeline with order ids and nanosecond timestamps, so the exclusion is verifiable rather
than trusted.

One further operational note: the probe must **not** be allowed to interrupt the recorder.
It runs as a separate job; the recorder's workflow has
`concurrency: cancel-in-progress: false` under its own group, so a probe dispatch cannot
cancel it.

## 7 · What every attempted trade preserves

decision timestamp → T+ / TTE → contract → strike → published `LRRR_XBT` → reference
distance `D_t` → full L2 both sides with sizes → chosen side → requested price/quantity →
order type → submission → ACK/reject → latency → resting duration → partial/full fills →
actual fill price → fees from the authenticated record → subsequent L2/reference
trajectory → settlement → redemption/cashflow.

And the comparison against the recorder **at the same instant**, which is the point of the
exercise: whether displayed depth was obtainable; slippage versus our modelled executable
price; rejection/rounding/min-size behaviour; maker vs taker classification from the
venue's own aggressor flag; latency between signal, book and submission; stale-book
problems; partial fills; cancellation behaviour; settlement/reconciliation problems.

---

## Stopping here

Per your instruction I am **not** sending the first live order. To proceed I need, in one
message: the authorization id, the confirmation that it is a **live money-moving**
authorization, and your acceptance of q=1 with the 0.90 price cap (or your correction of
either). On receipt I pin the full SHA, run the no-credential gate, and report before
dispatch.

**Accounting labels on anything this probe produces:** SUBMITTED → FILLED → SETTLED →
CASH_CONFIRMED → REALIZED, and nothing is called realized profit without the venue's own
cashflow record. **Its P&L is mechanics evidence, not strategy evidence.**
