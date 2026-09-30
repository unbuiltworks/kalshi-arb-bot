# OOS ADVERSARIAL AUDIT — CORRECTED RESULT

**49 PMUS BTC-15m windows, 2026-09-28. Label: SHADOW / HISTORICAL REPLAY.**
No money moved. No parameter was fitted to these 49 windows: θ=0.96, variant B,
PROFIT_LOCK=$5, hold=60s, residual_cap_k=2 are read from the frozen engine and
never written. R7 is not armed and its mandate is not renewed.

The audit was run on the posture that the published −$0.07/window q10 result
might be wrong. It found one real defect (chronological ordering), corrected it
at the loader boundary, and re-derived everything from the 13 preserved raw
slices. The corrected numbers are close to the defective ones; the conclusion
survives, the evidence standard did not.

---

## A. DATA INTEGRITY

### Provenance chain

| stage | artefact | note |
|---|---|---|
| raw | `data/pmus_btc15m_tape/raw_railway_logs/2026-09-28/*.jsonl.gz` (13 slices) | `PMUS_TAPE_ROW` lines exactly as Railway returned them |
| derived — DEFECTIVE, **preserved** | `data/pmus_btc15m_tape_oos/oos_tape.jsonl` | 2 chronological inversions (`0445z`, `1400z`) |
| derived — CORRECTED | `data/pmus_btc15m_tape_oos/oos_tape_ordered.jsonl` | 4,474 rows, re-derived from the 13 slices, ordered at derivation |
| boundary | `src/l5_replay_input.py` | `ordered()` repairs **and reports**; `assert_monotonic()` raises |
| loader | `scripts/pmus_completeness_rule.load_tape` | sorts and records into `LOAD_ORDER_REPORT` — never silently |
| settlement | `analysis/l5/r7_oos_settlement_authoritative.json` | 49/49 independent re-query |
| economics | `analysis/l5/r7_oos_corrected_ledger.json`, `..._corrected_tails.json` | 196 ledger rows (49 × 4 clips) |
| defect sweep | `analysis/l5/r7_oos_defect_class_sweep.json` | per-invariant verdicts |

### The defect that was found

`load_tape` returned rows in **file order**, not chronological order. Two windows
carried an inversion in which a `book` row was consumed before an earlier `bbo`
row — handing that decision a ladder from its own future. That is future-data
leakage, and it is why this audit could not be answered by rerunning the prior
artefact.

`scripts/audit_economic_complete.load()` **does** sort. That is precisely why the
frozen 25-window checkpoint kept validating: the checkpoint exercised the
*engine* through a sorting loader and said nothing about the *out-of-sample
loader*. The fix therefore went in at the earliest loader boundary, and the new
invariant **reports** inversions rather than quietly repairing them.

### Cohort

- 51 slugs in the corrected tape; **49 admitted** by the completeness rule; 2 rejected (`0230z`, `1500z`).
- Overlap with the frozen 25-window sample: **0**. The cohort is genuinely out of sample.

### Authoritative settlement (step 10)

Re-queried independently via `GET gateway.polymarket.us/v1/markets/{slug}/bbo`,
reading `settlementPx` together with `state`. **Terminal `lastTradePx` was not
used as settlement evidence.**

- requested 49 / answered 49 / **resolved 49** / unresolved 0 → settlement verification **PASS**
- all 49 `MARKET_STATE_EXPIRED` **with** a settlementPx present (terminal requires both, never the state alone)
- independent agreement with the prior winners map: **49/49, zero disagreements**
- `settlementPx != lastTradePx` on **49/49** windows (0.0000/1.0000 vs 0.0100/0.9900).
  The *direction* agrees on all 49, so the prior economics were not corrupted —
  but `last` was never adequate evidence, and is not used here.

