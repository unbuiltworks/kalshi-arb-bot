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

## 6. THE COMPLETION CAP IS THE BINDING CONSTRAINT — and the ceiling is not separable from clip size

The test proposed in §5 was run by the peer session (`1cbb1f1b`,
`L5_PMUS_COMPLETION_CAP_FINDING.md`): 19 venue-native PMUS windows, 407 book frames,
`pair_cost = bid + (1-ask)`, giving min 0.9700 / median 0.9900 / max 0.9900 and **0 of 407
frames clearing the 0.96 cap**.

**Independently reproduced here** on a different slice — `data/pmus_btc15m_tape`, 42 two-sided
frames, 1 market, 2026-09-27:

| | |
|---|---|
| pair_cost | min **0.9600**, median 0.9900, max 0.9900 |
| spread | min 0.0100, median 0.0100, max **0.0400** |
| `<= 0.960` — cap accepts | **4 frames, 9.5%** |
| `0.960-1.004` — refused, inside break-even | 38 frames, 90.5% |
| `> 1.004` — correctly refused | 0 frames, 0.0% |

**One correction to the peer's result: "completely unreachable" is too strong.** Four frames sat
at pair cost exactly 0.9600 (spread exactly 0.0400) and the cap is `<= 0.96`, so it accepts
them. Both samples are small, so neither 0% nor 9.5% is the rate. The defensible statement is
that **the cap binds on roughly 90-100% of frames, so it is the dominant constraint and it is
configuration rather than market.** The gamma scan agrees wider spreads occur:
`share_spread_le_0_01` is 0.5769 for crypto, so 42% of crypto markets sit above a cent.

`pair_cost = bid + (1-ask) = 1 - (ask-bid) = 1 - spread` is an **identity**, so the
"pair-cost distribution" IS the spread distribution and the finding reduces to: **PMUS BTC15m
spreads are a cent, and a 0.96 cap requires four.** Triangulated by the 37,116-market gamma
universe scan: crypto `median_spread` 0.010, `median_tick` 0.01.

This also closes the `possible_misses: 0` contradiction. Both figures were honest: **nothing
was missed, everything was declined, by configuration.** The 1945z lifecycle is the confirming
case — `COMPLEMENT_PAIR_ABOVE_CAP` 12 with `order_resting FALSE` at close.

### CORRECTED — the ceiling moves with clip size, but 0.99 pays at EVERY clip

**I first published this table with a $0.02/pair per-order fee and concluded that a 0.99
completion LOSES $0.0044 at the authorized clip. That was wrong and is withdrawn.** The
$0.01/order came from `analysis/adverse_entry/replay_c.py`'s `PER_ORDER` — a **Kalshi harness
constant** — which I applied to a PMUS calculation. The peer session caught it (`8f1ccd5e`);
it is the same phantom fee they had themselves found and withdrawn earlier in `569c42d8`.

Verified here independently before accepting: **`src/pmus_fees._fee` is purely
`theta * C * p * (1 - p)`** — no additive term, no flat component anywhere in that module,
which is effective-dated and reconciled against all 99 published rows. And the committed
1.004-1.008 ceiling reproduces exactly at **zero** per-pair fee (1.004 from a one-cent-a-leg
credit, 1.008 from two), which is how `PMUS_INCENTIVE_BREAKEVEN.md` describes the range; with
a $0.01/order fee the same inputs would give 1.000 and 1.004 instead.

`net/pair = q*(1-pair) + credited_rebate(leg1) + credited_rebate(leg2)`

| q per leg | credited/pair | ceiling | net at pair 0.99 |
|---|---|---|---|
| **1.56** (authorized clip) | $0.00 | **1.0000** | **+$0.0156** |
| 2.00 | $0.02 | 1.0100 | +$0.0400 |
| 2.70 | $0.02 | 1.0074 | +$0.0470 |
| 5.00 | $0.04 | 1.0080 | +$0.0900 |
| 10.00 | $0.06 | 1.0060 | +$0.1600 |

**A 0.99 completion is profitable at every clip, including the authorized 1.56.**

The underlying insight survives in weaker form: the ceiling **does** move with clip size
because the rebate is a step — but that sets how much headroom exists **above 1.00**
(1.0000 at q=1.56 vs 1.0100 at q=2.00), not whether 0.99 pays. The 2-D sweep design stands;
only its hypothesis changes, from "needs q >= 2 to pay at all" to "profitable at all clips,
headroom above 1.00 needs q >= 2".

Note on `fee_extra_per_order_usd: "0.01"` in `L5_LIVE_GATES`, since it looks like a
contradiction: it is a conservative allowance for per-**fill** rounding on **taker** fees
inside the worst-case deficit bound. It is not a venue charge and it is not on the maker path.

### Size of the prize, recomputed

A 0.99 pair at the authorized clip nets **$0.0156**. At that rate **$480/day is ~30,800
completed pairs/day — still three to four orders of magnitude past the authorized envelope.**

This changes the **diagnosis** — the cap, not the market, is why we never complete — and it
does **not** produce the target. No cap change is proposed here: `TS.max_pair_cost` is a risk
control, the service is in shadow with `armed=false`, and the decision is the owner's.

### Process note: fee constants must come from one module

Within about four hours we each caught the other reintroducing a fee that is not on the
venue's published schedule, in opposite directions. `src/pmus_fees.py` is the effective-dated,
reconciled source of truth. The replay harnesses in `analysis/adverse_entry/` carry their own
`TAKER_THETA` / `MAKER_THETA` / `PER_ORDER` constants and operate on **Kalshi** tape; their
outputs must never be quoted as PMUS economics. Warning headers have been added to all four.
