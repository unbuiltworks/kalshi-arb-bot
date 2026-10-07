# PMUS incentives and rebates — current state, resolved from authoritative evidence

**Lane C.** Retrieval window for this document: **2026-10-07T00:2x–00:5xZ**.
Read-only throughout. No order placed, previewed, signed or cancelled. No
credential value printed. No git command run. `analysis/l5/s3/**`,
`live_exec*`, `deploy/**` and Railway untouched.

## 0. Three corrections to the brief, up front

**(1) The "SEVEN credits totalling $0.51 / $52.2573 maker notional / 97.59 bps
on POST /v1/transfers, type AdminCredit, purpose 'Predictions maker rebate'"
is GEMINI, not Polymarket US.** Verified in
`REBATE_ECONOMICS_1.md`, `REBATE_APPLICABILITY_1.md` and
`analysis/gemini/rebate_reconciliation_20260921T175241Z.json`. `/v1/transfers`,
`/v1/tradevolume`, `/v1/notionalvolume`, `AdminCredit` and `eid` are Gemini API
surfaces; the sources cited are `developer.gemini.com/prediction-markets/maker-rebate-program`
and `gemini.com/fees/predictions`. The 97.59 bps figure therefore **does not
belong in the PMUS column at all**, and
`REBATE_APPLICABILITY_1.md §6` already rules it out for prospective use even on
Gemini.

**(2) On PMUS, maker rebates are not transfers.** They are credited per fill as
a negative `commissionNotionalCollected` on our own execution
(`TRANSFER_2PT51.md`; `src/pmus_incentive_audit.py::_commission`). Only the
*liquidity reward* payouts arrive as `ACTIVITY_TYPE_TRANSFER`. So fill-level
attribution for PMUS maker rebates **does exist** — unlike Gemini — and the
repo has it print-by-print.

**(3) The PMUS actually-credited total is larger than the brief states.**
Account lifetime to 2026-09-14T22:59:21Z: **maker rebates $9.14 over 737
credited fills of 1,174; liquidity rewards $7.04 (3 transfers, 44 PAID rows);
confirmed incentives $16.18.** (`TRANSFER_2PT51.md`, Ledger C.)

## 1. Access and sources

| source | status |
|---|---|
| our own committed account artifacts | **USED** — primary |
| live authenticated PMUS read | **NOT AVAILABLE.** No `PMUS_*`/`POLYMARKET_*` credential is present in this container (checked by name, presence-boolean only: none found). No GET was issued against the account. |
| `docs.polymarket.us`, `polymarket.us/rewards`, `gateway.polymarket.us` | **EGRESS-BLOCKED** from this container. `curl` → `CONNECT tunnel failed, response 403`; proxy status logs `connect_rejected … docs.polymarket.us:443`, policy denial. WebFetch → `EGRESS_BLOCKED`. |
| verbatim venue doc copies committed 2026-09-10 | **USED** — `data/pmus/sports_incentives_2026-09-10/doc_incentives_{overview,liquidity,volume}.md`, quoted `docs.polymarket.us/fees` in `analysis/l5/PMUS_SPORTS_INCENTIVES.md` |
| WebSearch (extended), 2026-10-07 | **USED** for change-detection only, labelled SECONDARY |

Because the doc host is unreachable, every "current" claim below is either
(a) our own 2026-09-10 verbatim capture, or (b) a 2026-10-07 secondary
search result. Where they conflict the conflict is recorded, not resolved.

---

# BUCKET 1 — PUBLISHED / PROGRAM-ELIGIBLE

## Q1. Maker commission / rebate mechanics

> "**Maker Rebate:** Applied using the same formula structure with Θ = -0.0125,
> generating a maximum rebate of \$0.31" … "Maker rebates are credited at fill
> time" … "All fees and rebates are rounded to the nearest \$0.01 using
> banker's rounding" … "Effective exchange-wide from 12 AM ET, Wednesday
> July 1, 2026"
> — `docs.polymarket.us/fees`, quoted verbatim in `analysis/l5/PMUS_SPORTS_INCENTIVES.md`,
> **retrieved 2026-09-10T17:14Z**

