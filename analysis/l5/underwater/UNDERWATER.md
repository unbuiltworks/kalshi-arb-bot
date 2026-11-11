# P0 — Underwater inventory behaviour, and the recorder memory diagnosis

Read-only. No production change. `src/l5_underwater.py` · `tests/test_l5_underwater.py`
· `python3 analysis/l5/underwater/run.py`

L5 corpus: **2,201 episodes, 71,847 decision points**, aligned at each episode's
first fill. Every feature is computed from fills already seen; the next fill
supplies only the ACTION label.

## Q1 — When deeply underwater, L5 EXTENDS. He does not hammer the complement.

| gain bucket | n | EXTEND | SWITCH | HOLD | dwell p50 | residual p50 |
|---|---|---|---|---|---|---|
| **<−20c** | 10,637 | **69.2%** | 27.1% | 3.7% | 1.0s | 192.7 |
| −20..−10c | 12,514 | 69.6% | 28.3% | 2.1% | 2.0s | 143.0 |
| −10..−5c | 6,875 | 68.7% | 29.6% | 1.7% | 2.0s | 112.2 |
| −5..0c | 8,117 | 65.7% | 31.5% | 2.8% | 3.0s | 82.2 |
| 0..+5c | 11,559 | 62.9% | 31.8% | 5.3% | 3.0s | 52.9 |
| >+5c | 21,918 | 65.9% | 31.5% | 2.6% | 2.0s | 118.3 |

He adds to the cheap held side ~2/3 of the time **at every gain level**, and
carries a far larger residual when underwater (192.7 vs 52.9). He almost never
abandons (HOLD ≤ 5.3%).

## Q2 — The gain gradient is real but weak, and survives the TTE control

SWITCH probability by gain × TTE (cells with n≥30):

| gain | tte<30s | 30-120s | 2-5m | 5-10m | >10m |
|---|---|---|---|---|---|
| <−20c | 19.1% | 26.5% | 28.0% | 27.1% | 29.6% |
| −5..0c | 26.4% | 28.1% | **37.2%** | 31.1% | 30.5% |
| >+5c | 24.1% | 29.3% | 32.0% | 31.2% | 33.3% |

Monotone-ish within every column, so it is not a TTE artefact — but the whole
span is ~5–9pp. **Gain shifts his switch rate; it does not gate it.**

## Q3 — A third of our COMPLETEs were never the model's choice

Of 239 acted COMPLETEs (37-market corpus): **163 (68.2%) sampled COMPLETE**,
**76 (31.8%) SHELL-FORCED** — 72 `sampled extend → acted complete`, 4 from open.

**This is the finding.** The shell converts EXTEND into COMPLETE, and EXTEND is
exactly what L5 does 69.2% of the time in the state we were in.

## Q4 — Our repeated underwater attempts were mostly the same economics

| market | attempts | distinct books | per book | distinct maker prices | pair range |
|---|---|---|---|---|---|
| cribuc-biaand | 104 | 34 | **3.1** | **1** (0.66) | 1.14–1.24 |
| andfen-ryaseg | 14 | 13 | 1.1 | 7 (0.45–0.62) | 0.92–1.02 |

The underwater market was re-posted at **one price** into economics that never
approached 0.97. The market that moved — and where we repriced with it — is
where the only fill and the only SUPPORTED row came from.

## Q5 — Underwater positions mostly recover, and match MORE often

- ever underwater: **1,446/2,201 = 65.7%**
- recovered to gain>0: **865/1,446 = 59.8%**
- deeply underwater (<−20c): 586 episodes, **67.2% recovered**
- matched fraction: **0.615 underwater** vs 0.455 overall

Being underwater is not terminal in his tape; it is the normal mid-episode state.

## Q6 — Attempt intensity differs by ~50×

L5 per episode: p50 **6 fills, 2 switches**, 180s span. Dwell **2.0s whether
underwater or in profit** — no hesitation either way.

Ours, single episodes: **104** COMPLETE attempts (cribuc-biaand, 0 fills), **123**
(lad-mia, 4 fills). `rest_s` p50 2.1s.

## Q7 — The obvious candidate rule FAILS out of sample

Rule: after K consecutive COMPLETE attempts at the same maker price with no
fill, stop re-posting until the price would change. No lookahead.

| corpus | K=1 | K=2 | K=3 | K=5 |
|---|---|---|---|---|
| live cohort (118 attempts, 5 fills) — fills lost | 4 | 0 | 0 | 0 |
| streak_shadow (239 attempts, 16 fills) — **fills lost** | 10 | **9** | **7** | 0 |

**At K=2 it destroys 9 of 16 fills on the larger corpus.** Only K=5 is
fill-neutral, and there it suppresses 127 attempts that never filled — changing
no matched pair and no terminal residual. Against the stated bar (must improve
matched-pair AND terminal residual economics, not merely reduce order count)
**this rule does not qualify, and at plausible K it is actively harmful.**

The rule the evidence points at — allow EXTEND when underwater instead of
converting it to COMPLETE — is **not** tested here and must not be assumed: the
residual-cap study already measured **zero extra matched inventory at every
cap**, so extra EXTENDs bought directional residual, not pairs. Two findings
point opposite ways; neither is promoted.

`gain_per_unit` stays an explanatory variable. No "COMPLETE only when gain>0"
rule is proposed: L5 switches above $1.00 on 47.16% of his switches, so such a
gate would refuse roughly half of what he actually does.

---

# Recorder memory diagnosis (read-only; nothing restarted or changed)

RSS **0.599 GB → 6.45 GB over ~6h**, still climbing ~1 GB/h (1h avg 5.39,
current 6.45) against a 32 GB limit — roughly a day of headroom at this rate.

**Stale accumulation has NOT returned.** Live `state_prune` rows, every ~70s:

    {"kept":1500,"evicted_idle":0,"evicted_over_cap":44,"protected":160,
     "max_idle_s":7200.0,"max_markets":1500}

`evicted_idle: 0` every cycle — nothing ever reaches the 2h idle rule. `kept`
is pinned at the **1500 cap** and eviction is driven entirely by it, replacing
28–81 markets per cycle (~1,500–4,200/hour).

**No unbounded structure exists.** `mstate` ≤1500 markets, `bbo_history` ≤2000
per market (`del s["bbo_history"][:-2000]`), `trades` ≤500 (`del s["trades"][:-500]`).

**The memory is not about the 160 seats.** It is the 1500-market COLD state —
**9.4× the seat count**. Designed ceiling ≈ 1500 × (2000 bbo + 500 trades) ≈
3.75M small dicts ≈ 2 GB of live objects. Observed 6.45 GB is that plus churn:
`snapshot_state` shallow-copies every market's full history lists every status
cycle (~27s), and 28–81 markets/cycle are destroyed and rebuilt — the classic
CPython arena-fragmentation pattern, and RSS is a high-water mark.

**The 0.709 GB baseline was a cold-start reading.** `RECORDER_EXPANSION.md`
records the before as "8.27 GB (avg 7.99, max 8.30)" and the after as a bare
"0.709 GB" with no average or max — a spot measurement taken before `mstate`
refilled. "RSS fell ~12×" compared a filled process against an empty one.

**Verdict:** not a regression of the old bug, not a leak, but not free either.
The binding parameter is `PMUS_WS_STATE_MAX_MARKETS=1500`, not the seat count.
No restart, capacity change or deployment made or proposed here.
