# 0x8dxd — reconciled financial bridge, from the venue's own accounting

**CORRECTION FIRST. My earlier conclusion that 0x8dxd's profitability was
primarily referral-driven was WRONG.** The venue's own account-level
accounting shows referral income is **2.28% of settled P&L** and all
incentives together are **5.59%**. The remaining 94.4% is trading. The
corroborating report is right and my conclusion is withdrawn. The error
was one of scope: I measured **BTC5m only**, over **2026-03-12 to
08-25**, which is 14.3% of this account's trades and excludes its four
profitable months almost entirely.

**Production disarmed. No executor or parameter changed. No
infrastructure added. Probability model non-voting.**

---

## 1. What the ~$2.1M is — field by field, scope confirmed

Source: `GET https://data-api.polymarket.com/v2/user-stats?user=0x63ce…ba9a`,
field `all_time_pnl`. Scope is **whole account, all markets, all time
from join date 2025-12-04** — not BTC5m, not a single strategy.

The field meanings are not guessed. Every one is pinned by an identity
that holds **exactly** in the venue's own numbers:

| identity | result |
|---|---|
| `fees + fees_refunded == fees_paid` | EXACT |
| `trade_pnl + fees_paid == position_pnl` | EXACT → **`trade_pnl` is BEFORE fees** |
| `position_pnl − unrealized_pnl == realized_pnl` | EXACT |
| `maker_rebate + taker_rebate + reward + yield + referral == wallet_income` | EXACT |
| `realized_pnl + wallet_income == settled_pnl` | EXACT → **`settled_pnl` INCLUDES incentives** |
| `settled_pnl + unrealized_pnl == economic_pnl` | EXACT |

So **`settled_pnl = $2,171,918.83` is realized trading P&L net of actual
fees, PLUS all incentives.** It is not gross proceeds and not a
redemption total. The ~$2.1M is corroborated.

**One figure that is NOT the $2.1M:** `/v2/value` returns **2110.8** —
dollars, being the mark of the 12 still-open positions, and it matches
the $2,110.80 of unclaimed redeemable winnings I reported earlier. A
reading of `2110.8` as "$2.1M" would be a coincidence worth ruling out,
and the user-stats fields above rule it out: the $2.1M is independently
real.

`sponsored_income $50,019.23` is **absent from `wallet_income`**, so it
is **not** inside `settled_pnl`. Its scope is unconfirmed and it is
excluded from the bridge below rather than assumed either way.

## 2. The bridge (all-time, whole account)

| step | amount | % of USDC volume |
|---|---|---|
| gross trading result, before fees (`trade_pnl`) | **+$2,372,850.15** | +2.61% |
| fees charged (`fees`) | −$4,494,761.88 | −4.95% |
| fees refunded (`fees_refunded`) | +$4,172,346.52 | 92.8% **of fees charged** |
| **= net fees actually borne** (`fees_paid`) | **−$322,415.36** | −0.355% |
| **trading P&L net of actual fees** (`position_pnl`) | **+$2,050,434.78** | **+2.26%** |
| less open-position mark | −$2.11 | |
| **= realized trading P&L** | **+$2,050,432.67** | |
| maker_rebate | +$71,344.68 | 3.28% of settled |
| taker_rebate | +$122.25 | 0.01% |
| reward_income | +$583.13 | 0.03% |
| yield_income | +$0.23 | 0.00% |
| referral_income | +$49,435.88 | **2.28%** |
| **= incentives** (`wallet_income`) | **+$121,486.16** | **5.59%** |
| **= SETTLED P&L** | **+$2,171,918.83** | |
| plus unrealized | +$2.11 | |
| **= ECONOMIC P&L** | **+$2,171,920.94** | |

Volume: `volume_usdc $90,842,347.69`, `volume $200,617,496.12`,
`trade_count 5,160,944`, `trades 34,810`.

### Priority 2 — rebates reconciled, total vs March-only

| | all-time (venue) | my tape (2026-03-12 → 08-25) | share |
|---|---|---|---|
| maker_rebate | **$71,344.68** | $13,537.44 | 19.0% |
| taker_rebate | $122.25 | $122.25 | **100%** |
| referral_income | $49,435.88 | $49,435.88 | **100%** |
| reward_income | $583.13 | $583.13 | **100%** |
| yield_income | $0.2271 | $0.23 | 100% |

Taker rebate, referral, reward and yield reconcile **exactly** — every
one of those credits fell inside my capture window. Only
`maker_rebate` differs, and the missing **$57,807.23 (81%)** was earned
**before 2026-03-12**, which my tape never covered. Nothing is
contradictory; my figure was a window, not a total.

## 3. Monthly bridge — which months and strategies actually made money

Differenced from the venue's own cumulative hourly series
(`/v2/user-pnl?user=…&interval=all`, 8,328 points, `fidelity 1h`).

