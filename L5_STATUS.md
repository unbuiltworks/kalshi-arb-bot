# L5 Canonical Pipeline — Status (2026-09-02)

Subject: Polymarket trader `l5zn1bwom8etsk`
(`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`). Research only: every code
path here is public-data GET-only; nothing trades, authenticates for
orders, or touches live services.

## Current Live Transfer / Handoff (2026-09-02, for fresh sessions)

Exact state to re-establish from GitHub alone. Labels are strict:
HISTORICAL (source data), SHADOW/MODELED (never realized), LIVE
submitted/filled/settled, REALIZED (authoritative settlement + cashflow).

* **Probe branch**: `feature/gemini-l5-dynamic-q1-v1policy`, current head
  `f62697224278447233846f28eebda4d9a9fff3b4` (prior verified base
  `dda0c040761ac0665cbd19da3e6ad4d34a534e60`). Frozen v1 model
  `a846e6bc96fc8079` (Mar 27+ regime; ex-ante gate PASSED 2026-09-01,
  clustered log-prob CI [+0.021, +0.099]).
* **LIVE probes to date** (q=1, BTC-5m, operator-run in Docker from
  `dda0c040`):
  - #1: order accepted, no fill, $0. Account flat after.
  - #2: NO filled @0.50 (submitted→filled authoritative), v1 said EXTEND,
    q=1 cap → one-sided hold; YES settled (authoritative settlement);
    **REALIZED −$0.51**. Account flat after.
  - #3: order accepted, no fill, $0. Account flat after.
  - **Cumulative realized live trading: −$0.51.** Nothing else is
    realized; every other economic figure in this repo is HISTORICAL or
    MODELED.
* **Bounded-extension HISTORICAL result** (`extension_study.py`,
  `l5-canonical@496b62ab`): after "one-sided + next relation EXTEND",
  L5 later complements 74.8% of the time (median 60 s) — but with median
  interim imbalance ~$72 (p90 $281), far above any q=1 budget; modeled
  q=1 counterfactuals: hold P(loss) 44.2%, forced completion 33.7%.
* **Simplified next configuration at `f6269722`** (implemented, tested,
  DISARMED): BTC-5m only, q=1, one campaign/session,
  `FORCED_COMPLETION_RISK_OVERLAY` (`GEMINI_L5_FORCED_COMPLETION=1`):
  180 s first-leg maker rest, then immediately after the first
  authoritative fill post the fee-safe opposite-side complement
  preserving ≥1¢ completed-pair edge computed from the ACTUAL execution
  price, kept working maker-only until the TTE stop; no same-side
  extension exists; frozen-v1 decisions are still recomputed and logged
  every re-evaluation (`v1_pure_decision`) but never suppress the
  complement — an explicit transfer/risk overlay, NOT validated L5
  behavior. All shell safety semantics inherited (durable exact IDs,
  exec-price accounting, exact-inactive proof, residual fail-closed,
  `settlement_required`, `realized_pnl=None` until settlement).
  **692 tests pass** on the probe branch.
* **Arming**: operator-side only (dual-key envs + Docker on the
  credentialed host). **Nothing is currently armed**; this research
  environment holds no exchange credentials by design.

### Always-on architecture (added 2026-09-02, probe branch @ post-d0f19dd8)

The one-off probes are superseded by a durable supervisor on
`feature/gemini-l5-dynamic-q1-v1policy`:

* `src/gemini_l5_transfer_supervisor.py` — continuous loop, **SHADOW
  (mutation-free, public-data) by default**, `mode` stamped on every emit
  and ledger row. Shadow follows the exact live decision path (selector →
  v1 flat gate → TRANSFER_INITIALIZER → 180 s rest → forced complement at
  the fee-safe bound) with THEORETICAL fills detected from the public
  trade tape. LIVE mode delegates single campaigns to the proven dynamic
  owner (operator dual-key arming unchanged; the supervisor never arms).
  Hard bounds: BTC-05M, q=1, one campaign at a time, residual cap 1,
  campaigns/day cap (default 6), **daily realized-loss stop $1**
  (authoritative settlement only), persistent HALT on any
  unresolved/ambiguous settlement, owner exception, or loss stop —
  survives restarts, operator-cleared only. Durable state root:
  state.json / campaigns.jsonl / heartbeat.json / daily summaries.
  One-command report: `... gemini_l5_transfer_supervisor status`.
* `src/gemini_l5_settlement_audit.py` — settlement authority
  (`positions/settled` rows only), now with **local exact-identity
  filtering** (`contractMetadata.eventTicker` precedence) after the
  live-observed bug where the endpoint returned mixed events; ambiguous
  rows fail closed as `AMBIGUOUS_SETTLEMENT_IDENTITY`.
  `available_usd` / `positions=[]` are never settlement proof.
