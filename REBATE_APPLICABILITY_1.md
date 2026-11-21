# Is the maker rebate applicable to BTC5m now — and what is it worth to us?

**Item 1 of the owner's priority order.** Answer: **the programme is published,
current, and covers BTC5m — and at our size it pays exactly $0.00, by
arithmetic rather than by estimate.**

**Sources** (public, read 2026-09-19):
`https://developer.gemini.com/prediction-markets/maker-rebate-program` and
`https://www.gemini.com/fees/predictions`.
Code: `src/gemini_execution/rebate_program.py` · 21 tests.

---

## 1. The published rules

```
Maker Rebate = Rebate Rate × Taker Rate × C × P × (1 − P)
               capped at 5% of fill notional
eligible execution price band   $0.20 – $0.80
contracts listed on or after    2026-03-18
rounding                        DOWN to the next cent
payment                         daily, 5pm ET, in USD
```

Fee schedule effective 2026-03-18: **taker rate 0.07**, **maker rate 0.0175**.

Rebate rate by market, with the current row last:

| market | period | rate |
|---|---|---|
| Crypto & Commodities | 2026-04-09 – 2026-06-10 | 0.70 |
| **Crypto & Commodities** | **2026-06-11 →** | **0.30** |
| Politics | 2026-06-11 → | 0.30 |
| Sports | 2026-06-11 → | 0.30 |
| Economics | 2026-06-11 → | 0.30 |
| All other markets | 2026-03-18 → | 0.30 |

**Applicability to BTC5m: yes.** It is a Crypto contract, listed well after
2026-03-18, and the current-period row carries **no end date**. The page says
rates "are subject to change", so this is the published position as of
2026-09-19 and nothing more.

So for our contracts the rebate is

```
0.30 × 0.07 × C × P × (1 − P)  =  0.021 × C × P × (1 − P)
```

## 2. The published rules explain our confirmed credits

Two independent checks, neither of which I could run before:

**The payment time matches exactly.** The rules say *paid daily at 5pm ET*.
Five of our seven confirmed credits are stamped **21:03Z**, which is 5pm ET
under EDT. That is strong evidence the $0.51 we hold was paid by **this**
programme rather than some other one.

**The aggregate rate is consistent.** Under the formula,
`rebate / notional = 0.021 × (1 − P)`, so the confirmed **97.59 bps** implies a
notional-weighted average fill price of **0.5353** — comfortably inside the
$0.20–$0.80 eligible band.

**Consistent is not proven.** Fills outside the band sit in the denominator
earning nothing, which drags the observed rate down and biases the implied
price. This check can only fail to contradict, and it does not contradict.

**No per-day attribution is produced, because none closes.** I tried both
mappings: same-day requires Aug 28 to have an average fill price above 1.00;
T+1 requires the same of Aug 30. Neither is possible, so neither is true, and
the module produces no daily mapping.

## 3. Why the credit record alone could not answer the question

Confirmed credits stop on **2026-09-08**. It is tempting to read that as the
programme ending. It is not evidence of that, because **our maker volume also
stops**:

| day | venue maker notional |
|---|---|
| 2026-08-27 … 2026-09-02 | $5.64, $3.20, $5.25, $18.98, $0.25, $17.45, $1.47 |
| 2026-09-03 … 2026-09-17 | **none** |
| 2026-09-18 | **$0.0173** (C4 + C5 + C6, our own fills) |

Sixteen days with no volume cannot produce a credit under any programme. And
the $0.0173 we did trade on Sep 18 accrues **$0.00017** — which rounds down to
$0.00 and could never appear as a credit even if the programme is fully live.

**The credit record is therefore silent on current applicability, and the
published rules are the only thing that answers it.** They say it applies.

## 4. What it is worth at our size — the arithmetic that matters

Per 0.01-contract maker fill, at the current crypto rate:

| execution price | eligible? | rebate accrued |
|---|---|---|
| 0.14 (C4 NO) | **no** — below $0.20 | **$0** |
| 0.21 (C6 YES) | yes | $0.0000348 |
| 0.31 (C8B YES) | yes | $0.0000449 |
| 0.46 (C5 NO) | yes | $0.0000522 |
| 0.50 (the maximum, where P(1−P) peaks) | yes | $0.0000525 |
| 0.92 (C4 YES) | **no** — above $0.80 | **$0** |

**All five fills this programme has ever taken accrue $0.000132 in total, and
would be paid $0.00.** The round-down is to the next cent, so a day's whole
accrual must reach $0.01 before any cash moves:

- at the best possible price (P = 0.50): **1.905 contracts = 191 fills of 0.01**
- at our actual fill price (P = 0.31): **2.226 contracts = 223 fills**

A bounded run that produces ten 0.01 fills accrues about **$0.0005** and is
paid **$0.00**. Not "probably nothing" — arithmetically nothing.

**Two of our five fills were outside the eligible band entirely.** C4's YES at
0.92 and NO at 0.14 earn nothing at any volume. The L5 OPEN rung follows the
bid, and the habitat census puts **19.9% of cycles with a YES bid at or below
0.10** — where the rebate does not reach at all.

**At 0.01, the maker fee is invisible too.** The maker fee is
`0.0175 × C × P(1−P)`; at P = 0.31 that is $0.0000374, which is why C8B's fill
recorded `fee_amount 0`. The rebate (0.021) exceeds the maker fee (0.0175) by
**20%**, so maker execution is net fee-*positive* under the current schedule —
by $0.0000075 on a 0.01 fill. Both sides round to zero at our size.

## 5. What this does to the proposal I made

**It kills E2.** My previous envelope proposal named, as one of two purposes,
"test whether a maker rebate credit follows a window whose maker volume we
recorded". At $1.00 of *committed capital* the run generates roughly $0.03 of
maker **notional** — about 1/1500th of the $52.26 that produced the confirmed
$0.51, and about 1/30th of the $1.02 of notional needed for a single cent.

**E2 is unreachable at this size by roughly two orders of magnitude, and no
amount of patience or window selection changes that.** The only things that
would are size or volume, and the owner has ruled out increasing either to
chase this. So the revised proposal must drop E2 rather than keep it as a
stated purpose it cannot serve.

**What survives:** the rebate is now *computable per fill from published
rules*, so it can be **accrued** and reported beside the confirmed line
without fabricating anything. `rebates.realized_ledger` still keeps accruals
in `estimated_not_counted`, and a rebate remains **CONFIRMED only when a
Gemini account record shows the credit**.

## 6. Prospective expectancy: ~97.6 bps must not be used

The owner asked specifically whether ~97.59 bps may be used in prospective
expectancy. **No** — for three separate reasons, any one of which is
sufficient:

1. **It is an aggregate over trading that was not this programme's.** The
   $52.26 denominator is mostly Aug 27 – Sep 2 activity across BTC5m, BTC15m,
   ETH5m, XRP5m and a tennis contract.
2. **It embeds an average fill price of ~0.535 that our policy does not
   produce.** The rebate is `0.021 × (1 − P)` of notional: at our observed
   fill prices it is 145 bps at P = 0.31 and **zero** outside the band. A
   single rate hides both.
3. **Below ~$1.02 of daily eligible notional the realised rate is exactly
   0 bps**, whatever the formula says, because of the round-down. At our size
   that is the operative rate.

**The defensible prospective statement is: $0.00, with the accrual reported
separately and never counted.**
