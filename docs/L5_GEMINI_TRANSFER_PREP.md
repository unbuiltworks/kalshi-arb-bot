# L5 → Gemini transfer prep (OWNER 5554263833)

Parallel lane. PMUS recovery/ramp is untouched and stays highest priority.
Nothing in this lane places an order, touches the Gemini AWS supervisor,
or creates a Railway service. Every P&L figure below is THEORETICAL.

## 1. Transfer-gap matrix

Canonical fields of the L5 state/action interface vs the two venues.
Machine-readable copy: `analysis/l5/transfer_gap_matrix.json`.

| field | L5 canonical | PMUS | Gemini | PMUS | Gemini |
|---|---|---|---|---|---|
| outcome labels | Up/Down | YES/NO on a team; no natural Up | single UP contract, DOWN = 1−UP | CONVENTION | MATCH |
| price / tick | 0..1, fine grid | per-market tick (0.005 seen) | 0.01 grid, band 0.20..0.80 | MATCH | MISMATCH (coarser; floors the bound by ≤1c) |
| quantity / size unit | shares, p50 ≈12 ($4.2) | penny contracts, unit = first fill | whole $1-payout contracts; min size not in retained evidence | CONVENTION | GAP (a q=1 unit costs $0.20–0.80; cannot fit the $0.05 PMUS cap) |
| time to expiry | fixed 5 min; 27% of actions <5m TTE | no clock → always `tte>10m` | fixed expiry stamped in the ticker | MISMATCH | MATCH (Gemini is closer to L5 than PMUS here) |
| inventory / matched / residual / pair cost | exact shares, signed residual | same arithmetic, behavioral units, cap 3 | same (`L5Inventory` shared) | MATCH | MATCH (hold duration ≤5 min vs hours) |
| book / spread / depth | CLOB both sides | bbo, bid qty only; spread 0.005–0.05 | both sides with sizes; entry spread p50 0.11 | MISMATCH | MISMATCH (≈5× wider) |
| flow / prints | print stream with side | sharesTraded delta + WS tape | /v1/trades with side; two-sided 49% of windows (MLB 82.5%) | MATCH | MATCH |
| maker / taker | maker joins, taker share ↑ near expiry | post-only; taker disabled | maker-only vendored; taker fee not vendored | MATCH | GAP |
| fees / rebates / rewards | no maker fee | taker 0.06p(1−p); rebate never counted | maker fee ceil-cent 0.0175p(1−p) COUNTED; rebates never counted | MATCH | MISMATCH (bound 2c/pair tighter) |
| settlement | resolution | POSITION_RESOLUTION → `afterPosition.realized` | 60s RFR at expiry; replay uses a mid proxy | MATCH | MISMATCH (replay is theoretical) |
| provenance | canonical sequence | probe log + recorder tape | collector bbo tape + public trades | MATCH | MATCH |
| open-order authority / deadman | n/a | `/v1/orders/open` fail-closed; cancel-all deadman | must be its own (active orders + cancel) | MATCH | GAP (build per venue) |
| concurrency / capital | many windows | one-market lock; $0.05/market; 3 markets | own lock; allocator cap 0 (DISARMED) | MATCH | CONVENTION |

Semantic mismatches that the model never saw and that no adapter can hide:
size unit (money), spread width, maker fee, and settlement-by-proxy in
replay. TTE is the one axis where Gemini is more faithful than PMUS.

## 2. Reuse vs new

| piece | status | where |
|---|---|---|
| v2 policy (`proposal`, `v2_dist`, `relation_mass`) | REUSED unchanged | `src/pmus_l5_persistence_v2.py` |
| shadow fields (seeded draw, L5 size, aggressiveness) | REUSED unchanged | `src/pmus_l5_fidelity_live.py` |
| state key / buckets | REUSED unchanged | `src/polymarket_l5/action_model.py` |
| Gemini mechanics (fees, complement, fills, expiry, tape loaders, outcome proxy) | REUSED (vendored @80f40c86) | `src/polymarket_l5/transfer_backtest.py` |
| PMUS bounds (`complement_bound`, `entry_ok`, taker fee) | REUSED | `src/pmus_q1_executor.py` |
| normalized book / inventory / decision record / adapters | NEW | `src/l5_venue_adapter.py` |
| Gemini replay + realtime shadow (zero orders) | NEW | `src/gemini_l5_shadow_v2.py` → `analysis/l5/gemini_l5_shadow_v2.json` |
| shared capital allocator (per-venue + total caps, no double reservation) | NEW, not wired | `src/l5_capital_allocator.py` |
| Gemini execution / reconcile / deadman / ledger / health gate | NOT BUILT (gap; needs its own authority) | — |
| DISARMED Gemini probe config | NEW, config only | `deploy/gemini_l5_probe/DISARMED.md` |