* Service staging (NOT deployed): `deploy/l5_supervisor/Dockerfile` +
  `l5-supervisor.service` (systemd template; arming envs deliberately
  absent).

**Probe #4 (`v1probe-20260902-04`, BTC05M2609021210) current state**:
LIVE submitted → filled 1 YES + 1 NO (`MATCHED_V1_COMPLETION`, maker
turnover 0.97, logged fees 0.02, residual 0) → **settlement NOT yet
source-confirmed**: the +$0.03 balance move and `positions=[]` are NOT
proof; realization awaits exact-identity `positions/settled` rows via the
corrected auditor. Cumulative REALIZED live trading remains **−$0.51**
(probe #2) until then.

## Consolidation lineage (Phase 1 audit result)

| Source branch | What was taken | Where it lives now |
|---|---|---|
| this lineage (`claude/polymarket-l5-historical-analysis-2ih9ui`) | full-history windowed backfill (706k-row offset-cap workaround), cashflow ledger + positions reconciliation, bands/eras/evolution studies | `src/polymarket_l5/{backfill,ledger,bands,eras,evolution}.py` |
| `analysis/polymarket-l5-disciplined-clone` | no-hindsight fill-path replay v2 + P&L reconciliation + tests | `disciplined_clone{,_v2}.py`, `reconcile_disciplined_clone.py` |
| `feature/l5-market-redeem-audit` | proof-gated per-market redemption reconciler | `redeem_audit.py` |
| `feature/l5-realtime-shadow-mirror` | realtime read-only collector + tests | `realtime_mirror_base.py` (base), `realtime_mirror.py` (canonical wrapper) |
| `feature/l5-polymarket-replay-v1` | account-level lifecycle/rebate-timing collector | `lifecycle.py` |
| `feature/l5-polymarket-replay-main-v1`, `src/l5/` screenshot lineage | superseded (data path obsoleted by API backfill); left untouched on their branches | — |
| Gemini L5 branches | audited for inventory/risk/deadman patterns only; none reproduced the strategy (confirmed by our C-series and L-series shadows) | — |

Nothing was deleted or overwritten on any old branch.

## Pipeline commands

```
python3 -m src.polymarket_l5.backfill --positions   # network: full-history harvest
python3 -m src.polymarket_l5.cli build              # offline: raw -> CSVs
python3 -m src.polymarket_l5.canonical_sequence     # offline: canonical per-trade dataset
python3 -m src.polymarket_l5.ledger                 # offline: cashflow ledger + reconciliation
python3 -m src.polymarket_l5.behavior               # offline: 12-question behavioral report
python3 -m src.polymarket_l5.reconcile_disciplined_clone   # offline: no-hindsight replay recon
python3 -m src.polymarket_l5.action_model           # offline: state->next-action model
python3 -m src.polymarket_l5.early_era              # offline: Mar->May cohorts, eras, refinement
python3 -m src.polymarket_l5.microstructure         # offline: UI-grounded fill-level tests
python3 -m src.polymarket_l5.venue_control          # network: Mar 27 venue-liquidity control
python3 -m src.polymarket_l5.ops collect|shadow|regression|status   # 24/7 ops layer
python3 -m src.polymarket_l5.realtime_mirror --minutes 30  # network: READ-ONLY live mirror
python3 -m src.polymarket_l5.realtime_mirror --compare <stream.jsonl>
python3 -m pytest tests/test_l5_canonical.py tests/test_polymarket_l5_disciplined_clone.py tests/test_l5_realtime_mirror.py
```

Canonical dataset: `analysis/l5/l5_canonical_sequence.csv.gz` (schema v1,
per-fill: window, asset, 5m/15m, ts, seconds-to-settlement, Up/Down, price,
qty, maker/taker-where-determinable, cumulative inventories, matched qty,
pair cost, signed residual, settlement outcome, provenance). Deterministic:
same inputs → identical `content_sha256_16` (tested).

## Coverage — read this before citing any number

Two datasets with DIFFERENT coverage:

* **Ledger** (`l5_ledger.json`): FULL history, 2026-03-19 → 2026-09-01,
  742,964 trades / 16,501 windows / $7.42M turnover, reconciled per-market
  against Polymarket's own closed-position rows (median abs diff $0.49,
  74.8% within $1).
* **Canonical sequence / behavior report**: the 2,216 windows where
  full-payload activity rows exist (Mar 19–Apr 25 + Aug 28–Sep 1). The
  middle months live only in the slim gap cache (fields sufficient for
  cashflow, not for per-fill sequencing). Extending the sequence to the
  middle is a listed unknown, not a silent hole.