So, per maker fill:

    maker_rebate = round_half_even( 0.0125 × C × p × (1 − p) , $0.01 )

Makers pay **no** commission: `maker fees $0.00 — OBSERVED (Polymarket charges
makers nothing)` (`VENUE_INCENTIVE_RECONCILIATION.md`, 2026-09-10), and
`venue_charged_us_on_our_fills_usd 0.0000` across all five audits
(`PMUS_INCENTIVE_BREAKEVEN.md §D`).

**Change since our capture (SECONDARY, 2026-10-07):** the *taker* theta was
raised exchange-wide effective **2026-10-01** to **0.0695** (max $1.74 per 100
contracts at p=0.50); the **maker rebate Θ = −0.0125 was left unchanged**
(max −$0.31 per 100 contracts).
— WebSearch extended, 2026-10-07T00:3xZ, summarising `docs.polymarket.us/fees`.
**This matters economically:** the published maker rebate is an absolute
coefficient, *not* a share of the taker fee, so a taker-fee rise does **not**
raise our rebate. Our rebate rate is unchanged by the 10-01 schedule change.
**Our code is stale on the taker side:** `src/pmus_incentive_audit.py` pins
`TAKER_RATE = D("0.06")`. Not edited here (read-only lane); flagged.

## Q2. Programs that exist today

From the verbatim overview page (`doc_incentives_overview.md`, retrieved
2026-09-10T17:09Z) — still the live program set per the 2026-10-07 search:

| program | access | pays for |
|---|---|---|
| Deposit Incentive | Open | qualifying deposit |
| Refer-A-Friend | Open | referrals |
| Daily Trading Incentive | **Coming Soon** | daily trading activity |
| Deposit and Trading Incentive | **Coming Soon** | deposit + trading |
| **Volume Incentive** | Open (institutional) | **taker** volume share |
| **Liquidity Incentive** | Open (institutional) | **resting orders, whether they fill or not** |
| Market Maker Program | **Application** | stable liquidity across markets |
| Referral Incentive | Application | affiliate referrals |

Plus the exchange-wide **maker rebate**, which is a fee-schedule term rather
than a "program".

## Q3. Eligibility

* **Maker rebate** — no enrolment. Any passive fill. Eligible price range is
  the whole quotable band (`p` from $0.01 to $0.99); PMUS publishes **no**
  $0.20–$0.80 eligibility band. (That band is Gemini's —
  `REBATE_APPLICABILITY_1.md`. Do not carry it across.)
* **Liquidity Incentive** — Open, no signup. Per-side qualification:
  > "Target Size is the minimum aggregate size of resting orders (across all
  > participants) needed on a side of the book for that side to qualify… If
  > Target Size is reached before your price level, your order will not score,
  > regardless of how close it is to the best price."
  > — `doc_incentives_liquidity.md`, retrieved 2026-09-10T17:2xZ
* **Volume Incentive** — > "**Eligible Volume**: Taker-side notional only. Only
  trades executed between 3c and 97c (inclusive) count." NBA example adds a
  "Minimum **\$500 notional** required to be eligible for payouts".
  — `doc_incentives_volume.md`, retrieved 2026-09-10T17:2xZ
  **We are maker-only by owner rule, so our eligible volume is structurally
  $0.00.** Not a tuning question.
* **Market Maker Program** — application, contractual. Never applied for.
* **Daily Trading / Deposit+Trading** — "Coming Soon". Not available.

## Q4. Calculation formulas

**Maker rebate** `0.0125 × C × p × (1 − p)`, banker's-rounded to the cent.
As a rate *of maker notional* (`p·C`) this is

    rebate / notional = 0.0125 × (1 − p)

= **125 bps as p→0, 87.5 bps at p=0.30, 75.0 bps at p=0.40, 62.5 bps at
p=0.50, 50.0 bps at p=0.60, 37.5 bps at p=0.70.** The rate rises as price
falls, while the dollar amount peaks at p=0.50.

