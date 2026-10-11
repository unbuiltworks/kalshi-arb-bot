# Execution-lane state — 2026-09-16T01:3xZ

Polymarket.com is the **observation/reference** venue only. The two destination
execution lanes are Polymarket US sports and Gemini. Nothing below proposes
trading Polymarket.com.

`ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 = FAIL` stands unmodified. It falsifies that
run-cap implementation; it is not a venue verdict on either lane.

Repo `8e7e36bf` on `claude/l5-execution-instrumentation-io6nzi`.
Project `50fb7357…`, env `f3642d27…`. 35 services, 1 crashed (`pmus-ws-book-diagnostic`, a 10-day-old diagnostic).

---

## LANE A — POLYMARKET US SPORTS

| field | state |
|---|---|
| **COLLECTING** | **YES** |
| **SHADOWING** | **YES** |
| **EXECUTION_PATH_EXISTS** | **YES — deployed** |
| **CURRENT_POLICY** | canonical L5 v2 sequential, `model_sha bbb55bf90638d2bd` |
| **STRICT_EVIDENCED_FILLS** | 3 strict completions, **1 market**, all pair_cost 0.9800 |
| **SETTLEMENT_ACCOUNTING_READY** | **YES — venue-confirmed on real money** |
| **READY** | **NOT_READY** |

**Collecting.** `pmus-ws-evidence-recorder` (SUCCESS, `/state/pmus-ws`): authenticated
socket `conn=connected auth=ok`, 160 subscribed of a 4,536 universe, discovery cycle
2,764 (hot 96 / warm 48 / cold 16), 986 fresh / 591 stale, 28.09M messages,
5,000 market-data frames/60s, 508 trades/300s, `stream_alarm=false`, `pool_alarm=false`.
`pmus-sports-forward-collector` (SUCCESS, `/state/pmus-sports`) tracks 4,650 markets,
695 with 15m data. `pmus-shadow-breadth` adds inferred-flow breadth (explicitly
labelled "direction inferred… never evidence" — not strict).

**Shadowing.** `PMUS_SPORTS_L5_DECISION` every ~90s over 8 candidates, emitting
OPEN/EXTEND/COMPLETE/HOLD with inventory, `residual_cap:3>=3`, `v2_off_distribution`,
`book_stale:NNNs`. `order_entry: false`, `realized_pnl: null`, DISARMED.

**Execution path.** The shadow names it itself:
`execution_path_if_approved = src.pmus_spring_l5_probe_entry` → `pmus_spring_l5_session`.
The deployed CMD of `pmus-spring-l5-probe` (artifact `6adcc4c4`) is
**`src.pmus_l5_continuous_session`** — the continuous select → session → reselect
accumulation loop, not the one-shot spring gate. It is running now, in `shutdown_idle`.

**Envelope (R1 rung, active).** `per_market_cap_usd $0.05` cumulative OPEN/EXTEND ·
`residual_units 3` · `markets_per_campaign 3` · `campaigns_per_day 1` ·
`max_actions_per_session 6` · `concurrency 1` · maker/post-only · one outstanding
order · ≤0.98 completion with ≥1c after known fees · residual held to settlement ·
restart NEVER.

**Strict evidence, now.** 84 `spring_watch` rows: `Q1_GATE_CLEARED` 3,
`SHADOW_CANDIDATE` 7, `NOT_READY` 74. `two_sided_trades` true in 10 of 84.
Exactly **one** market has any strict spring completion:

`atc-lmx-pue-tol-2026-09-04-tol` — 3 × `SPRING_MATCHED`, pair_cost **0.9800** each,
locked edge 0.0200, matched_fraction 0.75, completion latency p50 67.3s,
`spring_evidence_missing: []`.

**0 of 3 completions are ≤0.96.** Missing-evidence reasons across all rows:
`no_trades_in_window` 69, `no_evaluable_slots` 3.

**Settlement accounting.** Proven on real money: Ledger D **−$44.4898 across 34
venue-confirmed settled sports markets**, 12 `SETTLED_REALIZED_UNCONFIRMED` correctly
held out of the net, nested settlement extraction verified.

**LIVE_PROBE_BLOCKERS**
1. `PMUS_SPRING_SHUTDOWN` latch set — service HALTED. Owner action.
2. **Category bar not met on 0/84 rows** — needs ≥2 completions from ≥2 distinct
   same-category markets in 60m; best is 3 completions from **1** market.
3. **Pair cost 0.98, not ≤0.96.** CLAUDE.md keeps near-$1 tolerance LOCKED.
4. Dual-key arm unset (`PMUS_SPRING_LIVE` / `_ARM` / `_SESSION_ID` / `_MARKET_SLUG` / `_GATE_EVIDENCE`).
5. Cumulative Ledger D −$44.49. (The −$30 budget reads "permits" only because the
   daily realized counter rolled over at UTC midnight; the latch is what holds.)
6. **Naming trap:** `spring_le_096_completions` counts completions **≤0.98**, not
   ≤0.96 — source comment says the key name was "kept for consumer compatibility".
   Reading it as ≤0.96 evidence would arm on a discipline that is not met.
7. To reconcile before arming: the shadow proposes `q1_size: 1` at a $0.27 touch
   ($0.27 notional) against a `$0.05` per-market cumulative cap. One of the two is
   not what it appears; the cap is the authoritative bound.

**SMALLEST_SAFE_PROBE** — already specified and deployed, nothing to build:
one market, one outstanding maker/post-only order, q1, **≤$0.05 cumulative
OPEN/EXTEND notional per market**, ≤0.98 completion at ≥1c after fees, residual to
settlement, 6 actions, 900s deadline, stop 120s before settlement.
Worst case ≈ **$0.05 per market × 3 markets × 1 campaign/day = $0.15/day**.

