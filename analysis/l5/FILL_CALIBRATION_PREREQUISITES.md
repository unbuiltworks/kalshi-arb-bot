# What exists, what is missing, and whether validating maker fills needs a
live order

Answers items 1–5. No production change, no order, no limit change. The
twelve simulations and the pre-registered one-tick experiment continue
unchanged.

**Headline: one of the five items overturns a figure the deployed report
currently asserts, and a second shows the programme's own earlier analysis
already reached the audit's conclusion by a different route.**

---

## 1. Inventory of real PMUS maker-order records

| field | exists? | where | notes |
|---|---|---|---|
| order submission ts / price / qty | **partial** | production order store, Railway volume `/data/l5-live-20261012z` | only for the handful of cancellation-test orders; not on disk here |
| venue ack / order id | **partial** | same | same orders |
| cancellation + terminal status | **yes, for those** | same | exact-order cancellation is proven on this account |
| **fills** (price, qty, ts) | **yes** | `analysis/l5/l5_r5f_cohort.json` | **88 maker fills, 0 taker**, 16 settled markets |
| contemporaneous book observation at our order's price | **NO** | — | the authenticated ladder recorder (`AuthBook`/`Ladder`) was built later and runs only on BTC15m |
| trade prints at our level over the order's life | **NO** | — | same |
| queue position at placement | **NO** | — | never recorded for any real order |

Everything on disk under `data/l5/` (`raw/`, `ops/`, `exante/`, `mirror/`,
`sep2026/`) is **L5's** Polymarket activity for wallet
`0xb945…db68`, not ours.

**Our own real deployment, source-confirmed** (`l5_full_wallet_economics`):
19 settled markets, $554.49 cost basis, 1,167.25 contracts,
venue-confirmed realized **−$35.81**, maker rebates **+$2.13 credited**,
total −$33.68. The cohort subset is 16 markets with 88 maker fills. **No
BTC15m market appears in either** — slug prefixes are `aec`, `atc`, `tsc`.

## 2. The exact missing fields blocking queue-model validation

For each real maker order, validation needs all five. We have 1, 2 and 5
for a few orders, and **never** 3 or 4:

1. submission timestamp, price, side, quantity — *have (few)*
2. venue ack and order id — *have (few)*
3. **the ladder/book snapshot at submission, at our price level** →
   without it `queue_ahead` cannot be reconstructed for any past order
4. **every trade print at that price level between submission and terminal
   state** → without it `volume_through_level` cannot be reconstructed
5. terminal outcome and fill timestamp — *have (few)*

Fields 3 and 4 are precisely the two inputs the fill rule consumes.

## 3. Can anything be calibrated offline, without inventing queue
positions?

**Partly — and the part that can be is worth doing.**

Validatable now, with no order:

- **Queue measurement.** `Ladder.ahead_of_us` / `depth_at` can be checked
  against the venue's published depth at the same instant, using the live
  BTC15m feed we already record. This tests whether our queue input is
  the venue's own number.
- **Volume attribution.** `volume_through_level` can be reconciled against
  the venue's public trade tape for the same window and level. This
  directly tests the audit's open concern — whether aggregate print volume
  sweeping several levels is being credited to our level. The 1730z
  pair-making fill (11 ticks behind the touch, behind 16,554 contracts,
  credited on 16,753 of volume in 43 seconds) is the case to test.
- **Fill-rule monotonicity and refusal behaviour** — already covered by
  the existing unit tests.

**Not validatable offline:** whether an order resting behind a known queue
actually fills when that volume clears. That is a statement about the
venue's matching engine and our position in it. No record we hold contains
a real order with a known queue position and an observed outcome, and
inventing one is exactly what must not be done.

## 4. L5's $62.52 / 5.70% — verified, with one important correction

Arithmetic checks against the source
(`data/l5/l5_btc15m_realized_economics.json`, wallet `0xb945…db68`, raw
Polygon activity):

- 5,533 resolved windows, total **$345,940.64**, mean **$62.52**, median
  $44.66, wins 4,056 (73.3%), min −$751.73, max +$1,038.91.
- `345,940.64 / 6,069,977.86 = 5.699%` ✓.

