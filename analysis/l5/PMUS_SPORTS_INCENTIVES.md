# What our sports fills are actually worth: current PMUS terms, and credited dollars

OWNER P0: *"I want actual current sports terms plus actual credited sports
incentives—not modeled crypto coefficients."*

Both halves are answered from primary sources, fetched **2026-09-10 17:09–17:32Z**.
No crypto coefficient is reused anywhere in this document. The live probe
(`a88dff63` / deploy `7ed717fc`) was not touched.

---

## Answer 1 — a $1.00 completion is worth 0.47%–0.63% of the capital it ties up, and nothing else

At a pair cost of exactly $1.00 the matched-pair P&L is **$0.00 by
construction** — you pay a dollar, you redeem a dollar. So the entire
economic content of a $1.00 completion is the maker rebate on the two legs:

> **Maker Rebate:** "Applied using the same formula structure with Θ = -0.0125,
> generating a maximum rebate of \$0.31"… "Maker rebates are credited at fill
> time"… "All fees and rebates are rounded to the nearest \$0.01 using banker's
> rounding"… "Effective exchange-wide from 12 AM ET, Wednesday July 1, 2026"
> — docs.polymarket.us/fees, fetched 2026-09-10T17:14Z

`rebate = 0.0125 · C · p · (1−p)` per leg, so `0.025 · C · p · (1−p)` for a
completed pair, against `$1.00 · C` of capital:

| entry price p | rebate per contract-pair | % of the $1.00 tied up | min contracts for 1¢ on a leg |
|---|---:|---:|---:|
| 0.50 | $0.006250 | **0.625%** | 1.60 |
| 0.60 | $0.006000 | 0.600% | 1.67 |
| 0.70 | $0.005250 | 0.525% | 1.90 |
| 0.75 | $0.004688 | **0.469%** | 2.13 |
| 0.90 | $0.002250 | 0.225% | 4.44 |

The doc says the schedule "applies symmetrically across all contract types"
and carries **no per-category variation** — sports, crypto and politics are
charged and rebated identically. So the 0.0125 coefficient is not a crypto
model we are transplanting; it is the published exchange-wide rate, and
Answer 2 shows it predicts our actual sports credits to the cent.

**What that buys, in context.** Two of our three sports markets that settled
today lost **−$3.90** and **−$3.60** on directional residual. At 0.625%, one
−$3.90 residual costs **$624 of completed pair notional** to repay. Our
session notional cap is $7.50.

> The maker rebate is real, it is credited immediately, and it is roughly
> **two orders of magnitude too small to fund a residual mistake.** It pays
> for completing; it does not pay for being wrong.

---

## Answer 2 — credited sports incentives: $0.17, and it reconciles exactly

Every sports market this account settled today, from the probe's own
venue-sourced settlement rows (`commissionNotionalCollected`, our side only):

| market | trade rows | credited | modeled from the published formula | match |
|---|---:|---:|---:|---|
| `aec-setkamecz-mulpat-holtom-2026-09-10` | 11 | $0.1000 | $0.10 | ✅ |
| `aec-cs2-ica-ff-2026-09-10` | 6 | $0.0400 | $0.04 | ✅ |
| `aec-setkamecz-krajan-svoluk-2026-09-10` | 3 | $0.0300 | $0.03 | ✅ |
| **total** | **20** | **$0.1700** | **$0.17** | **exact** |

Cost basis $30.9563 → **credited incentive is 0.549% of basis.**

The reconciliation is stronger than the totals suggest: the venue's own count
of *fills that carried a credit* (9 / 4 / 3) equals, market for market, the
count of fills whose modeled rebate rounds to at least one cent (9 / 4 / 3).
The two sub-cent fills in `cs2-ica-ff` (1.00 contracts at $0.56 → $0.00308
raw) are credited nothing by the venue and modeled as nothing here. **Banker's
rounding at the cent is where our small size leaks.**

### The three streams for these markets, kept apart

| market | matched pairs | pair cost | matched-pair P&L | residual | residual P&L | incentives | venue realized |
|---|---:|---:|---:|---:|---:|---:|---:|
| mulpat-holtom | 19.87 | 0.9477 | **+$1.0393** | 0.00 | — | $0.1000 | unconfirmed |
| cs2-ica-ff | 4.62 | 0.9800 | +$0.0924 | −9.08 | **−$3.9952** | $0.0400 | −$3.9028 |
| krajan-svoluk | 0 | — | $0 | −11.24 | **−$3.6028** | $0.0300 | −$3.6028 |

Note the contrast with L5's crypto book, where incentives run ~4× trading
P&L. Here the one cleanly completed market earned **$1.04 of matched-pair
contribution (DERIVED arithmetic — that market is `SETTLED_REALIZED_UNCONFIRMED`,
the venue has not yet published its realizedPnl) against $0.10 of
venue-credited incentive — roughly 10× the subsidy.** On
sports, at our size, *the trade is the business and the rebate is a tip.*
That is the opposite of the L5 September picture and it should change how we
read fidelity gaps against him.

---

## Answer 3 — the terms of the other two sports programs, and why neither pays us

### Volume Incentive Program — pays us $0.00 by construction

> "**Eligible Volume**: Taker-side notional only. Only trades executed between
> 3c and 97c (inclusive) count." — docs.polymarket.us/incentives/volume

We are maker-only by owner rule. Taker-side notional is zero, so this program
is unreachable for us at any size. Not a tuning question; a structural one.

### Liquidity Incentive Program — active, large, and shaped against our design