| month | net trading | incentives | settled | **edge (bps of volume)** | volume_usdc | fee refund % |
|---|---|---|---|---|---|---|
| 2025-12 | **+$321,062** | $0 | +$321,062 | **+232** | $13.8M | **no fees charged** |
| 2026-01 | **+$518,789** | $17,194 | +$535,983 | **+224** | $23.2M | 92.1% |
| 2026-02 | **+$739,989** | $31,836 | +$771,824 | **+236** | $31.4M | 93.1% |
| 2026-03 | **+$470,459** | $32,143 | +$502,602 | **+216** | $21.8M | 93.0% |
| 2026-04 | +$175 | $24,904 | +$25,079 | — | $23k | — |
| 2026-05 | +$1 | $3,373 | +$3,374 | — | $15 | — |
| 2026-06 | −$0 | $7,107 | +$7,107 | — | $0 | — |
| 2026-07 | −$0 | $4,772 | +$4,772 | — | $0 | — |
| **2026-08** | **−$1,601** | $158 | **−$1,443** | **−28** | $567k | 0.0% |
| 2026-09 | +$1,056 | $0 | +$1,056 | — | $0 | — |

**The answer to "which strategies produced confirmed positive net
trading returns":**

**The two-sided maker strategy, 2025-12 through 2026-03.** Four
consecutive months at **+216 to +236 bps of volume** — remarkably
stable — totalling **+$2,050,299 of net trading P&L** against **$81,173
of incentives in the same months (3.8%)**. This is a real trading edge,
not an incentive artifact.

**December 2025 settles the fee-refund question.** That month the venue
charged **zero fees** and paid **zero rebates**, and the account still
returned **+232 bps**. The edge therefore does not depend on the fee
refund; the charge-then-refund mechanism only began in January. From
January the refund ratio is 92–93% every single month, which is
consistent with the venue's declared international schedule
(`takerOnly: true` — a maker pays no fee) rather than with a
discretionary programme.

**That 92–93% ratio is also new evidence on a question I had marked
UNOBSERVABLE.** Per-fill role is still absent from both routes, but at
the account level, a charge-then-refund ratio that stable implies the
**overwhelming majority of fills were MAKER**. This is account-level
evidence, not per-fill, and it is reported as such.

**April–July: they stopped trading** (volume $23k → $15 → $0 → $0) while
continuing to draw referral income — which is exactly why a
referral-weighted read of those months misled me.

**August: the 97–99¢ single-sided era LOST money, −28 bps**, confirmed
independently by the venue's own monthly accounting.

## 4. Priority 3 — the August audit reconciles exactly

My BTC5m-only reconstruction gave **−$545.86**. The venue gives August
net trading **−$1,601** and September **+$1,056** on zero volume — the
September line is August's redemptions settling after the month
boundary. Combined:

> **−$1,601 + $1,056 = −$545**, against my **−$545.86**.

Agreement to the dollar, with the residual being redemption-timing: I
booked settlement entitlement at window close, the venue books it at
redemption. August volume `$567,207` also matches my BTC5m August buy
cost of `$569,371` to within 0.4%, confirming August was essentially
all BTC5m. **The August figure is audited and stands.**

## 5. Priority 4 — the on-chain March reconstruction is now largely moot

Its purpose was to recover SPLIT-funded cost basis and settle whether
March was profitable. **The venue's own accounting answers that
authoritatively: March net trading +$470,459 at +216 bps.** My
BTC5m-slice figure of −$39,585 was the understated-cost artifact I
flagged, and it is now superseded rather than merely suspect.

On-chain work would still attribute that profit to specific windows and
show the SPLIT mechanics directly. It remains blocked
(`polygon-rpc.com`, `rpc.ankr.com/polygon`, `api.polygonscan.com` all
refused by this environment's network policy) and I am **not** proposing
the change, because the profitability question it was commissioned to
answer is now answered from the venue. Available on request.

## 6. What this means for replicating profitable decisions

The profitable policy is now identified with confidence: **two-sided
maker quoting at mid prices, ~93% maker, 106–207 fills per window,
earning ~+220 bps of traded volume.** That is the straddle shape, not
the late-favorite shape, and it matches the three gaps already
documented in `0X8DXD_VS_OUR_STRADDLE.md`.

Two things it does **not** establish, and neither should be assumed:

1. **Edge per dollar is not edge per clip.** Their +220 bps was earned
   at ~$1,000 per window as a dominant maker with queue priority. A $2
   clip at the back of the queue is a different fill distribution
   entirely, and adverse selection is exactly what the back of the queue
   buys. Nothing here measures our fill distribution.
2. **It is a different venue.** Theirs declares `takerOnly: true`, so a
   maker pays nothing. PMUS currently declares `feeCoefficient 0.095`
   with **no BTC5m rebate programme** (0 of 100 published programmes
   cover any BTC or updown market), and our own observed PMUS pair costs
   have been **above par** (1.0367–1.0725). A +220 bps gross edge does
   not survive a 4.95%-equivalent fee unless the maker exemption applies
   to us, which is unconfirmed for this instrument.

**The open question is therefore no longer "did 0x8dxd have an edge" —
it did, ~+220 bps for four months. It is "does PMUS pay a maker on
BTC5m, and can a $2 clip reach the front of that queue."** The first
half is a documentation/account question answerable read-only; the
second is a shadow measurement. Neither requires an executor change.
