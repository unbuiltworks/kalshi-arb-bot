# PMUS BTC15m — products, measurement, transfer, infrastructure, economics

All venue facts below are read from **public, unauthenticated** PMUS endpoints
and from the venue's own published fee/rewards pages. No credentials are held
in this container, none were requested, and no PMUS order was placed or
prepared. Gemini B2 is untouched and still frozen.

---

## P0 — the products, exactly

Discovery path: `GET /v1/series` → match slug → `GET /v1/events?series_id=<id>`
→ each event carries its `markets[]` → `GET /v1/markets/{slug}/bbo` and
`/book`.

| series id | slug | title | created | events | cadence |
|---|---|---|---|---|---|
| 375 | `btc-updown-15m` | Bitcoin Up or Down (15m) | 2026-09-22T15:15:08Z | **128** | **exactly 15 min, 127/127 gaps** |
| 378 | `btc-updown-1h` | Bitcoin Up or Down (1h) | 2026-09-22T15:16:02Z | 43 | exactly 60 min, 42/42 gaps |
| 387 | `btc-updown-5m` | Bitcoin Up or Down (5m) | 2026-09-23T06:10:06Z | **0** | declared, not launched |
| 388 | `btc-updown-4h` | Bitcoin Up or Down (4h) | 2026-09-23T06:10:07Z | **0** | declared, not launched |

Also present and **not** this instrument: `btc-strike-hourly`, `btc-range-hourly`,
`btc-strike-daily`, `btc-range-daily`, `btc-strike-weekly`, `btc-range-weekly`,
`btc-hit-monthly`.

- **Continuous recurrence: YES for 15m and YES for 1h.** Both series pre-create
  windows hours ahead (15m out to 2026-09-23T23:00Z, 1h out to
  2026-09-24T09:00Z) and both had exactly one live window at observation.
- **Identifiers.** Event `btc-updown-15m-YYYY-MM-DD-HHMMz`; market
  `cpc-btc-updown-15m-YYYY-MM-DD-HHMMz`. One market per event.
- **Contract structure.** Binary. Two `marketSides` (`Yes` `long: true`,
  `No` `long: false`), `notional` $1.00 to the winner.
- **Window timing.** `startTime` on the :00/:15/:30/:45 boundary; market
  `endDate` is start + 15 min + ~10 s. The clock is *literal* — no translation.
- **Tick size.** `orderPriceMinTickSize: 0.01`, uniform. Price range 0.01–0.99.
  (Unlike Kalshi, which tapers to 0.001 in the tails.)
- **Settlement source.** CF Benchmarks **BRTI**.
- **Exact resolution formula**, quoted from the market description:
  > settles to Up if the Bitcoin price at `T_end` is greater than or equal to
  > the price at `T_start`. … in the last minute before each of those two
  > times, 60 BRTI prices are collected. The official value for that time is
  > the simple average of these prices, rounded to the nearest 2 decimal places.

  This is the **same rule, from the same index, to the same rounding** as
  Kalshi KXBTC15M and as Gemini's BTC15m.
- **Fees.** Published schedule, exchange-wide, effective 12 AM ET
  2026-09-17 (`docs.polymarket.us/fees`):
  > `Fee = Θ × C × p × (1 − p)` — **taker Θ = 0.0695**, **maker Θ = −0.0125**.
  > "Maker rebate is applied at the point of trade."

  A taker rebate tier exists but starts at **$250,000** of prior-calendar-month
  taker volume. We are nowhere near it, so it is **not available to us** and is
  counted at zero.
- **Maker/taker treatment.** The maker is **paid** `0.0125·C·p(1−p)` at the
  moment of fill — no enrolment, no tier, no program. This is the single most
  important economic fact in this document.
