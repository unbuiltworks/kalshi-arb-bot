# Handoff — analysis session → implementation owner

**From:** `session_01YBGSQCRFVuSgniYgkxneM9` (analysis, read-only)
**To:** `session_01UzBdAD7nfP8SBPk1bB3uDs` — **sole implementation owner of
`feature/l5-transfer-backtest` and of all deploys**

**This session modified nothing, deployed nothing, placed no order. All
collectors and trading safety controls are as found. No live orders without
separate authorization.**

---

## 1. Deployed state, as verified at 2026-10-09 ~15:10Z

**`pmus-ws-evidence-recorder`** — service `71b6ea99`, **live**

| | |
|---|---|
| branch | `feature/l5-transfer-backtest` |
| commit | `ddc2195cb83db` |
| **running entrypoint** | **`src.pmus_ws_evidence_recorder_enriched`** (not the base module) |
| latest deployment | `ed464f2d` SUCCESS, 2026-10-02T17:12Z |
| volume | `/state/pmus-ws`, 50 GB, live, region `us-east4-eqdc4a` |
| restart policy | ALWAYS, 1 replica |
| **auto-deploy** | **`watchPatterns` include both recorder files — any push to this branch restarts the collector** |

**`r5ft-clean-2ebb50f-buildcheck`** — service `bc7a1f5b`: **11 deployments
today**, latest `2dc16077` SUCCESS at **14:55:30Z**, all from branch
`claude/august-l5-forensic-replay-788cml`, whose HEAD moved to `7803ba82`
at **15:08Z**. This is the implementation owner's active line.

Other services referenced in the L5 line and **not inspected by me**:
`pmus-spring-l5-probe` (`d84d9a68`), `pmus-q1-executor` (`fbd8eab5`),
`pmus-sports-forward-collector` (`3ef72cbf`), `l5-ops-research-live`
(`39616574`), `l5-transfer-supervisor-shadow` (`aed83a6f`). Project
`arb-ev-wallet-shadow` holds 57 services; I enumerated but did not probe
them.

## 2. Reconciliation — cross-shadow P3/P6 vs six-policy replay P3/P6

**The labels mean different things in the two studies.** I had this wrong
in an earlier report and corrected it.

| | **Study A — cross-shadow** | **Study B — six-policy replay** |
|---|---|---|
| P3 / P6 are | **section** labels: P3 = economic decomposition, P6 = L5 fidelity | **policy** names: **P3 = BOTH_SIDES_RESTING**, **P6 = INVENTORY_AWARE** |
| policy under test | cross the complement **as taker** at the instant of a real production COMPLETE, thresholds 0.97/0.98/0.99/1.00 × delays T0/T10/T30/T60 (16 cells) | P3 quotes both sides from the start, independently; P6 completes whatever fits the pair budget, continuously |
| source data | **260 real production COMPLETE decisions / 589 order rows**, `pmus-spring-l5-probe`, 2026-09 | **222 BTC up/down markets / 7,630 observations**, clone-shadow tape, cycle 1620, 2026-10-09 14:26Z, commit `b8ce4b40d` |
| fill assumption | **strict executability** — needs authenticated quote ≤5 s **and** recorded ask depth; missing evidence → `UNKNOWN_EXECUTABILITY`, never a fill | **queue-cleared simulation** — touch without volume never fills, volume inside the queue never fills, only excess over the queue **at placement** is ours, missing ladder ≠ empty queue |
| result | **0 supported crosses, 16/16 cells** | **P3 50.0% / P6 21.1% completion**; median pair cost **0.990 / 0.940** |
| report path | `analysis/l5/cross_shadow/RESULT.md`, `p4_p5_p6.json`, spec `PREREGISTRATION.md` (sha256 `7e31c447…`) | `analysis/l5/clone/PAIR_POLICY_SENSITIVITY.md` |

**Why one reported 21%/50% and the other zero — three independent reasons:**

1. **Different question.** A asks whether *one taker cross* was executable
   *at one instant*. B asks how often a *resting policy eventually
   completes* over a whole window — many chances, not one.
2. **Opposite evidence standards, and this is decisive.** **74.7% of A's
   stops are "the evidence does not exist"** (56.2% no authenticated quote
   age + 18.5% no depth recorded; the remaining 25.0% is a stale quote,
   5.1 s–826 s). **A never reached a price test.** B simulates fills against
   a tape that *does* carry ladders, so it never meets that problem.