The live PMUS session (`pmus_spring_l5_session.py`) was not modified; its
`Inventory` and decision-record shape are mirrored in the adapter module so
the two venues emit identical records.

## 3. Shadow / replay under the PMUS shell

Retained tape: 400 BTC-5m symbols with quotes and prints, 4 days
(2026-08-30..09-02), 118 entry-eligible windows. Shell identical to the
live PMUS engine: q=1 behavioral unit, maker join at the touch, post-only,
rest ≤30s and cancelled when the touch moves, residual cap 3, off
distribution → HOLD, complement ≤0.98 pair and ≥1c after the venue's
known fees (Gemini maker fee counted), HOLD to settlement, 6 decisions
per session. Strict = a print through our price; optimistic = at price
(upper bound). Modal = what the live engine executes; stochastic = the
seeded draw the live engine only logs.

| metric | modal:strict | modal:optimistic | stochastic:strict | stochastic:optimistic |
|---|---|---|---|---|
| campaigns (entry-eligible windows, 4 days) | 118 | 118 | 118 | 118 |
| decisions | 560 | 563 | 560 | 563 |
| acted by relation | {'extend': 11, 'hold': 57, 'open': 492} | {'extend': 53, 'hold': 51, 'open': 459} | {'complete': 8, 'extend': 4, 'hold': 56, 'open': 492} | {'complete': 18, 'extend': 28, 'hold': 58, 'open': 459} |
| refusals | {'entry_band_left': 55, 'residual_cap': 2} | {'entry_band_left': 49, 'residual_cap': 2} | {'entry_band_left': 54, 'off_distribution_hold': 2} | {'entry_band_left': 54, 'off_distribution_hold': 4} |
| orders submitted (all maker, q=1) | 503 | 512 | 504 | 505 |
| first-leg fills | 8 | 27 | 6 | 23 |
| campaign first-fill rate % | 5.1 | 19.5 | 5.1 | 19.5 |
| touch-moved cancels | 446 | 437 | 442 | 418 |
| complement orders / fills | 0 / 0 | 0 / 0 | 8 / 1 | 18 / 2 |
| two-sided (matched) campaigns | 0 | 0 | 1 | 2 |
| one-sided holds | 6 | 23 | 5 | 21 |
| median pair cost after fees | None | None | 0.99 | 0.99 |
| pair P&L theoretical | 0 | 0 | 0.01 | 0.02 |
| hold P&L theoretical (mid proxy) | -0.67 | 2.65 | -0.38 | 2.93 |
| holds won / lost / ambiguous | 0 / 2 / 4 | 8 / 6 / 9 | 0 / 1 / 4 | 7 / 5 / 9 |
| net theoretical | -0.67 | 2.65 | -0.37 | 2.95 |
| adverse selection 30s after first fill | 75.0% adverse, mean -0.11 (n=8) | 48.1% adverse, mean -0.0115 (n=27) | 66.7% adverse, mean -0.0917 (n=6) | 39.1% adverse, mean 0.0087 (n=23) |
| first-fill rest s p50 | 5.55 | 3.2 | 10.65 | 5.7 |
| entry spread p50 | 0.11 | 0.11 | 0.11 | 0.11 |
| window flow two-sided % | 49.2 | 49.2 | 49.2 | 49.2 |
| capacity $/day matched at q=1 | 0.0 | 0.0 | 0.24 | 0.48 |
Sensitivity (let the shell keep deciding for the whole window instead of
6 decisions):

| track | decisions | first fills | matched | holds | net theoretical | adverse % |
|---|---|---|---|---|---|---|
| modal:strict:max_actions=30 | 1079 | 13 | 0 | 10 | -0.99 | 61.5 |
| stochastic:strict:max_actions=30 | 1063 | 11 | 2 | 8 | -0.3 | 54.5 |
Reading:

* **Action supply is there, fills are not.** 503 maker orders in 118
  windows, but 446 (89%) were cancelled because the Gemini touch moved
  within seconds, and only 8 (1.6%) filled strictly. The live PMUS
  cancel-on-touch-move rule is the binding constraint on Gemini; the
  vendored 180s-rest engine filled 30% of first legs on the same tape.
