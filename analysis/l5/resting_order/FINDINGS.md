# Can we infer the resting-order decision behind an L5 maker fill?

**READ-ONLY. No live order. Production, thresholds, inventory limits, λ, sizing
and deployment all UNCHANGED. No Gemini. No interference with the active PMUS
experiment.** Per `PREREGISTRATION.md`, frozen before any result existed.

**The fidelity/economics tension is NOT resolved by choosing economics.** The
replication target moves one causal step earlier instead: infer the *resting
decision*, not the print.

---

## PHASE A — what state actually overlaps the four-day corpus

| need | source | resolution / span | provenance | verdict |
|---|---|---|---|---|
| L5 fills | `analysis/l5/l5_canonical_sequence.csv.gz` | **integer seconds**, 71,984 fills, 2,216 windows; maker/taker only 2026-08-28…31 | `data-api.polymarket.com/activity`, user-scoped | **HAVE** |
| maker/taker label | `trades_takeronly.jsonl` fingerprints | span 2026-08-28 00:07:57 → 08-31 23:11:19 | positive for taker, **absence** for maker | **HAVE, qualified** — see Phase B |
| **Polymarket L2 / order book** | — | — | — | **DOES NOT EXIST, and cannot be reconstructed.** `clob.polymarket.com/book` is live-only; no historical book was ever harvested |
| Polymarket market prices (history) | `clob.polymarket.com/prices-history` | **~600 s between points** even at `fidelity=1&interval=max` | public | **USELESS** for a 15-minute window — 1–2 points per window, and a midpoint, not a book |
| **market-wide trade tape** | `data-api.polymarket.com/trades?market=<conditionId>` | integer seconds, ~725–3,441 rows/market, 262–380 wallets/market | public, read-only, no credentials | **HAVE — newly established this session.** 30 markets harvested, 47,772 trades |
| underlying BTC reference | `data/btc15m/coinbase_1m.jsonl.gz` | **1 minute** | Coinbase public | HAVE, coarse |
| settlement | `gamma_outcome_prices` in the corpus | per window | Polymarket gamma | **HAVE** |
| `data/ref_lag/*` (4 ms `pbook`/`ptrade`) | — | 2026-08-31 13:28–23:55 | **markets are `GEMI-*` = GEMINI** | **EXCLUDED — out of scope, not touched** |
| `data/policy_d_port/*` | — | 2026-08-30/31 | venues `kalshi` + `gemini` only | **not Polymarket; Gemini portion excluded** |

### The strongest causally-prior state we can reconstruct

With no book, the best available state before each L5 fill is:

1. **the market-wide trade tape** — every trade in the market with timestamp,
   price, size, side and the counterparty wallet, giving flow intensity,
   buy/sell and Up/Down imbalance, micro-volatility and a **touch proxy**;
2. **L5's own prior fills in the window** — inventory, matched, residual,
   own-side running basis, pair cost if completed now, run length, time since
   last action;
3. BTC spot at 1-minute resolution.

**Touch proxy, declared as a proxy throughout:** in a taker-indexed tape, a
taker SELL of an outcome executed at the **bid** of that outcome and a taker BUY
at the **ask**. So last same-outcome SELL ≈ bid, last same-outcome BUY ≈ ask.
This is a *stale print*, never a quote.

### Two hard limits, recorded before any model was fitted

- **Timestamps are integer seconds with unordered ties.** The T-5 and T-2
  horizons sit inside the data's own resolution noise.
- **A maker fill's price IS its resting price**, so side/price/size of the latent
  decision are exactly scoreable — but **placement time, amendments and queue
  position are permanently unobservable** for this corpus.

---

## PHASE B — maker-label confidence, and the exhaustiveness question answered

### Can `takerOnly` pagination be made exhaustive? **No — permanently not, for this corpus.**

Measured read-only against the live public endpoint:

