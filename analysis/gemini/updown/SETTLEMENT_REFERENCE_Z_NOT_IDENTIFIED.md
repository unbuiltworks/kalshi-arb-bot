# Settlement reference: exact Z is **NOT IDENTIFIED**

Correction accepted — and it needs one amendment of its own before anything else.

**Status: `Z_STATUS = NOT_IDENTIFIED`.** 7 of 8 required parameters are unknown from
this environment. No Z was fabricated. The unconditional executable favorite/underdog
calibration is unaffected and stands as delivered.

---

## 1. Amendment: there are two unknown indices, not one

You are right that Gemini identifies `GRR-KAIKO_RFR_BTCUSD_60S`, and our own recorded
contract objects confirm it. But they also show **the index changed mid-study**:

| index | era (by expiry) |
|---|---|
| `GRR-KAIKO_RFR_BTCUSD_60S` | through **2026-10-02T17:45Z** |
| `LS-LRRR-XBTUSD` | from **2026-10-02T17:50Z** onward |

The switch is visible inside a single day in `ie_usable_windows.json` (129 windows on
the old feed, 236 on the new).

**This matters more than the Kaiko parameterization**, because the only Gemini tape
that carries a reference-price series alongside executable books —
`ie_tte_ladder.json`, the 133 BTC5m windows the whole calibration rests on — is
**entirely the LS-LRRR era**. Fully identifying `GRR-KAIKO_RFR_BTCUSD_60S` would
therefore *not* license a Z on that tape. Nothing about `LS-LRRR-XBTUSD` is
established here: not the provider, not the statistic, not the window.

So the identification task is larger than stated: the richer tape sits under the index
that is not even nominally Kaiko.

---

## 2. Step 1 — authoritative evidence: not obtainable from this container

`kaiko.com`, `docs.kaiko.com` and `marketing.kaiko.com` are all **denied by this
environment's network policy** (the Benchmark Rates Rulebook PDF returned a 403 at the
CONNECT stage). `api.gemini.com` is denied too. The rulebook could not be read here.

What I *do* hold, and treat as authoritative:

| fact | source |
|---|---|
| `SourceDetails` — *"Structured data source information for price observation … Present for crypto Up/Down events"*, `agency` exampled "Kaiko", `index` exampled `GRR-KAIKO_BTCUSD_60S` | Gemini OpenAPI, sha256 `53caea56…69d165`, preserved in-repo |
| `StrikeType.reference` — *"Crypto Up/Down reference strike price captured at `availableAt` time"*, null until then | same |
| both index strings, per contract, per era | our own recorded contract objects |
| 1,322 resolved `reference`-type contracts with strike + settlement | our own records |

What I have and explicitly **do not** treat as authoritative: a search-engine summary
of Kaiko's general aggregation approach (equal-size time partitions, a volume-weighted
median per partition at the 50th percentile of cumulative volume, then a
recency-favouring time weighting, with real-time windows selectable from
15/20/30/60/120/300s). That is a description of the **family**, not the
parameterization of this ticker — exactly the inference your brief forbids. It is
recorded as secondary and nothing is computed from it.

### Parameter status

| parameter | status | what is missing |
|---|---|---|
| calculation window length | **PARTIAL** | the ticker ends `_60S`, and our own fit to 133 resolved settlements prefers a 60s mean ($0.99 median error) over an instantaneous print ($3.09) and over a 30s mean ($1.94). Unknown: whether the window *ends* at expiry or is offset by a publication lag |
| partition size / count | **UNKNOWN** | everything |
| intra-partition statistic | **UNKNOWN** | whether a volume-weighted median applies here, and at what percentile definition |
| cross-partition weighting | **UNKNOWN** | **the weight function — which is precisely what the Z variance depends on** |
| source venues / trade filters | **UNKNOWN** | "vetted centralized exchanges" is not a list, so a faithful reconstruction is impossible even in principle |
| publication timing | **UNKNOWN** | emit cadence, and the lag from window close to print |
| missing-data behaviour | **UNKNOWN** | fallback when a partition has no qualifying trades |
| `LS-LRRR-XBTUSD` construction | **UNKNOWN** | provider, statistic, window — and it governs the only usable tape |

---

## 3. The estimator form is already correct in-repo — one assumption is the blocker

I did not need to build the Z machinery: `src/gemini_maker/fair_value.py` already
implements the **right form**, i.e. the distribution of the settlement *statistic*, not
of spot:

```
tau >= w :  E[settle] = spot                 Var = sigma^2 * ((tau - w) + w/3)
tau <  w :  E[settle] = (realized*(w-tau) + spot*tau)/w
                                             Var = sigma^2 * tau^3 / (3 w^2)
```

with `Z = (E[settle] − strike) / sqrt(Var)`. This is **not** spot-distance over
`sigma*sqrt(T)`, and it is not borrowed from any other venue: the `tau^3/(3w^2)` term is
the variance of the *remaining* part of an average whose earlier part is already
realized, which is the quantity your brief asks for.

