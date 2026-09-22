# P9-D — which Gemini family can pay a sequential inventory trader?

**Date:** 2026-09-22 · **Controlling audit: `ed4573b`** · **No live orders.
Nothing armed. No rebate term entered any selection.**

Per the audit: **raw trades, no mirrored-twin deletion, no capture thinning**,
`tid`/`timestampms` carried through, every fill labelled HYPOTHETICAL and kept
apart from the venue's 56 confirmed maker fills.

```
python3 analysis/gemini/p9d_families.py   <out.json>            # live slate
python3 analysis/gemini/p9d_families.py   <out.json> settled    # resolved slate
python3 analysis/gemini/p9d_report.py     <pull.json> analysis/gemini/p9d_spreads.json
python3 analysis/gemini/p9d_pricespace.py <pull.json> analysis/gemini/p7_bulk_tape.json
python3 analysis/gemini/p9d_markout.py    <pull.json> analysis/gemini/p9d_spreads.json
```

Event-level output for independent recomputation:
`analysis/gemini/p9d_events/btc_arms_events.jsonl` (4,643 hypothetical fills,
one row each, carrying arm, spread, skew, leg, price, the print that filled it,
aggressor, evidence tier, TTE, lane and split).

---

## The ranking cannot be produced, and the reason is the finding

**A Gemini contract's `/v1/trades` feed is not one price series. It interleaves
the contract's own price space with its complement's, and a row cannot be
assigned to a side without a contemporaneous book.**

The test needs no venue call and no book. Take consecutive prints sharing a
timestamp **to the second** — a real price cannot move in zero time — and
classify each pair as SAME (`|Δp| ≤ 0.02`), MIRROR (`|p₂ − (1−p₁)| ≤ 0.02`) or
OTHER. Prices in 0.45–0.55 are excluded, where p and 1−p are indistinguishable.

| source | family | same-second pairs | SAME | **MIRROR** | OTHER |
|---|---|---|---|---|---|
| live slate | Sports · Pro Baseball | 3,979 | 42.3% | **56.0%** | 1.7% |
| live slate | Politics · Presidential | 3,361 | 47.2% | **51.7%** | 1.1% |
| live slate | Sports · College Football | 1,687 | 48.1% | **50.8%** | 1.1% |
| live slate | Sports · Pro Football | 4,482 | 68.5% | **29.8%** | 1.7% |
| live slate | Economics · Macro Indicators | 1,195 | 69.2% | **30.3%** | 0.5% |
| BTC corpus | BTC 1h | 4,238 | 69.7% | **20.1%** | 10.1% |
| BTC corpus | BTC 15m | 1,963 | 63.3% | **20.1%** | 16.6% |
| BTC corpus | BTC 5m | 585 | 43.8% | **28.7%** | 27.5% |

On the slate the distribution of same-second price changes is **essentially
{0, mirror} with ~1% in between**. No price process produces that. The natural
reading is that the feed reports both legs of a match — a YES buyer at p and a
NO buyer at 1−p — each in its own price space, which also explains the 3:1
dominance of `buy` labels (45,712 to 15,054): both counterparties bought.

Two supporting observations, same direction:

* **62–100%** of a contract's distinct prices have their mirror also present,
  and **42–100%** of contracts print across both halves of [0,1]. A
  Presidential contract's tape spans 0.806 of the price range in a few dozen
  trades.
* The P8 "mirrored twins" were the same phenomenon seen at one instant. The
  audit was right that they are distinct executions with distinct `tid`s — a
  venue assigns an id to each leg — and right that deleting one was wrong. What
  they were evidence *of* is this.

### What that invalidates, and what survives

| quantity | needs a side assignment? | status |
|---|---|---|
| prints/min, $/min, duration, trading days, contract counts | no | **valid** |
| quoted spread (from `bestBid`/`bestAsk`) | no | **valid** |
| two-sided *maker-fill* frequency | **yes** | **not computable** |
| time to opposite-side print after first fill | **yes** | **not computable** |
| achievable matched-pair margin | **yes** | **not computable** |
| orphan frequency and orphan settlement P&L | **yes** | **not computable** |
| conditional completion probability after a first fill | **yes** | **not computable** |
| markout per hypothetical maker fill | **yes** | **not computable** |

So the eleven ranking criteria split: four are measurable now, seven are not.
Producing the ranking anyway would be inventing the sides.

**What it means for P8 and P9-A/B/C/F.** Those read the BTC tape as one space,
where the contamination bound is 20–29% of same-second pairs. A decoding error
manufactures complements that were never offered and pairs that never existed —
it biases **toward** finding an edge. Every conclusion in those rounds is
negative, so they are conservative and survive; but any positive figure inside
them (DEV +7.62, the matched-P&L column) is unreliable in a direction that
flatters it.

A live cross-check was run and is **inconclusive rather than contradictory**:
of 132 prints from the last hour compared with their contract's current book,
53.0% sit inside it as printed, only 3.8% only-if-mirrored — but 41.7% sit
outside it either way, because an hour-old print against a current book is
unmatchable. The book must be recorded *beside* the tape, not fetched after.

---

## What is measurable: the habitat, decoding-free