**Liquidity Incentive** (verbatim, `doc_incentives_liquidity.md`):
> "Every second, a random snapshot of the order book is taken. Your score for
> each resting order is: `Score = Discount Factor ^ (ticks from best price) ×
> Order Size`" … "the bid side and ask side are each independently normalized
> to 1.0 per snapshot, provided Target Size is met on that side."

Our derived payout form (`PMUS_SPORTS_INCENTIVES.md`), for size `X` at the
touch against other-depth `D`, duty fraction `duty`, pool `P` shared by `M`
markets: `payout = P · X/(X+D) · duty / (2M)`.

**Volume Incentive**: your share of total eligible taker notional × pool.

## Q5. Caps and tiers

* Maker rebate: **max $0.31 per 100 contracts** (i.e. at p=0.50); a hard
  per-fill **cent floor** — anything under $0.005 raw pays **$0.00**. No tiers,
  no volume ladder, no 30-day notional tier published.
* Liquidity Incentive: > "Is there a cap on how much one person can earn? **No.**
  Your payout is purely proportional to your share of the total score." But
  > "Rewards under \$1.00 are not paid out."
  Target Size acts as a de-facto gate, not a cap.
* Volume Incentive: pool-capped per contract; $500 notional minimum in the
  published NBA instance.

## Q6. Market / category restrictions

Our 2026-09-10 primary capture: the fee schedule "applies symmetrically across
all contract types" and carries **no per-category variation** — sports, crypto
and politics rebated identically (`PMUS_SPORTS_INCENTIVES.md`).

**CONFLICT, unresolved → see UNKNOWN.** Third-party pages retrieved 2026-10-07
assert per-category *taker* thetas on PMUS (tech/mentions 0.04, sports 0.05,
politics ~0.04, crypto 0.07, with maxima $1.00–$1.75 per 100 shares) and
separately quote a "maker rebate 20%/25% of matched taker fee" split by
category. The 20%/25% language is **Polymarket international's** Maker Rebates
Program (`help.polymarket.com/articles/13364471`), a different venue, and must
not be imported. The per-category taker theta claim is plausible but
unverifiable from here (host blocked). **For the maker rebate the only
primary-sourced coefficient remains Θ = −0.0125, exchange-wide.**

Liquidity Incentive coverage *is* strongly category-shaped. Live census by
`GET /v1/incentives?symbols=…` (public, no auth), 2026-09-10T17:2xZ, over the
full open sports slate of 33,517 markets:

| market type | live | with an ACTIVE program |
|---|---:|---:|
| MONEYLINE | 175 | 174 |
| SPREAD | 7,362 | 5,682 |
| TOTAL | 5,050 | 3,690 |
| PROP | 12,952 | 7,036 |
| DRAWABLE_OUTCOME | 49 | 27 |

## Q7. Does BTC15m (`btc-updown-15m`) qualify?

**Maker rebate: YES.** Exchange-wide, no category carve-out, no enrolment. A
BTC15m maker fill earns `0.0125 × C × p × (1−p)`, cent-floored.

**Liquidity Incentive: NO ACTIVE PROGRAM FOUND, and this is the strongest
negative we hold.** Nearest direct evidence:
`analysis/l5/evidence/PMI_REWARD_STATE_2026-09-10/reward_state.json`
(fetched 2026-09-10T14:45:33Z) — every up/down series across BTC/ETH/SOL/XRP/
HYPE/BNB/DOGE at 5m/15m/4h is `reward_state: "EXPIRED"`,
`rewards_daily_rate: null`, `in_sampling_rewards_list: false`, and
`sampling_markets_containing_updown: 0` out of a 17,562-market sweep.
**Caveat, stated: that artifact is `venue: polymarket_international`, not PMUS.**
The PMUS-side evidence is weaker but points the same way:
`our_traded_slugs_programs.json` (2026-09-10T17:18Z,
`gateway.polymarket.us/v1/incentives`) returns `with_programs: 0` for all 239
slugs we have traded/observed/ranked, and
`PMUS_INCENTIVE_BREAKEVEN.md §D` records
`published_programs_covering_our_markets = 0` in all five audits.
**A current, PMUS-specific `GET /v1/incentives?symbols=btc-updown-15m-*` read
is the missing measurement and is blocked by egress here.**