3. **Different corpora.** Different services, different schemas, different
   months, no overlap.

**They do not contradict.** A's zero is a statement about **instrumentation**.
B's 21%/50% is a statement about **simulated queue-cleared completion**.
Only B speaks to whether PMUS can form matched inventory.

## 3. Depth and timestamps — already captured, confirmed

The recorder **never dropped them**: `pmus_ws_evidence_recorder.py:273–298,
450–451` computes, stores and serves `bid_qty`/`ask_qty`, and prints them in
`PMUS_WS_STATUS`. The loss was in the consumer, `_recorder_book`
(`pmus_spring_l5_session.py:2488`), **and was fixed on 2026-09-13** by a
purely additive allowlist capture,
`pmus_cross_shadow_live.observational_capture` (line 408), which persists:

* `executable_depth`, `executable_depth_status`, `executable_depth_field`
* `executable_complement_price`, `executable_complement_price_unknown`
* `status_utc`, `probe_receipt_utc`, `probe_transport_delta_s`
* `age_at_probe_receipt_s`, beside the **untouched** `authenticated_age_s`

Every prior key keeps its exact value and meaning; fail-soft on exception.
**No further recorder change is needed, and I made none** — it would have
been a no-op whose only effect was an unnecessary collector restart, into a
branch another session was pushing to minutes earlier.

## 4. Remaining evidence gaps

| gap | status | cost to close |
|---|---|---|
| **Opposite-side flow while our quote rests** | **MISSING — the highest-value field.** Prints on the complement at or through our price between placement and cancel/expiry. **No flow ⇒ no market; flow we did not get ⇒ placement or queue.** This single field separates those two hypotheses, and nothing currently distinguishes them | extra field on a tape already being written; no new service |
| **Queue-ahead evolution** | **PARTIAL.** Study B has queue-ahead **at placement** only. Without it at each print, "someone traded ahead of us" is indistinguishable from "we were never near the front" | extra field on the same tape |
| **Authoritative settlement** | **MISSING.** Closed-market sweep finds **0 of 18** held markets; `/v1/markets/{slug}` returns **404** on this gateway. Every residual P&L is `SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK`, never venue-confirmed | needs a venue endpoint that does not appear to exist publicly — the one genuine external blocker |
| **Persistent data retention** | **GAP.** `pmus-spring-l5-probe` has **no persistent volume** (per the L5 recovery note; I did not re-probe the service). Captured observations are not durable across restarts | service-config change, not code |

## 5. Corrected conclusion on PMUS matched inventory

**Modelled matched inventory IS possible on PMUS. Full-inventory
profitability is UNPROVEN.**

I previously reported that PMUS "never forms matched inventory — zero class
A at every cap". **That was too strong and is withdrawn.** It came from the
*residual-cap* study, which asked whether extra **same-side extends** later
convert — and zero did. That is a different question from whether a
**both-sides-resting** policy can complete a pair.

| consistent definitions | L5 (observed fills) | PMUS (Study B replay) |
|---|---|---|
| matched formation | 76.1% of windows | **P6 21.1%, P3 50.0%** of entries |
| achieved pair cost | median **0.980** | **P6 0.940, P3 0.990** |
| residual left open | 99.2% of windows | **P6 79%, P3 50%** |
| net on matched legs | **+1.43¢/pair** | **P6 +1.74¢/entry, P3 +0.50¢** |
| adverse selection t+30 | — | **P6 −0.100, P3 −0.090** (vs −0.030 immediate taker) |

**Why profitability is unproven:** P6 and P3 are positive **on matched legs
only**, and leave **50–79%** of entries holding a directional residual whose
P&L cannot be venue-confirmed. The out-of-sample half has **6 entries** —
not validation. And the two cheapest policies carry the *worst* markouts, so
patience buys pair cost and pays in adverse selection.

## 6. CF BRTI history — provenance, independently checked

The implementation owner's commit `3a5cfd6ad` states no BRTI history exists.
**I verified that independently rather than repeating it.**

**BRTI appears only as contract metadata, never as a price series:**

