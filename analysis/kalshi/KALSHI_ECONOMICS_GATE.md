# Section 7 — Kalshi economics gate

**One question:** can the specific accessible Kalshi 15-minute markets actually
provide the per-contract compensation that L5's incentive-era behaviour appears
to require?

**Verdict, up front:**

- **KILLED at Kalshi for every market where we have fill evidence.** Sourced
  maker compensation there is **$0.00/contract** against a **$0.2380/contract**
  break-even. No fee coefficient and no pair economics can close that deficit.
- **UNSOURCED, not insufficient, for the subsidised markets.** Their headline
  arithmetic is *not* below the hurdle. Eligibility and the pool-scoring rule
  could not be sourced, and fills there are entirely unmeasured.
- **Recommend no trading experiment at Kalshi.** The next step is a
  measurement, named in §7, not a strategy.

Read-only. No venue writes, no order path, no credentials present
(`KALSHI_*` all ABSENT; presence checked, never values).

---

## 1. The benchmark

| quantity | value | basis |
|---|---|---|
| unrebated maker P&L | **−$0.2380 / contract** | Gemini BTC5m, 46 windows, 10,698 evidenced maker fills |
| window-level bootstrap CI | [−$0.2831, −$0.1935] | same |
| break-even compensation | **$0.2380 / contract** | |
| pessimistic bound | **$0.2831 / contract** | |
| $480/day at q25 | **$0.3047 / contract** | |
| non-toxic states | **0**, across every bin of every pre-fill feature | |

**Transfer caveat, load-bearing.** This hurdle was measured on **Gemini**, not
Kalshi. No per-contract maker adverse-selection measurement exists for Kalshi
15m strikes. Applying −$0.2380 to Kalshi assumes comparable maker toxicity and
that assumption is **UNMEASURED**. It is why M5 below is a blocking measurement
rather than a nicety.

---

## 2. Terms: NOT sourced this session

Every Kalshi host is hard-blocked by this container's egress proxy:

| host | result |
|---|---|
| `api.elections.kalshi.com` | `CONNECT tunnel failed, HTTP 403 Forbidden` |
| `trading-api.kalshi.com` | `CONNECT tunnel failed, HTTP 403 Forbidden` |
| `kalshi.com` | `CONNECT tunnel failed, HTTP 403 Forbidden` |
| `docs.kalshi.com` | `CONNECT tunnel failed, HTTP 403 Forbidden` |

The proxy reports these as `connect_rejected` — "gateway answered 403 to CONNECT
(policy denial or upstream failure)". TLS verification was not disabled and
`HTTPS_PROXY` was not unset.

Consequently **every `SOURCED_TERMS` figure below is inherited** from the prior
P22 survey's dated public-API pulls (2026-09-09 and 2026-09-23) and is carried
with that provenance *and that staleness*. Nothing here is a live reading.
Where the prior survey's numbers were recomputable from on-disk evidence, they
were recomputed this session rather than trusted.

---

## 3. Verifying the 83.1% claim — true, and misleading

Recomputed from `analysis/kalshi/leg_tape.jsonl.gz`: **1,886 / 2,269 = 83.12%**
of rows under an active program. **The claim is VERIFIED.**

It is also economically misleading, and the decomposition is the point:

| series | rows | program rows | fraction | pool | target size |
|---|---|---|---|---|---|
| KXCRYPTOLEAD15M | 935 | 935 | 1.00 | $20.00 | 1,000 |
| KXSILVER15M | 197 | 197 | 1.00 | $20.00 | 300 |
| KXWTI15M | 196 | 196 | 1.00 | $20.00 | 300 |
| KXGOLD15M | 195 | 195 | 1.00 | $20.00 | 300 |
| KXCOPPER15M | 185 | 185 | 1.00 | $20.00 | 300 |
| KXNATGAS15M | 178 | 178 | 1.00 | $20.00 | 300 |
| **KXETH15M** | 192 | **0** | **0.00** | — | — |
| **KXBTC15M** | 191 | **0** | **0.00** | — | — |

