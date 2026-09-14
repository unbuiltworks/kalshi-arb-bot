# L5 redemption / settlement lifecycle audit

Accounting and lifecycle, not strategy. Read-only over local caches; no
network, no order path, no venue action. Reproduce with
`python3 analysis/l5/redemption/audit.py`.

## 0. The rule this exists to enforce

> **A $1,701.60 redemption is NOT $1,701.60 profit.**

Measured across L5's entire public history:

| line | amount |
|---|---|
| acquisition cashflow (BUY, 760,815 fills) | **−$7,605,335.88** |
| trading sales (SELL, **2** fills) | +$2.18 |
| settlement/redemption cashflow (15,729 redemptions) | **+$7,623,781.77** |
| **= net trading cash** | **+$18,448.07** |
| incentives (Ledger C — additive, never redemption) | +$71,551.74 |
| **= grand total cash** | **+$89,999.81** |

**Reading redemption gross as profit would overstate by $7,605,333.70** — a
factor of 413. Net trading cash is +$18,448 on $7.6M of turnover: a 0.24%
margin. **Incentives are ~80% of the grand total.** On this evidence L5's
economics are an incentive business wrapped around a roughly break-even
trading book, not a trading business.

## 1. What a REDEEM activity row represents

A redemption converts **winning shares into USDC at exactly $1.00 each**. On
all **15,729** rows `usdcSize == size` exactly; `price` is always `0`; `side`
and `asset` are always empty. **It is not a trade** — it is the settlement
conversion, and it is not an incentive.

## 2. Category coverage

**Crypto only.** Every TRADE and REDEEM row in the entire history falls in a
BTC/ETH Up-or-Down window; **zero non-crypto slugs appear anywhere**.

| cohort | redemptions | redemption cashflow | net cash |
|---|---|---|---|
| btc-15m | 5,706 | $6,396,029.79 | **+$19,176.71** |
| btc-5m | 7,191 | $1,043,580.75 | −$1,700.04 |
| eth-5m | 2,309 | $134,754.33 | +$1,224.73 |
| eth-15m | 523 | $49,416.90 | −$253.33 |

Essentially all of the trading profit sits in **btc-15m**.

## 3. Hold through resolution, or sell before it?

**L5 does not sell.** Two SELL fills totalling **$2.18** against **760,815
BUY fills** and $7.6M of acquisition — and only **2 of 17,046 markets** show
any sale at all. Exits are settlement redemptions, ~100%.

## 5. BUY → matched → residual → outcome → redemption

* **100.00%** of settled markets (15,517/15,517) redeem **exactly one
  outcome** — the binary resolves, only the winner redeems.
* **97.4%** (15,113/15,517) redeem a quantity equal to the BUY quantity on
  the winning side. The rest are slightly *under*, consistent with rounding.
* **matched inventory is 87.45% of everything bought; residual is 12.55%.**

That 87/13 split is independent confirmation of the matched-pair thesis from
cashflow alone: L5 buys both sides in near-balance and the residual is the
small remainder.

## 6. Delay from resolution to redemption

Measured from the **window end** (the resolution instant), not from the last
trade — a 5m window's last trade sits near expiry and would flatter it.

| p10 | p50 | p90 | p99 | max | before resolution |
|---|---|---|---|---|---|
| 36 s | **76 s** | 112 s | 10,762 s | 2,022,674 s | **0** |

27.5% within 60 s, **95.6% within 5 minutes**, 98.2% within an hour. Zero
redemptions precede resolution (sanity check passes).

## 7. Automatic, batched, or explicit?

**Explicit and essentially unbatched, on a tight automated cadence.** 15,729
redemptions across **15,271 distinct transaction hashes** — mean 1.03 per
transaction, and only **81 transactions (0.5%)** carry more than one. Each
redemption is its own on-chain action.

The p50 delay of 76 s with a p90 of 112 s is far too tight and too consistent
for manual initiation, and far too slow for same-block automation. The
evidence supports **an automated redeemer polling on a short cycle**. Whether
it is L5's own bot or a venue-side helper is **UNKNOWN from public data** and
is not inferred.

## 8. Do OUR venues require a redemption action?

**The venues differ and their vocabularies are not interchangeable.**

| venue | lifecycle | action required by us |
|---|---|---|
| **Polymarket** (L5's, polymarket.com) | RESOLVED → REDEEMABLE → REDEEM_SUBMITTED → REDEEM_CONFIRMED → CASHFLOW_CONFIRMED | **Yes** — an explicit on-chain REDEEM per position |
| **PMUS** (ours, polymarket.us) | RESOLVED → POSITION_RESOLUTION_EMITTED → CASHFLOW_CONFIRMED | **No** — the venue settles automatically |
| **Gemini** (ours) | **UNKNOWN_FOR_OUR_ACCOUNT** | unverified — we have never held inventory there |

**Our engine contains no redemption code path at all**, and on PMUS it needs
none: settlement arrives as a `POSITION_RESOLUTION` activity carrying
`beforeNet → afterNet: 0.0000` and the venue's own `realizedPnl`, which the
ledger already ingests. Confirmed live today by `ararus-samste` (−$2.3248)
and `noalui-eneper` (−$1.3806), both booked with no action from us.

**The one real gap: Gemini.** Its settlement is venue-driven per the adapter
("payout 1.00 to UP or DOWN"), but because we have only ever observed there,
whether our account would need an explicit claim is **UNVERIFIED**. That must
be established from source before any Gemini position is ever held — not
assumed from PMUS's behaviour.

## 9. Reconciliation with the existing ledger — NOT a second path

`analysis/l5/l5_ledger.json` (2026-08-31) already carried these cashflows.
This audit reads a **larger** cache (the gap file) and is a superset:

| quantity | this audit | ledger (2026-08-31) |
|---|---|---|
| buy_usdc | 7,605,335.88 | 7,423,850.29 |
| sell_usdc | 2.18 | 2.18 |
| redeem_usdc | 7,623,781.77 | 7,437,534.55 |
| net_trading_cash | **+18,448.07** | **+13,686.44** |
| REDEEM rows | 15,729 | 15,243 |
| markets | 17,046 | 16,501 |
| income_total | 71,551.74 | 68,208.40 |

Both agree on every conclusion that matters: redemption gross dwarfs net
trading cash, trading cash is near zero, and incentives dominate the total.
The existing ledger is **not** superseded or rewritten.

**Deduplication is load-bearing and was a real defect caught here.** The two
caches overlap: 3,149 TRADE rows appear in both files and ~4,359 repeat
within them. Summing raw rows inflated acquisition by ~$65k and **flipped net
trading cash from +$18,448 to −$46,825** — a sign error in the headline. Every
row is now keyed on its full identity before being counted, pinned by a test.
