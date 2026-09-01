# L5 Canonical Pipeline — Status (2026-09-01)

Subject: Polymarket trader `l5zn1bwom8etsk`
(`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`). Research only: every code
path here is public-data GET-only; nothing trades, authenticates for
orders, or touches live services.

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