| window | state | settlementPx | lastTradePx | winner |
|---|---|---|---|---|
| 0245z | EXPIRED | 0.0000 | 0.0100 | down |
| 0300z | EXPIRED | 1.0000 | 0.9900 | up |
| 0315z | EXPIRED | 0.0000 | 0.0100 | down |
| 0330z | EXPIRED | 1.0000 | 0.9900 | up |
| 0345z | EXPIRED | 0.0000 | 0.0100 | down |
| 0400z | EXPIRED | 1.0000 | 0.9900 | up |
| 0415z | EXPIRED | 1.0000 | 0.9900 | up |
| 0430z | EXPIRED | 1.0000 | 0.9900 | up |
| 0445z | EXPIRED | 1.0000 | 0.9900 | up |
| 0500z | EXPIRED | 0.0000 | 0.0100 | down |
| 0515z | EXPIRED | 0.0000 | 0.0100 | down |
| 0530z | EXPIRED | 1.0000 | 0.9900 | up |
| 0545z | EXPIRED | 1.0000 | 0.9900 | up |
| 0600z | EXPIRED | 1.0000 | 0.9900 | up |
| 0615z | EXPIRED | 0.0000 | 0.0100 | down |
| 0630z | EXPIRED | 1.0000 | 0.9900 | up |
| 0645z | EXPIRED | 0.0000 | 0.0100 | down |
| 0700z | EXPIRED | 0.0000 | 0.0100 | down |
| 0715z | EXPIRED | 0.0000 | 0.0100 | down |
| 0730z | EXPIRED | 0.0000 | 0.0100 | down |
| 0745z | EXPIRED | 1.0000 | 0.9900 | up |
| 0800z | EXPIRED | 1.0000 | 0.9900 | up |
| 0815z | EXPIRED | 0.0000 | 0.0100 | down |
| 0830z | EXPIRED | 1.0000 | 0.9900 | up |
| 0845z | EXPIRED | 1.0000 | 0.9900 | up |
| 0900z | EXPIRED | 0.0000 | 0.0100 | down |
| 0915z | EXPIRED | 0.0000 | 0.0100 | down |
| 0930z | EXPIRED | 0.0000 | 0.0100 | down |
| 0945z | EXPIRED | 1.0000 | 0.9900 | up |
| 1000z | EXPIRED | 1.0000 | 0.9900 | up |
| 1015z | EXPIRED | 1.0000 | 0.9900 | up |
| 1030z | EXPIRED | 1.0000 | 0.9900 | up |
| 1045z | EXPIRED | 1.0000 | 0.9900 | up |
| 1100z | EXPIRED | 1.0000 | 0.9900 | up |
| 1115z | EXPIRED | 1.0000 | 0.9900 | up |
| 1130z | EXPIRED | 1.0000 | 0.9900 | up |
| 1145z | EXPIRED | 1.0000 | 0.9900 | up |
| 1200z | EXPIRED | 1.0000 | 0.9900 | up |
| 1215z | EXPIRED | 1.0000 | 0.9900 | up |
| 1230z | EXPIRED | 0.0000 | 0.0100 | down |
| 1245z | EXPIRED | 1.0000 | 0.9900 | up |
| 1300z | EXPIRED | 0.0000 | 0.0100 | down |
| 1315z | EXPIRED | 1.0000 | 0.9900 | up |
| 1330z | EXPIRED | 1.0000 | 0.9900 | up |
| 1345z | EXPIRED | 0.0000 | 0.0100 | down |
| 1400z | EXPIRED | 0.0000 | 0.0100 | down |
| 1415z | EXPIRED | 0.0000 | 0.0100 | down |
| 1430z | EXPIRED | 0.0000 | 0.0100 | down |
| 1445z | EXPIRED | 1.0000 | 0.9900 | up |

---

## B. REPLAY INTEGRITY — PASS/FAIL per invariant