**100% of the program coverage sits in series we have no fill evidence for, and
0 of 383 rows in the two liquid series are under any program.** The 83.1% is an
artifact of *which tickers were polled*, not of where flow is. Pool values
recomputed independently: `reward_raw = 200000` centi-cents = **$20.00** per
15-minute period, uniform across all 1,886 program rows;
`discount_factor_bps = 5000`.

### The leg tape is thin — flagged wherever load-bearing

**2,269 book snapshots, 72 tickers, 83.3 minutes of a single day (2026-09-09).**
It contains **no fills, no maker P&L and no credited rewards**, so it cannot
support any per-contract realized number. It is used here only for (a) program
presence and (b) book shape — both point-in-time census facts. Program presence
on one 83-minute slice is also *not* evidence of program persistence; M4 exists
because of that.

### Pair economics offer no offset

`pair_cost` = `yes_bid + no_bid` at the touch, over all 2,269 snapshots:

| min | p10 | median | max |
|---|---|---|---|
| 1.0010 | 1.0050 | **1.0200** | 1.1700 |

**`pair_cost < 1.00` in 0 of 2,269 snapshots. `<= 0.96` in 0 of 2,269.** The
spring-discipline fee-safe pair-completion target is never satisfied on this
sample, so there is **no locked pair-economics benefit** available to subsidise
the maker hurdle. (Thin sample — 83 minutes — but the margin is not marginal:
the *minimum* observed is 1.001.)

---

## 4. Fills: measured only where there is no subsidy

Independent sample, `data/policy_d_port_ext/*.jsonl`: **40,791 deduped prints**
(by `trade_id`), **2026-08-31T22:03:04Z → 2026-09-01T04:55:32Z (~6.9 hours)**,
51 market-windows.

| series | windows | trades | contracts | median contracts/window | notional | active program? |
|---|---|---|---|---|---|---|
| KXSOL15M | 17 | 15,916 | 298,065 | 14,854 | $145,760 | **no** |
| KXXRP15M | 17 | 15,315 | 287,386 | 13,822 | $128,748 | **no** |
| KXDOGE15M | 17 | 9,419 | 176,978 | 7,866 | $90,288 | **no** |

**Flow and subsidy are measured in disjoint market sets.** The three series with
real fill evidence have no program. The six series with a program have **zero
fill evidence anywhere on disk** — the only observation of their activity is the
prior survey's 2026-09-23 reading of KXCRYPTOLEAD15M windows at *13 trades /
$177 notional*.

---

## 5. Fees — not the binding constraint

**SOURCED TERMS** (`GET /trade-api/v2/series/*`, 2026-09-23, 8 series;
`fee_changes` returned 0 pending):

```
fee_type               = "quadratic"
fee_multiplier         = 1
price_level_structure  = "tapered_deci_cent"
maker/taker split      = UNSOURCED
clearing/settlement fee = UNSOURCED
```

The numeric coefficient is **UNSOURCED** (`kalshi.com` 403 here, HTTP 429 in the
prior session). Carried as a sensitivity, never hard-coded. Per-contract fee
under the quadratic form ≈ `c · P · (1−P)`:

| coefficient c | fee @ P=0.50 | fee @ P=0.20 | pair fee (both legs) | break-even pair cost | **as % of the $0.238 hurdle** |
|---|---|---|---|---|---|
| 0.000 | $0.00000 | $0.00000 | $0.0000 | 1.0000 | 0.0% |
| 0.035 | $0.00875 | $0.00560 | $0.0175 | 0.9825 | 3.7% |
| 0.070 | $0.01750 | $0.01120 | $0.0350 | 0.9650 | 7.4% |
| 0.100 | $0.02500 | $0.01600 | $0.0500 | 0.9500 | **10.5%** |

