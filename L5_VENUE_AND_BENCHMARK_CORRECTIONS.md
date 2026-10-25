# Corrections to the durable record, 2026-10-25

Three of my own claims are withdrawn or narrowed here. Two were found by a peer session
(`session_01255E3fhWg7BWTviGCMRknt`) and independently verified before being recorded; one I
found myself. Nothing below is adopted on a peer's assertion alone.

## 1. The settlement benchmark — WITHDRAWN as stated

**I claimed** `GRR-KAIKO_RFR_<ASSET>USD_60S` is the BTC15m settlement reference.

**Correct:** PMUS and Kalshi BTC15m settle off **CF Benchmarks BRTI**. The source predates the
error and is already in this repo: `analysis/pmus/P24_PMUS_BTC15M.md` quotes the market
description. `data/ref_lag` is 34,944 window/settlement rows, **100% GEMI-\* markets**,
sourced only from `GRR-KAIKO_RFR_{BTC,ETH,SOL,XRP}USD_60S` — zero Kalshi, zero PMUS. That is
where the Kaiko reference came from, and it is **Gemini's**.

Two further structural facts I did not have:

- settlement is **endpoint-vs-endpoint**, not price-vs-fixed-strike. BOTH ends are
  60-observation averages, so the effective "strike" is `BRTI(T_start)` and is **already
  determined at window open**. Ties go to UP.
- the averaging boundary — `[T-60,T)` vs `(T-60,T]` — is **UNRESOLVED**, a difference of one
  observation in sixty.

## 2. The (60-tau)/60 determinedness claim — ARITHMETIC STANDS, EXPLOITABILITY WITHDRAWN

**I claimed** that at tau before expiry, `(60-tau)/60` of the settlement value is already
determined, and treated that as a tradable edge.

The arithmetic survives the benchmark correction, because it is a property of any 60-point
average and not of which index is averaged. The weight fixed at tau=10 is 0.8333 exactly.

**The exploitability does not survive, and I withdraw it.** Peer measurement
(`L5_BENCHMARK_AUDIT_AND_SETTLEMENT_ESTIMATOR.md`, commits `2e1c9242` / `91cd3900`):

- the required move in the remaining ticks is amplified `60/tau`
- the remaining mean is a **walk, not independent draws** — assuming independence understates
  its sd by **6.2x** at tau=10
- decidedness is 0% at tau=60 rising to 100% at tau=10
- where the mechanism is informative (tau <= 30s) the market's own Brier is **0.00032** and
  **PERFECT** settlement knowledge is worth **$0.008/contract — net negative against a
  $0.0174 taker fee**
- where perfect knowledge is worth $0.309 (tau >= 300s) the mechanism tells you nothing

So the mechanism is real and worthless. Do not revive it as an edge.

## 3. Venue contamination of every per-contract number I derived — VOID

The 130-window dataset in `analysis/adverse_entry/windows.json` is **KALSHI**:
`analysis/l5/taker_entry/extract.py` filters `venue == 'kalshi'` and the series are
`KXBTC15M`/`KXETH15M`/`KXSOL15M`/`KXXRP15M`/`KXDOGE15M`. I applied **PMUS** fee and rebate
coefficients to it. The peer adds that 77 of those 130 windows were labelled
`strike_vs_reference` from `data/ref_lag`, which is 100% GEMI-\*, so the set mixes **three
venues**.

Every per-contract economic figure I derived from it is void, specifically including the
two-sided market-making result (-$99..-$156/82 windows) and the reward-capture conclusions.
**Both withdrawn.** The peer's own `$0.0051` touch / `$0.0074` sweep adverse-selection figures
are likewise **Kalshi**, not PMUS — they flagged this themselves before I adopted it, and
pushed `60bc48c0` to make it explicit. Those numbers apply no fee coefficient of any venue,
so they are clean about the wrong venue rather than contaminated.

Held as a MECHANISM result only, not a PMUS economic one: raising the completion ceiling
0.96 -> 1.00 cut stranded windows 23 -> 7 and raised the match rate 60% -> 87%. The direction
moved hard, which is informative; the dollars are Kalshi dollars.

## 4. The rebate is a STEP, not a SLOPE — narrowing my own claim

**I claimed** a $1.00 completion is worth 0.47-0.63% of the capital it ties up.

The smooth figure checks out: two maker legs at 0.44/0.56 accrue `0.0125 x 0.44 x 0.56` each,
about 0.62% of the dollar tied up. **But the credit is cent-rounded per print**, and from the
25 real credited prints totalling $0.42 (`src/l5_incentive_breakeven.py`,
`analysis/l5/PMUS_INCENTIVE_BREAKEVEN.md`) the venue has credited exactly $0.01 or $0.02 per
print and never more, so a complete pair collects **at most $0.04**. At q=1.56, p=0.44 the raw
credit is $0.0048 and rounds to **$0.00 — nothing at all**.

The empirical break-even completion ceiling is therefore **1.004-1.008**, which I reproduced
independently by running that module (it reports 1.003-1.008). Consequences:

- **0.96 -> 1.00 is SUPPORTED** — 1.00 sits inside the empirical ceiling
- **1.01 is NOT**: a 1.01 pair at 5 shares loses $0.01-0.03, a 1.02 pair loses $0.06-0.08,
  with every observed credit counted
- the headroom is roughly **1.000-1.005 on the 0.005 grid, and it vanishes at small clips**

## 5. What PMUS data can and cannot answer — the blocker, split correctly

`src/pmus_book_tape.py` emits `bbo`, `book`, `error`, `book_error`, `tape_start`, `reslate`,
`drain_end`. **There are no print/trade rows.** A bbo row carries `bid`, `ask`, `bid_depth`,
`ask_depth`, `bid_shares`, `ask_shares`, `last`, `shares_traded` (cumulative),
`shares_traded_delta_lower_bound`, `state`. The peer reports zero prints across all 19 windows
of their `pmus_windows.json`, which matches.

| question | answerable from the existing PMUS tape? |
|---|---|
| **pair-cost distribution at the moments we declined** | **YES** — `maker pair = bid + (1-ask) = 1 - spread`, from the book alone |
| queue advance / fill modelling | PARTLY — volume deltas give a lower bound, but no aggressor side |
| **sweep identification, fill-time adverse selection** | **NO** — needs per-print size and aggressor side |

This matters for prioritisation. The owner's named bottleneck — adverse selection at entry —
is the one question PMUS data **cannot** answer. The completion lever, which both this session
and the peer independently concluded is where the money is (a 1945z-style loss is settlement
of an unpaired residual at roughly 50x fill-time adverse selection), **is** answerable from
data we already hold.