- **Ordinary-user incentives.** One liquidity program covers these markets,
  read from `polymarket.us/rewards`:
  ```json
  {"programId":"crypto_up_down_20260922","name":"Crypto UP Down",
   "period":"daily_event","rewardPool":30,"discountFactor":0.25,
   "targetSize":500,
   "symbols":[{"slug":"cpc-btc-updown-15m-…"},{"slug":"cpc-btc-updown-1h-…"}]}
  ```
  Pool **$30 per daily_event period**, shared across every market in the
  program (≈120 markets/day), target size 500 contracts resting per side,
  discount factor 0.25. Scoring is per the published LIP: every second,
  `score = discount_factor ^ (ticks from best) × size`, pool split pro-rata.
- **Public endpoints.** `/v1/markets/{slug}/bbo` and `/v1/markets/{slug}/book`
  work. **`/v1/markets/{slug}/trades` is a 404 — there is no public trade tape.**
  The authenticated WS (`wss://api.polymarket.us/v1/ws/markets`) carries the
  real trade tape and the live book; `src/pmus_ws_evidence_recorder.py` already
  speaks it.
- **Execution compatibility.** `src/pmus_q1_executor.py` contains **zero**
  sports-specific references — its order path is slug-based
  (`/v1/order/preview`, `/v1/orders`, `/v1/order/{id}/cancel`) and works on a
  `cpc-` slug exactly as on an `aec-` one. Two real blockers are listed in P3.

### A correction to my own method