**Across the entire plausible range the fee is $0.000–$0.025, at most ~10.5% of
the hurdle.** Fees are therefore *not* the binding constraint, and the unsourced
coefficient **cannot flip this gate in either direction**. Note also that the
quadratic fee is *maximal at P=0.50* — exactly where a two-sided inventory
recycler quotes.

---

## 6. Compensation per filled contract — the three columns

Nothing advertised appears as realized. `CONFIRMED_CREDITED` is **$0.00 for
every Kalshi program**; no credit has ever been observed in this container.

### Class A — liquid, unsubsidised
KXBTC15M, KXETH15M, KXSOL15M, KXXRP15M, KXDOGE15M. **Fill evidence: yes**
(40,791 prints, 51 windows, 6.9h).

| column | value |
|---|---|
| SOURCED TERMS — pool | **$0.00 / period** |
| ESTIMATED compensation | **$0.0000 / filled contract** |
| MAXIMUM compensation | **$0.0000 / filled contract** |
| CONFIRMED CREDITED | **$0.00** |
| required | $0.2380 / contract |
| **shortfall** | **−$0.2380 / contract** |

Sourced basis: 0 of 383 KXBTC15M+KXETH15M leg-tape rows under an active program
(recomputed this session); the prior full-corpus pull (238,339 programs,
cursor-exhausted, 2026-09-23) found KXBTC15M's last program *started 2026-05-12*,
was `volume` type and `paid_out`.

**The arithmetic:**

```
  $0.0000  sourced compensation per filled contract
−    >= 0  quadratic fee (non-negative at every coefficient)
+  $0.0000 pair-economics offset (pair_cost < 1.00 in 0 of 2,269 snapshots)
= <= $0.0000  achievable compensation
   vs $0.2380  break-even requirement
  ------------
  deficit >= $0.2380 per contract  (>= $0.2831 against the pessimistic bound)
```

This result is **independent of the unsourced fee coefficient**: zero
compensation minus a non-negative fee is at most zero. The gate does not need
the missing terms to close here.

### Class B — subsidised, illiquid
KXCRYPTOLEAD15M, KXNATGAS15M, KXCOPPER15M, KXGOLD15M, KXWTI15M, KXSILVER15M.
**Fill evidence: none.**

| column | value |
|---|---|
| SOURCED TERMS — pool | **$20.00 / 15-min period** (= $1,920/day/market headline) |
| SOURCED TERMS — target size | 300 or 1,000 contracts |
| SOURCED TERMS — discount factor | 5000 bps |
| SOURCED TERMS — scoring rule | **UNSOURCED** |
| SOURCED TERMS — eligibility | **UNSOURCED** |
| ESTIMATED compensation | **NOT COMPUTABLE** |
| MAXIMUM compensation | $20.00 / filled contract (degenerate ceiling) |
| CONFIRMED CREDITED | **$0.00** |

Honesty requires saying this plainly: **class B is not killed by the
per-contract test.** Reward per filled contract is `pool_share / our_fills`,
which *rises* as fills fall, so a per-contract hurdle can always be cleared by
being filled less. The tests that actually bind are absolute dollars and
eligibility:

- At **full** $20.00 pool capture, break-even tolerates up to **84 filled
  contracts per period** (8,067/day).
- Headline ceiling is **$1,920/day/market**, which is **not below** the $480/day
  target. One market at 25% pool capture would be exactly $480/day on headline
  terms.

So headline arithmetic does not kill class B. What stops it is that
`ESTIMATED` cannot be filled in at all — `pool_share` needs the unsourced
scoring rule and `our_fills` needs fill evidence that does not exist for any
subsidised market — compounded by the observation that these markets are empty
(13 trades / $177 per window) while the markets printing 14,854 contracts/window
pay $0.00. **$1,920/day of headline pool in a market with $177 of flow is a
term, not a business.**