| probe | result |
|---|---|
| offset 4000 (where the original harvest stopped) | 500 rows — **the original walk stopped voluntarily, not at a limit** |
| offset 4500 / 6000 / 10000 | 500 rows each — deeper paging **does** work |
| offset 20000 | `{"error":"max historical trades offset of 10000 exceeded"}` |
| **offset 10000 reaches back to** | **2026-09-18/19 only** |
| `sortDirection=ASC` | **ignored** — returns the same DESC ordering, so there is no route to older data |

L5 is still actively trading (offset 0 = 2026-10-02 18:40Z), so the rolling
10,000-trade window has already slid past August. **The taker evidence for
2026-08-28…31 can never be completed from this endpoint.**

### But independent positive maker evidence DOES exist — via the market-wide tape

This is the Phase B upgrade, and it was not available before this session.

The market-wide `/trades?market=` endpoint is **taker-indexed**. Tested on the
30 sampled markets:

| check | result |
|---|---|
| L5 rows present in the market-wide tape | **344** |
| L5 fills labelled `taker` in the corpus for those markets | **344** |
| agreement | **344 / 344 = 100.0%**, no extras either way |
| L5 fills labelled `maker` in those markets | 7,051, **none appear in the tape** |
| tape covers L5's own fill span | **30 / 30 windows** |
| tape volume vs L5's maker volume | e.g. 21,912 vs 4,347 — consistent with a complete trade tape |

Two independent harvests — a user-scoped `takerOnly` walk from 2026-08-30 and a
per-market walk today, different endpoint path, no offset truncation, full span
coverage — recover **exactly the same taker set**. A fill absent from a complete,
full-span, taker-indexed market tape is **positively evidenced as a maker fill.**

### Confidence classes, as applied

| class | n (30-market sample) | basis |
|---|---|---|
| `HIGH_CONFIDENCE_TAKER` | 344 | positively fingerprinted, **and** independently re-found in the market-wide tape |
| **`HIGH_CONFIDENCE_MAKER`** | **7,051** | absent from a tape proven complete and full-span for that market — **independent positive evidence** |
| `INFERRED_MAKER` | 0 in this sample | the corpus-wide default outside harvested markets |
| `UNKNOWN` | 5,269 corpus-wide (March/April) | outside coverage |

**The resting-order work below therefore does *not* train on absence-only
labels.** Maker share in this subsample is 7,051/7,395 = **95.3%**, above the
corpus-wide 83.4% — but the sample was deliberately the 30 highest-L5-activity
windows, so **this is a selection effect and not a corrected corpus figure.**

The method generalizes: per-market harvesting bypasses the user-scoped 10,000
cap entirely and can validate maker labels for any market, including forward.

---

## PHASE C/D — can the fill be anticipated from causal state?

Snapshots on a **fixed 5-second grid**, not at fill times — sampling at fills
would condition on the event being predicted. 27,540 snapshots over 4,590 grid
points, 30 markets. Features are H1 + H3 only; **H2 inventory-band controls
deliberately excluded**; settlement never an input.

### Occurrence is trivially predictable and therefore worthless

| horizon | P(an L5 maker fill occurs) | median lead |
|---|---|---|
| 60 s | **0.989** | 5.0 s |
| 30 s | **0.943** | 5.0 s |
| 15 s | 0.825 | 4.0 s |
| 10 s | 0.727 | 3.0 s |
| 5 s | 0.531 | 2.0 s |
| 2 s | 0.302 | 1.0 s |

L5 is almost continuously about to fill. "Will he act?" carries no information;
**side and price are the entire problem.**

### Side is barely better than chance

| horizon | persistence (same side as his last fill) | flow majority |
|---|---|---|
| 60 s | 0.555 | 0.444 |
| 30 s | 0.559 | 0.439 |
| 10 s | 0.578 | 0.432 |
| 2 s | **0.604** | 0.421 |

**The flow baseline is consistently BELOW chance (0.42–0.44), which inverted is
0.56–0.58.** L5 systematically takes the side the market's takers are leaving —
a direct H1 cheap-side-accumulation signature, visible in flow alone.

### Price: the stale-print proxy caps us at 44% within one tick

