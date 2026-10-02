# Gemini rebate economics — source-accounted audit of existing live activity

2026-10-02. No new Gemini trades. No executor rebuilt, no experiment rerun.
PMUS sports research untouched; Kalshi frozen at `1ac07f69`; L5 benchmark
(`1f43db3b`) unchanged; no SOL infrastructure touched.

**The correction is accepted and confirmed from account records:** Gemini has
actually paid us maker rebates. This is not a venue where we need to discover
whether compensation exists.

**But the audit cannot answer the primary question**, and the reason is
specific and already recorded in this repository rather than newly discovered.

---

## The primary question

> How much did it cost us in trading economics to earn $1 of actual Gemini
> rebate?

**UNANSWERABLE from existing evidence.** The rebate leg is authoritative and
exact. The trading leg is not established at all. Dividing one by the other
would be the fabrication this audit exists to prevent.

What *is* exact, and it is the useful half:

> **$1 of Gemini rebate requires $97.20 of maker notional turnover.**

That converts the open question into a sharp, testable one: *does our maker
flow lose more or less than 1.029% of its notional?* Below that, Gemini pays
for itself. Above it, it does not.

---

## One correction to my own reading, made before it propagated

I first read `api_maker_fee_bps: 60` across all 71 preflight captures
(2026-09-17 → 2026-09-23) as evidence that Gemini charges makers and pays no
rebate, which would have contradicted the premise. **That is the spot fee
schedule, not predictions.** Prediction markets have a separate published
maker-rebate program:

    rebate = 0.30 x 0.07 x C x P x (1 - P),  eligible price band [0.20, 0.80]

Source: `developer.gemini.com/prediction-markets/maker-rebate-program` +
`gemini.com/fees/predictions`, read 2026-09-19 and recorded in
`analysis/gemini/maker_rebate_reconcile_*.json`. The two schedules coexist.

---

## B. Total actual rebates credited — **$0.56** (SOURCE-OBSERVED, venue-authoritative)

From `GET /v1/prediction-markets/maker-rebate/payouts`, the venue's own payout
ledger, captured in `maker_rebate_reconcile_20260923T074605Z.json`. Nine rows,
eight PAID:

| created_at (UTC) | status | fills | volume $ | rebate $ |
|---|---|---|---|---|
| 2026-08-27T21:03:29 | PAID | 3 | 1.23 | 0.01 |
| 2026-08-28T21:03:23 | PAID | 15 | 7.31 | 0.07 |
| 2026-08-29T21:03:39 | PAID | 2 | 5.25 | 0.05 |
| 2026-08-30T21:03:27 | PAID | 3 | 14.20 | 0.14 |
| 2026-09-01T21:03:30 | PAID | 25 | 16.46 | 0.17 |
| 2026-09-08T22:23:46 | PAID | 3 | 5.03 | 0.06 |
| 2026-09-08T22:30:57 | PAID | 2 | 1.00 | 0.01 |
| 2026-09-19T21:04:30 | **ZERO_AMOUNT** | 3 | 0.0098 | 0 |
| 2026-09-22T21:02:54 | PAID | 11 | 3.95 | 0.05 |
| **CREDITED TOTAL** | | **64** | **$54.43** | **$0.56** |

**Use this, not the transfers list.** The account's `/v1/transfers` rows
labelled `"purpose": "Predictions maker rebate"` (type `AdminCredit`) total
only **$0.51** over 7 transfers — every capture of that endpoint returned 9 or
10 rows, the unpaginated default page. The transfer view is **truncated**, the
payout ledger is complete for the window it covers, and the $0.05 difference
is exactly the kind of silent shortfall that truncation produces. Using the
larger authoritative figure is not optimism; it is the correct source.

## A. Total trading P&L before rebates — **UNRESOLVED**