| # | invariant | verdict | detail |
|---|---|---|---|
| A1 | loader inversions on the **corrected** tape | **PASS** | 0 repaired — already ordered at derivation |
| A2 | `assert_monotonic` over all 51 slugs | **PASS** | raises on any inversion; none raised |
| A3 | cohort determinism | **PASS** | admitted 49, rejected 2, frozen-25 overlap 0 |
| A4 | frozen q50 checkpoint through the corrected path | **PASS** | `6.116725359099999999999999992` — exact match |
| A5 | `net == matched + residual − fees` | **PASS** | 196/196 ledger rows |
| D1 | future-data leakage | **PASS** | monotonic input on 51 slugs; a `book` row can no longer precede its own earlier `bbo` |
| D2 | NO inversion (own-terms `up=bid`, `down=1−ask`, `bid<=ask`, `up+down<=1`) | **PASS** | 2,219 bbo rows, 0 violations |
| D3 | fee sign | **PASS** | no negative fee, 49 windows × 4 clips |
| D4 | quantity conservation `bought == 2·matched + |residual|` | **PASS** | 49/49, instrumented at `Book.add` so taker completion legs are counted |
| D5 | fill prices strictly inside (0,1) | **PASS** | no fill at or outside the bounds |
| D6 | duplicate / missing rows | **PASS** | 4,424 rows unique by `(slug, kind, t)` |
| D7 | settlement inversion | **PASS** | settlementPx direction agrees with lastTradePx direction, 49/49 |
| D8 | authoritative terminal settlement for every window | **PASS** | 49/49 EXPIRED with settlementPx |
| D9 | end-of-window / non-OPEN rows | **PASS** | 79 non-OPEN bbo rows present, all skipped; no fill taken on one. A resting order at window end costs nothing — only fills are costed |
| D10 | partial fills | **PASS** | 1 partial fill across the cohort at q10, costed at its own qty |
| D11 | cash identity `payout − cash_paid − fees == net` | **PASS** | 49/49 within 1e-18 (Decimal division residue in `avg()`) |
| D12 | adverse-price maker fill (resting order filled *worse* than its own limit) | **PASS** | **0 occurrences** — instrumented directly; the engine re-quotes to the touch each tick, so resting price == fill price |

D4 and D10 each failed a *first* specification of the invariant and passed the
corrected one. Both first versions were my errors, not the engine's: D4 counted
only `NativeEngine.on_fill` and so missed taker completion legs that reach
`Book.add` directly, and D10 treated `unknown_events` as uncosted money when it
is a ladder-depth-shortfall counter. Recorded here rather than quietly dropped.

D12 matters for the economics below: the worst windows show matched pairs bought
for **more than $1**, and the first hypothesis was a replay defect — a resting
order being filled at the current touch instead of its own limit. Instrumented
across all 49 windows, that never happens. The above-$1 pair cost is the
engine's own behavior at prices the tape really showed.

**Verdict: every replay-integrity invariant PASSES on the corrected path.**
The economics below may therefore be interpreted.

---

## C. ECONOMICS (corrected, chronological input)

Per-window shadow net, 49 out-of-sample windows:

| clip | n | mean | median | losing | ≥ $5 | worst | best | matched-pair | residual | fees |
|---|---|---|---|---|---|---|---|---|---|---|
| q10 | 49 | **−0.068** | +1.091 | 19/49 | 9 | −18.80 | +7.30 | +1.602 | −1.346 | 0.324 |
| q20 | 49 | **−0.964** | +2.847 | 17/49 | 20 | −37.60 | +10.84 | +2.258 | −2.675 | 0.547 |
| q25 | 49 | **−1.483** | +3.517 | 17/49 | 23 | −47.00 | +13.55 | +2.540 | −3.386 | 0.637 |
| q30 | 49 | **−1.458** | +5.126 | 15/49 | 28 | −56.40 | +16.26 | +2.316 | −2.999 | 0.776 |

The shape is the whole finding: **the median window makes money at every clip and
the mean loses money at every clip.** Scaling the clip scales the median up and
the tail down faster. The q20→q25 "knee" seen in the frozen sample does not
survive out of sample.

### The tail mechanism (step 9)

Ten distinct windows occupy the worst-10 at *all four* clips — the tail is the
same windows every time, not clip-specific noise. Two mechanisms, cleanly separable:

| window | winner | clip | net | matched_qty | resid_qty | resid P&L | matched-pair P&L | maker | taker | mechanism |
|---|---|---|---|---|---|---|---|---|---|---|
| 0315z | down | 10 | -4.23 | 120.0000 | 0 | +0.00 | -3.28 | 160 | 80.0000 | PAIR COST > $1 |
| 0315z | down | 20 | -8.27 | 240.0000 | 0 | +0.00 | -6.38 | 320 | 160.0000 | PAIR COST > $1 |
| 0315z | down | 25 | -10.29 | 300.0000 | 0 | +0.00 | -7.93 | 400 | 200.0000 | PAIR COST > $1 |
| 0315z | down | 30 | -12.32 | 360.0000 | 0 | +0.00 | -9.48 | 480 | 240.0000 | PAIR COST > $1 |
| 0330z | up | 10 | -4.80 | 40 | 10 | -3.24 | -1.56 | 90 | 0 | BOTH |
| 0330z | up | 20 | -9.60 | 80 | 20 | -6.48 | -3.12 | 180 | 0 | BOTH |
| 0330z | up | 25 | -12.00 | 100 | 25 | -8.10 | -3.90 | 225 | 0 | BOTH |
| 0330z | up | 30 | -14.40 | 120 | 30 | -9.72 | -4.68 | 270 | 0 | BOTH |
| 0530z | up | 10 | -17.45 | 30 | 20 | -18.43 | +1.03 | 60 | 20 | ADVERSE RESIDUAL |
| 0530z | up | 20 | -34.89 | 60 | 40 | -36.87 | +2.07 | 120 | 40 | ADVERSE RESIDUAL |
| 0530z | up | 25 | -43.62 | 75 | 50 | -46.08 | +2.58 | 150 | 50 | ADVERSE RESIDUAL |
| 0530z | up | 30 | -52.34 | 90 | 60 | -55.30 | +3.10 | 180 | 60 | ADVERSE RESIDUAL |
| 0545z | up | 10 | -4.17 | 70 | 0 | +0.00 | -4.00 | 130 | 10 | PAIR COST > $1 |
| 0545z | up | 20 | -8.34 | 140 | 0 | +0.00 | -8.00 | 260 | 20 | PAIR COST > $1 |
| 0545z | up | 25 | -10.43 | 175 | 0 | +0.00 | -10.00 | 325 | 25 | PAIR COST > $1 |
| 0545z | up | 30 | -12.51 | 210 | 0 | +0.00 | -12.00 | 390 | 30 | PAIR COST > $1 |
| 0600z | up | 10 | -8.20 | 40 | 0 | +0.00 | -8.20 | 80 | 0 | PAIR COST > $1 |
| 0600z | up | 20 | -16.40 | 80 | 0 | +0.00 | -16.40 | 160 | 0 | PAIR COST > $1 |
| 0600z | up | 25 | -20.50 | 100 | 0 | +0.00 | -20.50 | 200 | 0 | PAIR COST > $1 |
| 0600z | up | 30 | -24.60 | 120 | 0 | +0.00 | -24.60 | 240 | 0 | PAIR COST > $1 |
| 0715z | down | 10 | -5.72 | 50 | 0 | +0.00 | -5.60 | 90 | 10 | PAIR COST > $1 |
| 0715z | down | 20 | -11.43 | 100 | 0 | +0.00 | -11.20 | 180 | 20 | PAIR COST > $1 |
| 0715z | down | 25 | -14.29 | 125 | 0 | +0.00 | -14.00 | 225 | 25 | PAIR COST > $1 |
| 0715z | down | 30 | -17.15 | 150 | 0 | +0.00 | -16.80 | 270 | 30 | PAIR COST > $1 |
| 0930z | down | 10 | -4.40 | 60 | 0 | +0.00 | -4.20 | 90 | 30 | PAIR COST > $1 |
| 0930z | down | 20 | -8.81 | 120 | 0 | +0.00 | -8.40 | 180 | 60 | PAIR COST > $1 |
| 0930z | down | 25 | -11.01 | 150 | 0 | +0.00 | -10.50 | 225 | 75 | PAIR COST > $1 |
| 0930z | down | 30 | -13.21 | 180 | 0 | +0.00 | -12.60 | 270 | 90 | PAIR COST > $1 |
| 1230z | down | 10 | -11.27 | 20 | 20 | -8.33 | -2.77 | 50 | 10 | BOTH |
| 1230z | down | 20 | -22.53 | 40 | 40 | -16.67 | -5.53 | 100 | 20 | BOTH |
| 1230z | down | 25 | -28.17 | 50 | 50 | -20.83 | -6.92 | 125 | 25 | BOTH |
| 1230z | down | 30 | -33.80 | 60 | 60 | -25.00 | -8.30 | 150 | 30 | BOTH |
| 1300z | down | 10 | -3.20 | 30 | 20 | +0.52 | -3.72 | 80 | 0 | BOTH |
| 1300z | down | 20 | -6.40 | 60 | 40 | +1.04 | -7.44 | 160 | 0 | BOTH |
| 1300z | down | 25 | -8.00 | 75 | 50 | +1.30 | -9.30 | 200 | 0 | BOTH |
| 1300z | down | 30 | -9.60 | 90 | 60 | +1.56 | -11.16 | 240 | 0 | BOTH |
| 1415z | down | 10 | -18.80 | 0 | 20 | -18.80 | +0.00 | 20 | 0 | ADVERSE RESIDUAL |
| 1415z | down | 20 | -37.60 | 0 | 40 | -37.60 | +0.00 | 40 | 0 | ADVERSE RESIDUAL |
| 1415z | down | 25 | -47.00 | 0 | 50 | -47.00 | +0.00 | 50 | 0 | ADVERSE RESIDUAL |
| 1415z | down | 30 | -56.40 | 0 | 60 | -56.40 | +0.00 | 60 | 0 | ADVERSE RESIDUAL |