## Established facts (source-accounted cashflows)

* Grand total: **+$81,895** = net trading **+$13,686** + incentives
  **+$68,208** (maker rebates $33.9k, rewards $27.0k, taker rebates $7.3k).
  Trading edge per dollar: **0.184¢/$, 95% CI [−0.08, +0.55]** —
  indistinguishable from zero. The business is paid liquidity provision.
* Decomposition: pair-locked **+$155,076** vs directional residual
  **−$141,390**; sells over the whole history total **$2.18** (he never
  unwinds — exits are settlement redemptions only, 15,243 REDEEM rows).
* No SPLIT/MERGE/CONVERSION anywhere in his history.
* **Maker-dominant**: within takerOnly-feed coverage, 55,647 maker vs
  11,068 taker fills (83/17); 5,269 fills outside coverage are "unknown",
  never assumed maker.
* Price-band study: no band except >90¢ has positive directional edge; the
  ≤2¢ tail wins exactly as often as its price implies (fair, not clever);
  78–89% of every band's shares end up matched into pairs.

## Behavioral reconstruction (canonical-sequence windows; see l5_behavior.json)

1. **Both sides bought: 72.7% of windows** (96% on BTC-15m, ~42% on ETH).
2. **Pair cost**: median 0.970, p10 0.781, p90 1.244 — **38.8% of pairs
   cost >$1** (deliberate guaranteed-loss pairs; the subsidy pays for them).
3. **Residual**: 99% of windows end unbalanced (median 56 shares); the
   residual lands on the winning side 49.9% of the time — a coin flip,
   directly confirming no directional skill; 39% of residual windows he is
   still trading inside the final 60s.
4. **Imbalance evolution**: spend imbalance share starts ~0.71 (first
   fills are one-sided by construction) and declines monotonically to
   ~0.28 by window end — he pairs down continuously, never fully.
5. **Size vs price**: median fill size RISES with price (9–15 sh below
   50¢ → 20–25 sh at 80–100¢) plus a bump at ≤10¢ — sizing follows
   confidence/favorites, with a cheap-completion tail.
6. **After price crosses 55–80%**: next fill is the SAME side ~60% of the
   time at every threshold — mild momentum-following, not fading.
7. **After a sharp move (≥10¢)**: chase winner 30%, average down 31%,
   cheap complement 22%, recovering complement 17% — he does BOTH sides of
   the ChatGPT hypothesis, near-uniformly; no single dominant reflex.
8. **Seconds-to-expiry regime change is the sharpest pattern found**:
   taker share rises 2.8% (>10min) → 27% (2–5min) → 48% (<30s). He rests
   maker early and pays taker to adjust late.
9. **Max inventory imbalance**: median $54 per window, p90 $458, p99
   $1,289, absolute max **$2,370** — the residual cap he tolerates.
10. Maker/taker: 83/17 (fact above).
11. P&L attribution: facts above (pairs +$155k / residual −$141k /
    rebates+rewards +$68.2k).
12. **BTC vs ETH is structural**: BTC-15m is his flagship (96% both-sides,
    $358 median max imbalance); ETH windows complete both sides <45% —
    ETH behavior is closer to one-sided flow capture.

## Inference (plausible, NOT established)

* WHY he tolerates >-$1 pairs and 50/50 residuals: consistent with reward
  farming (two-sided uptime), but intent is unobservable.
* Queue position, resting quote shapes, and cancel behavior: invisible in
  public prints; the "ladder" is inferred from fill distributions.
* Whether the incentive stream will persist at ~1.4¢/$ (venue-controlled).

## Realtime mirror (Phase 2E/F) — validated live

3-minute validation run (2026-09-01 13:36 UTC) captured **186 L5 fills
across 5 windows** into the canonical schema and rendered the live-vs-
historical comparison (both-sides %, pair cost, residual). Stream files:
`data/l5/mirror/mirror_*.jsonl`.

## Action-by-action comparison foundation (`action_model.py`)

The direct machinery for "at this exact market state, what did L5 do next —
and what would our clone have done?":

* **State** = last-fill price band × time-to-settlement bucket × residual
  imbalance bucket (coarse on purpose; sample size beats resolution).
* **Action** = relation to inventory (`open` / `complete` the deficit side /
  `extend` the surplus side / `sell`) × size bucket.
* Built from the canonical sequence: **69,768 decisions over 2,216 windows
  → 136 visited states** (`analysis/l5/l5_action_model.json`). A state L5
  never visited returns `None` — itself the off-distribution alarm for a
  clone.