Not "approximately zero", not "small". **Not established.** The repository's
own settlement gate says so, and I am reporting its verdict rather than
overriding it:

    gate: BLOCKED,  n = 6,  n_blocked = 6
    missing_union: [AUTHORITATIVE_FILLS, CASH_CONFIRMED,
                    FEES_ACCOUNTED, POSITION_AT_EXPIRY]
    public_surface: PUBLIC_SURFACE_CANNOT_ESTABLISH_SETTLEMENT

The single settled live window (EP011, CAP-4, 2026-09-21) reconstructs:

    COST $1.47   PAYOUT $2.00   FEES $0.00   REBATE not credited
    RECONSTRUCTED +$0.53   -->   explicitly NOT REALIZED

because `proof_chain.first_break = settlement_cashflow`: *"no attributable
cash movement; a balance change without source identity is not attribution."*
Of the four bounded BTC15m windows, EP010 stopped at the imbalance cap, EP011
is the one above, EP012 was an accounting investigation that found a phantom
$2.16 double-count, and EP013 submitted nothing. **One window with
reconstructed economics, zero windows with realized trading cash.**

## C–M

| | Quantity | Value | Class |
|---|---|---|---|
| A | Trading P&L before rebates | **UNRESOLVED** | gate BLOCKED |
| B | Actual rebates credited | **$0.56** | SOURCE-OBSERVED |
| C | Fees | **$0.00** in the one settled window; venue-wide **UNRESOLVED** (`FEES_ACCOUNTED` missing) | mixed |
| D | Final realized net | **UNRESOLVED** — B is realized, A is not | — |
| E | **Cost per $1 of rebate** | **UNKNOWN** — requires A | — |
| F | **Rebate per $ maker turnover** | **$0.010288 (1.029%)** = $0.56 / $54.43 | SOURCE-OBSERVED |
| G | Trading P&L per $ maker turnover | **UNKNOWN** | — |
| H | Net per window | UNKNOWN; rebate per payout day $0.56/8 = **$0.07** | — |
| I | Net per capital-hour | **UNKNOWN** — numerator unresolved | — |
| J | Worst realized window | **none realized** | — |
| K | Residual exposure distribution | EP010 peak imbalance **2** (hit the cap); EP011 peak **1**, one matched pair at 0.83 effective; `matched_is_verified: false` | OBSERVED, unverified at venue |
| L | Maker/taker mix | **64 credited maker fills**; taker fills **UNKNOWN** (payout rows carry maker only); EP011 ran maker-or-cancel throughout | partial |
| M | **Capacity actually demonstrated** | **$54.43 maker notional over 8 payout days** spanning 2026-08-27 → 2026-09-22 ≈ **$2/day**, 64 fills total | SOURCE-OBSERVED |

The 1.029% in F sits just below the published in-band rate (1.05–1.26% of
notional at p = 0.40–0.45), consistent with cent-rounding on sub-dollar
payouts and some out-of-band fills. The agreement is corroboration that the
payout ledger and the published formula describe the same thing.

### A ratio I computed and am discarding

`cost_basis_usd` is populated on only a handful of the 39 PMUS settlement rows
and sums to $1.90, so "realized as % of cost basis" came out at −2,345%. That
is a denominator artifact, not a loss rate. **PMUS maker turnover: UNKNOWN
from this ledger.** Reported so it is not quietly reused.

---

## Does Gemini show the L5 economic structure?

The authoritative L5 finding is:

    trading negative  +  venue incentives  =  positive realized economics

| Leg | L5 (frozen benchmark) | Gemini (this audit) |
|---|---|---|
| Venue incentives | **+$23,047.40**, proven | **+$0.56**, proven |
| Trading | **−$6,338.70**, proven | **UNPROVEN — not established either way** |
| Structure | **demonstrated** | **one of two legs demonstrated** |

**No. Our actual Gemini history does not show the same economic structure,
because only the incentive leg has been proven.** Gemini has reproduced the
*existence* of the compensation term, at 1/41,000th of the scale, and has
established nothing about the sign or magnitude of the term it is supposed to
offset. Claiming the structure transfers would mean asserting the half of the
equation the venue has never let us measure.

## The exact missing source