**Volume Incentive: NO**, structurally — maker-only, so taker notional is zero.

## Q8. Do sports / other categories qualify differently?

**Yes, and the difference is entirely in the liquidity program, not the
rebate.** Sports liquidity programs are emphatically ACTIVE (table in Q6);
crypto up/down liquidity rewards are expired/absent. Measured against real
books at our working size of 12 contracts (~$6), 61 programs sampled
2026-09-10T17:29Z:

| programId | markets M | pool | other-depth D | our share | payout @100% duty |
|---|---:|---:|---:|---:|---:|
| `nfl_player_props_live_20260902` | 1,314 | $28,500 | 0.5 | 0.959 | $10.40 |
| `nfl_period_spreads_live_20260904` | 310 | $3,750 | 1.1 | 0.917 | $5.55 |
| `nfl_period_spreads_day_of_20260904` | 310 | $1,875 | 1.1 | 0.917 | $2.77 |
| `nfl_props_early_20260902` | 1,868 | $10,500 | 0.5 | 0.959 | $2.70 |
| `cfb_t2_ml_live_20260904` | 92 | $1,200 | 22.5 | 0.348 | $2.27 |
| `ufc_prelim_moneyline_live_20260712` | 18 | $800 | 176.8 | 0.064 | $1.41 |

**6 of 46 clear the $1.00 minimum at 100% duty; 40 do not clear it at all, so
their correct expected value is exactly $0.00.** These are ESTIMATES (bucket 2)
and upper bounds: depth was sampled on a Thursday afternoon for pools that pay
during Sunday live play, and our duty cycle is near zero by design
(`maker_rest_max_s = 5.0`, one outstanding order).

## Q9. Payout timing and mechanism

* **Maker rebate** — "credited at fill time", as a negative
  `commissionNotionalCollected` on our own execution. Immediate, per fill, no
  minimum other than the cent rounding.
* **Liquidity Incentive** — > "Rewards are calculated within 5 business days of
  each time period ending and credited to your account within 2 business days
  of the end of that calculation." … "Rewards under \$1.00 are not paid out."
  Mechanism on our account: `ACTIVITY_TYPE_TRANSFER`, `currency: USD`,
  `description: "liquidity reward payout"`, status
  `ACCOUNT_BALANCE_CHANGE_STATUS_COMPLETED`, carrying **no** market/order/fill
  reference and empty `metadata`. Attribution to markets is only possible by
  matching against `/v1/incentives/earnings` rows.
  (A 2026-10-07 secondary result says international Polymarket pays liquidity
  rewards "daily at midnight UTC, $1 minimum" — different venue, not applied.)
* **Volume Incentive** — pool settlement per contract; never credited to us.

---

# BUCKET 2 — ESTIMATED (modelled; never counted as income)

| line | estimate | basis |
|---|---|---|
| maker rebate on any prospective maker fill | `0.0125·C·p·(1−p)`, cent-floored | published formula; reproduces 23/25 of our actual credits, and 19/19 of the exactly-priced ones (`PMUS_INCENTIVE_BREAKEVEN.md §A`) |
| rate of maker notional | 37.5–87.5 bps over p∈[0.30,0.70] | `0.0125·(1−p)` |
| liquidity reward at 12 contracts, 100% duty | $0.00 for 40 of 46 programs; $1.41–$10.40 for 6 | `PMUS_SPORTS_INCENTIVES.md`, Thursday-afternoon depth, upper bound |
| Gemini-side accrual on our fills | $0.000132 across all five fills, **paid $0.00** | `REBATE_APPLICABILITY_1.md §4` |

Nothing in this bucket is in any realized or $/day figure anywhere.

---

# BUCKET 3 — ACTUALLY CREDITED (venue-confirmed cash, with an id)

