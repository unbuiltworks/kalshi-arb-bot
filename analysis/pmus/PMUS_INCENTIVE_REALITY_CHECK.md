# EXP-004 — PMUS INCENTIVE REALITY CHECK

Read-only. No deposit, no live order, no deployment, no infrastructure change.
No capital increase is proposed here; the economics are returned first, as asked.

Labels kept strictly separate: **EXPECTED** (formula × our volume) · **ELIGIBLE**
(program says we qualify) · **CREDITED** (the venue actually paid it) ·
**REALIZED** (settled cash). Nothing modeled enters PMUS net.

---

## CORRECTION REQUIRED TO PMUS_ECON_003

PMUS_ECON_003 as frozen reads "authoritative observed PMUS credited incentives
remain $0." **That is correct for liquidity rewards and taker rebates, and wrong
for maker rebates.** The authoritative venue-confirmed record is:

| stream | CREDITED | evidence |
|---|---|---|
| maker rebates | **+$2.1300** | 293 maker fills across 55 markets; `src/l5_breakeven.py` / `src/l5_capital_efficiency.py` / `src/l5_full_wallet_economics.py`, sourced from the realized bridge (venue numbers only) |
| liquidity rewards | **$0.0000** | every row SKIPPED or PENDING; accrued face value is never revenue |
| taker rebates | **$0.0000** | below the $250,000 first tier |
| fees charged | **$0.0000** | maker-only; the venue charged none against those 293 fills |

Credited maker-rebate rate: **0.384% of notional**, over 19 settled markets,
$554.4908 cost basis, 1,167.25 contracts.

Proposed amendment: PMUS_ECON_003 should read *"credited liquidity rewards and
taker rebates are $0; credited maker rebates are non-zero but small (+$2.1300
over 293 fills, 0.384% of notional). Modeled rebate is still never substituted
for credited."* Not edited unilaterally — flagged for your ruling.

## PRIOR ART CONFLICT (surfaced by the mandatory check)

`src/l5_full_wallet_economics.py` already contains a directly opposing
conclusion: **"THE BINDING CONSTRAINT IS NOT THE PAYMENT. Our credited rebate
rate, 0.384% of notional, is already inside L5's own range and above his early
months. The gap is that OUR TRADING LOSES 175 TIMES MORE PER DOLLAR THAN HIS
DOES — 6.458% against 0.037%."**

That contradicts PMUS_ECON_002 (missing incentives = 74.7% of the q25 gap).
Both can hold, because the populations differ:

| | population | trading loss per dollar |
|---|---|---|
| wallet economics | 19 settled live-probe markets, $554 basis | **−6.458%** |
| frozen queue-aware shadow | 5 BTC15m windows, $126.65/window | **−0.38%** at q25 |

The live probe lane loses 17× more per dollar than the BTC15m shadow does. So
"incentives are the gap" is true of the shadow policy and false of the live probe
lane. **PMUS_ECON_002's scope must be read as the queue-aware BTC15m shadow only.**

---

## 1. Exact current maker rebate formula

```
maker_rebate = 0.0125 × C × p × (1 − p)
```

- Source: `docs.polymarket.us/fees`, via `src/pmus_fees.py` (`SOURCE`).
- Verification strength: the venue publishes a 99-row table of what a 100-lot
  maker receives at every price $0.01–$0.99. **All 99 rows reproduce exactly**
  from θ_maker = 0.0125 with banker's rounding
  (`tests/test_pmus_fees.py` against `analysis/pmus/pmus_published_fee_schedule.json`).
  This pins the coefficient *and* the rounding rule.
- θ_maker did **not** change at the 2026-09-17 reschedule; only the taker
  coefficient moved (0.06 → 0.0695).
- Taker fee for symmetry: `0.0695 × C × p × (1 − p)` from 2026-09-17 04:00 UTC.

## 2. Rounding / crediting rules

- **Banker's rounding (ROUND_HALF_EVEN) to the cent, per fill.**
- **Credited at the point of trade**, to balance — not at settlement.
- It is a **credit, not a negative fee**: never netted against cost to make an
  action look affordable.
- Taker-rebate tiers on prior-calendar-month taker notional: $250k → 10%,
  $1M → 25%, $10M → 50%. This account is below the first tier, so 0.

**The per-fill cent rounding is economically decisive at small clips** (see §5).

## 3. Liquidity-reward eligibility and calculation for BTC15m

**BTC15m is in NO liquidity program.** Enumerated the public
`/v1/incentives` programs: `category=CRY` returns **54 markets, all of them
long-dated** — `cpc-btc-100k-*`, `cpc-btc-150k-*`, `cpc-btc-above-yr-*`,
`cpc-btc-hitprice-*`, `cpc-btc-pricerange-yr-*`. Searching every enumerated
entry for `updown`, `15m`, `cpc-btc-updown` returns **zero matches**.

The crypto program that does exist:

```
programId       crypto_20260924
programType     liquidityProgram
status          active
period          daily
rewardPool      400        (USD/day, shared)
discountFactor  0.3
targetSize      15000
start           2026-09-24T02:00:00Z
```

`maxSpread` is published on culture programs but absent from the crypto record,
so the crypto spread requirement is **NOT SOURCEABLE** from this endpoint.

The pool is a **shared daily pool across all 54 qualifying markets**, so any
individual participant's share is a function of competing size — not a rate we
can compute from published terms alone.

## 4. Do our account and markets qualify?

| | verdict | basis |
|---|---|---|
| maker rebate on BTC15m | **YES — ELIGIBLE and demonstrably CREDITED** | +$2.13 over 293 fills |
| liquidity rewards on BTC15m | **NO** | BTC15m is in no program; scope exclusion, not an account failure |
| liquidity rewards on long-dated BTC | ELIGIBLE by market scope; **share not computable** | shared $400/day pool, competing size unknown |
| taker rebate | **NO** | below the $250k first tier |

