# Polymarket BTC Up/Down 15-minute: lead-hold replay

2026-10-02. **No orders placed. No maker PnL claimed.**

## Verdict

**1. Does the lead signal survive fees?** **No.** The signal is real and
replicates almost exactly — but at an ask equal to the published hold rate it
is already fully priced. The best edge available anywhere, across all three
entry times and all six tradable bins, is **+0.81 cents per share**, against
a required **+3 cents**. A shortfall of 2.19 cents. At published + 2 cents
every bin is negative, worst −4.11 cents.

**2. How often does a window qualify?** **Zero of 12,959** — 0.0%, at 8, 10
and 12 minutes, in-sample and out-of-sample, at both assumed asks. Nothing to
report a hit rate or PnL distribution on, because nothing clears the filter.

**3. Is the $5 maker target tested?** **No. Untested, and untestable from
what exists.** See Part 3.

The lead-hold table is a genuine and well-calibrated description of BTC
15-minute windows. It is not, by itself, a trade.

## Data source and evidence class

**MINUTE-RESOLUTION COINBASE SPOT PROXY — NOT A SETTLEMENT REPLAY.**

| | |
|---|---|
| Source | `api.exchange.coinbase.com/products/BTC-USD/candles`, granularity 60 |
| Span | 2026-05-20T00:00Z → 2026-10-02T01:38Z |
| Candles | **194,478** 1-minute bars |
| Windows | **12,959** complete 15-minute windows |

Why not the specified sources, each checked rather than assumed:

- **Chainlink BTC/USD 60s TWAP** and **CF Benchmarks BRTI** — unreachable
  from this environment. So nothing here is settlement.
- **`api.binance.com`** — **HTTP 451** (geo-blocked). The published table's
  own 1-second Binance basis cannot be reproduced.
- **`api.binance.us`** — answers, but quotes a near-dead book (1-minute bars
  with `0.00000000` volume). Not a usable proxy.
- **No reachable venue serves sub-minute history.** Coinbase, Kraken, Gemini
  and Binance.US all floor at 1 minute.

### The proxy's weakest point is where the test is most sensitive

The rules settle on a **60-second average**, and a 1-minute OHLC bar cannot
produce one. Opening and closing 60-second averages are approximated by
**OHLC4 = (O+H+L+C)/4** of the first and last minute of the window. That is
worst for near-tie windows — precisely the `<2 bp` bin and the tie-goes-Up
rule. **Disagreement with the published table in the small bins is as likely
to be my resolution as their venue**, and should not be read as a finding
about Binance vs Coinbase.

Window `[T, T+15)` at :00/:15/:30/:45. Price to beat `P0` = OHLC4 of the
minute starting at `T`. Final `F` = OHLC4 of the minute starting at `T+14`.
Up wins iff `F >= P0` (tie is Up). Lead at `e` minutes = close of the minute
ending at `T+e` versus `P0`, in bp. Leading side is Up iff lead `>= 0`.

## Part 1 — lead-hold calibration, pooled Up/Down

Gap = mine − published (11,519-window Binance 1-second replay).

### 8 minutes elapsed
| bin | n | hold % | published | gap |
|---|---|---|---|---|
| <2 | 2,413 | 57.8 | 58.8 | −1.0 |
| 2–5 | 2,962 | 68.9 | 69.5 | −0.6 |
| 5–10 | 3,266 | 80.9 | 80.8 | **+0.1** |
| 10–15 | 1,777 | 88.0 | 87.7 | +0.3 |
| 15–25 | 1,564 | 91.6 | 92.9 | −1.3 |
| 25–40 | 666 | 96.5 | 96.3 | +0.2 |
| >40 | 311 | 97.4 | 97.8 | −0.4 |