## PMUS — account lifetime 2026-08-14 → 2026-09-14T22:59:21.565847915Z

| stream | credited | records | source |
|---|---:|---|---|
| **maker rebates (per-fill commission credits)** | **$9.14** | 737 credited fills of 1,174 | `/v1/portfolio/activities` walk, 1,269 rows / 64 pages, `commissionNotionalCollected` on our own execution; `TRANSFER_2PT51.md` Ledger C |
| **liquidity rewards PAID** | **$7.04** | 44 PAID rows → 3 transfers: `CCRG00G5T00Q` $2.88 (09-09T12:28:25Z), `CD6Q1RAG000Q` $1.65 (09-10T13:14:05Z), `CG78P5C0000Q` $2.51 (09-14T22:39:20Z) | `/v1/incentives/earnings` + activity walk |
| taker rebates | **$0.00** | maker-only; `venue_charged_us_on_our_fills_usd 0.0000` | `PMUS_INCENTIVE_BREAKEVEN.md §D` |
| Volume Incentive | **$0.00** | taker notional is zero | ibid. |
| **CONFIRMED INCENTIVES TOTAL** | **$16.18** | | |

Deliberately excluded: rewards **PENDING $0.70** (11 rows) and **SKIPPED
$1.37** (24 rows) — accrued, not credited. Excluded: the $10.00 ISV goodwill
credit `CAVGFQ5ZA00M` (2026-09-06 outage remediation) — neither trading P&L nor
an incentive. Excluded: the formula estimate $9.31, which corroborates the
$9.14 and is never counted.

**Verified sub-cohorts (consistent, narrower windows, not additive to the above):**

* 25 credited prints, 2026-09-06/07 spring-probe ladder, **$0.4200** on
  119.49 contracts / $56.8624 notional = **73.9 bps of notional**, $0.00351 per
  share. Every row carries a PMUS slug and `credited_to_us: true`.
  (`analysis/l5/venue_rebate_credits.json`, `VENUE_INCENTIVE_RECONCILIATION.md`,
  generated 2026-09-10.) Six same-session prints of 0.11–1.41 shares were
  credited **$0.00** — the cent floor, OBSERVED.
* 20 sports fills settling 2026-09-10, **$0.1700** credited vs **$0.17**
  modelled — exact, market for market, and the venue's count of credited fills
  (9/4/3) equals the count whose modelled rebate reaches a cent (9/4/3).
  Cost basis $30.9563 → **54.9 bps**. (`PMUS_SPORTS_INCENTIVES.md`.)

**Attribution status.** PMUS maker rebates are attributable **to the fill**.
PMUS liquidity rewards are attributable to the program and the account with
certainty, to specific markets **not yet** (the earnings-row join was blocked by
our own 50-row truncation of a 79-row list; fix staged, not deployed), and to a
specific order or fill **never** — the venue does not publish that linkage on a
payout.

## Gemini — a different venue, recorded here only to keep it out of the PMUS column

7 credits, **$0.51**, all `type: AdminCredit`, `purpose: "Predictions maker
rebate"`, `status: Complete`, each with an `eid`
(368822540373, 368823437387, 368824547191, 368825667773, 368827790024,
368835478761, 368835488128), 2026-08-27 … 2026-09-08, against $52.2573 of
venue-reported maker notional (`POST /v1/tradevolume`, cross-checked to
$52.2604 from `POST /v1/mytrades`) = **97.59 bps**, `attribution_confidence:
UNATTRIBUTED` / WINDOW_LEVEL because `/v1/transfers` carries no order id,
symbol or fill linkage. **CONFIRMED as stated in the brief — and it is Gemini.**
`REBATE_APPLICABILITY_1.md §6` already forbids using 97.59 bps prospectively,
for three independent reasons.

**Combined venue-confirmed incentive cash across both venues: $16.69.**

---

# BUCKET 4 — UNKNOWN

1. **The live `docs.polymarket.us` / `polymarket.us/rewards` text as of
   2026-10-07.** Egress policy denies the host. Everything "published" above is
   either a 2026-09-10 verbatim capture or a 2026-10-07 secondary search
   summary.