* `src/l5_venue_adapter.py:379–382` — the settlement *rule* ("Up iff
  BRTI(T_end) ≥ BRTI(T_start)", 60-price average to 2 dp)
* `analysis/pmus/p25_shadow_trackB.json:14`, `P27_SHADOW_AND_GATE.md:68`,
  `THREE_VENUE_READINESS.md:42`, `CATEGORY_RANKING.{md,json}` — the same
  rule quoted as a venue property

**Nearest artifacts, and why none is BRTI:**

| artifact | what it is | why not BRTI |
|---|---|---|
| `data/btc_spot_proxy/btc_1m.json` (982 KB) | Coinbase 1-minute spot, 2026-08-20..25 | **self-labelled `PROXY_NOT_SETTLEMENT_INDEX`**, with an explicit `what_this_is_not` field saying so |
| `data/ref_lag/reflag_20260831T*.jsonl` (3 files, ~7 MB) | Gemini Kaiko-RFR strike recordings, 2026-08-31 | Kaiko RFR, a different index; status `Z_STATUS: NOT_IDENTIFIED` |
| `feature/gemini-kaiko-historical-proxy`, `feature/gemini-kaiko-proxy-calibration` | `gemini_kaiko_historical_proxy*.py`, `gemini_kaiko_proxy_calibration*.py` + tests | **code only, no committed history**, and Kaiko ≠ CF Benchmarks BRTI |

I searched eight candidate branches by path for `brti|cf_bench|cfbench|rfr|
kaiko|reference_price|refprice|settlement_index`. **Only the two Kaiko
branches matched, and only with source files.**

**Conclusion: no CF Benchmarks BRTI history exists anywhere in this
repository, on any branch I checked.** Settlement inference from the
terminal book is the correct fallback, and its label must travel with every
number derived from it.

---

## Recommended ownership

| role | owner | scope |
|---|---|---|
| **Canonical deployment owner** | **`session_01UzBdAD7nfP8SBPk1bB3uDs`** | sole authority over `feature/l5-transfer-backtest`, `claude/august-l5-forensic-replay-788cml`, and every Railway deploy. It holds the live context and has deployed 11 times today |
| **Canonical analysis owner** | **`analysis/l5/clone/PAIR_POLICY_SENSITIVITY.md` + `src/pmus_l5_pair_policy_study.py`** | the single canonical PMUS execution study. It has the queue model, the policy grid, the in/out-of-sample split and now inferred settlement. The cross-shadow P3/P6 should be **retired as a results source** and kept only as the instrumentation audit it actually is |

My own work stays where it is and is not merged into either: branch
`research/l5-independent-forensic-package-grok-perplexity` (scripts + docs)
and orphan branch `l5-blind-package` (the external research package, 125
files). Neither touches `src/` or any deploy path.

## Next research deliverable (for whoever owns analysis)

**Compare L5's observed order-placement and matching behaviour against
PMUS's defensibly executable fills, including all unmatched residual P&L.**

Required for it to be worth anything:

1. **One accounting definition for both venues.** Pair cost FIFO on actual
   lots; residual attributed **pro-rata** (order-free) — FIFO and LIFO move
   the per-mode magnitudes 3–6× and only pro-rata is convention-free. My
   `E1_attribution_audit.py` reconciles the identity
   `window P&L == locked matched + residual P&L` to **0.0 error** and is
   reusable.
2. **Every unmatched residual counted**, including the undecidable ones —
   reported as a separate count, never dropped, since dropping biases toward
   whichever windows happen to converge inside the tape.
3. **L5's side held fixed at the measured values:** 81% maker / 19% taker
   (11,810 observed takers, 49,929 inferred makers, 10,811 unclassifiable);
   maker mean price 0.468 at median 441 s to settlement; taker 0.504 at
   144 s; maker residual win rate **12.4–13.4%**, taker **50.1–51.2%**.
4. **Stated asymmetry, not papered over:** L5's figures are *observed fills*
   on a venue we cannot trade; PMUS's are *simulated queue-cleared fills* on
   a venue we can. They are not the same kind of evidence, and the
   comparison must say so on every row.

My reusable scripts, none of which touch `src/` or any service:
`E1_attribution_audit.py`, `E2_inventory_control.py` (entry-side controls,
no lookahead), `A1_maker_taker.py` (feed verification + maker/taker
labelling), `C1_settlement_and_markout.py` (equivalent-definition markouts),
`92_passive_vs_aggressive.py` (fill vs prevailing book).