* `compare_action(model, state, action)` scores one live/replayed action
  (probability under L5, rank, modal action); `compare_policy(model,
  policy)` scores any `policy(state)->action` hook by expected
  per-decision agreement, exact and at relation level. Tested with a
  perfect-mimic (agreement 1.0), a constant policy (0.0), and an abstainer.
* Headline read of the empirical table: modal actions are `complete` vs
  `extend` at 15–20% probability each — L5's next action is genuinely
  stochastic at this granularity; no single deterministic rule reproduces
  him, which is why comparison is distributional, not exact-match.

## Market-agnostic separation (`adapters.py`)

Venue specifics (slug regex, outcome label conventions, field names) are
isolated behind a `VenueAdapter` protocol (`parse_window`,
`normalize_fill` → frozen `WindowInfo`/`NormalizedFill` dataclasses).
`PolymarketUpDownAdapter` is the only implementation; the realtime mirror
consumes the adapter, so policy/analysis code never sees venue fields.
Porting the pipeline to another binary-window venue (e.g. Kalshi KXBTC15M)
means one new adapter, not policy changes.

## Launch and refinement chronology (`early_era.py`, 2026-09-01)

Three-era comparison confirms a ~48h behavioral regime change (FACT-level
frequencies; "automation" stays inference):

| | Mar 19–25 | Mar 26 | Mar 27 → Apr 25 |
|---|---|---|---|
| windows | 7 | 8 | 398 |
| median $/window | $1.00 | $26.33 | $227.43 |
| both-sides % | 0% | 50% | 96.2% |
| fills/window (med) | 1 | 12 | 21 |
| matched qty % | 0% | 68.5% | 90.4% |

* Regime similarity (total variation on action relations): Mar 27–Apr 4 vs
  Aug 28–Sep 1 = **0.05** (near-identical) vs Mar 19–26 = 0.12 on only 122
  decisions. **Mar 27+ is the correct primary training cohort**; state-level
  TV vs Aug (0.66) is dominated by the market-mix shift (Mar–May is 100%
  BTC-15m; 5m windows come later).
* **The Mar 27 launch was not incentive-driven**: first credited incentive
  row in the source stream is ~Apr 20; Mar 27–Apr 19 ran on trading economics
  alone (mostly negative). The volume ramp (13.8 → 35+ windows/day, notional
  ×3–8) begins exactly the week incentives first appear (Apr 20–26).
* Scaling decomposition (Mar 27–Apr 10 vs May 11–31): turnover ×37 =
  windows/day ×2.6 × notional ×8.1; matched 82→91%; pair cost ~unchanged;
  trading ¢/$ −0.90 → +0.87; incentives 0 → 0.24¢/$. Same policy, larger
  size, better completion, plus incentives — not a second regime change.
* Refinement (Mar 30–Apr 2 vs Apr 6–10): opposite-leg gap median 38s → 116s,
  final pair cost 0.9992 → 0.9230, residual tolerance up (0.106 → 0.197 of
  qty), notional DOWN ($95 → $62), size unchanged, first-leg price/TTE
  unchanged. Consistent with the "became more patient about the second leg"
  hypothesis; time-of-day shifted (17–22 UTC → 13–20 UTC), which is a
  confound we flag rather than resolve. Maker/taker is UNKNOWN for Mar–May
  (takerOnly coverage is Aug 28–31 only).
* **Venue-liquidity control** (`venue_control.py`, all-participant tape +
  CLOB rewards config, 13 pre / 24 post markets): pre-breakpoint BTC-15m
  windows already carried median **3,780 trades / $63.9k volume / ~800
  wallets**; Mar 27 windows show 3,828 / $52.6k / ~757 — no venue
  liquidity increase — and the rewards config is IDENTICAL on both sides
  (min_size 50, max_spread 4.5). L5's own share is ~0–0.2% of window
  volume pre and 0.1–0.6% post. **The Mar 27 breakpoint is trader-side
  activation, not a venue regime shift.** (Pre-era sample = windows L5
  chose to trade; that bias could only overstate pre-era liquidity, which
  strengthens the no-increase finding.)

## Fill-level microstructure (`microstructure.py`, UI-grounded)

* **Sizing**: share qty rises with price (8 sh at 15–30c → 24 sh at 85–95c)
  with a cheap-tail completion bump (<5c completion legs median 34.9 sh);
  notional rises steeply ($0.75 at <5c → $21–27 at 85c+). Neither
  constant-dollars nor constant-shares; matches the UI screenshots.
* **Completion tendency**: next buy lands on the deficit side ~49% —
  share-count deficit does NOT drive the next action; 57.1% of completion
  fills create a sub-$1 incremental pair.
* **Fragmentation**: 11,080 runs of consecutive same-side fills at an
  identical price (max run 24); verified fills inside runs are 96% maker
  (18,372 vs 712 taker) — the resting-order-fragment signature is real.
