# Benchmark audit and the settlement-reference estimator — NO-GO, with the mechanism

Issue #192, P0 correction. **Read-only throughout: nothing deployed, no service
created, no orders, no risk-limit change, no money spent.** The authorized
three-window maker-only validation was NOT run and remains unarmed, per the
correction's "do not deploy live" instruction, which supersedes the earlier
authorization in the same turn.

---

## 1. Where `GRR-KAIKO_RFR` came from — the mismatch is real

**It is GEMINI's benchmark, and it entered our estimator plan by being carried
across venues.**

| evidence | finding |
|---|---|
| `analysis/gemini/*.json` market descriptions | `GRR-KAIKO_RFR_BTCUSD_60S`, each citing `assets.gemini.com/predictions/terms_and_conditions/btc_*.pdf` |
| `data/ref_lag/` `window` + `settlement` rows | **34,944 rows, 100% `GEMI-*` markets.** Zero Kalshi, zero PMUS. Sources: `GRR-KAIKO_RFR_{BTC,ETH,SOL,XRP}USD_60S` only |
| `src/gemini_maker/fair_value.py`, `ref_lag_recorder.py` | Kaiko, under `gemini_maker/` — correctly scoped to Gemini |
| **`L5_INVENTORY_ENGINE_VALIDATION.md` §7 (mine)** | recommended building the **PMUS** estimator on "strike vs `GRR-KAIKO_RFR_*_60S`" — **wrong benchmark, now withdrawn** |

Perplexity and ChatGPT were right. The error was mine, in the previous
checkpoint, and it was a cross-venue carry rather than a misreading of any
source.

## 2. The authoritative PMUS/Kalshi rule, and two structural facts I had wrong

From `analysis/pmus/P24_PMUS_BTC15M.md`, quoting the market description — this
was **already in the repo**, correct, before the error:

> settles to Up if the Bitcoin price at `T_end` is greater than or equal to the
> price at `T_start`. … in the last minute before each of those two times, 60
> BRTI prices are collected. The official value for that time is the simple
> average of these prices, rounded to the nearest 2 decimal places.

Index: **CF Benchmarks BRTI**. Same rule and rounding on Kalshi KXBTC15M.