Not an assumption to fill — a specific capability we lack:

1. **Per-fill prediction-market trade history.** `/v1/mytrades` returns HTTP
   200 with **zero rows** for prediction symbols (`GEMI-BTC05M…`), so the
   venue's general trade endpoint does not carry this product. Without it
   there are no authoritative fills, prices, sides or fees.
2. **Attributable settlement cash.** `/v1/prediction-markets/settlements` and
   `/v1/settlements` both **404**. `/v1/prediction-markets/positions` returns
   200 but gives position state, not cashflow. Balance deltas exist but carry
   no source identity, which is why EP011 is NOT REALIZED.
3. **A paginated `/v1/transfers` walk**, to confirm $0.56 is the complete
   credited total rather than the complete *captured* total.

The most promising unexplored path, and the one I would check first because it
costs one read: the maker-rebate API already yields `total_fill_count` and
`total_volume_usd` per payout, so **the venue does hold our fill-level data on
this product**. Whether `/v1/prediction-markets/maker-rebate/payouts` exposes
a per-payout detail or per-fill breakdown is **not yet established** — the
published spec lists `payouts`, `rates` and `summary/total`, and no detail
path has been probed. That is a read-only question.

---

## Answers

**1. Did Gemini rebates historically exceed the cost of earning them?**
**UNKNOWN, and not answerable from existing records.** $0.56 was certainly
earned; what it cost is not established. What is established is the exchange
rate: **$97.20 of maker notional turnover per $1 of rebate.** Gemini pays for
itself only if maker flow loses less than 1.029% of notional.

**2. Which existing Gemini controller had the best source-accounted
economics?** **None can be ranked.** The payout ledger aggregates by UTC day,
not by controller, and the per-day fills cannot be attributed to a controller
variant without the per-fill records that item 1 above says we lack. The
highest-rebate day (2026-09-01, $0.17 on 25 fills, $16.46 volume) predates the
labelled cohort/AB runs entirely. **Ranking the variants requires the same
missing source**, which is a reason to get it rather than to guess.

**3. Is there a materially new L5-informed hypothesis worth shadowing?**
Yes, one, and it is new because of this audit's only clean number. Gemini pays
**1.029% of maker notional**; PMUS pays `0.0125 x (1-p)` of cost — **0.625% at
p = 0.50**. **Gemini pays roughly 1.6x PMUS per dollar of maker turnover**,
and unlike PMUS it pays on a *price-banded* formula that peaks at p = 0.50
rather than decaying toward the tails. That makes mid-price maker provision
the compensated regime on Gemini and the *least* compensated on PMUS — a
genuine structural difference worth shadowing, and the opposite of the
assumption that the two venues differ only in scale.

**4. Is there a credible path to >= $3 average realized net per window?**
**Not demonstrated, and the frequency gap is three orders of magnitude.** At
1.029%, $3 of rebate per window needs **$292 of maker notional per window**.
Demonstrated capacity is $54.43 across a month — roughly **$2/day**. Reaching
$3/window on rebate alone would require ~583 q1 contracts per window against
64 fills total. And that is the *gross rebate*, before the trading cost that
item A says is unmeasured. **No credible path on current evidence.**

**5. What capital/capacity would it require?** Capital is trivial —
$292 of notional at q1 is a few hundred dollars of collateral, and the
demonstrated windows used under $4. **The binding constraint is fill
frequency, not capital**, which is consistent with the standing L5 rule to
favour frequency before size. But increasing frequency increases exactly the
adverse-selection exposure that item A has never measured, so frequency
cannot be raised on this evidence either.

## Recommendation

**No new Gemini live trades.** The next step is read-only and cheap: probe for
a per-fill detail path on the maker-rebate API and paginate `/v1/transfers`.
If fill-level records exist, A becomes computable from history already paid
for, E and G follow, and the controller ranking in question 2 resolves without
a single new order. If they do not exist, then Gemini cannot settle its own
trading economics from the account surface, and that is itself the answer —
one that no further live window would improve.