* **Pair-cost trajectory**: median ≥$1.00 at the window's first quartiles,
  descending to 0.9705 by the end — pairs start expensive and are worked
  DOWN below $1 late; the final pair cost understates the intra-window path.
* **Extreme bands**: <5c buys are 65% extension / 33% completion on a
  share basis — cheap fills mostly deepen the already-share-heavy cheap
  side (dollar-balanced books are share-skewed cheap); ">95c" buys are 60%
  completion. "Cheap = lottery ticket" remains unsupported; "cheap =
  always completion" is also too simple on shares.
* **Concurrency**: 73.9% of window entries happen while ≥1 other window is
  live (max 4 concurrent); concurrent entries carry ~4× less notional
  (median $67 vs $256 solo) — capital is split, not cloned, across windows.

## 24/7 READ-ONLY operations layer (`ops.py` + `.github/workflows/l5-ops.yml`)

Claude is NOT the always-on process: `ops collect|shadow|regression|status`
are one-shot, restart-safe, idempotent subcommands for ordinary durable
infrastructure. Collector: cursor + fingerprint dedup, append-only dated
raw JSONL with provenance, heartbeat with cursor lag, failure artifacts
retained under `data/l5/ops/failures/`. Shadow: incremental behavioral
comparison of new fills vs `l5_behavior.json` + the action model (state
coverage, mean probability of observed actions) — no P&L claimed.
Regression: canonical-sha check, action-model rebuild comparison, test
suite. Live validation (Sep 1): 4,000 fills collected (cursor lag <60s),
123 windows shadowed, mean action probability 0.138, **1 off-distribution
decision out of 3,772** — live L5 sits inside the historical model.
The workflow is scheduled (30-min collect+shadow, daily regression) but
GitHub only runs schedules from the default branch, so on
`feature/l5-canonical` it is inert except manual dispatch — merging to
`main` is the owner's explicit activation decision. Adapter-agnostic:
new venues/categories plug in via the `ADAPTERS`/`SOURCES` registries.

## Remaining unknowns blocking a "we reproduced L5" claim

1. **Per-fill sequencing for May–mid-Aug** (slim cache lacks token ids) —
   behavior report covers 2,216 of 16,501 windows; era drift in behavior is
   only spot-checked.
2. **Maker/taker for 7% of fills** and all fills outside takerOnly span.
3. **Order-book context**: we see his fills, never his quotes — quote
   depth/replenishment/cancels are unobserved, so a clone's fill rate at
   equal prices is not predictable from this data.
4. **Rebate/reward attribution granularity**: income rows are account-level
   daily credits (prior-UTC-day aligned per the replay-v1 audit); per-fill
   rebate attribution is estimated, not sourced.
5. **Capital base**: cash injections vs balance are invisible; return on
   capital is bounded, not measured.
6. **Counterfactual capacity**: whether a second maker at his prices would
   have received similar fills (queue competition) is untestable offline —
   and our Gemini C/L-series show venue flow, not mechanics, is the binding
   constraint for any clone.

## 2026-09-13 — where the fidelity loss actually is (SHADOW; nothing deployed)

Measured on the engine's own durable-ledger `fidelity_gate` rows
(**n = 10,679** decisions, 21:23Z, commit `f7a3e22410a3`) and on every
decision Railway retention still holds (**650**, 37 markets, 13:00–21:10Z).
See `analysis/l5/streak_shadow/STREAK_SHADOW.md`.

* `l1_sampled_vs_l5 = 0.0064` — **the model reproduces L5.**
  `l1_acted_vs_l5 = 0.4675` — the divergence is entirely downstream of it.
* That 0.4675 is two numbers: an **EXTEND deficit of 0.2494 (53.3%)** and a
  **COMPLETE excess of 0.1995 (42.7%)**. We take 2,196 extends where L5's
  distribution asks for 4,825.
* Sampled → acted conversions never create an extend. On the 650-decision
  slice, 77 of 650 (11.85%) convert, **97.4% of them through
  `entry_band_left`** (an ENTRY-admission price band applied to decisions
  that are usually not entries), 2.6% through the declared `residual_cap`.
* Classified: **97.4% class B** (the venue would have taken it; our policy
  refused), 2.6% class A (a declared owner risk bound). This is not a
  licence to remove the band — the prior band study splits COMPLETE +7.69
  against EXTEND −2.74 — but it names the largest single lever on fidelity.
* Six of the eleven economic override categories never fire at all: pair
  ceiling, fee-safe, post-only, stale evidence, settlement horizon,
  order/rest limit.
