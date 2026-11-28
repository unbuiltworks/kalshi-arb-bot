# Is L5's late cheap-complement accumulation executable on PMUS? Verdict.

Read-only, existing pipeline, on commit `88ce21d6`. No configuration changed,
no limit raised, nothing armed, crossing still unauthorized.

## Verdict in one line

**Yes — as a resting maker bid, and the edge is spread capture, not the cheap
pair. Crossing is NOT required and would destroy it. But the per-contract
economics are ~400x below the scale needed for $480/day.**

## 1. Marginal inventory economics of late complement purchases

45,405 late (t >= 450 s) purchases on the **deficient** side, split into
genuine completion (up to the deficit) and new opposite-side exposure
(beyond it), pro rata where a trade flips the imbalance.

The honest measure is the **locked** component, `matched x (1 - pair basis)`:
a matched pair redeems exactly $1.00 either way, so it is bankable the
moment the pair exists — independent of the outcome and of any instant's
price.

| split | kind | locked marginal value |
|---|---|---|
| TRAIN | completion, **cheap** (<0.25) | **+$24,974** |
| TRAIN | completion, dear (>=0.25) | **−$15,212** |
| TEST | completion, **cheap** | **+$44,764** |
| TEST | completion, dear | **−$41,400** |
| TRAIN/TEST | new exposure (cheap/dear) | +$1,828 / −$1,248, +$3,688 / −$2,889 |

**Cheap completions create certain value (+$4.25/trade); dear completions
destroy it (−$2.19/trade).** Consistent in both halves. New opposite-side
exposure is a minor term, so the effect is genuinely completion, not
re-opening.

**A correction I had to make mid-analysis.** My first pass valued the
residual at the price of the very trade being judged, which is circular —
buying at market price is neutral by construction, and the measure came out
exactly $0.00 everywhere. That near-miss matters, because it is also the
reason the "cheap pair" is **not** itself an edge: each leg was fair at its
own instant. What is real is that the pair they form pays a certain $1.00.

## 2. Maker execution feasibility below 0.25 — the decisive test