**The denominator is TURNOVER, not capital at risk.** `pnl = redeem_in +
sell_in − buy_out`, realized cashflow only, and `cash_deployed` is the
cumulative buy side. Across 5,533 sequential 15-minute windows the same
capital recycles, so **5.70% is a margin on cumulative cash spent, not a
return on capital employed.** Per-window deployment is ~$1,097; capital
employed depends on concurrency, which this dataset does not state.

**Venue incentives are inside that total**: the ledger carries 111
MAKER_REBATE, 73 TAKER_REBATE and 20 REWARD rows. Settlement cashflow is
`redeem_in`; unredeemed windows are booked as total losses under
`all_windows_worst_case` (which moves the total only $415).

**An unresolved inconsistency I am flagging rather than explaining away:**
5,533 windows at 15 minutes is ~57.6 days of continuous coverage, giving
~$6,005/day — about **12.5× the $480/day programme target**. Either L5 did
not trade every window across a longer calendar span, or the two figures
measure different things. The dataset does not carry a date range, so I
cannot settle it here, and the $480/day target should not be treated as
reconciled with this file until someone does.

## 5. Which BTC15m fee/rebate terms actually apply — and a figure that is
wrong in production

**Fees: "CONFIRMED $0.0000" is not supported by the evidence.** The
cohort records `fees_usd` as **`venue_unreported` on all 16 settlement
rows**, and states "every derived figure here is PRE-FEE". No document in
this repository shows a venue-reported fee amount of zero.
`l5_breakeven.py` nonetheless says "the venue charged none against 293
maker fills", `l5_full_wallet_economics` hardcodes `"fees": D("0.0000")`,
and the **deployed** shadow report emits
`maker_fee_provenance: "CONFIRMED zero over 293 actual maker fills on this
account"`.

That chain converts **absence of a reported fee into a measured zero**,
and the fill count (293) does not match the cohort's 88 either. At q=1 a
maker fee of even half a cent would exceed the entire half-tick spread
capture, so this is not a rounding concern.

**Rebates: measured, credited, and NOT zero.** $2.13 credited against our
own notional = **0.384% of notional**, which
`l5_full_wallet_economics` notes is already inside L5's observed range
(+0.275% → +1.323%). The deployed report's `$0.0000 UNCREDITED` is
conservative but understates a rate we have actually been paid. Critically
the rebate is a **rate on notional**, so scaling up does not improve it.

**Neither term has ever been observed on a BTC15m market of ours**, at any
size. Both the fee and the rebate for BTC15m specifically are unknown.

### The programme already knew the shape of this

`l5_full_wallet_economics` reaches the audit's conclusion independently,
per dollar traded: **ours −6.458% trading, +0.384% incentive; L5's
−0.037% trading, +0.275%→+1.323% incentive.** Its own words: *"our trading
loses 175 times more per dollar than his does … the rate is plausibly
adequate, and it is irrelevant until the trading loss falls by roughly two
orders of magnitude."*

So **L5's trading P&L per dollar is approximately zero and slightly
negative; its entire positive return is incentives.** Our pair economics
being near-zero is therefore *consistent with L5's*, and the gap is
execution friction at q=1 — not strategy and not the incentive rate.

---

## Minimum additional evidence to validate maker fills

**Tier A — needs no order, do first.** Reconcile, over windows we already
record: (i) `Ladder.ahead_of_us` against the venue's published depth at
the same instant; (ii) `volume_through_level` against the venue's public
trade tape at the same level. This settles whether the model's two inputs
are the venue's own numbers, and tests the over-crediting concern on the
1730z fill. Cost: offline analysis only.

**Tier B — requires live orders. Not proposed here.** The fill rule itself
needs, for N real resting orders: price, submission time, the ladder
snapshot at submission, every print at that level over the order's life,
and the terminal outcome. **Minimum useful N is a handful of orders across
differing queue depths, held to a terminal state rather than cancelled
immediately** — a place-and-cancel probe proves cancellation, not filling,
so it cannot answer this. That means orders genuinely exposed to fill
risk, which is a money-moving action needing explicit authorization, a
bounded notional, and its own pre-registration.

**Also needed, and obtainable without trading:** the venue's own published
BTC15m maker fee schedule and rebate terms for our account tier. That is
a documentation/support question, not an experiment, and it should be
settled before any probe, because if the BTC15m maker fee is non-zero the
q=1 ledger is already decided.

**Recommended order:** fix the false fee provenance; get the venue's
written BTC15m fee/rebate terms; run Tier A; only then consider Tier B.