* **`frozen_key × side_streak` does NOT earn promotion.** Out of sample
  (train pre-2026-08-31, 14,430 held-out transitions) it is +0.239%
  log-loss and +0.17pp accuracy, with same-side calibration (ECE 0.19212 →
  0.19564) and action-distribution L1 (0.0081 → 0.0089) both slightly
  WORSE. Correction to the prior cycle: P2 scored streak against the
  three-part key, but production serves `base|last_rel|last_size`, which is
  worth 13.9% of log-loss over the three-part key on its own — most of the
  streak signal was already in the live key. Built, tested, parked.
* `src/pmus_ledger_export.py` — a safe offline ledger export (read-only,
  allowlisted, bounded, fail-closed, no endpoint, no credentials, no
  mutation) is designed and proven by 16 isolation regressions. **Not
  deployed**; wiring it means a new image on a money-moving service.

## 2026-09-13 — the entry band and the held-inventory band (SHADOW; nothing deployed)

`analysis/l5/band_semantics/BAND_SEMANTICS.md`. Corpus: the 75 `entry_band_left`
firings in 650 live decisions, 13:00–21:10Z.

* They are ALREADY two rules and it was never written down. With HELD
  inventory only the LEGAL-PRICE band (0.20–0.80) can fire; FLAT is gated by
  the observation band (0.25–0.75) as well. 74 of 75 firings are the HELD
  case, so a rule justified as market ADMISSION is in practice adjudicating
  whether to add to a position we already own.
* The band never fires on a marginal price: bid above 0.80 in 70 cases,
  below 0.20 in 5, **inside the legal band in 0**. And 67 of 75 are ONE
  market (`aec-mlb-lad-mia-2026-09-13`).
* **THE BAND IS NOT THE BINDING CONSTRAINT.** With it removed entirely,
  lad-mia's 67 refused extends become **3 fills — 64 are stopped by the
  RESIDUAL CAP** (R5FT `residual_units = 6`). FILL_ASSUMED and
  TAPE_SUPPORTED give the same 3, so the ceiling is the risk bound, not the
  fill rate and not the band.
* Arms (band widened for HELD inventory only; entry unchanged in every arm;
  every other gate re-checked against the SHADOW inventory): entry-only +6
  fills / $2.21 capital / **+$1.5994** settlement delta; ±5c +1/+0.9945;
  ±10c and ±15c +5/+1.5919; unrestricted = entry-only. Flat from ±10c up.
* That +$1.5994 is **six** fills across **two** settled markets with
  opposite signs (lad-mia +1.9938, sapole-salvas −0.3944) on $2.21 of
  capital. Not an expectancy measurement. Matched-pair P&L delta is
  **0.0000 by construction** — no completion may be imputed after a
  counterfactual extend, so every arm buys pure directional residual.
* **PROVENANCE of COMPLETE +7.6905 / EXTEND −2.7402** (R5F-RUN-2026-09-07):
  they are NOT two halves of one study. +7.6905 is `no_legal_maker_price`
  (20 refused COMPLETEs); −2.7402 is `entry_band_left` (33 refused EXTENDs).
  Both FILL-ASSUMED, valued at the venue's actual resolution, per decision,
  **incentives excluded**, residual settlement included, 9 of 53 rows
  UNCONFIRMED. Heavy selection bias, acknowledged in the original: 15 of 20
  COMPLETEs are one market, and the EXTEND refusals "mostly stopped buying a
  side the venue then settled against". **Do not use −2.7402 as the argument
  either way** — today's corpus is its mirror image and gives the opposite
  sign for the same reason.
* **VERDICT against the owner's rule: EVIDENCE INSUFFICIENT. Keep
  collecting. No live band change.**

### Durable export — the answer changed

* **A second service CANNOT mount the volume read-only.** Railway: "A volume
  can be attached to one service." No read-only attach, no shared mode; and
  volume-backed services cannot run two deployments at once, so detaching
  costs the probe downtime.
* **But the Railway CLI exposes first-party volume file access**
  (`railway volume files list|download`), verified present at CLI 5.54.1.
  That reads the ledger through Railway's authenticated control plane with
  **no probe change, no second writer, no second executor and no change of
  ledger ownership**. It needs the CLI authenticated in the research
  environment — a credential decision for the owner; no token has been
  created. This is now preferred over the stdout exporter, which stays built
  and tested (default OFF) as the fallback.

## 2026-09-13 — residual-cap shadow arms (SHADOW; live cap stays 6)

`analysis/l5/residual_cap/RESIDUAL_CAP.md`. CONTROL (band=current, cap=6)
reproduces production exactly: 0 extra fills, 0 production extends refused,
settlement delta 0.0000. Each arm carries its OWN inventory, seeded from the
position production already held at the first retained decision.