## 5. EXPECTED credited maker rebate per fill

`0.0125 × C × p × (1 − p)`, banker's-rounded to the cent, **per fill**:

| q | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 |
|---|---|---|---|---|---|---|---|
| 1 | **0.00** | **0.00** | **0.00** | **0.00** | **0.00** | **0.00** | **0.00** |
| 5 | 0.01 | 0.01 | 0.02 | 0.02 | 0.02 | 0.01 | 0.01 |
| 10 | 0.02 | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 | 0.02 |
| 25 | 0.05 | 0.07 | 0.08 | 0.08 | 0.08 | 0.07 | 0.05 |
| 50 | 0.10 | 0.13 | 0.15 | 0.16 | 0.15 | 0.13 | 0.10 |

Unrounded, to show what rounding destroys:

| q | 0.20 | 0.50 | 0.80 |
|---|---|---|---|
| 1 | 0.0020 | 0.0031 | 0.0020 |
| 5 | 0.0100 | 0.0156 | 0.0100 |
| 25 | 0.0500 | 0.0781 | 0.0500 |
| 50 | 0.1000 | 0.1562 | 0.1000 |

**q=1 earns exactly $0.00 at every price** — the maximum unrounded value is
$0.0031. Per-fill cent rounding annihilates the entire rebate at clip 1. The
rebate only becomes non-degenerate from about q=2 upward (q=2 at p=0.50 →
$0.0062 → $0.01, which *over*-pays by 61%).

## 6. EXPECTED incentive $/window on our OBSERVED queue-aware fills

Using the frozen queue-aware fill set (NOT optimistic), real fill prices and
sizes, cent-rounded per fill, n=5 windows:

| q | maker fills/win | maker notional/win | EXPECTED credited/win | unrounded | rate of notional |
|---|---|---|---|---|---|
| 1 | 9.2 | $5.23 | **$0.0000** | $0.0202 | 0.000% |
| 5 | 9.2 | $26.13 | $0.1060 | $0.1012 | 0.406% |
| 10 | 9.2 | $52.26 | $0.2000 | $0.2023 | 0.383% |
| **25** | **9.0** | **$126.65** | **$0.5020** | $0.4964 | **0.396%** |
| **50** | **9.0** | **$253.30** | **$1.0020** | $0.9929 | **0.396%** |

**Cross-validation:** our EXPECTED 0.396% of notional matches the independently
CREDITED 0.384% over 293 historical fills. The formula and our fill model agree.

## 7. Capital required (existing risk envelope)

Peak simultaneous cost basis on the observed queue-aware fill path:

| q | peak capital/window (mean) | worst window | contracts/window |
|---|---|---|---|
| 1 | $5.23 | $6.98 | 9.2 |
| 5 | $26.13 | $34.90 | 46.0 |
| 10 | $52.26 | $69.80 | 92.0 |
| **25** | **$126.65** | **$174.50** | 225.0 |
| **50** | **$253.30** | **$349.00** | 450.0 |

**The binding constraint is not capital, it is the loss budget.** The remaining
budget is **$0.49320**. A single 1830z-like residual episode cost **$20.25 at
q=25** — 41× the entire remaining budget. On the observed 1-in-5 frequency, q=25
is not fundable under the current envelope regardless of incentives, and neither
is q=5 ($4.05 worst-case residual, 8× the budget).

## 8. Do expected incentives move q25/q50 above zero, or toward $5?

| q | frozen net | EXPECTED rebate | net + rebate | gap to $0 | gap to $5 |
|---|---|---|---|---|---|
| **25** | −0.5682 | +0.5020 | **−0.0662** | $0.0662 | $5.0662 |
| **50** | −1.6646 | +1.0020 | **−0.6626** | $0.6626 | $5.6626 |

**No. Neither clears zero.** q=25 comes within **6.6 cents** of break-even —
88.3% of the deficit closed — but does not cross it. q=50 closes 60% and remains
$0.66 short.

Toward $5/window: the rebate supplies 9.9% of what q=25 needs and 15.0% of q=50.
For the maker rebate alone to pay $5/window at 0.396% of notional requires
**$1,263 of maker notional per window** — **10.0× our observed $126.65**, i.e.
roughly **q≈249**, at ~$1,263 peak capital and a proportionally scaled residual
tail of ~$200 per episode.

Rate comparison against L5:

| stream | L5 CREDITED | PMUS achievable |
|---|---|---|
| maker rebate | 0.4570% | **0.3964%** |
| liquidity rewards | 0.3631% | **0.0000%** (BTC15m in no program) |
| taker rebate | 0.0986% | **0.0000%** (below tier) |
| **total** | **0.9188%** | **0.3964% = 43% of his rate** |

Our maker-rebate rate is essentially at parity with his (0.396% vs 0.457%). The
entire remaining incentive gap is **structural program access**: the liquidity
program that BTC15m is not in, plus a taker tier we are three orders of magnitude
of volume away from.

## Conclusion

The incentive stream is real, correctly formulated, already credited, and at
parity with L5 per dollar of maker notional — and it is **still not enough**,
because (a) BTC15m has no liquidity program, which is 40% of L5's incentive rate,
and (b) our frozen deficit slightly exceeds what the maker rebate pays at the
clip sizes the loss budget could ever support.

Two things would have to change together, and neither is proposed here: access to
a liquidity program covering the traded series, and a residual tail small enough
to be funded. **No capital increase is recommended on this evidence** — at q=25
the incentive closes 88% of a deficit whose tail risk is 41× the remaining budget.