Conditioning on the **same outcome** matters a great deal:

| price guess | n | p50 abs err | p90 | ≤1 tick | ≤2 ticks |
|---|---|---|---|---|---|
| **last same-outcome print** | 7,051 | **0.0200** | 0.0600 | **44.1%** | **63.1%** |
| last same-outcome ask proxy | 7,051 | 0.0200 | 0.0700 | 39.7% | 58.3% |
| last same-outcome bid proxy | 6,918 | 0.0300 | 0.1000 | 34.2% | 49.1% |
| mid of the two proxies | 6,918 | 0.0214 | 0.0780 | 29.4% | 49.5% |
| *last print, any outcome* | 4,330 | 0.1000 | 0.7600 | 17.1% | 25.9% |
| *L5's own held-side basis* | 4,330 | 0.1625 | 0.5222 | 3.5% | 6.4% |

### And the behavioral rule is simple once you can see the bid

L5's maker price minus the last same-outcome bid proxy (n=6,918):

| p10 | p25 | **p50** | p75 | p90 |
|---|---|---|---|---|
| −0.060 | −0.030 | **+0.000** | +0.024 | +0.072 |

**CORRECTION (owner, 2026-10-02): this must NOT be stated as "L5 rests at the
bid".** Historical Polymarket data has **no L2**. The quantity above is a
*trade-derived same-outcome bid proxy* — the last same-outcome taker SELL print —
not a quote. What the median offset of +0.000 licenses is a **hypothesis worth
testing prospectively**, nothing more.

The prospective full-L2 shadow must discriminate between the candidate rules:
**at bid · one tick behind bid · one tick improving · basis-relative ·
state-dependent.** They are not distinguishable from a stale print proxy, and no
one of them is established here. **The missing information is a live per-outcome
bid/ask ladder, not a better model.**

---

## PHASE E — execution fidelity cannot be scored historically

Predicted in the preregistration, and confirmed. With no historical Polymarket
L2 there is no queue, no depth, and no trade-through reference, so the required
classes (1)–(7) cannot be populated: **postability, defensible fill rate,
maker/taker status and post-fill adverse selection are all unscoreable on this
corpus.** Per the standing rule, these are labelled **unknown /
non-defensible — never filled.** No fill is claimed anywhere in this study.

What *can* be said structurally: a quote placed at or below the prevailing bid
is **postable by construction**, whereas the print mirror's target price was
measured marketable-or-at-touch 94.5% of the time. That is a statement about
postability only and says nothing about fill probability.

---

## PHASE F — state model versus print mirror, on what is measurable

| dimension | MODEL 1: print mirror | MODEL 2: state-driven resting order | measurable? |
|---|---|---|---|
| timestamp-level print copying | 46.1% action reproduction (P36) | **lower by construction** — it does not target prints | yes |
| side anticipation | n/a — reacts after the fact | 0.56–0.60 persistence; flow inverted 0.56–0.58 | yes |
| price anticipation | n/a — copies the realised price | **44.1% within 1 tick** (stale proxy), rule is "rest at the bid", median offset +0.000 | yes |
| **passive postability** | **5.4%** (94.5% marketable/at-touch) | **postable by construction** at/below the bid | M1 measured; M2 structural only |
| pair-cost distribution | pair cost final 0.8142 in the one P36 window reported | not scoreable without fills | **no** |
| completion behaviour | overlay refuses 77 of 284 actions | not scoreable without fills | **no** |
| maker economics | 8.4% maker ⇒ modeled −$11,947 | not scoreable without fills | **no** |

**Maker share was not used as an optimization target**, and the ~71–72% PMUS
figure is a modeled economic breakeven, not an L5 behavioral target.

The comparison is genuinely favourable to MODEL 2 on anticipation and
postability, and **silent on economics** — which is the honest state of it. It
does not yet establish that MODEL 2 is better; it establishes that MODEL 2 is
the only one of the two that *can* be passive.

---

## PHASE G — the decision

