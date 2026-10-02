# PMUS sports liquidity rewards vs PMUS crypto

2026-10-02. Kalshi frozen at `1ac07f69`, untouched. L5 accounting benchmark
(`1f43db3b`) untouched. No PMUS order submitted. No new executor built.

**Central question:** can PMUS pay us enough for L5-style liquidity provision
that venue compensation exceeds the cost of producing that liquidity?

**Answer: not demonstrated, and our own account is evidence against it — but
the gap between what the rules permit and what we earned is large, unexplained,
and resolvable read-only. No live probe yet; a read-only per-second shadow
first.**

---

## 1. Authoritative current PMUS incentive mechanics

Source: [docs.polymarket.us/incentives/liquidity](https://docs.polymarket.us/incentives/liquidity),
[Incentives API](https://docs.polymarket.us/api-reference/incentives/overview),
[fees](https://docs.polymarket.us/fees). All SOURCE-OBSERVED.

The three compensation streams are separate and must stay separate:

| # | Stream | Mechanics | Requires a fill? |
|---|---|---|---|
| 1 | **Per-fill maker rebate** | `0.0125 x C x p x (1-p)`, credited to balance at the time of the fill as `commissionNotionalCollected`. Taker fee `0.06 x C x p x (1-p)`. Cent-rounded, banker's rounding; small trades round to $0.00 | **Yes** |
| 2 | **Resting-liquidity rewards** (`liquidityProgram`) | Per-second random order-book snapshot; `Score = DiscountFactor ^ (ticks from best price) x Order Size`; each side independently normalized to 1.0 per snapshot provided Target Size is met on that side; every second weighted equally; **rewards under $1.00 are not paid**; paid within 5 business days + 2 to credit | **No** |
| 3 | **Category/program-specific** | Parameters vary *per program*, not per category: `discountFactor` 0.10–0.90, `targetSize` 2,000–150,000, `maxSpread` absent or 0.05–0.07, `period` one of `live` / `day_of` / `daily` / `daily_event` / `early` | **No** |

**Is the per-fill maker formula identical across sports and crypto?** Yes. The
fee schedule is venue-wide — one rate, `0.0125 x C x p x (1-p)`, with no
contract or category exception published. All category variation lives in
stream 2's per-program parameters. This is SOURCE-OBSERVED from the fee docs
and corroborated ACCOUNT-OBSERVED by our own credited rebates (below).

### The structural contrast with Kalshi, which is the whole story

Kalshi scores distance from a **Reference Price sitting Target/5 deep**, so a
maker can rest well behind the touch at a full 1.0 multiplier. **PMUS scores
distance from the best price**, with a discount factor of 0.15–0.35 on most
programs. One tick back already scores 15–35%; two ticks 2–12%.

**PMUS pays only for liquidity at the touch — which is exactly the liquidity
that gets adversely selected.** On Kalshi you can be paid to be safe. On PMUS
you are paid only to be exposed. That difference, not the pool size, is what
determines whether this transfers.

## 2. Complete current program census

`scripts/pmus_incentive_census.py` -> `data/pmus/incentives/programs.json`.
Source `GET https://gateway.polymarket.us/v1/incentives` (public; the
authenticated `api.polymarket.us` mirror 401s without a key). Fetched
2026-10-02T00:00Z, 92 pages, **45,883 programs / 73,855 (market, period)
rows, 73,851 active**. All `programType: liquidityProgram` — there is no
second program type.

| Category | Active rows |
|---|---|
| **SPR** sports | **66,305** |
| POL politics | 6,497 |
| CUL culture | 525 |
| MAC / TECH / FIN / CLI | 312 |
| **CRY crypto** | **54** |
| GEO / SCI | 16 |

Parameter distribution (active): `discountFactor` 0.30 (25,507), 0.25
(15,808), 0.20 (9,841), 0.40 (6,096), 0.15 (5,771), 0.90 (706);
`targetSize` 2,000 (15,461) up to 150,000; `maxSpread` absent on 66,596 of
73,851; `period` `day_of` (31,525), `live` (30,454), `daily_event` (10,595),
`daily` (1,165).

**PMUS is a sports incentive venue that also lists crypto.** 66,305 to 54 is
not a tilt, it is a different business.

## 3. Sports vs crypto

| | **A. PMUS crypto** | **B. PMUS NFL** | **C. Best other sports** |
|---|---|---|---|
| Active markets | 54 | ~20,000 across 10 NFL programs | CFB/MLB/UFC/soccer/tennis |
| Program | one: `crypto_20260924` | `nfl_spreads_live` etc. | `cfb_t1_totals_live` |
| Pool / market | **$400 / daily** | **$10,350** (`nfl_spreads_live`) | $2,700 (`cfb_t1_totals_live`) |
| Target size | 15,000 | 28,000 | 5,500 |
| Discount factor | 0.30 | 0.25 | 0.15 |
| Instruments | **long-dated BTC thresholds** (`cpc-btc-100k-12-31-2026`) | live game lines | live game lines |
| Settlement horizon | months | hours | hours |
| Fill required | no | no | no |

**Crypto is not a candidate, and not for lack of pool.** All 54 markets are
long-dated BTC price-threshold contracts settling in months. There is no
short-horizon crypto program at all — no PMUS analogue of the BTC5m/BTC15m
regime the L5 engine was built for. A 15,000 target against a $400 daily pool
on a market that resolves in December is a different activity from L5's.

## 4–5. Best executable candidates, and conservative obtainable reward

`scripts/pmus_reward_share.py` walks each real book, infers the tick, scores
every level by `size x df^ticks`, and **enforces the Target Size qualification
gate on each side** (a side below target earns nothing; an earlier revision
omitted this and reported $422 for a q1 order against a near-empty offer side
— the same error the Kalshi pass made, reading an empty book as a free pool).

Top MODELED instantaneous shares, books at 2026-10-02T00:05Z:

| Program | Pool | Target | df | Side score | q1 | q10 | Capital (q10) |
|---|---|---|---|---|---|---|---|
| `cfb_t1_totals_live` | $2,700 | 5,500 | 0.15 | 19.4 (ask) | $66.19 | $459.26 | $6.60 |
| `nfl_player_props_live` | $1,350 | 2,000 | 0.35 | 7.4 | $80.45 | $388.15 | $7.00 |
| `nfl_totals_live` | $8,550 | 11,000 | 0.25 | 208 | $20.45 | $196.10 | $9.80 |
| `nfl_spreads_live` | $10,350 | 28,000 | 0.25 | 1,540 | $3.36 | $33.38 | — |
| crypto `crypto_20260924` | $400 | 15,000 | 0.30 | — | far below $1 | — | — |

**The largest headline pool is not the best.** `nfl_spreads_live` has 7.7x
`cfb_t1_totals_live`'s pool and earns 14x less per contract, because its
competing near-touch score is 80x larger.

### These MODELED figures are not the conservative estimate, and should not be used as one

They come from **one instantaneous book**, assume 100% uptime across the whole
program period, assume every second qualifies, and assume no competitor
responds to our quote. The high values arise specifically where the near-touch
book is thin while a far-away wall (e.g. 15,625 contracts at $0.99) carries the
side over Target Size. Whether such a state persists across a period's seconds
is **UNKNOWN from a single snapshot**, and it is precisely the thing a
per-second sample would settle.

### The conservative estimate is ACCOUNT-OBSERVED, and it is two to three orders of magnitude lower

From our own PMUS account (`analysis/l5/incentives/TRANSFER_2PT51.md`,
`analysis/l5/venue_rebate_credits.json`, venue-confirmed):

| Stream | Account-lifetime credited |
|---|---|
| Maker rebates on fills | **$9.14** over 737 credited fills of 1,174 ($0.0124/fill) |
| Liquidity rewards PAID | **$7.04**, 44 market-rows, 3 transfers ($0.16/rewarded market) |
| **Confirmed incentives** | **$16.18** |
| Excluded: PENDING $0.70, SKIPPED $1.37 | accrued, not credited |

**Conservative obtainable reward per eligible market: $0.16** (ACCOUNT-OBSERVED).
Not $66. The modeled and observed figures differ by roughly **400x**, and that
discrepancy is the most important result here.

## 6. Expected adverse-selection cost — ACCOUNT-OBSERVED

From `analysis/l5/cross_shadow_live/ledger.jsonl`, 39 distinct settled PMUS
sports markets with `realized_confirmed: true` (one `venue_unreported` value
reported, not silently zeroed):

| | |
|---|---|
| Realized trading/settlement | **−$44.58** |
| Incentives credited in that scope | +$0.02 |
| Per settled market | **−$1.14** |
| Net per market incl. credited incentives | **−$1.14** |
| Leagues | Setka/ITF tennis (23), NFL (4), ATP/WTA (4), MLB, EPL, Dota2, WTT |

**This is the cost of producing sports liquidity on this account, measured, not
modelled: $1.14 per market.** Against a $3 target the shortfall is $4.14 per
opportunity; against the $5 stretch, $6.14.

## 7. Capital and capacity

Capital is not the binding constraint and never was. q10 at the touch in the
best candidates costs **$0.80–$9.80 per market**. Reward per capital-hour is
not meaningfully computable: the numerator is the 400x-uncertain quantity above,
and dividing an untrusted numerator by a precise denominator manufactures
precision. **Reward/capital-hour: UNKNOWN**, pending section 8.

Capacity, SOURCE-OBSERVED: 66,305 active sports rows across ~10 NFL, ~8 CFB
and a dozen other programs, with `live` and `day_of` periods renewing daily.
Eligible-opportunity count is not the limit either. **Plausible
opportunities/day: hundreds; realized net per opportunity: −$1.14.** Scaling a
negative number is the one thing capacity guarantees.

## 8. Is a bounded live measurement probe justified? — **No, not yet**

The gate is not economics, it is interpretability. A live probe run today could
not distinguish between three live hypotheses, and would therefore teach
nothing:

1. the near-touch-thin states that produce the high modeled shares are real and
   persistent, and our $16.18 reflects quoting the wrong markets and sides;
2. those states are transient, the per-second average collapses them, and the
   modeled figures are an artifact of single-snapshot sampling;
3. the Target Size gate behaves differently than I modelled — in particular
   whether a far-away wall at $0.99 counts toward the accumulation.

**All three are decidable read-only, from per-second sampling of reward-program
books over one full program period.** No orders, no capital, no executor.

### The existing data does not settle it, and I checked before proposing work

Our existing PMUS evidence establishes the **cost** side well (39 settled
markets, 1,174 fills, 737 credited rebates, venue-confirmed settlement) but not
the **reward** side: the per-market attribution of the $7.04 is blocked by our
own audit truncation (`rows_detail` was `rows[:50]` against 79 rows), and no
existing tape samples a reward-program book per second across a period. That is
the demonstrated gap.

### What to run, reusing what exists

`src/pmus_sports_collector.py` already snapshots `/v1/incentives` periodically
and reads `/v1/markets/{slug}/book`; `src/pmus_incentive_audit.py` already
reads credited earnings. The work is a read-only sampling loop over a chosen
program's markets plus the already-staged `rows_detail` cap fix to close the
$7.04 attribution — **no new executor, no new collector, no order path**.

**Recommendation: WAIT. No live PMUS order.** The $3 target is $4.14 away from
measured reality, and the only evidence that could close that gap is the
modelled 400x, which I do not trust enough to risk capital against. If the
per-second shadow shows the thin-side states are real and persistent, the probe
becomes justified and I will say so; if it shows they collapse, PMUS sports
answers the central question in the negative and we stop.