Live census by `GET /v1/incentives?symbols=…` (public, documented, no auth),
fetched 2026-09-10T17:2xZ, against the full open slate of 33,517 sports markets:

| market type | live | with an ACTIVE program |
|---|---:|---:|
| MONEYLINE | 175 | **174** |
| SPREAD | 7,362 | 5,682 |
| TOTAL | 5,050 | 3,690 |
| PROP | 12,952 | 7,036 |
| DRAWABLE_OUTCOME | 49 | 27 |

So sports liquidity rewards are emphatically **ACTIVE** — this is not the
expired PMI crypto picture. The scoring, though, is a different function from
the one that paid L5:

> "Every second, a random snapshot of the order book is taken. Your score for
> each resting order is: `Score = Discount Factor ^ (ticks from best price) ×
> Order Size`"… "the bid side and ask side are each independently normalized
> to 1.0 per snapshot, provided Target Size is met on that side"… "If Target
> Size is reached before your price level, your order will not score,
> regardless of how close it is to the best price."… "Rewards under \$1.00 are
> not paid out." — docs.polymarket.us/incentives/liquidity

And the pool is not per market:

> "The total amount paid out for a time period, shared across the program's
> markets — never summed per market." — polymarket.us/rewards

Which gives, for size `X` at the touch on one side of one market, against
measured other-depth `D`, over a fraction `duty` of the period, in a program
whose pool `P` is shared by `M` markets:

    payout  =  P · X/(X+D) · duty / (2M)

Measured against real books (one live market sampled per program, 61
programs, at 17:29Z) at our working size of 12 contracts (~$6):

| programId | markets | pool | measured other-depth | our share | payout @100% duty |
|---|---:|---:|---:|---:|---:|
| `nfl_player_props_live_20260902` | 1,314 | $28,500 | 0.5 | 0.959 | **$10.40** |
| `nfl_period_spreads_live_20260904` | 310 | $3,750 | 1.1 | 0.917 | $5.55 |
| `nfl_period_spreads_day_of_20260904` | 310 | $1,875 | 1.1 | 0.917 | $2.77 |
| `nfl_props_early_20260902` | 1,868 | $10,500 | 0.5 | 0.959 | $2.70 |
| `cfb_t2_ml_live_20260904` | 92 | $1,200 | 22.5 | 0.348 | $2.27 |
| `ufc_prelim_moneyline_live_20260712` | 18 | $800 | 176.8 | 0.064 | $1.41 |

**6 of 46 programs clear the $1.00 minimum at 100% duty. 40 do not clear it
at all**, so for those the correct expected value is exactly $0.00, not a
small number.

### Three reasons this is a ceiling, stated plainly

1. **The depth sample is taken at the wrong time.** NFL player-props books at
   17:29Z on a Thursday are nearly empty — 0.51 contracts at the touch — and
   the pools above pay for the *live* period on Sunday, when those books will
   be orders of magnitude deeper. A share of 0.959 will not survive kickoff.
   I could not measure live-period depth today because no NFL, CFB or UFC
   event is live; that measurement is genuinely missing rather than estimated.
2. **`duty` is the term we are worst at.** The program pays for *resting,
   sampled every second*. R5F rests `maker_rest_max_s = 5.0` and holds one
   outstanding order. Our duty cycle against a 3-hour live period is close to
   zero, and it is close to zero *by design* — the design chases fills, and
   this program pays for orders that never fill.
3. **One market at a time is the wrong shape.** The `1/(2M)` term means
   reward accrues per market occupied. A program with 1,314 markets sharing a
   pool rewards breadth exactly the way the August PMI programme did.

---

## What our own lane looks like right now

**Zero of the 239 sports slugs on our analysis record — markets we have
traded, observed or ranked — carries an active program.** That is *not* evidence they never did: `statuses=closed` returns
zero venue-wide, so the endpoint is de facto **active-only** and cannot speak
to a settled market. Verified with a positive control (a known programmed
politics slug returns 1 row; a mixed batch returns only that row; a nonsense
slug returns 0), so the filter works and the zero is about *time*, not
about the query.

What the endpoint *can* speak to is the live slate, and there the finding is
sharp: **there are no live tennis match markets at all.** 82 tennis markets
are open and every one is a FUTURE — the US Open is over. Our probe's lane
for the last four days has closed underneath it, which is why it is now
working a county-cricket market (`aec-county-not-ham-2026-09-08`) that
carries no program either.

---

## What I am not concluding

- **No change to the live probe, the ceiling, or any cap.** This is
  measurement. `7ed717fc` is untouched and still holding.
- **The $10.40 row is not an opportunity estimate.** It is what the rule
  would pay against a book measured three days before the pool it belongs to
  opens. Treat it as an upper bound with a known direction of error.
- **The 0.549%-of-basis credit rate is three markets and $31 of basis.** It
  reconciles exactly to the published formula, which is the part worth
  trusting; the rate itself will move with price and size.
- **Nothing here says sports is worse than crypto.** It says the sports
  subsidy is paid for a behaviour (continuous resting breadth) that our
  current engine does not perform, while the sports *trade* — one clean
  completion at 0.9477 earning $1.04 — carried itself without subsidy.

## Provenance

Raw artifacts under `data/pmus/sports_incentives_2026-09-10/`: the program
census by market type (gzipped, with `fetched_at`, source URL and query
param), the per-program depth measurements, the shared-pool economics, the
fill-by-fill credited reconciliation, and verbatim copies of the three venue
doc pages. Public unauthenticated reads only; the credited figures come from
the probe's own authenticated settlement rows. No credential was used, read
or printed here.