Eligibility detail: Kalshi's API-key documentation gates key creation on
"Premier or Market Maker" usage levels. Whether the Liquidity Incentive Program
is gated the same way is **UNKNOWN**, and with no credentials present no
authenticated eligibility check is possible from here.

---

## 7. Verdict and the next measurement

**Class A — KILLED.** For every accessible Kalshi 15-minute market in which we
have fill evidence, sourced maker compensation is $0.00/contract against a
$0.2380 break-even. The deficit is ≥ $0.2380/contract, it is robust to the
unsourced fee coefficient, and pair economics provide no offset. **Recommend no
further trading experiment there.**

**Class B — TERMS UNSOURCED.** This is explicitly *not* a finding that the
economics are insufficient. It is a finding that they are **unknowable from this
container**, on top of a market set with no measurable flow.

**No strategy is designed here.** The next steps are measurements, all at zero
capital, and all of M1/M2/M4/M5 are blocking before any live step:

| id | what must be confirmed | how / from where | blocking |
|---|---|---|---|
| **M1** | Does an ordinary non-market-maker account qualify for the LIP **at all**? | Kalshi LIP terms page + API-key usage-level docs; failing that, a written answer from Kalshi support quoting current program rules. Both hosts are 403 here, so this must be obtained outside this container. | **yes** |
| **M2** | The pool-scoring rule — how `target_size`, `discount_factor_bps` and proximity-to-reference-price convert rested size into a pro-rata share. | Published LIP methodology. The API exposes the parameters but **not** the rule, so it cannot be reverse-engineered from the public API. | **yes** |
| **M3** | The numeric quadratic fee coefficient; whether maker and taker fees differ; any clearing/settlement fee. | Kalshi published fee schedule. | no — ≤10.5% of the hurdle, cannot flip the verdict |
| **M4** | Does a liquidity program ever reappear over a **liquid** series (KXBTC15M / KXETH15M / KXSOL15M / KXXRP15M / KXDOGE15M)? | The existing read-only collector already logs `KALSHI_INCENTIVE_STATE` every cycle. Watch it. **No new code.** | **yes** |
| **M5** | A **Kalshi-native** per-contract maker adverse-selection number, to replace the transferred Gemini −$0.2380. | Shadow maker quotes against the existing read-only tape, labelled strictly shadow. Requires a fills tape far longer than the 83-minute leg tape. | **yes** |

M4 is the one observation that would make Kalshi interesting again. Until it
fires, the two halves of the business — the flow and the subsidy — remain in
different markets, and that is the whole finding.

---

### Accounting labels used

| label | meaning |
|---|---|
| `SOURCED_TERMS` | published/declared terms. **A term, not a cashflow.** |
| `ESTIMATED_COMPENSATION` | arithmetic under those terms. **Not income, not realized.** |
| `CONFIRMED_CREDITED` | an attributable cash credit. **$0.00 for every Kalshi program.** |

### Sample sizes and date ranges for every number

| evidence | size | date range |
|---|---|---|
| hurdle (Gemini, transferred) | 46 windows, 10,698 maker fills | — |
| leg tape — program presence, book shape, pair cost | 2,269 rows, 72 tickers, **83.3 min** | 2026-09-09 00:16Z–01:40Z |
| print tape — fill volume | 40,791 deduped prints, 51 windows, ~6.9 h | 2026-08-31 22:03Z – 2026-09-01 04:55Z |
| incentive corpus (prior, inherited) | 238,339 programs, cursor-exhausted | 2026-09-23 |
| venue census (prior, inherited) | 183,329 programs, 2,790 active, 300 series | 2026-09-09 00:50Z |
| fee terms (prior, inherited) | 8 series, 0 pending changes | 2026-09-23 |
| credited Kalshi rewards | **0 observations** | — |

Machine-readable: `analysis/kalshi/KALSHI_ECONOMICS_GATE.json`.
Recomputation: `scripts/kalshi_reward_gate.py` (read-only, no network, no credentials).