**The single blocking assumption is the weight function.** Both variance expressions
assume the settlement statistic is an **equal-weight time average** over a fixed `w`.
Under partitioned volume-weighted medians with recency weighting, the weights change —
and with them *both* the realized share and the variance of the remaining part. The Z
that module would emit today is therefore a different number from the Z the question
needs, which is why it stays unused.

Worth flagging: the repo contains a **third, distinct** statistic.
`src/gemini_maker/ref_lag_recorder.py` computes `sum(p)/len(dq)` — an unweighted mean
of *trades* in the trailing 60s, which is neither an equal-weight **time** average nor
Kaiko's construction. Trade-count weighting and time weighting diverge whenever trade
arrival is uneven, which is exactly the late-window regime that matters.

---

## 4. Step 2 — verification against published values: run, and it bounds the answer

`analysis/gemini/ref_lag_study.json` already contains the attempt: an equal-weight 60s
mean of **single-venue Gemini spot** trades, scored against Gemini's *own published
settlement values*. Restricted to BTC (the artifact pools XRP/SOL/ETH/BTC, so its
dollar statistics are meaningless pooled — a trap I walked into once before correcting
it), **16 windows** (10 × BTC5m, 6 × BTC15m):

| | median | p75 | p90 | max |
|---|---|---|---|---|
| absolute error, USD | **$8.39** | $14.84 | $16.91 | $19.54 |
| absolute error, bps | 1.075 | 1.907 | 2.152 | 2.505 |

**Sign of (settlement − strike) agreed in 16 of 16.**

Now the number that settles the question. Across the 133 BTC5m windows, the settlement
margin `|settlement − strike|` is:

| median | p25 | p10 | min |
|---|---|---|---|
| **$20.45** | $9.17 | $2.65 | $0.00 |

- **22.6%** of windows (30 of 133) have a margin smaller than the proxy's *median* error
- **43.6%** (58 of 133) smaller than its *p90* error

So a crude proxy answers *"which side settles"* reliably, but **its error is the same
order as the quantity Z must measure**. In roughly a quarter to a half of windows the
reconstruction error alone is as large as the entire distance to the strike. That is a
measured bound, not a guess, and it is why I will not dress this proxy up as Z.

---

## 5. Step 3 — the same-price test is unrunnable on this corpus *regardless of Z*

Your framing of the candidate edge is the right one, so I checked whether the test can
be executed at all before Z is identified. The unit is the window, so feasibility
depends only on windows per cell — knowable now.

Within (executable-price bucket × TTE), BTC5m, 133 windows, 65 non-empty cells:

| feasibility | cells |
|---|---|
| usable (≥10 windows per Z tercile) | **0** |
| marginal (5–10) | 1 |
| underpowered (<5) | **64** |

The largest single cell holds **15 windows** (120s / 80–90¢), i.e. **5 per tercile**.
Detecting a 10pp win-rate separation between extreme Z strata at a ~70% base rate needs
on the order of **300 windows per cell** at 80% power.

**So identifying the Kaiko construction is necessary but not sufficient.** Even with an
exact Z in hand, this tape cannot answer the same-price question. The window count is
the binding constraint, and it is the same constraint that put BTC15m at COLLECT MORE.

---

## 6. What this does and does not change

**Unchanged and standing:** the unconditional executable calibration. It never used Z.
BTC5m underdog 5.04% vs 22.80% breakeven; favorite −16.32pp at 120s; two executable
asks summing to a median 1.330 against probabilities summing to ~80.8%; the late-window
favorite adverse selection (leader wins 67–79% overall but 17–26% where a ≥50¢ ask
exists). Verdicts hold: **BTC5m KILL, BTC15m COLLECT MORE.**

**Withdrawn:** the earlier `settlement_state_calibration.json` surfaces used a Coinbase
spot proxy against the strike and were labelled as proxy throughout, but under your
correction they are not a settlement-state variable at all and must not be read as one.
They remain in the repo as the descriptive price/spot surfaces they are, and the
state-conditioned favorite test built on them is superseded by this NOT IDENTIFIED
status rather than by a better number.

**Not fabricated:** no Z, no σ√T substitute, no imported remaining-bucket formula.

## 7. What would close it

1. **The authoritative Kaiko rulebook** for this exact ticker — partition size/count,
   intra-partition statistic, weight function, venue set, publication timing,
   missing-data rules. The environment's network policy currently denies Kaiko's
   domains; adding them under Network access in the environment settings (Edit →
   Custom → Allowed domains, keeping the package-manager defaults) would unblock it.
   Steps: https://code.claude.com/docs/en/cloud-environments#network-access
2. **The same for `LS-LRRR-XBTUSD`**, which actually governs the usable tape and about
   which nothing is known.
3. **Multi-venue trade data** for the index's venue set, since single-venue
   reconstruction is error-bounded at ~$8–17 against a $20 median margin.
4. **More windows.** Even an exact Z needs ~300 windows per price×TTE cell; the corpus
   has 133 windows total and a 15-window maximum cell.

## Artifacts

`settlement_reference_identification.json` (machine-readable parameter status,
verification, resolvability bound and power check) · script
`scripts/gemini_settlement_reference.py`.
Accounting label: documentation and historical replay on Gemini's own records.
No order was sent.