### 10 minutes elapsed
| bin | n | hold % | published | gap |
|---|---|---|---|---|
| <2 | 2,185 | 59.8 | 60.2 | −0.4 |
| 2–5 | 2,700 | 74.9 | 74.0 | +0.9 |
| 5–10 | 3,227 | 85.3 | 86.1 | −0.8 |
| 10–15 | 1,877 | 92.4 | 91.4 | +1.0 |
| 15–25 | 1,762 | 94.9 | 95.6 | −0.7 |
| 25–40 | 779 | 97.3 | 97.4 | −0.1 |
| >40 | 429 | 98.6 | 99.3 | −0.7 |

### 12 minutes elapsed
| bin | n | hold % | published | gap |
|---|---|---|---|---|
| <2 | 2,002 | 61.7 | 62.0 | −0.3 |
| 2–5 | 2,529 | 79.9 | 81.1 | −1.2 |
| 5–10 | 3,140 | 90.7 | 90.9 | −0.2 |
| 10–15 | 1,989 | 96.0 | 95.8 | +0.2 |
| 15–25 | 1,861 | 98.6 | 98.6 | **0.0** |
| 25–40 | 929 | 98.7 | 99.6 | −0.9 |
| >40 | 509 | 99.6 | 99.4 | +0.2 |

**Every one of 21 cells lands within 1.3 points of the published table, on a
different venue and at a coarser resolution.** That is strong independent
corroboration of the lead-hold structure.

### Volatility split at 10 minutes elapsed (5 minutes left)

My trailing-1h median is **2.966 bp/min** against the published ~3.9
reference — the sample is calmer, so "calm" and "volatile" are defined by my
own median, not theirs. Cross-venue comparison of the two halves is therefore
looser than the pooled table above.

| bin | n calm | calm % | pub calm | n vol | vol % | pub vol |
|---|---|---|---|---|---|---|
| <2 | 1,506 | 62.5 | 63.9 | 678 | 53.7 | 52.1 |
| 2–5 | 1,691 | 80.4 | 77.7 | 1,009 | 65.8 | 67.4 |
| 5–10 | 1,802 | 90.6 | 90.6 | 1,424 | 78.5 | 80.1 |
| 10–15 | 784 | 95.7 | 94.9 | 1,093 | 90.0 | 88.5 |
| 15–25 | 505 | 98.0 | 97.8 | 1,256 | 93.6 | 94.7 |
| 25–40 | 152 | 98.7 | 98.8 | 627 | 97.0 | 97.0 |

The direction and magnitude of the calm/volatile spread both replicate.

## Part 2 — trade rule

Rule as specified: enter only at 8–12 minutes; skip `|lead| < 2 bp`; buy the
leading side only if its Part-1 hold rate beats ask + taker fee by **≥ 3
cents**. Taker fee `0.07 × price × (1 − price)`. Two assumed asks, since no
historical asks exist: published hold rate, and published + 2 cents.

**Qualified windows: 0 of 12,959 in every configuration.** The per-bin edge
shows why:

| e | bin | n | realized | ask | fee | edge @ pub | edge @ +2c |
|---|---|---|---|---|---|---|---|
| 8 | 2–5 | 2,962 | 0.6887 | 0.69 | 0.0150 | −0.0162 | −0.0357 |
| 8 | 5–10 | 3,266 | 0.8086 | 0.81 | 0.0108 | −0.0121 | −0.0312 |
| 8 | 10–15 | 1,777 | 0.8801 | 0.88 | 0.0074 | −0.0073 | −0.0262 |
| 8 | 15–25 | 1,564 | 0.9156 | 0.93 | 0.0046 | −0.0190 | −0.0377 |
| 8 | 25–40 | 666 | 0.9655 | 0.96 | 0.0027 | **+0.0028** | −0.0159 |
| 8 | >40 | 311 | 0.9743 | 0.98 | 0.0014 | −0.0071 | −0.0257 |
| 10 | 2–5 | 2,700 | 0.7493 | 0.74 | 0.0135 | −0.0042 | −0.0235 |
| 10 | 5–10 | 3,227 | 0.8528 | 0.86 | 0.0084 | −0.0156 | −0.0346 |
| 10 | 10–15 | 1,877 | 0.9238 | 0.91 | 0.0057 | **+0.0081** | −0.0107 |
| 10 | 15–25 | 1,762 | 0.9489 | 0.96 | 0.0027 | −0.0138 | −0.0325 |
| 10 | 25–40 | 779 | 0.9730 | 0.97 | 0.0020 | **+0.0010** | −0.0177 |
| 10 | >40 | 429 | 0.9860 | 0.99 | 0.0007 | −0.0047 | −0.0233 |
| 12 | 2–5 | 2,529 | 0.7987 | 0.81 | 0.0108 | −0.0220 | −0.0411 |
| 12 | 5–10 | 3,140 | 0.9070 | 0.91 | 0.0057 | −0.0087 | −0.0276 |
| 12 | 10–15 | 1,989 | 0.9603 | 0.96 | 0.0027 | −0.0024 | −0.0211 |
| 12 | 15–25 | 1,861 | 0.9860 | 0.99 | 0.0007 | −0.0047 | −0.0233 |
| 12 | 25–40 | 929 | 0.9871 | 1.00 | 0.0000 | −0.0129 | −0.0315 |
| 12 | >40 | 509 | 0.9961 | 0.99 | 0.0007 | **+0.0054** | −0.0132 |

