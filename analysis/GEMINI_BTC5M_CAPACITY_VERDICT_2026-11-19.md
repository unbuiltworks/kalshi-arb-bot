# Gemini BTC5m capacity verdict — 2026-11-19

Written in response to the priority change naming **Gemini Predictions BTC
5-minute** the new primary lane, targeting $480/day of confirmed settled
realized net profit.

Status ladder in force: `historical replay -> shadow -> live probe -> submitted
-> filled -> settled -> realized`. Nothing below is realized. **No order was
submitted, nothing was armed, no authorization or capital was changed.**

---

## 1. Does the venue offer the contracts? YES — verified live

Measured against `GET https://api.gemini.com/v1/prediction-markets/events`,
a public endpoint that needs no credential, at ~12:20–12:34Z.

- Series `BTC05M` is live and **recurring every 5 minutes**; 12–14 windows are
  listed at any moment, each a single binary `Up` contract.
- Symbol form `GEMI-BTC05M2611191220-UP`; event ticker `BTC05M<YYMMDDHHMM>`.
- `startTime` / `expiryDate` are exactly 300s apart. `template: crypto-up-down`,
  `eventTags: ["Five Minute", "Up Down"]`, index `LS-LRRR-XBTUSD`.
- `quantityMinimum` **0.01**, `quantityIncrement` **0.01**, `priceIncrement`
  **0.01**, `priceMinimum` 0.01.
- Strike arrives in the API response (`strike.value`, `availableAt`), not in the
  symbol — per the docs' 2026-04-08 change. Code parsing strikes out of tickers
  is stale.
- `ETH05M`, `SOL05M`, `XRP05M` and `BTC15M` exist alongside it.

So the contracts are real and the integration surface is small. **That is the
only question in the brief that comes back positive.**

## 2. Do the fee waiver, rebates and liquidity rewards apply? PARTLY — and they do not rescue it

From `src/gemini_execution/rebate_program.py` (published rules, read
2026-09-19) and `src/gemini_maker/subsidy_verify.py`:

| Programme | Rule | Worth to us |
|---|---|---|
| Maker fee | `ceil_cent(0.0175 x C x P x (1-P))` | see §4 — the **ceil** is what matters, not the rate |
| Maker fee waiver | referenced as in force from **2026-09-02**; 18 BTC15m maker fills reported venue `n = 0.0000` | **NOT CASH-CONFIRMED.** `maker_fee_cash_probe` exists precisely because a venue field at fill time is an assertion, not a settlement |
| Maker rebate | `0.30 x 0.07 x C x P x (1-P)`, band **$0.20–$0.80**, capped 5% of notional, **floored to the cent**, paid daily 5pm ET | **$0.00 at probe size.** The first cent needs ~1.9 contracts of daily volume at the best possible price |
| Liquidity rewards | separate daily USD pool split among qualifying two-sided makers | **unquantified.** No eligibility or pool size has ever been obtained for `BTC05M` |

Total Gemini cash this programme has **ever** been credited: **+$0.51**, as 7
`AdminCredit` rows, on 61 maker fills / $52.26 of maker notional.

The waiver is the single highest-leverage unknown on the venue, and it is a
read, not a build — see §7.

## 3. The tape — measured, not modelled

`python -m src.gemini_btc5m_tape_probe` (new; read-only, no credentials),
plus a 13-cycle sampler over 12:19–12:34Z.

| Measurement | Value |
|---|---|
| Completed `BTC05M` windows observed | 3 |
| Their total volume (ALL participants) | $54.72, $25.86, $35.00 → **mean $38.53** |
| Implied whole-series tape | **~$11,100/day** |
| Touch spread observations (one-sided books excluded) | 148 |
| **Spread = exactly $0.01** | **133 / 148 (90%)** |
| p25 / median / p75 spread | $0.01 / $0.01 / $0.01 |
| Widest non-degenerate spread seen | $0.17 |

One window quoted 0.43 / 0.58 and it is an **outlier**. Taking it as the spread
would overstate the venue's gross margin fifteenfold, which is why the probe
reports the median and excludes books with nothing on one side.

For scale, from the same read: Gemini **Sports** carries $184,167 across 831
listed events and the **daily** BTC market $35,989 — i.e. the venue has
volume, just not in the 5-minute crypto series.

September's reading (`analysis/MONEY_PATH_STATUS.md`, 2026-09-23) put the BTC5m
tape at $36,937/day. It is now roughly **a third of that**.

## 4. The arithmetic that decides it

The identity the new gate is built on: resting `buy UP` at the bid and
`buy DOWN` at the complement of the ask costs `1 - (ask - bid)` and pays $1.00,
so

> **the gross margin of a matched two-sided maker pair IS the touch spread.**

At the measured median that is **one cent per contract pair**. Against it:

- **Maker fee, if charged at all:** each leg rounds UP independently, so a pair
  owes **$0.02** minimum however small the clip. A perfectly matched pair at
  the median spread therefore **loses a cent**. At the venue minimum
  `C = 0.01` the fee is $0.01 on $0.0043 of notional — 13x the trade.