**Fact 1 — it is endpoint-vs-endpoint, not price-vs-fixed-strike.** *Both* ends
are 60-observation averages. The "strike" is `BRTI(T_start)`, itself the average
over the minute **before the window opens**, so it is fully determined at window
open and is not a round number set in advance. My earlier framing ("strike vs
reference") implied a fixed strike and a single moving reference. Favourable, as
it happens: the strike is knowable early.

**Fact 2 — the tie goes to UP** (`≥`), and rounding is to 2 dp half-up. Pinned
in `tests/test_brti_settlement.py`.

**Timestamp boundary: UNRESOLVED and recorded as such.** "the last minute
before" `T` is consistent with `[T−60,T)` and, if the observation stamped
exactly `T` belongs to the window, with `(T−60,T]`. They differ by **one
observation of sixty — 1.67% of the weight**. Small, but exactly the off-by-one
that decides a near-the-money window. `AveragingConvention` carries **both** and
no code picks one silently; a test asserts they produce different endpoint
values and that an unknown convention is refused rather than defaulted.
Resolving it requires the BRTI feed.

## 3. We hold ZERO BRTI observations

`src/btc_ref_recorder.py` — the `btc-ref-collector` service — says it itself:

> SOURCE. Coinbase Exchange public WebSocket … **Coinbase is a PROXY, not the
> settlement source.** PMUS BTC Up/Down settles off its own reference;
> **nothing produced from this file is a settlement replay.**

So the estimator as specified **cannot be built from existing data**. Not a
modelling gap — the benchmark series is not collected at all. The collector was
honest about this; the error was in planning an estimator on top of it anyway.

Kalshi's `cfbenchmarks_value` BRTI feed is referenced in
`src/l5_venue_adapter.py` as the documented *rule*, but **no collector
subscribes to it and no BRTI observation exists anywhere in the repo.**

## 4. Historical settlement labels — contamination found, quantified, and routed around

My 130-window Kalshi dataset used two label sources:

| source | windows | provenance | verdict |
|---|---|---|---|
| `terminal_print` | **53** | the market's own price resolving to ≤0.02 / ≥0.98 — **venue-native** | **CLEAN** |
| `strike_vs_reference` | **77** | strike vs `ref_lag` `settlement_value` — and `ref_lag` is **100% Gemini/Kaiko** | **CROSS-VENUE** |

The mitigating evidence is real: on the 47 windows where both methods resolve
they agree **47/47**. But 47/47 gives a one-sided 95% lower bound of only
**0.9382**, so **up to ~4.8 of the 77 labels could still be wrong** — and a
wrong settlement label corrupts both the training target and the replay's P&L.

**So I re-ran everything on the 53 venue-native windows only.** The conclusions
survive, and one earlier claim does not:

| quantity | all 130 (contaminated) | 53 venue-native | verdict |
|---|---|---|---|
| OOS Brier skill of mid+tilt | **−13.70 %** | **−13.63 %** | unchanged — no edge in book features |
| pairing controller, OOS | −$2.7204 | −$2.5093 | unchanged |
| inventory controller, OOS | $0.0000, WAIT 100 % | $0.0000, WAIT 100 % | unchanged |
| oracle bound | +$843.57 | +$350.00 | unchanged in kind |
| `b_imb` sign on settlement | **−0.0408** (t −1.55) | **+0.0243** (t +0.76) | **SIGN FLIPS** |

**Correction:** my previous statement that "the book-imbalance coefficient on
settlement is the wrong sign in all three splits" was an artefact of the
contaminated labels. On clean labels it flips positive. Either way it is
insignificant (|t| < 1.6) — it was never a signal, but the *sign* claim is
withdrawn. The PMUS dataset (19 windows) used venue-native
`terminal_print_expired` only and was never affected.

## 5. The 83% claim — arithmetically right, strategically empty

`determined_fraction(10) = 50/60 = 0.8333`. **The claim is exactly correct**, and
it is a statement about the **weight** of already-elapsed observations.

It is *not* a statement about determinacy, and the two are routinely conflated:

- `is_mathematically_determined()` is True only when the elapsed contribution
  **alone** clears the strike. DOWN can never be strictly guaranteed, since an
  unbounded up-move always rescues it. In practice this is almost never True.
- The required remaining mean is `(60·S − partial)/τ`, so **the move demanded
  of what is left is amplified by 60/τ**. At τ=10 a $1 shortfall in the partial
  average demands a **$6** move in the final ten observations.
- The remaining mean must be treated as a **random walk**, not independent
  draws: `var = σ²(τ+1)(2τ+1)/(6τ)`. Independence understates the sd by **6.2×
  at τ=10** on measured volatility, and would make a late window look far more
  decided than it is.

Empirically (`analysis/profit_search/brti_determinacy.py`, Coinbase **proxy**,
per-second sd $3.00 — a proxy is legitimate for the *structure* of an average
but settles nothing):

| τ | weight fixed | windows decided beyond 2 sd |
|---|---|---|
| 60 s | 0.0 % | **0.0 %** |
| 30 s | 50.0 % | 96.2 % |
| 10 s | **83.3 %** | **100.0 %** |

*(211 heavily-overlapping windows from a 2,978 s proxy series at 69.7 % second
contiguity — indicative of shape, not a precise rate.)*

## 6. Why the estimator is structurally dead — the decisive result

The mechanism's information and the available edge are **in disjoint time
regions.** Market Brier against realized settlement, **venue-native labels
only**:

| time to close | Kalshi n | Brier(mid) | skill vs coin | quotes already ≤0.05 or ≥0.95 |
|---|---|---|---|---|
| 15–30 s | 14 | **0.00032** | 99.9 % | **92.9 %** |
| 30–60 s | 63 | 0.01690 | 93.2 % | 88.9 % |
| 60–120 s | 312 | 0.02467 | 90.1 % | 75.6 % |
| 120–300 s | 1,226 | 0.06322 | 74.7 % | 46.8 % |
| 300–900 s | 2,584 | 0.12989 | 48.0 % | 3.5 % |

And the executable value of **perfect** settlement knowledge, against the real
spread and the real PMUS fee:

| time to close | gross/contract | spread | taker fee | **net taker** | net maker (×0.43 measured P(fill)) |
|---|---|---|---|---|---|
| **15–30 s** | **$0.00800** | 0.0023 | 0.0174 | **−$0.01054** | $0.00346 |
| 30–60 s | $0.04016 | 0.0041 | 0.0174 | +$0.02071 | $0.01737 |
| 60–120 s | $0.06813 | 0.0061 | 0.0174 | +$0.04768 | $0.02947 |
| 120–300 s | $0.14906 | 0.0078 | 0.0174 | +$0.12777 | $0.06447 |
| 300–900 s | $0.30935 | 0.0110 | 0.0174 | +$0.28643 | $0.13379 |

Read the two tables together:

> **Where the closing-average mechanism is informative (τ ≤ 30 s), perfect
> knowledge of settlement is worth $0.008 per contract and is NET NEGATIVE to
> trade — the fee alone is twice the edge. Where there is real money (τ ≥ 300 s,
> $0.31 per contract), the mechanism supplies ZERO information: decidedness at
> τ=60 is 0 %, and the outcome depends on fifteen minutes of BTC movement.**

That is a mechanism-level NO-GO, not a failure to fit. No improvement to a BRTI
estimator changes it, because the issue is *when* the mechanism knows things,
not how well it is modelled. The $0.31/contract early in the window is **BTC
directional prediction over 15 minutes** — the hard, efficient-market problem,
which this issue's prior evidence already found negative.

## 7. Verdict and the next bottleneck

**NO-GO on the settlement-reference probability estimator.** Not because the
data is missing (it is), but because even with perfect data and a perfect model
its information arrives in the time region where the edge is smaller than the
fee. Collecting BRTI to build it would be spending effort on a mechanism now
measured to be worth less than it costs to trade.

Nothing was fitted to a held-out sample; no threshold was tuned. The inventory
controller is unchanged — no risk rule or execution assumption was touched, and
it continues to WAIT (correctly) at $0.00.

**The next bottleneck, stated precisely, is no longer a data-access problem:**
the only region with tradeable edge (τ ≥ 120 s, $0.13–0.29/contract net) requires
**forecasting BTC direction over 2–15 minutes better than the market**, and the
measured market Brier there (0.063–0.130, 48–75 % skill vs a coin) is what any
candidate must beat. That is a different and much harder research question than
the one this checkpoint was given, and I would not start it without saying
plainly that it is the efficient-market problem rather than a mechanical one.

**What I would do instead, if asked:** the one measurement in this system still
genuinely unmeasured is the maker fill-survival curve past 32.2 s
(`PMUS_FILL_HORIZON_PROBE.md`) — built, tested, unarmed. It bears on whether a
*maker* strategy can harvest the 120–300 s region at $0.064/contract where a
taker's fee eats most of it. That is bounded, cheap, and not an edge claim.

### Code and tests

New: `src/gemini_execution/brti_settlement.py`,
`analysis/profit_search/brti_determinacy.py`,
`analysis/profit_search/market_calibration_by_tte.py`,
`analysis/profit_search/windows_clean_labels.json`,
`tests/test_brti_settlement.py` (15 tests).
**454 pass** across settlement, PMUS, probe, inventory and continuous suites.