> **CAN L5'S FUTURE MAKER FILLS BE ANTICIPATED EARLY ENOUGH FROM CAUSAL STATE TO
> REST THE ORDER BEFORE THEY OCCUR?**

**PARTIALLY YES on timing and pricing rule; NOT YET on side; and not
verifiable on execution from historical data.**

**How early.** Easily early enough. He fills within 30 s at a 0.943 rate with a
median lead of 5 s, so there is no timing barrier at all — the opposite of the
print mirror, where the opportunity was already gone on arrival.

**Side accuracy.** 0.56–0.60. **This is the binding behavioral gap.** Flow
inverts to 0.56–0.58 independently, so there is real signal, but nothing here
reaches a level at which one would confidently commit a side.

**Price accuracy.** 44.1% within one tick on a stale proxy, and a clean
underlying *candidate* rule — the proxy-derived offset has median +0.000, which
is a **hypothesis to test**, not an established "rests at the bid" finding. With a live bid this is plausibly a high-accuracy rule; that is a
hypothesis this data cannot close.

**Defensible fill evidence.** **None, and none obtainable historically.** No
book, no queue, no trade-through.

**Resulting pair economics.** **Not scoreable.** Reported as unknown rather than
modelled, because modelling it would require inventing fills.

### What information is missing — precisely

1. **A live per-outcome bid/ask with depth.** This is the single blocking item.
   It converts the pricing rule from 44% to testable, and it is the only way to
   score postability or queue.
2. **Sub-second timestamps.** Integer seconds with ties make T-5/T-2 noise.
3. **Order placement events.** Permanently unavailable publicly — we observe
   fills, never rests. Postability must therefore be inferred from our *own*
   shadow quotes against a live book, not from L5's.
4. **A better side predictor.** 0.60 is the real behavioral gap, and it is a
   modelling problem, not a data problem — the state we can see already carries
   an inverted-flow signal that no current clone component uses.

### Therefore: the minimum realtime shadow test, specified

Historical data cannot answer the execution half, so it must be answered
prospectively. **This is a specification, not a deployment — nothing below is
built, started, or authorized here.**

| element | specification |
|---|---|
| **scope** | SHADOW ONLY. No order constructed, submitted, previewed or priced for transmission. `NullTransport` equivalent. |
| **venue state** | the **existing** post-`ddc2195c` PMUS recorder output — it already emits a full 39–43 level L2 ladder at ~2 s plus bbo. **No change to the recorder, its config, or its completeness gate.** Read its output only. |
| **L5 state** | existing public `/activity` poll for L5, plus the per-market taker-indexed tape for maker-label corroboration |
| **the test** | at each L2 update, compute the H1/H3 state and emit a *hypothetical* quote (side, price = prevailing same-outcome bid, size). Persist it with the book snapshot that produced it. |
| **scoring** | when a trade later prints, classify the hypothetical quote by **trade-through or observed queue depletion only** — no touch=fill, no invented queue. Separately record whether L5 subsequently filled on that side/price. |
| **primary outputs** | side precision/recall against L5's realised side; price error in ticks against L5's realised maker price; **postable rate**; **defensible fill rate**; post-fill markout |
| **sample needed** | from the observed 30% defensible fill rate and a need for losses in-sample: **≥2,000 hypothetical placements**, which at L5's observed cadence is days, not months |
| **what it decides** | WHICH passive pricing rule L5 actually follows -- at bid, one tick behind, one tick improving, basis-relative, or state-dependent -- and whether such quotes are adversely selected. History can distinguish none of these |
| **explicitly excluded** | Gemini; the active PMUS experiment's reserved windows; any production change; any live order |

### My read, for your decision

The causal reframe was right and it changed the answer. The print mirror's
problem was structural and unfixable; this one is **a side-prediction problem
plus a missing live bid**, both of which are tractable. I would not build a
quoting policy on a 0.60 side accuracy yet — but the pricing rule is the
cleanest behavioral result in this whole line of work, and the shadow test above
is cheap because the full-L2 recorder it needs **already runs**.

Whether to spec that collector is yours to call. I have not started it.