**Mechanism 1 — adverse residual (`0530z`, `1230z`, `1415z`).** Unhedged
inventory on the losing side, settling at zero. `1415z` is the pure case:
`matched_qty = 0`, 20 contracts of residual, `net = −18.80` — entirely
directional, no pair economics at all. At q30 that single window is −$56.40.

**Mechanism 2 — matched pairs bought above $1 (`0600z`, `0545z`, `0715z`,
`0930z`, `0315z`).** These windows finish **fully matched with zero residual and
still lose**, because the pair cost exceeded $1. `0600z` at q10: 40 pairs for
−$8.20, i.e. −$0.205 per pair, a pair cost of ~$1.205. The fill trace is
unambiguous — DOWN filled at 0.57, 0.57, 0.19, 0.15 (avg 0.37) while UP filled
at 0.60, 0.78, **0.97, 0.99** (avg 0.835). The engine kept quoting the UP touch
as the market ran away from its DOWN inventory and completed at nearly $1.

This is the important structural finding, and it is not a bug in the replay
(D12): **θ=0.96 gates the taker `marginal_complete` path only. The maker leg is
ungated.** A maker OPEN/EXTEND can be filled on the second side at any touch the
market reaches, so the fee-safe ≤$0.96 pair discipline the operating model
assumes is simply not enforced on maker-completed pairs. Every one of these five
windows is maker-heavy (`0600z`: maker 80, taker 0).

Tail concentration (step 10 — **diagnostic only, no parameter is fitted here**):

| clip | all 49 | drop worst 1 | drop 2 | drop 3 | drop 5 |
|---|---|---|---|---|---|
| q10 | −0.068 | +0.323 | +0.701 | +0.961 | +1.321 |
| q20 | −0.964 | −0.201 | +0.537 | +1.039 | +1.719 |
| q25 | −1.483 | −0.535 | +0.382 | +1.003 | +1.839 |
| q30 | −1.458 | −0.314 | +0.793 | +1.545 | +2.564 |

Dropping 3 of 49 windows moves q30 from −$1.46 to +$1.55. That is a measure of
how concentrated the loss is, and it is **not** a result: leave-worst-k-out is
in-sample selection and carries no forward claim. Its only legitimate use is
what it says here — the mean is not diffusely negative, it is five-to-ten
identifiable windows negative, and both mechanisms behind them are named above.

### What this does and does not license

- It does **not** license advancing q25 or q30. The tail grows with clip faster than the median.
- It does **not** license a window selector fitted to these 49 windows.
- It does name two concrete, mechanical, testable deficiencies — an ungated maker
  pair cost and an uncapped adverse residual at settlement — either of which is a
  change to *execution discipline* rather than to strategy fitting.