* **matched contracts delta is 0.00 at EVERY arm.** Not one extra contract of
  matched inventory at cap 8, 10, 12 or UNBOUNDED, in any settled market.
  `matched = min(up, dn)`, so inventory added to the side already held cannot
  become matched unless the opposite side later exceeds what that side held
  at the time — and it never did.
* **P3: ZERO class A at every cap.** 0/30 extra extends produced matched
  inventory; all are B→C (opposite opportunity appeared, no supported fill,
  settled directional) or D (unsettled). Against the owner's own test — "if
  it simply increases C, reject it" — it increases C exclusively.
* Marginal curve, entry-only band: 6→8 +2.9754, 8→10 +3.9681, 10→12 +2.9744
  (≈ +$1.5–2.0 per unit) — **entirely directional residual P&L, and entirely
  one market** (`aec-mlb-lad-mia`, whose held side won). The other settled
  market is −0.3944 at every cap.
* **Under CURRENT band semantics the cap changes nothing that settles.**
  Raising it 6→UNBOUNDED frees exactly two decisions, both in an unsettled
  market. The band is UPSTREAM of the cap: the cap only appears binding once
  the held-inventory band is relaxed. They are not independent and not
  interchangeable.
* Tails: max terminal residual 8.58 → 34.60 contracts from cap 6 to
  UNBOUNDED while the median market is untouched. Extra capacity is consumed
  by the tail of a single market.
* **P5 — is 6 units truncating L5?** In the states we actually occupy
  (`imb10-50`, 62 of 75 band decisions) L5's own residual, normalised by HIS
  per-window median clip, is p50 **2.3 units**, p90 4.4, with only
  **2.4–2.8%** of his decisions at or above 6. Our cap is about his p95
  there. He runs 12–16 units only in `imb>50`, which we reach in 4 of 75
  decisions. **The difference is not the size of the residual but what
  happens to it: L5's inventory is 62% matched at the median window; our
  extra inventory was 0% matched at every cap.** He oscillates; we
  accumulate.
* **All five conditions for cap > 6 FAIL or are not reached. CAP REMAINS 6.**
* Durable ledger: still no volume-file read in the authorized Railway MCP
  surface, and `railway-agent` takes actions so it is not being pointed at
  the probe's project for a research read. No token exists in this
  environment and none was created. Required: a Railway read token for
  project 50fb7357… as `RAILWAY_TOKEN` here. The stdout exporter stays
  UNDEPLOYED per instruction.

The carry-forward finding is not about the cap: the extra inventory never
became matched. Complement reachability remains the binding problem — the
same conclusion P0-B and P0-D reached, now from a third direction.

## 2026-09-13 — complement reachability and L5's oscillation (SHADOW; nothing deployed)

`analysis/l5/oscillation/OSCILLATION.md`. The premise that our inventory
strands because the complement is unreachable is NOT supported.

* **7 of 8 markets with a first fill had a reachable complement** (5
  immediately, 2 later, 1 insufficient evidence, **0 never-reachable**). Min
  observed pair cost 0.48–0.91; **every market reached a pair ≤0.97 within
  0–122 seconds** and our own posted maker price was touched in 7 of 8. Two
  markets still ended with zero opposite fills — including buf-hou
  (realized −2.5896) whose complement was available at a pair of **0.515**.
* **No admission signal exists to find.** R3 is empty, so every univariate
  split has an empty "never" arm. A1 has nothing to separate at n=8.
* **We already oscillate as much as he does.** Switches per fill: L5 p50
  0.2857, ours 0.2500. Median run length 2.0 vs 1.5. **Matched fraction at
  settlement: L5 p50 0.625, OURS p50 0.7502.** Zero-switch windows: L5 27%,
  ours 33% (3/9) — indistinguishable at this n. Trajectory was never the
  gap.
* **L5's switch pair price (n=21,699): median 0.99, p75 1.1113, and 47.2%
  ABOVE 1.00.** Window-level effective matched pair cost: median 0.9648,
  **36.2% of his two-sided windows end above par**. Our target is 0.98 with
  a hard 1.00 ceiling, so we refuse the top third to half of what he pays.
  Maker/taker UNKNOWN for L5 and never inferred; economic price only.
* **But at the decision moment the gap is half a cent.** Over 48
  contemporaneous candidates (authenticated quote ≤5s): executable TAKER
  pair p50 **0.9677** against our posted MAKER pair p50 **0.9627**. We forgo
  ~0.5c to stay passive; COMPLETE fills 6.78%, EXTEND 14.07%.