| category | subcategory | contracts | settled | prints | prints/min | $/min | median life (min) | trading days | spread |
|---|---|---|---|---|---|---|---|---|---|
| Weather | Daily Temperature | 11 | 0 | 79 | **0.75** | **7.89** | 2,280 | 1 | 0.040 |
| Economics | Economics | 48 | 0 | 486 | **0.25** | **7.04** | 436,980 | 1 | 0.010 |
| Crypto | Solana | 24 | 0 | 367 | 0.02 | 0.27 | 10,770 | 4 | 0.030 |
| Crypto | Bitcoin | 45 | 0 | 1,437 | 0.01 | 0.17 | 10,770 | 4 | 0.030 |
| Crypto | XRP | 22 | 0 | 645 | 0.01 | 0.37 | 1,500 | 4 | 0.030 |
| Sports | Pro Baseball | 247 | 30 | 7,722 | 0.01 | 0.27 | 263,520 | 6 | 0.020 |
| Sports | Pro Football | 466 | **130** | 10,848 | 0.00 | 0.06 | 196,721 | 14 | 0.030 |
| Politics | Presidential | 116 | 0 | 5,675 | 0.00 | 0.03 | 1,411,921 | 5 | 0.010 |

Two things are plain without decoding anything:

1. **Outside short-duration crypto, these are slow markets.** Median contract
   lives run from days to years and prints per minute round to zero. A
   sequential inventory trader needs a second fill inside one session; a market
   printing 0.00 per minute over a 49-day span cannot supply one.
2. **Almost nothing on the live slate is settled** — Pro Football 130 of 466,
   Pro Baseball 30 of 247, essentially zero elsewhere — so even with a
   decodable tape, family P&L could only be computed for two sports lanes. A
   settled-slate collection mode now exists (`p9d_families.py … settled`) for
   when it can be used.

---

## Adaptive vs symmetric quoting, on the one lane dense enough to test it

This is reported because the owner asked for it specifically and because BTC is
where the tape is densest — **subject to the 20–29% decoding caveat above**.
Arms share one acquisition model; only the quoting changes. `S` is the P9-F
baseline (quote once at the first print, never move it); `A0` re-centres on
every print with one print of latency; `A1` adds "stop quoting the side you are
already long"; `A2` adds a 2¢ skew toward the side you need; `A3` adds crossing
to complete when the resulting pair costs ≤ 1.00.

| lane | split | arm | paired | orphan | P(2nd given 1st) | median pair margin | matched $ | orphan $ | **net $** | RoC |
|---|---|---|---|---|---|---|---|---|---|---|
| 5m | dev | S | 11.5% | 88.5% | 60.5% | **+0.280** | +5.04 | −19.21 | −14.17 | −14.9% |
| 5m | dev | A3 | **48.3%** | 51.7% | 48.3% | +0.070 | +5.03 | −18.46 | −13.43 | −13.1% |
| 5m | val | S | 9.6% | 90.4% | 60.2% | +0.280 | +2.24 | −23.10 | −20.86 | −38.0% |
| 5m | val | A3 | 29.4% | 70.6% | 29.4% | −0.030 | −1.53 | −20.25 | −21.78 | −42.1% |
| 15m | dev | S | 14.9% | 85.1% | 83.0% | +0.060 | +1.74 | −47.81 | −46.07 | −26.0% |
| 15m | dev | A3 | **75.3%** | 24.7% | 75.3% | −0.030 | −10.51 | −14.52 | **−25.03** | −13.5% |
| 15m | val | S | 12.5% | 87.5% | 82.5% | +0.060 | +1.20 | −33.81 | −32.61 | −22.2% |
| 15m | val | A3 | **71.1%** | 28.9% | 71.1% | −0.030 | −6.92 | −12.91 | **−19.83** | −13.9% |

**Adaptive quoting does exactly what it was supposed to and still loses.**
Pairing rises from 12–15% to 71–75% on BTC15m and the loss roughly halves
(−46.07 → −25.03 DEV, −32.61 → −19.83 VAL) — but the median pair margin falls
from the quoted spread to **below zero**. Re-centring pairs far more often, at
prices that cost more than a dollar, because the price moved between the two
fills. That is adverse selection stated in the accounting: the static quote
earns its spread on the rare pair and bleeds on orphans; the adaptive quote
stops bleeding on orphans and starts bleeding on pairs.

Every arm, every lane, every split is negative pre-rebate.

---

## Forward shadow test: none warranted yet

No family qualifies, and not because they all failed — because seven of the
eleven ranking criteria are not computable from the data we have.

**The gate is `analysis/gemini/p9e_book_tape.py`**, written this round: a
read-only recorder that polls each contract's `bestBid`/`bestAsk` and its
trades together, de-duplicating by `tid`, so every print lands beside a
contemporaneous book and can be assigned to a side. `lastTradePrice` cannot do
this job — it appears to echo the most recent tape row whatever space that row
is in, which makes comparing the two circular.

With a decoded tape the ranking becomes computable and the first candidates to
measure are the two dense lanes (Weather · Daily Temperature at 0.75
prints/min and $7.89/min; Economics at 0.25 and $7.04), then the short-duration
crypto lanes. Not Politics or long-dated Sports: their flow is too thin for a
second fill regardless of what the decoder says.

**Rebates are not reported.** They are incremental economics on a frozen
pre-rebate candidate, and no pre-rebate candidate has been frozen.

---

## Held, unchanged

No live orders. Nothing armed. `bundle_sha` unchanged. No rebate term in
selection. 09-23+ forward collector untouched and unread.