* **The modal policy never completes.** After a first fill the v2 modal
  relation is EXTEND (same behaviour seen live on PMUS), so modal tracks
  produced 0 matched pairs; the stochastic draw completed 1–2 pairs at
  0.99 after fees (+$0.01–0.02 theoretical).
* **Holds lose under strict fills.** Strict holds: 0 won / 2–3 lost,
  adverse selection 62–75% (mid moves −5 to −11c against the fill within
  30s). Optimistic looks positive (+$2.65) only because at-price fills
  are an upper bound; the frozen criteria read strict only.
* **Comparable PMUS/Polymarket sports (retained strict, MLB):** first-leg
  fill 51%, complement-given-fill 49%, two-sided flow 82.5%, pair cost
  0.99 — same engine class, an order of magnitude more fills.

## 4. Separation design (item 5)

Per venue, never shared: execution worker, one-market execution lock,
open-order authority and reconciliation (PMUS `/v1/orders/open`; Gemini
active orders), cancel-all deadman, ledger (submitted → filled → settled
→ realized from the venue's settlement rows only), venue health gate
(HTTP 5xx / stale book / no prints → that venue's gate closes).

Shared: the v2 policy + shadow fields, the normalized decision record,
the dashboard, and the capital allocator (`l5_capital_allocator.py`):
per-venue exposure cap, per-venue concurrency, per-venue and TOTAL
daily-loss stops, one reservation per (venue, market). A venue outage
closes only that venue's gate; the other keeps trading. Only the total
daily-loss stop freezes both, by design.

## 5. Simultaneous operation (item 7)

Two independent workers (PMUS probe as today; a Gemini worker that does
not exist yet) each run their own loop and ask the allocator before every
OPEN/EXTEND; the allocator's snapshot is the shared dashboard row set.
Default limits: PMUS R1 ($0.05 filled per market, 3 markets, $0.15 total,
−$1.00 day) and GEMINI cap 0 / 0 markets (DISARMED) until the owner
authorizes a Gemini probe with its own money cap.

## 6. Recommendation

**TRANSFER-UNPROVEN.** Under the exact PMUS shell the Gemini tape gives
0 modal matched pairs, a 5% strict first-fill rate, 62–75% adverse
selection on the fills that do happen, and negative strict net (−$0.37 to
−$0.99). Nothing here supports a live probe. It is not TRANSFER-REJECTED
because the constraint is the shell's rest/cancel discipline on a
fast-moving touch, not the state interface (TTE transfers better than on
PMUS), and because the tape is 4 days / 118 windows. What would move it:
a realtime Gemini shadow run (`--live`) over ≥3 days showing strict
two-sided fills ≥1/day at ≤0.99, and an owner decision on a Gemini-only
money cap and rest rule. Until then: shadow only, DISARMED.


## 8. Execution shells on the retained tape (OWNER 5554553793 B, strict fills only)

Same policy, same caps; only the rest/cancel discipline varies. Retained
tape, 118 entry-eligible windows, 4 days.

| shell : policy | orders | first fills | campaign fill % | requotes | complement fills | matched | holds | net strict (theoretical) | adverse 30s % |
|---|---|---|---|---|---|---|---|---|---|
| touch_cancel_30s : modal | 503 | 8 | 5.1 | 0 | 0 | 0 | 6 | -0.67 | 75.0 |
| touch_cancel_30s : stochastic | 504 | 6 | 5.1 | 0 | 1 | 1 | 5 | -0.37 | 66.7 |
| rest_60s : modal | 251 | 31 | 18.6 | 0 | 0 | 0 | 22 | -1.95 | 67.7 |
| rest_60s : stochastic | 270 | 27 | 18.6 | 0 | 3 | 3 | 20 | -1.81 | 66.7 |
| rest_120s : modal | 170 | 34 | 18.6 | 0 | 0 | 0 | 22 | -2.06 | 73.5 |
| rest_120s : stochastic | 186 | 28 | 18.6 | 0 | 8 | 8 | 15 | -1.64 | 75.0 |
| rest_180s : modal | 153 | 42 | 24.6 | 0 | 0 | 0 | 29 | -3.48 | 71.4 |
| rest_180s : stochastic | 173 | 37 | 24.6 | 0 | 11 | 11 | 19 | -4.21 | 73.0 |
| reprice_180s : modal | 141 | 22 | 11.0 | 859 | 0 | 0 | 13 | -1.69 | 45.5 |
| reprice_180s : stochastic | 141 | 17 | 11.0 | 847 | 4 | 4 | 10 | -0.98 | 47.1 |

Reading: a longer bounded rest does what the blocker analysis predicted
for FILLS (5% → 19–25% of campaigns get a first leg) but not for
ECONOMICS: every shell that rests longer collects more one-sided holds
that settle against it (adverse selection 66–75% at 30 s, strict net
−$1.6 to −$4.2 vs −$0.4/−$0.7 for the live shell). Re-joining the new
touch (`reprice_180s`) halves the adverse share (45–47%) at the cost of
~850 requotes per 118 windows and still nets −$1.0 to −$1.7. The modal
policy never completes a pair under any shell; the stochastic track
completes 1–11. On this tape the blocker is not only timing: fills that
arrive on a wide, fast Gemini touch are informed. The durable realtime
service (`src/gemini_l5_shadow_service.py`, Railway `gemini-l5-shadow`,
read-only) collects multi-day evidence for the same table.

## 9. Live shadow service and pre-registered gate (OWNER 5554728503)

Railway service `gemini-l5-shadow` (own volume `/state/gemini-shadow`, no
variables, no credentials, zero orders) runs `src/gemini_l5_shadow_service.py`:
public book + prints for the current and next BTC-5m window every 3 s; on
expiry the window tape is replayed through the same seeded v2 decisions
under all five shells × modal/stochastic (paired by window), strict fills
only; at-price/queue-unknown fills are recorded under a separate
`optimistic_unknown` label and never enter a metric. Durable evidence:
`tape.jsonl` (raw book+prints per window), `records.jsonl` (every
hypothetical order/fill/decision), `summary.json`, `report.json`. Rolling
reports every hour over 6h / 24h / cumulative: windows, actions, joins,
strict first-leg fills, complement-given-fill, matched pairs ≤0.99,
pair-cost quartiles, residual duration/outcome, adverse selection at
5/15/30/60 s, fees, capital-turn estimate, and each arm's delta vs the live
shell on identical windows.

Pre-registered gate `analysis/l5/gemini_transfer_gate.json` (frozen before
the service produced a record; sha in every verdict): primary arm
`reprice_180s:modal`; PASS needs ≥300 windows over ≥5 days, ≥15 strict
two-sided completions on ≥4 days, complement-given-fill ≥35%, ≥80% of
matched pairs ≤0.99 after fees, net after known fees ≥0 with ≥3
non-negative days, adverse selection at 30 s ≤50%, one-sided hold rate
≤25%. REJECT after 300 windows if net ≤ −$1.00 or fewer than 5
completions. Exploratory arms cannot pass on the sample that selected
them. Thresholds are read from the file and never tuned in code.

## 10. Ex-ante regime study (OWNER 5555118026) — no regime found

`src/gemini_l5_regime_study.py`, candidates frozen in
`analysis/l5/gemini_regime_candidates.json` (sha df99f3aadb13b775) before
any validation number was read; split by day (train 260830–260831 = 91
windows, validation 260901–260902 = 27 windows); features strictly at or
before the entry quote (spread, min depth, |mid move| 60/120 s, two-sided
prints in the trailing 120 s, TTE); selection = best strict net on train
with ≥10 admitted windows, then reported on validation. Result
(`analysis/l5/gemini_regime_study.json`): for every shell the
train-selected rule does NOT improve validation strict net above the
unconditional baseline, and no rule reaches a non-negative validation net
with fills (rest_120s: selected `two_sided_flow_120s`, validation 8
windows / 3 fills / −$0.89; reprice_180s: selected `late_entry_tte_le_240`,
validation 9 windows / 0 fills). Descriptive segments (train+validation,
not a rule): on rest_120s the fills after a ≥6c pre-entry move are 92%
adverse (net −$2.20 over 62 windows) versus 50% after a 3–6c move, and
one-sided trailing flow fills are 82% adverse (−$2.75) versus 43% after
two-sided flow (+$0.69, 26 windows); on reprice_180s the wide-spread
(≥12c) fills are only 11% adverse but still net negative. These are
hypotheses for a NEW pre-registration, not thresholds; the validation set
is 27 windows and underpowered. The live shadow service keeps collecting
the same features so the study can be re-run on out-of-sample days.

## 11. Why modal never completes while the stochastic draw does (OWNER 5555437740)

`src/gemini_l5_modal_decomposition.py` → `analysis/l5/gemini_modal_vs_stochastic.json`
(retained tape, strict, shells reprice_180s and rest_120s, READ-ONLY).

* In all 110 post-first-fill decisions (both shells, modal track) COMPLETE
  is never the argmax: P(complete) p50 0.39–0.42 vs P(extend) p50 0.51–0.66.
  Off-distribution decisions: 0 (rest_120s) / 1 (reprice). The states are
  in-distribution; argmax over a 40/60 split simply never selects the
  minority action, and the draw selects it ~40% of the time (sampled
  COMPLETE 31/71 and 15/39 decisions) — which is exactly the historical
  L5 rate in these imbalance states (base-state P(complete) 0.46–0.54 by
  TTE bucket, n 89–1101).
* State/adapter component: every lookup keys on `last_size=q<5` (our q=1
  unit) while L5's own size in these states was mostly q5-15/q15-40; the
  exact full-state keys are thin (n 2–16) so the mass is mostly the shrunk
  base state. Across all visited base states and every size bucket,
  COMPLETE is argmax in exactly one thin state
  (`p75-90c|tte30-120s|imb1-10|open|q5-15`). So the zero-completion result
  is not a size-bucket artifact either.
* Execution component: after the first fill the modal EXTEND was itself
  refused in 49/71 (rest_120s) and 18/39 (reprice) decisions
  (`entry_band_left`: the extend join would leave 0.20–0.80 or cross), and
  the residual cap held 10/9 more; so modal spends most post-fill
  decisions HOLDing. When the draw chose COMPLETE, a fee-safe complement
  price existed 18/21 (rest_120s) and 6/6 (reprice) times (`pair_target`
  regime), i.e. completion is executable at the bound; it is the strict
  fill that is rare.

Verdict: zero modal completion is the expected arithmetic of executing a
genuinely stochastic policy by argmax, not a state or adapter mismatch.
This is the same mechanism observed live on PMUS (HOLD 100% at the cap
while L5 completes 43–48%). No live policy is changed by this analysis;
any change (e.g. the prebuilt cap-fallback, or sampling instead of
argmax) stays an owner decision with OOS evidence.

The two-sided-flow observation is pre-registered as a separate hypothesis
(`analysis/l5/gemini_hypothesis_two_sided_flow.json`, frozen 23:17:44Z,
arm rest_120s:modal, applies only to windows entered after the freeze,
evaluated by the service in every rolling report under `hypotheses`,
never mixed with the transfer-gate sample).

## 12. Predeclared stochastic OOS arm + distributional scoring (OWNER 5555481658)

`analysis/l5/gemini_research_arm_stochastic.json` (frozen 2026-09-05T23:43:19Z,
sha 40e28081226917a9): arm `reprice_180s:stochastic`; relation drawn by
inverse CDF from the same v2 conditional masses the modal arm reads, with
a persisted deterministic seed (sha256 of symbol | decision index | model
sha bbb55bf90638d2bd), identical conditioning (band | TTE | behavioral
imbalance, last_rel, last_size q<5, shared side convention, q=1,
complement ≤0.98 / ≥1c after Gemini fees), identical safety filters, no
cap-fallback. SUPPORTED needs ≥300 windows / ≥5 days, ≥15 strict
two-sided completions on ≥4 days, ≥200 scored decisions with sampled
relation-frequency L1 distance to L5 ≤0.10 and post-fill COMPLETE
frequency within ±10 pp of the historical conditional, net after known
fees ≥0 with ≥3 non-negative days, adverse at 30 s ≤50%, one-sided hold
rate ≤30%, off-distribution rate ≤5%. FAIL after 300 windows on net ≤ −$1,
<5 completions, or L1 >0.20. Evaluated only on windows entered after the
freeze; separate from the primary gate and the flow hypothesis.

Distributional action-by-action scoring (`gemini_l5_shadow_v2.action_scoring`)
runs for every arm on untouched windows in each rolling report: expected
relation frequencies under L5 (mean of the model masses over the arm's
own decisions) vs SAMPLED (policy output) and ACTED (after the safety
shell; HOLD reported with refusal reasons), L1 distances, post-fill
COMPLETE/EXTEND frequency gaps in pp, calibration by P(complete) bin, and
the off-distribution rate. Behavior and executable economics must both
replicate before anything advances.