A displayed level is not a fill, so this requires the book to come **down to
our price** before counting anything. Source: `L5_SHADOW_DEFENSIVE_DECISION`,
6,179 samples over 93 window x variant series, carrying the engine's own
`maker_complement_own` (where it would rest, two ticks behind the touch) and
`exit_price_own` (the complement's ask).

| | |
|---|---|
| samples with a sub-0.25 maker completion price | **835** |
| TOUCHED — ask later <= our bid | 621 (74.4%) |
| **SWEPT — ask later < our bid** (conservative) | **582 (69.7%)** |
| never reached (unfilled) | **253 (30.3%)** |
| seconds until swept | p10 **7**, median **27**, p90 **117** |

| completion route, after real fees | pair cost |
|---|---|
| maker bid filled | median **0.5533** |
| crossed, including taker fee | median **0.5700** |
| **advantage of waiting** | **+$0.0167 / contract** |

**Maker completion is feasible and strictly better than crossing.** It is not
a case of "the maker quote failed, therefore we need to cross" — the maker
quote *works* about 70% of the time, quickly, and crossing gives up 1.67
cents a contract.

**Cost of the 30.3% that never fill:** the residual stays in place. Quoting
does not avoid it (median unmatched 1 contract in those samples). That is
the correct accounting and it is included in the expectation below.

## 3. The policy test — and why the 720 s cutoff is NOT the fix

I proposed extending `ts_complete_cutoff_s` past 720 s. **The data says that
was wrong.** Median time-to-sweep is 27 s and p90 is 117 s, while a bid
posted at 720 s still has **180 s** of window left. The cutoff already leaves
more runway than the p90 fill needs, so extending it buys almost nothing —
and it would have pushed orders toward settlement, against `cancel_margin_s`
(300 s) and the deadman. **No cutoff change is proposed, and no cancellation,
market-close or settlement-safety change is needed.**

What actually blocked us is narrower and already documented: in Stage A the
completion was approved and then refused **`WOULD_CROSS` 41 times**, leaving
**nothing resting at all**. The engine computes a completion price, finds it
would cross, and refuses — instead of resting at the maker side.

## 4. The first-leg decision — mostly NOT a stable signal

Net per matched contract by opening state:

| opening time | TRAIN | TEST |
|---|---|---|
| 11–30 s | +0.00465 | +0.01565 |
| 31–90 s | +0.01098 | +0.01947 |
| 91–300 s | **−0.00290** | **+0.04328** |
| >300 s | **−0.01113** | **+0.08141** |

**The late-opening buckets flip sign between halves** (small n: 22–85
windows). Opening *time* must not be used as a rule.

What is consistent: openings priced **0.35–0.65** carry the volume and a
positive net (TEST: +0.01725 at 0.35–0.45, +0.01274 at 0.45–0.55), and
openings that end with a **residual share above ~40%** are weak or negative
in both halves. Our configured band (0.20–0.60) already sits inside the
good region, so **the first leg is not the binding problem** — which is the
answer to whether an opening rule is needed alongside the completion rule:
on this evidence, not a new one.

## 5. The arithmetic closes by two independent routes

| route | per matched contract | per window | per day (96) |
|---|---|---|---|
| L5's own realized redemptions | **+0.01107** | +$9.09 | **+$872** |
| PMUS maker advantage (+0.0167) x sweep probability (0.697) | **+0.01164** | +$9.56 | **+$918** |

Within 5%, from entirely separate data — L5's on-chain redemptions versus our
own recorded PMUS books. **That is the strongest evidence in this report that
the mechanism is spread capture per matched contract**, and that it transfers.

## 6. Executable verdict

| question | answer |
|---|---|
| eligible opportunities | **835** sub-0.25 states, 93 series |
| with credible maker-fill evidence | **582 (69.7%)**, conservative (swept) |
| conservative net incl. unfilled | **+0.0167 x 0.697 = +$0.01164 / contract** |
| improvement vs current engine | current engine rests **nothing** on `WOULD_CROSS` (41 of 41 in Stage A), so the improvement is from **$0** to +$0.0116/contract expected |
| at Stage A size (~2 contracts) | **+$0.023/window ≈ $2.23/day** |
| at L5's volume (821 matched/window) | +$9.56/window ≈ **$918/day** |
| **plausibly scales to $480/day?** | **Not at our size.** It needs ~**430 matched contracts/window** against our current 2–17 — a **25x–400x** gap. That is a capital and limits question, explicitly out of scope here, and this rule does not address it. |

**So: the per-contract economics transfer and are confirmed twice over. The
volume does not exist. This rule is a prerequisite, not a path to the
objective, and it should not be sold as one.**

## 7. The one rule

**On `WOULD_CROSS`, reprice the completion to the best maker-side price that
still satisfies the pair ceiling and REST it, instead of refusing and leaving
nothing on the book.**

- Improves **acquisition quality**: it buys the completing leg at the maker
  price rather than not at all.
- Needs **no** limit, size, cutoff, concurrency or capital change, and **no
  crossing**.
- Out-of-sample support: 69.7% sweep within a median 27 s, +$0.0167/contract
  against crossing, on 835 recorded PMUS states.
- Its own failure mode is bounded and already accounted: 30.3% never fill and
  leave the residual exactly as it is today.

## 8. Smallest bounded validation

Still **no capital and no new service**: enable the repricing in the existing
shadow path only, and record, per `WOULD_CROSS` event, the maker price it
would have rested at, then score it against the book's subsequent motion the
same way this report does (swept / touched / never).

- **Success:** shadow sweep rate within the 60–75% band measured here, over
  >= 100 fresh events on windows not in this sample.
- **Failure, reported as such:** sweep rate materially below that, meaning
  the 69.7% is an artifact of the particular windows on the tape.
- Only after that would a bounded live probe be worth proposing, and it
  would be for the **repricing**, not for crossing and not for more size.

## 9. Not established

- The sweep test infers fills from book motion; it is **not** a print. We may
  still sit behind a queue even when the ask sweeps our level, so 69.7% is an
  upper bound on *our* fill rate, not a measured one.
- 835 eligible samples come from only 93 window x variant series on the tape;
  windows, not independent observations, are the unit, so treat the interval
  around 69.7% as wide.
- 65.5% of L5's windows remain excluded as unreconcilable, biased toward
  high-activity windows.
- L5's realized level is non-stationary (3.3x between halves); only the
  mechanism replicated.
- Nothing here is realized P&L for us. No order was submitted.
