# P20 — Measured habitat, and the replacement Probe B (B2)

## 1. Economic framing correction

The earlier Gemini BTC15m figure of **$11–23/day is a PROVISIONAL MODELED
RANGE, not expected realized profit.** Two inputs are not validated:

- **fees are unverified.** Zero observed on our own fills; 102 of 204
  account-lifetime rows carry a nonzero fee. Zero is not assumed.
- **adverse selection uses L5's rate (−0.0301% of spend), not a validated
  Gemini clone rate.** Our own measured rate is −71.59% of spend on n = 6
  windows, which is not projectable. The true Gemini clone rate is unmeasured
  and sits somewhere between.

Nothing in the range is realized, and no rebate in it has been credited.

## 2. Measured habitat

Method and provenance beside each figure. Kalshi tickers were **discovered
from the API's own payloads** (`custom_strike.Associated Events`), never
guessed. A first pass reported zeros because I read `volume_24h`/`yes_bid`;
the real fields are `volume_24h_fp`/`yes_bid_dollars` — my error, corrected.

| metric | Gemini BTC15m | Gemini BTC5m | **Kalshi KXBTC15M** | Polymarket (PMUS) | Polymarket BTC15m (L5) |
|---|---|---|---|---|---|
| instrument | BTC 15-min up/down | BTC 5-min | **BTC 15-min up/down — the same instrument L5 trades** | event/esports/sports | BTC 15-min up/down |
| measured volume | **$19,935/day** (96 contracts, tape) | **$36,937/day** (240 contracts) | **$622,403 per 15-min window** (one window, fully paged) | **~$10.4M/day** across top-60 open | ≥$177,486/day (L5's own spend) |
| trades/min | **0.321** | **0.868** | **1,738** (21,703 trades over 749 s) | — | ≈7.4 (L5 alone) |
| prints/window (median) | 4.0 | 3.0 | **21,703** (one window) | — | 110 (L5 only) |
| zero-print windows | 8.3% | 17.5% | 0 observed | — | 0 |
| two-sided interaction | 1 of 3 traded windows | — | **confirmed** (both taker sides present) | — | 146/146 |
| spread | median **0.060** | median 0.280 | **$0.01** | 0.001–0.02 | — |
| depth | not published | — | bid size 5,409 contracts | liquidity $31k–1.6M | — |
| duration / TTE | 15 min | 5 min | **15 min** | days–years, plus esports intraday | 15 min |
| maker economics | 1.2493% accrual observed | same | **UNVERIFIED — needs account access** | **UNVERIFIED** | $1,226/day credited |
| fees | unverified, nonzero historically | same | published schedule, **unverified for us** | unverified | not separable |
| realistic participation | 10–20% of tape | 10–20% | **unknown — depth suggests small share of a very large pool** | unknown | L5 occupies it |
| net $/day at that capacity | **$11–23 provisional modeled** | $22–44 provisional | **not estimable without the incentive schedule** | not estimable | — |
| data/execution readiness | **live armed path, proven evidence pipeline** | same | **public data yes; no execution integration, no credentials** | public data yes; no integration | read-only research |

Sources: Gemini `/v1/trades` (96 + 240 contracts, 24 h to 12:30Z, our prints
excluded); Kalshi `/trade-api/v2/markets` + `/markets/trades`, ticker
`KXBTC15M-26SEP230900-00`, 22 cursor pages to exhaustion; Polymarket
`gamma-api/markets?order=volume24hr`, top 60 open.

**The finding.** Kalshi runs the *same instrument* at roughly **5,400× Gemini
BTC15m's trades per minute** and a **6× tighter spread**. One Kalshi 15-minute
window carries ~31× Gemini BTC15m's entire daily notional. The flow L5's
mechanism consumes exists there and does not exist on Gemini.

**What is NOT established:** Kalshi maker incentives, our fee tier, and
whether any of that flow is capturable by a q=1 maker. Those are the three
numbers that would decide it, and none is public. No execution integration
exists.

## 3. Replacement Probe B — "B2"

Purpose: test the maker-improve hypothesis that Probe B was authorized for
and did not test. **Placement isolated.** No COMPLEMENT_SEEK, no extra
maintenance budget, no crossing, no pair-ceiling relaxation, no wider ladder,
no directional selection, no indicators. Persistence unchanged.

| parameter | value | change vs original B |
|---|---|---|
| spec | `btc5m_q1_improve` | **unchanged** |
| `spec_sha` | `bfe7ba23…` | **unchanged** — placement was always in the hash |
| `envelope_sha` | `be8b544b…` | **unchanged** |
| windows | 4 | unchanged |
| q / CAP / bounds | q=1, CAP2, $2.50 committed, $2.00 residual, 2 working orders | unchanged |
| action budget | 12 per window | **unchanged — persistence is not the variable** |
| entry band | 240:280 | unchanged |
| stops | $10 economic-loss, $5 drawdown | unchanged |
| concurrent markets | 1 | unchanged |
| structural ceiling | $10.00 | unchanged |
| **`executable_sha`** | `31ec46a6` → **`13cd5984`** | **unavoidable** — the placement fix and the capture both live in the driver |
| **`bundle_sha`** | `48ec77ac` → **`75a4bb2c`** | **unavoidable** — same |
| authorization id | **new one required** | AB-1 and AB-2 are both spent; the latch keys on the id alone |
| cohort chain | **4 fresh ledgers required** | B left W01 12/12, W02 7/12, W03 8/12; only W04 is unused |

**Control.** Original B's fills are the frozen-control observation on the same
instrument and stand as valid evidence of control behaviour: 4 windows, 6
fills, 1 complement at 43.7 s, matched pair 0.62, settlement-implied −0.33.
B2 is its treatment arm.

**Pre-registered primary endpoint:** does `improved: true` appear on the live
path, and does the opposite-side fill rate differ from the control's 1-of-3?
**Secondary:** `from_bid` distribution, whether any fill required the improved
price, and whether the improvement changed rest time before fill.

**Falsification:** if B2 shows `improved: true` throughout and complement
acquisition is still ~1 in 3 or worse, placement is not the lever and P15
(persistence) becomes the next experiment — which is exactly the role P15 is
held in reserve for.

## 4. Information value

B2 costs $10 and answers a question that is currently unanswerable and that
blocks P15's interpretation: we cannot tell whether persistence is the
binding constraint until we know placement is not. Against that, section 2
says the venue itself returns ~1–2% of the incentive income that makes the
mechanism pay, so B2 is worth running for **fidelity information**, not for
expectancy.