I first inferred "no crypto incentive" from `/v1/incentives?category=crypto`
returning zero. That filter is **not honoured** — `category=sports` returns
zero too, and so does a nonsense value. I also walked `/v1/incentives`
to 40,000 rows; that walk was **invalid**, because the `nextPageToken` does
not advance (page 2 returns page 1's first row). Neither result is used. The
program above comes from the rewards page's own payload.

---

## P1 — measurement

### The quick answer

> **A typical PMUS BTC15m window that trades at all carries ≈15,000 contracts
> (median), and about 61% of windows carry none at all.**

Contracts per window are **OBSERVED exactly**: `sharesTraded` is a monotone
per-market counter and each market lives exactly one window, so its value at
close *is* that window's volume. Across all **89 closed windows** since launch:

| | value |
|---|---|
| closed windows | 89 |
| windows with any volume | **35 (39.3%)** |
| zero-volume windows | **54 (60.7%)** |
| contracts/window, all closed (p50) | 0 |
| contracts/window, **traded windows only** | p10 552 · p25 7,133 · **p50 14,956** · p75 25,220 · p90 29,782 · max 48,112 |

The zero windows are **not** a listing artefact: traded and zero windows are
metadata-identical (`active: true`, `closed: true`, `manualActivation: false`,
same creation batch). The pattern is a near-perfect alternation —
`.V.V.V.V.V` across most of the history — which is the signature of one
dominant participant working every other window, not of a dormant product.

### Live window, from the tape

`data/pmus_btc15m/`, public book tape, 1.5 s polling, window
`cpc-btc-updown-15m-2026-09-23-1345z`, first 7.5 minutes:

| | value |
|---|---|
| observed contracts | **11,927** |
| derived notional (bursts × mid in force) | **$2,249** |
| two-sided samples | **100%** (167/167) |
| spread | median **$0.03**, p90 $0.04 |
| depth at touch | median bid **2,600** / ask **10,000** contracts |
| mid | median 0.21, p10 0.125, p90 0.29 |
| volume by third of span | 2,762 / 3,453 / **5,712** — *back-loaded* |
| fraction of samples inside the sports band 0.20–0.80 | **0.54** |

**Opposite-side interaction persists and intensifies**: more than a third of
the window's volume arrives in its final third, with both sides quoted
throughout. That is precisely the condition an L5-style repair/recycle needs,
and it is the condition Gemini BTC15m does not offer (median 4 prints/window,
49.5 s median inter-print gap).

### The measurement ceiling, stated plainly

**Trade count is not observable from the public gateway, and I am not
reporting one.** Two hard limits:

1. `/v1/markets/{slug}/trades` → 404.
2. The public book is a **~30-second cached snapshot**: across 88 consecutive
   `/book` polls there were only **9 distinct ladder states**, with identical
   `transactTime` repeated, median change gap **31.2 s**. Polling faster buys
   nothing.

So the module emits `print_bursts` — polls at which the counter moved — named
and labelled a **LOWER BOUND**, never a trade count. Inter-trade gaps are
similarly floored at the ~30 s refresh and are not real gaps. Real trade
counts, taker sides and a live book require the authenticated WS, and this
container holds no PMUS credentials.

### Hourly (secondary)

22 closed windows, **12 zero**, median 0, recent traded windows 3.4k–6.0k
contracts. Thinner than 15m both per window and per unit time. 15m is the lane.

---

## P2 — PM International L5 BTC15m │ PMUS BTC15m

| property | PM International (L5 corpus) | PMUS BTC15m | class |
|---|---|---|---|
| instrument | BTC up/down, fixed window | BTC up/down, fixed window | **IDENTICAL** |
| resolution rule | endpoint-vs-endpoint comparison | Up iff `BRTI(T_end) ≥ BRTI(T_start)` | **IDENTICAL** |
| settlement index | CF Benchmarks BRTI | CF Benchmarks **BRTI**, 60-price average, 2 dp | **IDENTICAL** |
| window length / clock | 15 min, literal TTE | 15 min, literal TTE from `startTime`/`endDate` | **IDENTICAL** |
| payout | $1.00 to the winner | $1.00 to the winner | **IDENTICAL** |
| contract structure | binary YES/NO | binary `Yes`/`No` `marketSides` | **IDENTICAL** |
| price representation | 0–1 decimal probability | 0.01–0.99 decimal | **IDENTICAL** |
| order-book mechanics | CLOB, resting limit orders, maker/taker | CLOB, resting limit orders, maker/taker | **IDENTICAL** |
| **tick size** | finer than $0.01 on the L5 corpus | **$0.01 uniform** | **DIFFERENT** — coarser; a one-tick improvement costs more here |
| maker treatment | maker rebate credited at fill | **maker rebate credited at fill**, Θ = −0.0125 | **TRANSLATABLE** — same mechanism, our own coefficient |
| taker fee | symmetric `Θ·C·p(1−p)` | same shape, Θ = 0.0695 | **TRANSLATABLE** |
| liquidity rewards | large, and the bulk of L5's income | **$30/day shared across ~120 markets** | **DIFFERENT** — same mechanism, ~2 orders of magnitude smaller |
| taker rebate | L5 earned +$7,323 | tier starts at $250k/month — **not accessible to us** | **DIFFERENT** |
| spread | UNKNOWN for the corpus | median **$0.03** (3 ticks) | **UNKNOWN** |
| depth | UNKNOWN for the corpus | 2,600 bid / 10,000 ask at touch | **UNKNOWN** |
| all-participant volume/window | **UNKNOWN** (corpus records L5's own fills, not the venue tape) | 14,956 contracts median in traded windows | **UNKNOWN** |
| interaction rate | 693,916 fills / 5,546 windows = **125 fills/window** (L5's own) | ≥15 print bursts in 7.5 min, floored by a 30 s cache | **UNKNOWN** |
| repair opportunity | oscillation through zero; 84.3% of episodes FLIP | volume back-loaded, both sides quoted throughout | **TRANSLATABLE** |
| zero-flow windows | UNKNOWN | **60.7%** | **UNKNOWN** |

### The central question

> **Can the behavioural model learned from L5 BTC15m transfer to PMUS BTC15m
> with substantially less venue translation than Gemini or Kalshi?**

**Yes — and the margin is large.** Six of the model's load-bearing properties
are *IDENTICAL*, not translatable: instrument, resolution rule, settlement
index, window length, payout and contract structure. The two economics rows
are *TRANSLATABLE* because they are the same functional form with different
coefficients. Critically, the maker-rebate-at-fill mechanism — the thing that
*was* L5's business — exists on PMUS for an ordinary account with no enrolment.

Two things must not be waved through. The tick is **coarser** ($0.01 vs finer),
which makes every price improvement a bigger commitment. And the liquidity
reward pool is **$30/day shared across ~120 markets**, against a program that
paid L5 $26,956. Same mechanism, different order of magnitude. Common branding
proves nothing; these two rows are where it stops being the same venue.

---

## P3 — admitting PMUS BTC15m to the existing pipeline

Target: `PMUS tape/book → normalized inventory state → frozen L5 controller →
shadow decisions → action-by-action evidence`.

**Most of this already exists and is already crypto-ready.** `PMUSAdapter` in
`src/l5_venue_adapter.py` already carries `tick = 0.01` (exactly PMUS crypto's
`orderPriceMinTickSize`), `min/max price 0.01/0.99` (exactly PMUS crypto's
range), the same `/v1/markets/{slug}/bbo` provenance, and `maker_fee()`
returning 0 because the rebate is never counted toward edge.

### The sports-specific assumptions, enumerated exactly

1. **`pmus_book_tape.LIVE_EVENTS` pins `categories=sports`.**
   *Impact:* BTC15m is invisible to discovery. *Status:* **fixed today** —
   `series_slugs()` added as a second slug source, `--series btc-updown-15m`,
   with a 45 s re-slate (a 15-minute market's whole life is shorter than the
   300 s sports re-slate) and the universe bounded to what is actually
   trading. Eight tests pin it. Sports discovery is unchanged and still the
   default.
2. **`PMUSAdapter.normalize()` falls back to `SPORTS_TTE_DEFAULT_S = 1e9`**
   ("no settlement clock → coarsest bucket") when `settlement_t` is None.
   *Impact:* this is the documented PMUS clock defect — *635/635 decisions key
   `tte>10m`*. *Work needed:* **none in the adapter.** The parameter already
   exists; the caller simply passes the market's `endDate`. On BTC15m the L5
   clock becomes **native**, which is the single largest fidelity win available
   anywhere in this programme.
3. **`PMUSAdapter.settlement()` returns "game end (no fixed expiry; TTE bucket
   coarsest)".** *Impact:* provenance only, but it would mislabel every crypto
   row. *Work needed:* a crypto branch returning the BRTI rule.
4. **`pmus_q1_executor.MIN_PRICE, MAX_PRICE = 0.20, 0.80` — the entry band.**
   *Impact:* material. Only **54%** of observed BTC15m mid samples fall inside
   it, and the median mid in the measured window was 0.21, at the very edge.
   A sports band applied to crypto would refuse roughly half the regime.
   *Work needed:* an explicit, separately reviewed crypto band — **not** a
   quiet widening of the sports one.
5. **`pmus_q1_executor.taker_fee` uses Θ = 0.06.** The venue's published
   coefficient has been **0.0695** since 2026-09-17. *Impact:* this is a live
   defect on the **execution** path, not a crypto issue: it understates taker
   cost by 15.8% and therefore **loosens the fee-safe complement bound** on
   every PMUS market, sports included. *Work needed:* correct it, and re-check
   any bound derived from it. Flagged, not silently patched.

Nothing else blocks admission. No new collector, no new adapter, no new
controller, no second strategy implementation.

---

## P4 — four venues, consistent units

**OBSERVED** — measured from each venue's public data.

| | PM Intl L5 BTC15m | **PMUS BTC15m** | Kalshi KXBTC15M | Gemini BTC15m |
|---|---|---|---|---|
| contracts / window | UNKNOWN (corpus = L5's own fills) | **14,956** (p50, traded windows) | **1,712,809** (p50) | UNKNOWN |
| notional / window | UNKNOWN | **~$2,249 per 7.5 min observed** | **$672,552** (p50) | **$122** (p50) |
| $ / day, all participants | UNKNOWN | not yet a full day of tape | ~$65M (p50 × 96) | **$19,935** |
| trades / window | 125 *L5's own fills* | **not observable publicly** | **24,701** (p50) | 4 (p50) |
| trades / minute | UNKNOWN | not observable publicly | **1,767** (p50) | 0.321 |
| zero-flow windows | UNKNOWN | **60.7%** | **0 of 14** | 8 of 96 |
| spread | UNKNOWN | **$0.03** median | **$0.01** | $0.060 |
| depth at touch | UNKNOWN | 2,600 / 10,000 contracts | deep, both sides | thin |
| two-sided | yes | **100% of samples** | **14/14 windows** | intermittent |
| tick | finer than $0.01 | **$0.01** uniform | $0.01 mid, $0.001 tails | $0.01 |
| taker fee | `Θ·C·p(1−p)` | **Θ = 0.0695** | `quadratic`, mult 1, **Θ unverified** | 0.07 |
| **maker treatment** | **rebate at fill** | **rebate at fill, Θ = −0.0125, no tier** | **none** | rebate exists; **$0.51 ever credited** |
| accessible incentive | L5: +$26,956 rewards | **$30/day shared, ~120 markets** | **$0 — withdrawn 2026-05-12** | unresolved |
| infrastructure readiness | n/a | **highest**: collector fixed today, executor already generic, WS recorder exists | read-only collector runs; no execution | driver armed-capable, B2 frozen |

**DERIVED — modelled, not realized.** At the published maker rebate
`0.0125·C·p(1−p)`, one contract made at p = 0.21 (the measured median mid)
earns **$0.00207**; at p = 0.50 it earns $0.003125. On a 14,956-contract
window at a 10% maker share that is **$3.10/window at p = 0.21**, or
**$4.67** at p = 0.50 — before any trading P&L, fees on the taker side, or
adverse selection, and **before** the 60.7% of windows that do not trade.
Scaled at 39.3% window participation that is roughly **$117/day modelled** at
a 10% share. This is a *modelled* figure from a published coefficient and an
assumed share. **It is not realized, not observed, and no probe has produced
a PMUS BTC15m fill.**

For contrast on the same line: Gemini BTC15m models to **$11–23/day
provisional**, and its only venue-confirmed cash ever is **$0.51**.

---

## Decision

> **Should PMUS BTC15m proceed immediately to a read-only L5 shadow? YES.**

The reasons, in order of weight:

1. **The clock becomes native.** PMUS's documented L5 defect is that 635/635
   sports decisions collapse into one TTE bucket. BTC15m serves a real
   15-minute expiry, so the frozen model keys on literal TTE — the same
   fidelity Kalshi and Gemini have, on the venue where we already have
   execution infrastructure and an account.
2. **Six properties are IDENTICAL, not translatable.** Same instrument, same
   BRTI rule, same rounding, same window, same payout, same binary structure as
   the corpus the model was learned on.
3. **The maker rebate is credited at fill, to an ordinary account, with no
   tier and no enrolment.** That is the mechanism that was L5's entire
   business, and it exists here. Kalshi withdrew its BTC15m subsidy in May;
   Gemini has credited us $0.51 in total.
4. **The flow is real and two-sided, and it is back-loaded** — both sides
   quoted in 100% of samples, with the largest third of volume arriving last,
   which is exactly when an inventory recycler needs the opposite side.
5. **A shadow costs nothing and risks nothing.** It is read-only, keyless, and
   the collector is already running.

What this decision is **not**: it is not a claim that PMUS BTC15m is
profitable, not a request to trade it, and not a reason to touch B2. The two
honest reservations are on the record — the reward pool is $30/day against
L5's $26,956 program, and **60.7% of windows do not trade at all**, so the
capacity figure will be roughly 39% of any naive per-window extrapolation.

### What proceeds

- the read-only collector, already running, gathering consecutive windows;
- the frozen L5 controller over that tape, with `settlement_t` supplied so TTE
  is literal, producing action-by-action shadow decisions;
- no PMUS orders, no authorization, no change to the entry band without an
  explicit separate review.

### What does not change

- **Gemini B2 stays exactly as frozen** — `spec_sha bfe7ba23…`, four fresh
  ledgers, $10.16 ceiling, awaiting only explicit approval.
- **The Kalshi evidence stands** and its collector keeps running, watching for
  the one event that would revive that lane: a liquidity program reappearing
  over a liquid 15-minute crypto series.