**Best edge anywhere: +0.0081/share. Required: +0.0300. Shortfall: 0.0219.**

Three bins are positive before the 3-cent filter, by 0.1 to 0.8 cents. Those
are smaller than the ±1.3-point cross-venue disagreement in Part 1, so they
are noise, not edge — which is exactly what the 3-cent floor is there to
prevent you from trading.

**The result is close to tautological, and worth stating plainly:** the
assumed ask *is* the published hold rate, so the test reduces to whether my
realized rate exceeds the published one by more than the fee plus 3 cents. It
does not, because the two tables agree to within 1.3 points. **Any edge here
lives entirely in the difference between the true hold rate and the actual
quoted ask — which is the one quantity this exercise does not have.** With
real historical asks the answer could differ in either direction.

Per the instruction: no hit rate, average ask, PnL distribution or 50-share
clip figures are reported, because no window qualified. A resolved 90-cent
favourite is not counted as profit anywhere here.

## Part 3 — the $5-per-window maker claim: **UNTESTED**

Stopping as instructed. There are no historical Polymarket CLOB bids/asks for
this market over the replay period and no fill logs.

The only BTC15m book data in the repository covers **2026-09-27T18:27Z →
2026-09-28T15:05Z** — about 21 hours, ~84 windows, **7,772 rows at roughly
0.1 Hz**, on the **PMUS venue instance**, not the one in question. Four
independent reasons it cannot score quoting:

1. wrong period (two days, not the replay span) and below the stated sample bar;
2. wrong venue instance;
3. ~0.1 Hz sampling cannot establish when the book traded **through** a
   resting quote, which is the whole measurement;
4. no fill logs, so no maker/taker classification and no rebate observation.

**Spread captured, adverse selection, and cancelling inside the last 60–90
seconds are not scoreable from candles.** Rebate is not counted anywhere —
it is unmeasured, not zero. No crossed spread is treated as maker, because no
quote is scored at all.

To test it you need, at minimum: full-depth Polymarket CLOB snapshots for
BTC Up/Down at ≥1 Hz across a multi-thousand-window span, or your own fill
logs with maker/taker flags and credited rebates.

## Code and reproduction

- `scripts/btc15m_fetch_coinbase.py` — candle fetch, resumable, documents why
  each specified source was unavailable.
- `scripts/btc15m_replay.py` — Parts 1 and 2.
- `data/btc15m/coinbase_1m.jsonl.gz` — the 194,478-candle cache.
- `analysis/btc15m/replay.json` — full machine-readable output.

Part 2 also reports a chronological 60/40 split (calibrate on the first 7,775
windows, trade the last 5,184), because the stated rule uses Part 1's hold
rate both to choose and to score a trade, which is in-sample. Both give zero
qualified windows, so the distinction does not change the verdict here — but
the out-of-sample figure is the one that would matter if any bin had cleared.