* **P2 mechanism: he alternates without waiting for price movement.**
  Median **9 seconds** between side switches, median run length 2 fills, and
  **58.0% of switches occur with the held side's own price having drifted
  less than 1c across the entire preceding run** (median drift 0.00). Near
  settlement he switches slightly LESS often (12s vs 9s), not more.
* Method correction made during the work: the first version measured the
  last-same-side vs first-opposite price gap and called it "price movement".
  In a binary market the sides sum to ~1, so that gap tracks the price LEVEL,
  not any temporal move. Replaced with the held side's drift over its own run.

**What is eliminated now:** +1-tick maker (P0-B), EXTEND suppression, the
held-inventory band, the residual cap, complement reachability, and market
admission. **What stands:** at a pair price we already accept, we post
passively and are not filled ~93% of the time, while L5 changes sides every
nine seconds at the same or worse economic prices.

No production change proposed. Any bounded crossing complement is an
execution-policy change on a money-moving service and needs its own
pre-registered shadow with fees resolved from source; fees remain UNKNOWN
(not reported per fill).

## 2026-09-13 — bounded complement-cross shadow, PRE-REGISTERED then run

Spec frozen at commit `135f5d79` (sha256 `7e31c447eaa1028c…`) BEFORE any
analysis code existed. `analysis/l5/cross_shadow/`.

* **ZERO supported crosses in all 16 cells** (.97/.98/.99/1.00 × T0/T10/T30/
  T60) over 260 real COMPLETE decisions in 8 markets — **not because prices
  failed, but because no row ever reached the price test.** First stop at
  T0: **no authenticated quote age 146 (56.2%)**, **depth not recorded 48
  (18.5%)**, **stale quote 65 (25.0%)** (5.1 s–826 s), malformed 1.
  T10/T30/T60 are 100% UNKNOWN — no book observation exists 10–60 s later.
* **The instrumentation gap is read-only and one line.** The recorder
  already computes, serves AND logs `bid_qty`/`ask_qty` per market
  (`pmus_ws_evidence_recorder.py:366`); the probe's `recorder_book` capture
  drops both. **0 of 589 order rows carry depth.** After-the-fact recovery
  from recorder logs is not viable: ~50–80 KB truncated status rows, ~1
  observation per market per 75 s, against a 5-second requirement.
* **FEES ARE NOW SOURCE-CONFIRMED** — docs.polymarket.us/fees fetched
  2026-09-13, effective 12 AM ET 2026-07-01, exchange-wide: taker
  `0.06·C·p·(1−p)` max $1.50; maker rebate `−0.0125·…` max −$0.31; reduced
  taker tiers start at $250,000 prior-month volume (we are far below, base
  rate applies); **"rounded to the nearest $0.01 using banker's rounding"**;
  per-category differentiation NOT STATED. Promotion-gate condition 1 MET.
* **The rounding bounds the question before any execution evidence.** All-in
  cost of a cross (confirmed fee + forgone maker rebate) is **1.6–2.3c per
  contract**. Break-even pair ≈ **0.977–0.988**, so **the .99 and 1.00 arms
  can never be net-positive at any size we trade**; at our actual clip
  (1.72 @ p≈0.5) only **.97** survives, and there a 0.97 pair nets $0.0116
  on $0.0516 gross — **fees take 77% of the edge.** Needs no depth data.
* **P5 adverse selection is NEGATIVE for crossing.** Over the 48 rows that
  passed everything except depth (labelled DEPTH_UNKNOWN, never fills), the
  complement got **cheaper** more often than dearer at every horizon inside
  a minute: +10 s 12.5% cheaper / 0.0% dearer; **+30 s 50% cheaper, mean
  −0.1028**; +60 s 50%/42.9%. Paying the ask bought a worse price than
  waiting on this corpus. n = 48/20/14, concentrated in collapsing markets.
* **P4 BUF-HOU**: 31 COMPLETE attempts, our maker price touched 11 times,
  realized −$2.5896. **No policy in the matrix eliminates the residual** —
  every attempt stops at a missing/stale quote or unrecorded depth before
  any price test. Whether crossing would have fixed it is UNKNOWN.
* **P6**: no claim is made that L5 crosses. With zero supported crosses the
  shadow trajectory is identical to production's, so the comparison reports
  UNKNOWN, not "no change".

**PROMOTION GATE: 1 MET, 2 and 3 FAIL, 4–6 NOT MEASURABLE, 7 UNKNOWN, 8 not
reached. → MAKER-ONLY STAYS.** No live bounded taker proposed. The $1.00
pair ceiling was not moved.

Carry-forward: any future version of this test should carry **.97** as its
ceiling, not 1.00. And the blocker is **instrumentation, not economics or
policy** — the fix is read-only but is still a change to a money-moving
service, so it is named and not made.
