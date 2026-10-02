# Gemini P0 — what did the $0.56 of rebates cost in trading economics?

2026-10-02. No orders. No experiment rerun. PMUS sports frozen at `60974917`.

## Answer

**The question cannot be answered, and the reason is sharper than "missing
data": the fills we can reconstruct P&L for are 91% disjoint from the fills
that earned the rebate.**

- **9%** of the credited rebate ($0.05 of $0.56, 11 of 64 fills, $3.95 of
  $54.43 notional) falls on a day where we captured our own fill records.
- **91%** ($0.51, 53 fills, $50.48 notional) falls on days with **no captured
  fill records at all**.

And a second finding changes what the question even means: **all 88 captured
own-account fills are BUYS. Zero sells.** This was not two-sided market
making whose cost is adverse selection — it was one-sided accumulation of
long `-UP` exposure, whose P&L is dominated by BTC direction.

## Can the live API settle it? No — not from here

`GEMINI_API_KEY` / `GEMINI_API_SECRET` are absent. Every private path returns
**401 `MissingSecurityHeaders`** (`/v1/prediction-markets/maker-rebate/payouts`,
`/v1/transfers`, `/v1/mytrades`; `summary/total` returns 405 for POST). So
this is a census of what was already captured, across **3,489 artifact files**,
not a new read.

### The per-fill attribution you asked me to look for does not exist

The payout rows carry exactly these fields:

    created_at, credited, id, labels, paid_at, status,
    total_fill_count, total_rebate_usd, total_volume_usd

**There is no per-fill breakdown and no detail path captured behind
`/payouts`.** The venue reports fill *counts* and *volume*, never the fills.

### Pagination of `/v1/transfers` did not help, and shows a shortfall

Union across every captured snapshot: **8 distinct transfers, 7 of them
maker-rebate, totalling $0.51.** Every capture returned the unpaginated 9–10
row default page and the union never exceeded it. Against the payout ledger's
**$0.56**, the transfer view is short by **$0.05** — exactly the 2026-09-22
payout. The payout ledger is the authoritative source; the transfer list is
truncated.

## 1. Authoritative maker notional and rebate — SOURCE-OBSERVED

From `/v1/prediction-markets/maker-rebate/payouts`, 10 distinct rows captured,
**8 PAID**:

| | |
|---|---|
| Credited rebate | **$0.56** |
| Maker notional | **$54.43** |
| Credited maker fills | **64** |
| Rebate rate | **1.029%** of maker notional |
| Span | 2026-08-27 → 2026-09-22 |

## 2. Attributable trading cash P&L — **NOT ESTABLISHED**

**No cash-confirmed trading P&L exists.** The repository's own settlement gate
reads `BLOCKED, 6/6`, missing `AUTHORITATIVE_FILLS`, `CASH_CONFIRMED`,
`FEES_ACCOUNTED`, `POSITION_AT_EXPIRY`. `/v1/prediction-markets/settlements`
and `/v1/settlements` both 404; `/v1/prediction-markets/positions` gives state,
not cashflow.

What *can* be reconstructed, on the 23 symbols where own fills and a venue
`resolutionSide` both exist (88 fills, 2026-09-19 → 09-23):

    fill cash   −$30.0345
    settlement  +$42.0100
    RECONSTRUCTED  +$11.9755      per $1 of maker notional: +0.399

**This is not a result, for four independent reasons:**

1. **It is not cash.** Same class as EP011's reconstructed +$0.53, which the
   proof chain marked NOT REALIZED at `first_break = settlement_cashflow`.
2. **It is 1.23 sigma.** Standard error **±$9.72** — contracts within a window
   are perfectly correlated, so the effective sample is **23 windows, not 86
   contracts**. Indistinguishable from zero.
3. **It is directional, not liquidity provision.** All 88 fills are buys of
   `-UP`. BTC went **80,914 → 84,504 (+4.44%)** across exactly that window.
   A rising tape against a one-sided long book is the simplest explanation of
   the whole +$11.98.
4. **It is the wrong population.** Only 9% of the credited rebate overlaps it.

## 3. Fees — SOURCE-OBSERVED, and zero

`fee_amount` is **"0" on all 88 captured fills**, with `aggressor: false` on
every one. Consistent with the published rule that makers pay no fee. Venue-wide
fee accounting remains unresolved (`FEES_ACCOUNTED` missing from the gate), so
this is zero *on the captured fills*, not a proven account-wide zero.

## 4. Settlement cashflows — **NOT ATTRIBUTABLE**

`resolutionSide` and `settlementValue` are available per symbol and are
internally consistent (0 conflicts across 350 resolved symbols). But
`settlementValue` is a *price*, not a cash movement to our account. No
attributable settlement cash exists for any window.

## 5. Unresolved / unattributable

| | |
|---|---|
| Credited rebate with **no captured fills** | **$0.51 of $0.56 (91%)** |
| Credited fills with no captured records | **53 of 64** |
| Maker notional with no captured fills | **$50.48 of $54.43** |
| Settlement cash attributable | **$0.00 — none, anywhere** |
| `/v1/transfers` shortfall vs payout ledger | $0.05 (truncated page) |

The unattributed period is **2026-08-27 → 2026-09-08**, which carries most of
the notional and predates every captured evidence packet.

## 6. Cost per $1 of rebate — **UNKNOWN**

Not computable. For 91% of the rebate there are no fills to price. For the 9%
that overlaps, the trading leg is reconstructed rather than cash-confirmed and
is 1.23 sigma. **No number here would be honest.**

## 7. Net per $1 of maker notional — **UNKNOWN**

The reconstructed +0.399 on the captured subset is ±0.32, directional, and on
a population that earned 9% of the rebate. Not transferable.

## The economic gate

> Was attributable trading loss below the 1.029% compensation rate?

**Undetermined — the gate cannot be passed or failed on this evidence.** The
only measurable subset shows trading *positive*, not negative, which would
clear the gate trivially if it were real. It is not established: it is a
1.23-sigma directional outcome on 23 windows during a +4.44% BTC move, on a
population nearly disjoint from the rebate-earning one.

**I will not report "Gemini trading beat its rebate rate" from this.** It is
the same shape as the PMUS dormant-book result — a number that looks good
because of what the sample happens to contain.

## Exactly what remains unknowable, and what would fix it

**Unknowable from the captured artifacts, permanently:**

- per-fill rebate attribution — the venue does not publish it on `/payouts`;
- any fill from 2026-08-27 → 2026-09-08, 91% of the rebate-earning notional;
- settlement cash attributable to our account, for any window.

**Obtainable with credentials, read-only, no orders:**

1. `/v1/prediction-markets/maker-rebate/payouts` with a **detail or per-payout
   fill path** — unprobed; the published spec lists only `payouts`, `rates`,
   `summary/total`, so this may not exist, and that would be a finding.
2. **Paginated** `/v1/transfers` over 2026-08-20 → now, to confirm $0.56 is the
   complete credited total rather than the complete *captured* total.
3. An order/trade history walk covering 2026-08-27 → 09-08. `/v1/mytrades`
   returns 200 with **zero rows** for prediction symbols, but the per-slot
   `venue_evidence` captures show a readable **order history** keyed by
   `client_order_id` / `order_id` with `filledQuantity` — that path was never
   walked for the August window and is the single most valuable unprobed read.

**Without credentials in this environment, none of the three can be run.** The
P0 question stays open, and no new Gemini shadow or controller is justified
until it closes — the gate was explicitly conditioned on answering it first.