2. **Whether PMUS now varies theta by category**, and if so whether the maker
   rebate Θ varies with it. Our primary capture says no variation; third-party
   pages say the taker side does. Unresolved.
3. **Whether `btc-updown-15m` carries a live PMUS Liquidity Incentive program
   today.** The direct `reward_state` artifact is the *international* venue; the
   PMUS evidence is a zero over 239 unrelated slugs. A current
   `GET /v1/incentives?symbols=btc-updown-15m-*` is the one read that settles
   it and it is blocked here.
4. **Our account's credited incentives after 2026-09-14T22:59:21Z.** No
   PMUS credential in this container, no later audit artifact committed. Three
   weeks of possible credits are simply unmeasured — a hole, not a zero.
5. **Live-period liquidity-program depth.** The 61-program depth sample was
   taken when no NFL/CFB/UFC event was live. Genuinely missing, not estimated.
6. **Whether `trade.realizedPnl` is already net of
   `commissionNotionalCollected`.** A $9.14 double-count risk. The settling test
   (reconstruct one settled market from fills, compare to `realizedPnl`) has not
   been run.
7. **Whether the Market Maker Program would accept us and on what terms.**
   Never applied for; terms are contractual and unpublished.
8. **Which program paid L5's $35,228 of MAKER_REBATE.** No program identifier
   appears on an activity row.

---

# What is and is not transferable from L5

**NOT transferable — stated explicitly.** L5's incentive observations
(`MAKER_REBATE $35,228.32 / 110 rows`, `TAKER_REBATE $7,525.73 / 73 rows`,
`REWARD $28,797.69 / 20 rows`, $71,551.74 total, 85% of it in 2026-08; the
~$3.2k–$3.7k/day of 2026-08-29/30/31 in `Q1_NOREBATE.json`) **must not be
carried onto our account.** Four independent reasons, any one sufficient:

1. **Different account.** The venue publishes no per-market reward row for
   another trader, so any split is invented
   (`L5_INCENTIVE_CLASSIFICATION_V1.md`).
2. **Different venue.** L5 settles in **pUSD** on the Polymarket
   (international) deployment — confirmed by direct `eth_call`:
   `name()` → "Polymarket USD", `symbol()` → "pUSD". Our account is Polymarket
   **US**. Different programs, different fee schedule.
3. **Different program era.** L5's REWARD stream exists only in 2026-08; the
   up/down liquidity programs we can observe read `EXPIRED` by 2026-09-10. The
   program that paid him may not exist now.
4. **Different scale and shape.** L5's MAKER_REBATE credits are *daily pool
   distributions posted at 00:00 UTC*; our PMUS maker rebate is a per-fill
   commission credit. `VENUE_INCENTIVE_RECONCILIATION.md` records that
   comparing the two directly was a category error once already, and says not
   to repeat it. L5 ran $7.4M of turnover and 4.16M matched contracts in
   2026-08; our largest credited cohort is $56.86 of notional.

**What IS transferable:** the *mechanism*, not the magnitude — that a
persistent incremental maker policy generates maker notional, and maker
notional on PMUS earns `0.0125·(1−p)` of itself at fill time with no enrolment.
The breakeven document converts that into the only question worth asking: how
much notional per day.

## Accounting discipline applied

Maker rebates, taker rebates and liquidity rewards are three separate lines and
are never merged into trading P&L. Per
`VENUE_INCENTIVE_RECONCILIATION.md` (OWNER 2026-09-10) the final sum
`realized net = matched-pair P&L + directional residual P&L + confirmed
credited incentives − other costs` may be computed **only** once a market is
filled, settled *and* credited; absent any precondition it is reported
unavailable rather than partial. That sum is still **not computed** here: the
incentive terms are account-lifetime ($16.18) while the settled trading term
(`cross_shadow_live/ledger.jsonl`, −$21.8092 over 23 markets, later −$25.0465
over 24) covers barely two days, and the `realizedPnl` double-count question is
open.