---

## LANE B — GEMINI

| field | state |
|---|---|
| **COLLECTING** | **YES** (crypto 5m + 15m) · **NO** (sports) |
| **SHADOWING** | **YES — continuous-inventory model IS running** |
| **EXECUTION_PATH_EXISTS** | **YES, but on another branch and not deployed** |
| **CURRENT_POLICY** | canonical L5 v2, same `model_sha bbb55bf90638d2bd`, maker/post-only, taker disabled |
| **STRICT_EVIDENCED_FILLS** | see per-cohort table |
| **SETTLEMENT_ACCOUNTING_READY** | **NO — mid proxy, never venue-confirmed** |
| **READY** | **NOT_READY** |

**The continuous-inventory question, answered directly.** It IS running, and it is
not the old pair experiment. `gemini-l5-queue-shadow` (SUCCESS, branch
`feature/l5-gemini-queue-shadow`, since 2026-09-14) runs
`"canonical complement decisions + 64-action multi-fill inventory path"` —
`max_actions_per_segment 64`, `max_segments 16`, with `inventory_path`,
`residual_sign_flips`, `switches_per_window`, `runs_per_window`,
`terminal_residual_abs_p50` and `flat_boundary_replays`. That is the repeated
accumulation / oscillation model. Separately, `gemini-l5-shadow` still runs the OLD
10-shell campaign/pair experiment (2,902 campaigns since 09-12) — both exist; they
are not the same thing.

**Per-cohort evidence (744 windows, multi-fill inventory path):**

| cohort | windows | decisions | fills | complete conv. | extend conv. | pair cost p50 | net (theoretical) |
|---|---|---|---|---|---|---|---|
| crypto 5m | 558 | 2,048 | 55 | 19.2% (5/26) | 10.3% (7/68) | 0.99 | −7.26 |
| crypto 15m | 186 | 422 | 10 | **0.0% (0/3)** | 14.3% (2/14) | **null — no pair ever completed** | −1.22 |
| sports | **0 — not collected** | — | — | — | — | — | — |

`matched_fraction_p50 = 0.0` in both cohorts. 51 of 744 windows had any fill.

**The result that is NOT a venue failure.** The queue-aware inside-spread ladder
shows real fee-safe completion opportunity:

| candidate | opportunities | fee-safe | strict completion | completions | latency p50 |
|---|---|---|---|---|---|
| max_fee_safe_post_only | 354 | 354 | **27.7%** | 98 | 69.6s |
| touch | 377 | 308 | 27.9% | 86 | 76.6s |
| inside_50 | 330 | 317 | 27.1% | 86 | 74.4s |
| forced-complete-if-model-extended | 59 | 59 | **35.6%** | 21 | — |

98 strict completions at 27.7% is not a zero-pair venue. The old R/S/D and
cap5/8/13 zero-pair results were properties of those arms' quoting, not of Gemini.

**Execution path.** `src/gemini_l5_canary.py` exists on
`feature/l5-gemini-queue-shadow` (NOT on the current branch — a grep of the checked-out
tree misses it). `ORDER_PATH /v1/prediction-markets/order`, HMAC-signed, plus cancel
and active-orders endpoints. Envelope already chosen: **q == 1 only, buy-only,
maker-or-cancel, one market at a time, `max_residual_usd 2.00`,
`daily_realized_loss_limit_usd 2.00`, `min_edge_after_fees 0.01`, BTC 5m/15m only,
`ARM_TOKEN "YES_I_APPROVE_LIVE_Q1"`**. Disarmed by default; the module states it
must not be imported by the shadow collector path, and every running service reports
`orders_placed: 0`.

`l5_risk.shell.GEMINI_PROBE_SHELL` independently exists: stop −5.00, inventory 10,
market spend 5.00, concurrency 1, deadman 300s.

**LIVE_PROBE_BLOCKERS**
1. **Settlement accounting is a mid proxy** — `settled_realized_net_usd_per_window:
   null`, `settled_realized_net_per_capital: null`, label "settlement by mid proxy;
   THEORETICAL". No venue-confirmed Gemini settlement exists. Nothing could be
   labelled realized.
2. Canary lives on a branch no running service executes; not deployed.
3. `GEMINI_L5_CANARY_ARMED` not set to the arm token.
4. `require_terms_accepted` / `TERMS_STATUS_PATH` — prediction-markets terms
   acceptance for this account is unverified.
5. Edge is at the floor: `min_edge_after_fees 0.01` against observed
   `pair_cost_after_known_fees_p50 0.99` — i.e. ~1c, with nothing spare.
6. 15m has **never** completed a pair (0 of 3 complete orders).
7. Entry spread p50 is **0.25** and prints are sparse (10 / 1 / 0 per 5m window).

**SMALLEST_SAFE_PROBE** — also already specified: q=1 BTC **5m** (not 15m, which has
no completion evidence), buy-only maker-or-cancel, one market at a time, residual cap
$2.00, daily realized stop $2.00, min edge after fees $0.01.
Worst case ≈ **$4.00** ($2.00 stop + one $2.00-capped residual).

---

## Shortest existing path to a bounded live L5-adapted probe

**Lane A (PMUS sports).** It is the only lane where settlement accounting is already
venue-confirmed on real money, the execution path is already the deployed CMD, and
the envelope is 100× smaller ($0.15/day worst case vs $4.00). It needs no new code —
it needs *evidence* (a second distinct same-category completion) plus an owner
decision on the latch, the dual key, and whether 0.98 completions satisfy a
discipline written for ≤0.96.

**Lane B (Gemini)** needs real engineering before it could be bounded honestly:
venue-confirmed settlement does not exist, and without it no Gemini probe can produce
a realized number at all.