- **Minimum viable clip** if the fee is charged: **2.0 contracts per leg**, from
  `min_viable_clip`. Current production caps are
  `straddle_max_unmatched_usd = $0.10` and `straddle_max_side_usd = $0.30`,
  which cannot reach 2 contracts. **The configured probe size and a charged
  maker fee are mutually exclusive.**
- **Rebate:** $0.00 at any size this lane is authorized for.

`venue_ceiling` on the measured tape, every term chosen in our favour:

| Scenario | Ceiling | vs $480/day |
|---|---|---|
| Waiver + perfect matching + **zero** adverse selection, 10% share | **$24.02/day** | CEILING_BELOW_TARGET |
| Same, capturing **100% of the entire tape** | **$240.27/day** | CEILING_BELOW_TARGET |
| Maker fee **charged**, perfect matching, 100% of tape | **$5.85/day** | CEILING_BELOW_TARGET |
| Waiver, **Probe A's measured shape** (0% matched), 10% share | **−$116.04/day** | CEILING_NOT_POSITIVE |

And the cleanest statement, with fees *and* adverse selection excluded
altogether so the number is a hard bound rather than a pessimistic estimate:

> **$480/day requires 4.10x the entire BTC05M tape**, perfectly matched.

Being the maker on every dollar that trades on the series, for free, with no
adverse selection, still yields **half** the target.

## 5. Adverse selection and unmatched inventory

These are the terms that turn the ceiling negative, and both are already
measured — elsewhere in this repository, not newly assumed:

- **Probe A: 5 fills, all on one side, p = 0.0128.** A one-sided fill under a
  two-sided quote is adverse selection, and `MONEY_PATH_STATUS.md` already
  names a repeat of it as lane-killing.
- **Probe A settlement-implied −$2.19; Probe B −$0.33.** Both live, both negative.
- `l5_straddle_ev` records the PMUS analogue: **15 lone legs in a row at −0.16
  each** against 2 completed pairs, and a measured lone-leg win rate of 9.81%
  against an 8% implied price over 503 shadow windows.
- `P(lone leg wins | filled at ~0.50 on Gemini BTC05M)` has **never been
  measured**. `l5_straddle_ev.entry_ev` refuses an unset probability rather
  than defaulting it, and the new gate does the same for participation share,
  match rate and lone-leg EV.

A 1-cent gross margin cannot absorb any of this. One stranded leg at 0.50 risks
$0.50 to earn $0.01 matched — **50:1 against**, before fees.

## 6. Verdict

**Gemini BTC5m cannot be the path to $480/day, and the obstruction is the
venue's tape, not our strategy.** No amount of simultaneous quoting,
replenishment, adaptive repricing or selective taker completion changes a
1-cent spread on an $11,100/day series. Building the unified strategy before
this arithmetic was done would have repeated the exact pattern
`ARB_STATUS.md` records seven times over: build, instrument, shadow, probe,
then discover the lane was never capable of the target.

What was built instead is the gate that makes this check cheap and repeatable
for the next candidate venue — see §8.

This does **not** say the lane is worthless. It says it is a **mechanism
laboratory at tens of dollars a day**, which is what the 2026-09-23 reading
already concluded and which has since got worse, not better.

## 7. The one read that could change the answer

**Is the maker fee actually $0.00 in cash?** It is the difference between
$240/day and $5.85/day at the ceiling, and between a 2-contract minimum clip
and none. It is a **read, not a build**: `gemini_execution.maker_fee_cash_probe`
already asks it over `/v1/mytrades`, `/v1/transfers` and
`/v1/prediction-markets/positions/settled`, and it needs only the Gemini
credentials, which live as GitHub Actions secrets and not in this container.

Even if the waiver is confirmed total, the ceiling is $240/day at 100% of the
tape. **Confirming it does not make the target reachable — it only decides
whether the lane is worth running at all.**

## 8. What this adds to the repository

| File | Purpose |
|---|---|
| `src/gemini_btc5m_capacity_gate.py` | Pure, fail-closed venue ceiling: the fee's ceil-cent, the rebate's floor-cent and band, `min_viable_clip`, `required_share_for_target`, `admits`. Every behavioural rate is a required input with no default. No I/O, no order surface. |
| `src/gemini_btc5m_tape_probe.py` | Read-only public measurement of the two inputs the gate refuses to guess. A command, not a service — nothing schedules it. |
| `tests/test_gemini_btc5m_capacity_gate.py` | 45 tests. 7 mutations tried, all caught. |
| `tests/test_gemini_btc5m_tape_probe.py` | 11 tests, pinning both exclusions (unstarted windows, one-sided books). |

Regression: baseline on the release SHA was **2141 passed / 1 pre-existing
failure** (`test_l5_live_oneshot.py::test_the_report_distinguishes_a_broken_feed_from_a_quiet_market`,
untouched by this work) over the straddle/btc5m/fee/rebate/capacity selection.
After this change: **2197 passed / same 1 pre-existing failure**.
