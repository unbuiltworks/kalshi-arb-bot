# One authoritative ledger, and the highest-cost divergence

---

## 1. The acquisition gap, reconciled to **$0.000000**

Your figures are both right; they are measuring different fill sets.

| | |
|---|---|
| old report `cash_deployed_all` | **$6,070,392.860757** |
| old report `total` (P&L) | **$345,525.641875** |
| → implied redemption | $6,415,918.502632 |
| canonical gross acquisition | **$6,352,044** |
| canonical redemption | **$6,396,030** |
| canonical fees | **$27,550** |
| canonical trading net | **+$16,436** |

**The three gaps, and they close exactly:**

| gap | amount |
|---|---|
| acquisition under-count (canonical − old) | **+$281,651.139243** |
| redemption over-count (old − canonical) | +$19,888.502632 |
| fees the old report never charged | +$27,550 |
| **sum** | **$329,089.641875** |
| old P&L − canonical net | **$329,089.641875** |
| **residual** | **$0.000000** |

**Exact to the cent.** The entire 21× P&L discrepancy is those three terms.

### What the $281,651 actually is: coverage, not cost basis

| | |
|---|---|
| canonical fills | 693,916 |
| old report legs | 680,690 |
| **difference** | **13,226 fills the old report never saw** |
| old average cash per leg | $8.9180 |
| **average cash per missing fill** | **$21.2953 — 2.39× the average** |

**Fees cannot explain it:** $27,550 is **43 bp** of acquisition; the gap is
**443 bp**. And the old report's own `sell_rows: []` confirms no sell-side
mis-signing. The missing fills are real, larger than average, and absent from
the older source — the old report ran against an incomplete activity set
(`activity.jsonl` + `activity_gap.jsonl.gz`, the gap file's name being the
clue).

### The period question you raised

| figure | window |
|---|---|
| old report | **165.51 days**, 2026-03-19 10:58 → 2026-08-31 23:17 |
| fee reconstruction | months 2026-03 … 2026-08 — **the same period** |
| incentive totals | **128 days** — a *different*, shorter window |

So trading and incentives were **not measured over the same span**. Normalised:
incentives **$71,551.74 / 128 d = $559.00/day**; trading **$15,707.45 /
165.5 d = $94.91/day**. The ratio is **85 : 15**, close to the 82 : 18 quoted
but not identical, and the 82% figure mixes periods. **The shape holds; the
precise share should be quoted as 82–85% with the span named.**

### THE AUTHORITATIVE LEDGER

| line | value | basis |
|---|---|---|
| BTC15m gross acquisition | $6,352,044 | `usdcSize`, canonical |
| BTC15m redemption | $6,396,030 | canonical |
| BTC15m fees | −$27,550 (43 bp) | reconstructed, 15.4% of fills fee-bearing |
| **BTC15m trading net** | **+$16,436** | |
| all four cohorts, trading net | **+$15,707.45** ≈ **$94.91/day** over 165.5 d | |
| incentives (separate window) | **+$71,551.74** ≈ **$559.00/day** over 128 d | |
| **credited to our account** | **$0.00** | `UNVERIFIED_NO_VENUE_CREDIT_RECORD` |

`data/l5/l5_btc15m_realized_economics.json` is **superseded** and its
+$345,525.64 must not be cited again. My own earlier "$2,088/day" came from it.

## 2. The highest-cost decision divergence

**Our entry gate requires a pair ≤ 0.96 before either leg may rest. L5's
median pair at first formation is 0.9900, and only 27.9% of his own entries
clear 0.96.**

| | L5 | us |
|---|---|---|
| median pair at first formation | **0.9900** | gate requires **≤0.96** |
| share of L5 entries our gate admits | **27.9%** | — |
| **share rejected** | **72.1%** | — |
| fills per window (his accumulating regime) | ~100 | **7 / 2 / 13** observed |
| matched contracts per window | — | **2.16** |
| pair cost trajectory | 0.9968 → **0.9603** over the window | planned 0.95, **realized 0.9827** |

**Economic impact: the gate rejects 72.1% of the states in which L5 actually
starts the sequence.** That is the largest single behavioural divergence we
have measured, and it is measured on his fills, not inferred.

## 3. The smallest code change — and why I recommend **against** it

The change is one constant: `maker_pair_ceiling` 0.96 → 1.00 in
`l5_runtime_config_verify` / `TS.max_pair_cost()`. One line, no new module.

**It is contraindicated, with numbers:**

| | |
|---|---|
| edge at a 0.99 entry | **+0.010/contract** |
| our measured adverse cost | **−0.046/contract** |
| **net at L5's median entry price** | **−0.036/contract** |

L5's 0.99 entry is profitable *only because* ~100 fills/window average it down
to 0.9603. **We get 2–13 fills per window.** Our realized effective pair was
**0.9827** against a *planned* 0.95 — we do not average down, we average
**up**, by the adverse cost.

**Out-of-sample validation, existing 384-window control:**

| | control | treatment |
|---|---|---|
| pair acquisition cost per matched | 0.7251 | **0.7297 — worse** |
| matched units | 158 | 369 |
| net | −68.96 | **−110.46** |
| `ladder_refused_pair_above_cap` | 0 | **2,680** |

The treatment arm already admits more entries — it refused 2,680 second legs
for pair-above-cap and still took far more — and it acquires at a **worse**
pair cost and nets **−$41.50** worse. **Loosening the ceiling is the same
direction, already tested, already negative.** All three fill scenarios agree
(−41.50 / −37.65 / −37.65).

**Verdict on the change: do not make it.** The divergence is real and it is
the largest one; closing it transfers L5's *entry price* without his *fill
count*, which is the half that makes it work.

## 4. Infrastructure checked, as instructed

| service | state |
|---|---|
| `l5-pmus-live` | `6e861476` SUCCESS |
| `l5-halt-watchdog` | `5ef90355` SUCCESS |
| **`l5-pmus-observer`** | **SUCCESS** — no critical failure |
| `pmus-ws-evidence-recorder` | SUCCESS |
| `l5-transfer-supervisor-shadow` | **FAILED** (2026-09-02) — a shadow supervisor, not a recorder; does **not** threaten campaign data completeness |
| **`btc-ref-collector`** | **`latestDeployment: null` — never deployed, holds no BTC reference series** |

I found no critical observer notification. If one is visible to you in the
Railway UI that the API does not expose, relay its text and I will act on it
rather than guess.

That last row also closes an open question from earlier: the BTC-spot series I
said "might be on the `btc-ref-collector` volume" does not exist. **The
spot-aware probability test remains untestable, and now for a definite
reason.**

## 5. Where this leaves the mission

One authoritative ledger exists. The transferable leg is **20.6 bp** and
**$94.91/day at L5's $59,400/day turnover**; the 82–85% that is incentives has
been credited to us at **$0.00**. The largest behavioural divergence is the
entry ceiling, and closing it is negative out of sample.

**No code change is recommended, no deployment proposed, no live campaign
requested.** Executor, watchdog, recorder and settlement reconciliation
preserved and verified healthy.
