# L5 Canonical Pipeline — Status

Subject: Polymarket trader `l5zn1bwom8etsk`
(`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`).

**This repository now contains money-moving paths on two venues.** Both are
unarmed. PMUS `l5-r1-probe` is HALTED with an EXPIRED mandate; Gemini has a
reviewed controller and no driver, and its arm constant names an
already-spent authorization. The research/replay paths remain public-data
GET-only.

Everything from "Consolidation lineage" onward is preserved historical
evidence and is not rewritten. Only this handoff section tracks current state.

## ⛔ READ FIRST — 2026-09-21: prospective recorder DEPLOYED; pmus_flow gap EXPLAINED

**P0 is prospective execution observability. Nothing is armed. Nothing is
fitted. No selector exists.** `REQUIRED_AUTH_ID` and `AUTHORIZED_SHA` remain
`None`; `src/gemini_execution/` has zero diff.

**1. The prospective L5 full-L2 recorder now runs on durable infrastructure.**
It is a supervised thread inside `l5-live-habitat` (`db9e951c-…`), writing to
`/state/l5-live/prospective/` on the 50 GB volume — not a new Railway service.
The image's isolation property (nothing that can sign, submit or read a
credential is copied in) is now enforced by
`tests/test_habitat_image_isolation.py`, not by a comment. See
`analysis/l5/PROSPECTIVE_DEPLOYMENT_20260921.md`.

**2. H-QUEUE-IRRELEVANT is WITHDRAWN. H-QUEUE-MISMEASURED replaces it.**
The withdrawn claim asserted a *mechanism* — complement arrival drives fills —
from n=16 in one market in one hour. The replacement is the claim the data can
adjudicate:

> `depth_ahead_of_fill_price`, as computed from the public CLOB ladder, does
> not represent the effective queue that governed L5's maker fill.

Tested by reconciliation (`src/l5_queue_reconciliation.py`): at the last public
look before each maker fill, do publicly observed book mutations plus published
trades account for the queue ahead of him disappearing? Six verdicts per fill;
`UNEXPLAINED` is the signature. **Confirming it would license no replacement
mechanism.** Results are reported **per market**; `pooled()` raises rather than
returning a number until a market reaches **n=200 scorable** fills. **Do not fit
at n=16.**

**3. The pmus_flow gap of 03:55:13Z–06:07:17Z was NOT a writer freeze.** My
earlier "stall"/"observability defect in the writer" reports are **withdrawn**.
The liveness map was built from `/v2/leagues` (50 leagues) while discovery
sampled `/v2/sports` (266), and the flow selector reads a missing event as
not-live; at diagnosis time **24 of 24** live events on the venue were invisible
to the map. Fixed by deriving both from one pass, which also deletes a whole
network pass. The `nfl_pre_control` branch wrote nothing because the next NFL
kickoff (MNF) was ~20 h out, outside its 12 h window — correct behaviour, not a
second bug. **That interval is CENSORED in any flow analysis, never zero flow.**
See `analysis/l5/PMUS_FLOW_INCIDENT_20260921.md`.

**4. Two defects the first durable smoke run caught**, both now pinned by
regressions: the venue served `outcomeIndex=999` and crashed the recorder
thread through a fixed `{0:..,1:..}` inventory dict; and the queue trail was
taken as the window *between* the pre-book and the fill, which is empty by
construction — every event read `trail: 0` while looking like a fact about
quiet markets.

**4b. An apparent 100% UNEXPLAINED rate at first light was entirely
instrumentation.** Re-scoring the smoke events gave 4/4 `UNEXPLAINED` — the
exact shape of H-QUEUE-MISMEASURED confirming itself. Those events had a single
book look, so the observed span was zero, the market rate was unmeasurable, and
`INCONCLUSIVE_GAP` could not fire: `UNEXPLAINED` was the only reachable verdict.
`reconcile_fill` now refuses to call a fill unexplained without a measurable
rate, and the same four score 0/4. **Neither figure is a result** (n=4, one
market, below every floor) — it is recorded because the whole difference was our
own measurement.

**4c. Two pre-existing red tests fixed, one left red for the owner.**
`deploy/l5_live_habitat/` was the only Dockerfile dir with no `railway.json`
(the guard against a service falling through to the order-capable startup), and
seven modules pinned the agent-proxy CA bundle — three with no fallback, which
would raise in any image lacking it. Both fixed. **`test_r1_source_lineage` and
`test_lane_a_probe_v0` are left RED**: `analysis/l5/authorization/` drifted from
its pinned blob (`c2551101…` -> `05daf9e0…`) because
`RESTING_LEDGER_REPAIR_20260921.md` was added in `319e4935`, the accepted
checkpoint, without the pin being updated. `forbidden_class` is empty — nothing
execution-class. **Re-pinning an authorization-lineage hash is an owner
decision and I have not done it.**

**5. Raw-stream volume is bounded and measured.** First smoke wrote 30.7 MB of
`raw_fills` in two minutes (~17 GB/day) by re-writing the same 500-fill backlog
every poll. Each distinct raw row is now written once, books are written on
`hash` change with a 60 s keepalive, firehoses rotate at 4 GB and shed below a
2 GB free-space floor while `events.jsonl` is never rotated.

## Current Live Transfer / Handoff (2026-09-18, for fresh sessions)

Labels are strict: HISTORICAL (source data), SHADOW/MODELED (never realized),
LIVE submitted/filled/settled, REALIZED (authoritative settlement + cashflow).

> **As of 2026-09-18T12:00Z the live executor is STOPPED and HALTED, and its
> authorization has EXPIRED.** `l5-r1-probe` ran under deployment
> `490ce8ac-4a73-4776-b7fa-0a5608f8d285` (SHA `64909c5cb0b6`, `mode: ARMED`)
> and ended at **2026-09-17T13:36:57Z with `CAMPAIGN_STOP:HALT`** — 21
> sessions, 0 filled markets, 10 settled, realized **−$0.0832**,
> venue-confirmed. `campaign_end` records `"next": "none: this status is an
> owner decision or a stop, and is never auto-restarted"`, and
> `restartPolicyType` is `NEVER`.
>
> The mandate `OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT` **expired
> 2026-09-18T00:41:20Z**. `HALT` was restored 2026-09-18 after
> `test_the_halt_and_the_authorization_are_consistent` went red with
> `mandate_expired` — the committed state had read "un-halted, no valid
> authorization" for 11.3 hours. No order was placed under an expired mandate
> and none could have been: an expired mandate is `NO_MANDATE` at the wire,
> and the process had been gone ~11 hours before it expired.
>
> Earlier text in this file naming a 10:46:53Z or 12:41:20Z mandate as
> CURRENT, deployment `75c3cef1`, SHA `2368fb6f`, or the executor as RUNNING
> AND ARMED is SUPERSEDED. Verify against Railway before acting on any of it.
> The old R1→R2→R3 rung/campaign program is still RETIRED — do not resurrect
> it, and do not clear a latch or set a dual key to "unblock" anything. Read
> `L5_CURRENT_PRIORITY.md` first.
>
> **PMUS's authenticated trade tape is WORKING** (measured 2026-09-18T12:00Z:
> `trades_300s` 140→192, `counts.trade` 142/cycle). The old `trades_300s: 0`
> blocker is CLOSED; do not reopen it and do not substitute gateway
> `sharesTraded`. P0 moved downstream to the evidence ladder: **PLACED →
> RESTABLE → BOOK_REACHED → TRADE_FLOW → EVIDENCED_FILL**.
>
> **Gemini is a P0 execution lane, not P1 collecting.** Transport is proven
> and closed, and the driver now exists: **seven bounded windows have run
> (CONTINUOUS-1..7, all spent).** See `L5_CURRENT_PRIORITY.md`,
> `analysis/gemini/CONTINUOUS_PROBE_1_FAIL_CLOSED.md` and
> `CONTINUOUS_{5,6,7}_RESULT.md`.
>
> **CONTINUOUS-8 (2026-09-19T01:36Z) PROVED THE TARGET CHAIN LIVE.** Entered
> at TTE 278.3s by the entry-band scheduler; eight cycles of OPEN/OPEN on
> flat inventory; YES 0.01 @ 0.31 filled as MAKER at 01:36:28.553Z; the next
> coherent refresh carried it (attempts 1, n_fills 1); and cycle 9 returned
> **EXTEND YES @ 0.12 and COMPLETE NO @ 0.69** — labels `classify()` derives
> from the residual, unreachable from empty inventory. Contract settled
> **YES**; the engine's own settlement_report gives directional-residual
> `gross_usd 0.0069`, status SETTLED_NOT_YET_CASH_CONFIRMED,
> `matched_pair_economics: null`, `realized: null`. **EXPECTED +$0.0069, not
> REALIZED**, and one 0.01 directional leg settling the right way is not
> evidence of edge.
>
> **THAT SEAM IS NOW CLOSED.** The coherence gate explained a disappearance
> from `fills ∪ trades ∪ terminal_seen` while `CP.recover` built inventory
> from `fills` alone, so read 13 reported `coherent: True` beside an
> inventory that still read flat. Both consumers now read ONE canonical
> execution view (`src/gemini_execution/executions.py`, a bundle member):
> executions group by ORDER, the finer-grained source wins, the aggregate
> CROSS-CHECKS it, a disagreement fails closed, a leg is never inferred from
> a client-order-id convention, and a terminal row can never become a fill.
> The exact C8B timing is pinned as a replay test — read 13 now carries
> `yes 0.01 / residual +0.01 / spend 0.0031 / n_fills 1`, and when filled
> history catches up the state is byte-identical rather than doubled.
>
> **The manually authorized plumbing-validation phase is COMPLETE.** The
> programme has moved to the bounded continuous L5 system; see
> `CONTINUOUS_SYSTEM_V1.md` for the lifecycle, the eight failure states and
> the run-level risk budget. No C9 is prepared and none should be.
>
> **AWAITING AUTHORIZATION — nothing continuous is live.** The smallest
> bounded continuous-live envelope is proposed in
> `CONTINUOUS_LIVE_ENVELOPE_PROPOSAL.md`: 12 windows, `max_total_spend_usd`
> $1.00, one market, the unchanged per-window envelope, 12 pre-created
> chained cohorts. Its purpose is EVIDENCE — a statable per-segment fill rate
> and a rebate credit attributable to a recorded window — explicitly not
> profit. **The −$0.25 realized-loss stop cannot fire on this venue**, because
> `realized_usd` moves only on venue-confirmed cash and none has ever been
> observed; the real bound is the $1.00 spend cap. The system stays in SHADOW
> until the owner signs the statement in §8 of that document.
>
> **Before C8, the binding constraint was FILL RATE, not plumbing.** C7 ran 12 clean cycles, spent all 6
> cohort slots, had every mutation accepted, explained a real double
> order-disappearance from authoritative terminal evidence on the first read,
> and terminated with ZERO orders live (the first window to do so). It filled
> nothing.
>
> **CORRECTED 2026-09-19 — the entry-TTE/fill correlation does not exist.**
> An earlier version of this paragraph said the windows entering before ~200s
> TTE filled and the later ones did not. The venue's own order records say
> otherwise: C4 (~131 s) filled **twice**, C5 (275.9 s) **once**, C6 (199.7 s)
> once, C7 (125.3 s) **none**, C8B (278.3 s) once. Five fills across five
> windows and no monotone relationship with entry TTE. Entry TTE remains
> experimental scheduling, not strategy knowledge. See
> `SEGMENTED_HABITAT_1.md` §0.
>
> **The binding constraint is FLOW.** 145 of 165 measurable adjacent-cycle
> pairs (87.9%) saw **zero** new public prints, and one contract went 21
> consecutive cycles — 198.5 s — without a single print. A passive quote
> cannot be filled by flow that does not exist. All three fills with full
> mutation records came from a rung placed once, never repriced, resting
> 57.8–63.7 s at the prevailing bid; all ten rungs on a repriced leg filled
> zero times. Reported **with** its confound (the repriced leg is the moving
> leg) and changing nothing in the controller.
>
> **Gemini REALIZED is $0.00 across all seven windows.** Three settlements
> carry EXPECTED P&L — C4 −$0.0006 (matched pair), C5 −$0.0046 and C6
> −$0.0021 (unmatched directional residual). These are EXPECTED, never
> REALIZED, and matched-pair and directional-residual are never summed. C7
> contributes nothing to any of them: no fill, no position, no economics.
>
> **The target chain — FILL → COHERENT REFRESH → INVENTORY CHANGE → CHANGED
> L5 ACTION — is PROVEN live, by CONTINUOUS-8.** Gemini REALIZED remains
> $0.00 across eight windows: 5 fills, four confirmed settlements carrying
> EXPECTED −$0.0006 (matched pair), −$0.0046, −$0.0021 and +$0.0069 (three
> unmatched directional residuals). They are different objects and are never
> summed. SETTLED IS NOT REALIZED.
>
> **COMPLEMENT REACHABILITY (2026-09-19) — the second half of the cycle does
> not happen here under current coverage.** 18 seeds (4 ACTUAL venue fills, 14
> HYPOTHETICAL from the large-print forensic, 13 contracts) replayed forward to
> settlement under 7 arms. NATIVE reaches **0 of 77** complement quotes;
> IMPROVE_1, all four minimum-rest arms and the Model B competitive-COMPLETE
> arm each reach **1 of 74** and form **one** matched pair. One matched pair
> against zero is **p = 0.50** on the exact test, so the verdict is
> `BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM`. Longer rest adds an EXTEND fill
> (7→8) and pushes the pair basis **0.84 → 1.02, above par** — on a 5-minute
> contract a held quote is stale, not patient, and the one rest "success" is
> the same fill IMPROVE_1 gets without rest, 18 cents worse.
>
> **The one-sidedness hypothesis is REFUTED and the mechanism is COVERAGE.**
> De-duplicated to 13 (symbol, leg) pairs there is MORE complement-direction
> notional than extend-direction ($998.81 vs $598.06, 37.5% extend). But
> **85.0% of it arrives after our last recorded cycle** (16 of 17 prints;
> $848.90 outside vs $149.91 inside), and **7 of 18 seeds had zero cycles
> after the first fill**, so no COMPLETE was ever quoted. The single seed with
> in-coverage complement flow is the single seed that matched, at a 0.84
> basis. This is the A/B/C/D attribution's C (timing, 46.0%) reached by an
> independent route. **Established: our coverage cannot support the second
> leg. NOT established: that the venue cannot.** All 77 native complement
> quotes sat AT the touch, so price and queue were not the obstacle. Residual
> at TTE 30 and TTE 0 is **unobserved in all 18 seeds** — recording stops
> between TTE 202.8 and TTE 36.2. See `COMPLEMENT_REACHABILITY_1.md`;
> everything in it is SHADOW/THEORETICAL and no bound was changed.
>
> **COMPLETION MODEL B (2026-09-19) — the metric is now RECYCLING, not fill
> count.** 18 initial fills over 13 contracts replayed to settlement under 31
> arms, every number denominated in dollars. Three results:
>
> 1. **The EXTEND veto is a Pareto win on observed data.** Every veto arm
>    dominates its own non-veto twin: all 7 EXTEND fills suppressed, **$0.0331
>    of residual avoided (25.7%)**, **$0.0000 of matched inventory given up**.
>    On Gemini as observed the EXTEND fills contributed nothing to matched
>    inventory. No threshold is proposed; this is the unconditional veto.
> 2. **Queue preservation does nothing.** The premise held — a complement was
>    already resting in 15 of 18 seeds, and preserving it held up to 50 of 77
>    complement quotes — but 0 pairs and 0 complement fills at every duration.
>    Keeping a queue place is worthless when nothing arrives to work through
>    it.
> 3. **The cross-venue difference is ACTIVITY and RUNWAY, and it is a gradient
>    inside L5's own book.** L5 BTC15m 122 fills/window → 96.0% complement;
>    BTC5m 6 → 83.1%; ETH5m 2 → **42.1%** with ext:comp rising to 1.40 and the
>    basis crossing par to 1.007; ours → 0%. L5's own recycling degrades the
>    way ours does when its market thins. And L5's first fill lands at median
>    **TTE 243** vs our **TTE 127** — it is not completing faster, it is
>    starting far earlier.
>
> **MATCHED/GROSS IS GAMEABLE.** `BEST_PERMISSIBLE_MAKER` posts the highest raw
> ratio purely by paying **1.25** for the pair `IMPROVE_1` buys at **0.84**, so
> the ranking runs on the par-constrained ratio with the raw one beside it.
> Stickiness beyond 15s crosses par in every column of the 4x5 matrix. No arm
> ever crossed the ask.
>
> **Nothing supports a threshold.** The hazard dataset has **3 INDEPENDENT
> positive states** (72 rows before de-duplicating a 24x arm pseudo-replication)
> against a floor of 30; the burst study has **9 bursts** (they do land late —
> TTE 90.7 vs 250.4 — after 65.5% depth depletion, but 9 is 9); the whole
> completion matrix rests on **one matched pair**. The TTE 60-30 phase is NOT a
> new bound (the frozen policy is already COMPLETE_ONLY there, pinned by test);
> it ran on 11 seeds and one recycled its full residual at a 1.00 basis.
> See `COMPLETION_MODEL_B_1.md`. Model A, the TTE bounds and the live envelope
> are unchanged and everything is SHADOW/THEORETICAL.
>
> **SEQUENTIAL-TAPE REPLAY (2026-09-19) — the arm expansion is FROZEN; the
> limiting resource is independent windows.** The tape is the clock now: prints
> are walked in order and inventory plus the Model-A action are recomputed
> after every hypothetical execution, before the next print. 35 windows
> offered, **29 sufficiently reconstructed**, 6 skipped and reported. Two
> policies only.
>
> **GEMINI_RECYCLE (B) dominates FROZEN_L5_NATIVE (A) and costs nothing.**
> Identical windows filled (16), windows paired (3) and COMPLETE fills (3),
> with **136 EXTENDs vetoed**: terminal residual **0.22 → 0.13 (−41%)**,
> capital at risk **$0.1323 → $0.0859 (−35%)**, matched/gross 0.107 → 0.158,
> settlement residual −$0.0167 → −$0.0101. Third independent confirmation of
> the EXTEND result, first on independent windows with a sequential clock.
>
> **But the pairs are mostly matched LOSSES.** Of 3 pairs the bases are 1.4675,
> 1.16 and 0.93 — **one window in 29 produced matched inventory at an
> acceptable basis.** B's better ratio comes from shrinking gross (0.28 →
> 0.19), not from better pairs. **Remove the nine >=100-contract prints and the
> paired result does not survive**: 3 pairs → 1, and that one is above par
> (1.2733 A / 1.49 B). The conclusion is burst-dominated.
>
> **THE COMPLETE PRICE FRONTIER IS THE DECISIVE RESULT. Price is not the
> lever.** Over 105 missed complement episodes: **81.0% would have required a
> CROSS** (not authorized), 13.3% were unreachable at any maker price within 40
> ticks, and only **6 of 105 (5.7%)** were reachable by improving the maker
> price — 1 at 0 ticks, 1 at 1 tick, 4 at >5. The analytical pair ceilings are
> FLAT from 0.98 to 1.05 (3 complements, 2.86% at every one); only dropping the
> ceiling entirely adds any, and it adds three pairs at 1.05-1.47. These
> ceilings are buckets for reading the table, NOT gates — the ruling against a
> pair-cost ceiling in the live path stands.
>
> **The rebate is negative net of fees at this size**: accrued $0.0010 (A) /
> $0.0007 (B), **payable after rounding $0.00**, against maker fees $0.0023 /
> $0.0015. Reported as its own line, never added to trading economics.
>
> On the 39.9% reachable notional: we get filled in 16 of 29 windows, reach a
> complement in 3, and reach an acceptable basis in **1**. B contains the
> damage; neither converts the reachable notional into acceptable matched
> inventory. 14 of B's 19 fills rest on an UNKNOWN NO-side queue treated as
> zero, the optimistic assumption, and are counted separately. See
> `TAPE_REPLAY_1.md`. Model A, the TTE bounds and the live envelope unchanged;
> everything SHADOW/THEORETICAL.
>
> **PRE-ENTRY HABITAT SELECTION (2026-09-19) — the signal is in the L5 sample,
> not ours.** Maker-price optimisation is FALSIFIED and frozen; no pricing arms
> added. One row per window, features sourced strictly before the first
> hypothetical order, boundary = the first recorded cycle, enforced by
> `check_no_leakage` and pinned by test. **0 of 31 rows fail the leakage
> check.**
>
> **Gemini cannot answer the question yet.** 31 independent windows, base rates
> initial fill 17, reachable print 3, COMPLETE 3, matched 3, **par-constrained
> 1**. With one positive against thirty, `any_meaningful: false` and **no
> feature survives a leave-one-out**. The six predecessor-rate features cannot
> form a contrast at all, because **the single positive window
> (GEMI-BTC05M2609191315-UP, basis 0.93) followed a predecessor with ZERO
> prints** — the exact opposite of what the L5 gradient predicts. Without the
> bursts the primary outcome never fires.
>
> **The oracle bound is high: 90.0% of gross exposure and 89.7% of capital
> avoidable with perfect foresight, residual to zero, keeping the good window.**
> Not deployable, cannot be run; the no-burst oracle reading 100% is degenerate
> (nothing to retain) and is reported as such.
>
> **THE L5 GRADIENT IS CONFOUNDED WITH REGIME — CORRECTED 2026-09-19 same
> day.** Par-constrained recycling BTC15m **75.7%** / BTC5m 50.2% / ETH5m
> 20.7% / Gemini 3.2%, and five of six pre-entry features move monotonically
> along it. But `src/l5_shadow/cohorts.py` records, cross-checked against the
> venue's own maker flag at 1,270/1,270 and pinned by
> `tests/test_l5_cohorts.py`, that **only BTC15m is a maker operation (95.5%
> maker, 121 fills/window)**; BTC5m is 0.05% maker and ETH5m 0.24%. A taker's
> round-trip and a maker's recycling are not the same quantity. The gradient
> therefore orders STRATEGIES as much as habitats, and the two features I
> called strongest — prior two-sided and prior side balance — are exactly what
> a maker produces by definition, so on the L5 side they may measure "was L5
> making here" rather than "was this market recyclable". **L5 BTC15m at 75.7%
> stands as the MAKER benchmark; the "five monotone habitat features" claim
> does not.** That module's own docstring warned that averaging these cohorts
> "produced every misleading cross-cohort figure this programme has
> published", and I published another one before consulting it. The Gemini
> findings are unaffected — they come from Gemini's own external tape.
>
> **Two defects caught, not shipped.** Leave-one-out counted only sign flips,
> so dropping the single positive — which empties a cell — read as "no flip"
> and made one observation look robust; drops that DESTROY a contrast are now
> counted and nothing survives. And I first wrote the module as `habitat.py`,
> silently destroying the collector of that name (`public_flow`,
> `ladder_shape`, `cycle_sample`, `window_summary`); the suite caught it, the
> original is restored from git, the module is renamed `habitat_selection.py`
> and a test pins the separation. No soak run hit it and every number is
> byte-identical after the restore.
>
> See `HABITAT_SELECTION_1.md`. Nothing live, no selector, no threshold, no
> authorization; Model A and the TTE bounds unchanged. GEMINI_RECYCLE stays the
> adapted execution control.
>
> **HABITAT DISCOVERY IS P0 (2026-09-19). Gemini BTC5m becomes continuous
> evidence collection, not the optimisation target.** Inventory first, per the
> owner: `L5_HABITAT_INVENTORY.md`. **Four habitats are replayable immediately
> with ZERO new infrastructure** — verified live at 14:35Z:
> `src/l5_shadow/venues.py` already serves Polymarket `pm_window`/`pm_book`/
> `pm_trades` and Gemini `gemini_book`/`gemini_trades` for BOTH 5m and 15m, and
> `src/gemini_execution/tape_replay.py` is venue-neutral (it takes cycles,
> prints, a resolution and an expiry). Live fetch today: PM BTC15m 48 prints +
> full ladder, PM BTC5m 100 prints, Gemini BTC15m 3 prints + book.
>
> **Polymarket BTC15m is the priority** — the one habitat where a maker regime
> is KNOWN to exist, with an EXTERNAL tape, so our hypothetical quote meets
> flow that is not L5's. `l5_all_trades.csv` is L5's own fills and cannot serve
> the apples-to-apples comparison the owner asked for.
>
> **Polymarket executability, sourced not assumed:** tick 0.01, taker fee 0.07,
> fee rebate 0.2, rewards max spread 4.5, and **minimum order size 5** against
> our Gemini size of 0.01. That minimum is its own matrix column: a habitat we
> cannot enter at our size is not a habitat for us whatever its flow looks
> like.
>
> Order: Gemini BTC15m (same code path, isolates timeframe) → PM BTC15m → PM
> BTC5m (completes the 2x2, separating venue from timeframe) → PMUS sports
> (one schema pass on the live recorder) → Gemini non-BTC discovery
> (`metadata.py` is hard-wired to BTC05M). Crypto is not assumed to win.
> `L5_HABITAT_MATRIX.md` reports evidence and sample sizes, no composite score.
>
> **THREE MECHANISM STUDIES + THE GEMINI SLATE (2026-09-19).** No execution
> arms added, Model A frozen, GEMINI_RECYCLE and the EXTEND veto kept.
>
> **1. CLOSEST APPROACH CLOSES THE PRICING QUESTION.** 124 post-first-fill
> episodes, strict chronology, maker territory = one tick under the ask.
> **118 (95.2%) NEVER came within 5 ticks; ZERO came within 2; ZERO reached
> the touch without executing. Median closest gap 22.0 TICKS.** The earlier
> "82% required a cross" could have meant just out of reach — it did not. The
> near-miss population is EMPTY: we were not losing races at the touch, the
> price we needed and the price on offer were in different neighbourhoods for
> the whole life of every quote. Midpoint moved toward us on 57% of episodes,
> a coin flip, so this is not directional adverse selection either. Depth built
> on 19 of 110 observable episodes.
>
> **2. UNDERLYING STATE: UNTESTED, NOT REFUTED.** Strike from the contract
> (`availableAt` = window open, contemporaneous), BTC from 1-minute candles
> (1,440 bars covering every window); `settlementValue` is the OUTCOME and is
> never a feature, pinned by test. **18 states, 1 recyclable, 17 leakage** —
> no contrast means anything. Honest spec limit: only the 60s velocity exists
> historically; 5/15/30s cannot be resolved from 1-minute bars and are
> recorded as None with that reason rather than interpolated.
>
> **3. LIQUIDITY PRESENCE: WE MEASURE THE WIRE, NOT PRESENCE.** Median print 8
> contracts, median touch depth 202.0. Our 0.01 is **1/800th of a median
> print and 1/20,000th of touch depth** — one typical counterparty could fill
> our whole quote 800 times over. The fills proved the order path, the
> coherence gate and the accounting; they say essentially nothing about
> liquidity provision. **Whether a larger quote would ATTRACT flow is labelled
> UNKNOWABLE from historical replay, not estimated** — that label is attached
> to every object the module returns. The verdict was binary and read
> "meaningful on at least one anchor" for these numbers, which was true and
> misleading; it is three-way now (MARGINAL) with the threshold flagged
> arbitrary.
>
> **4. THE SLATE: BTC05M IS THE WORST HABITAT ON GEMINI'S OWN BOOK.** 997
> events, **11,012 contracts**, 9,274 unresolved, 160 tapes read stratified by
> series. **BTC05M read ZERO prints across 8 sampled contracts — as did
> ETH05M, SOL05M, XRP05M, and every 15M series. The 5-minute timeframe is dead
> across every underlying, not just BTC.** Ranked by ALTERNATION (how often the
> aggressor direction changes — what an oscillator needs, and what a volume
> ranking cannot see): BTC15M 0.600, BTC1H 0.500 (balance 0.800), ZEC1H 0.231,
> SOL 0.207, **BTC 0.203 with 507 prints and 100% two-sided across all 5
> sampled**. Samples are 5-9 contracts per series and stated on every row;
> BTC15M's 0.600 rests on ONE contract with a stated balance. Leads, not
> conclusions. (A first pass sorted by newest expiry, spent its budget on
> far-future sports that had never traded, and was re-run stratified.)
>
> **The obvious next step, not taken without the owner: run the sequential
> replay against BTC1H / BTC15M / the BTC series** — same engine, same venue,
> no new infrastructure — to test whether the TIMEFRAME was the problem all
> along. See `MECHANISM_STUDIES_1.md`. Nothing live, no authorization, no
> artificial volume.

### P0: Polymarket US sports

**Live executor — STOPPED AND HALTED (2026-09-18); the text in this paragraph
describes the 2026-09-17 authorization that has since expired and is kept as
history, not as current state.** Previously RUNNING AND ARMED: `l5-r1-probe`
(`6111a5d4-a2d9-4762-a057-31f02d07586f`), deployment
`75c3cef1-6e45-4ba9-9e69-5f77de1387bf`, **serving SHA
`2368fb6fe4c0eb71eda81467c514cb4b3799336c`** verified from the runtime
`campaign_start` record rather than a build result, `mode: ARMED`, under
`OWNER-2026-09-17-CP1-LIVE-SHORT-HORIZON-COHORT` (authorized 10:46:53Z,
expires 22:46:53Z): cohort `*`, concurrency 1, 10 total markets, $5.00/market,
$15.00 campaign, residual 3, −$6.00 daily stop, target $0.10, max leg $0.125,
k 0.00619, maker/post-only. HALT is absent and a current mandate is present —
both reviewed commits. The safety architecture is unchanged: HALT is still read
before `armed()`, an absent mandate still means `NO_MANDATE`, and spend is
still admitted at the wire by economic effect. The `PMUS_SPRING_*` dual key
remains insufficient on its own.

**First completed money lifecycle (previous authorization, 2026-09-17).**
`aec-itfwo-erishi-jiahan-2026-09-17`: 3 maker fills, 0.48 NO at avg 0.3825,
venue-reconciled to delta 0.0000, settled `POSITION_RESOLUTION_SIDE_LONG`
(`beforeNet −0.4800 → afterNet 0.0000`), **REALIZED −$0.1836**,
venue-confirmed. Split, never summed: matched-pair **$0** (no pair completed),
directional residual **−$0.1836**, incentives **$0.0000**, fees
`venue_unreported`. The sequence was OPEN → EXTEND → EXTEND with no COMPLETE,
so it went to resolution as pure one-sided exposure — the outcome
`L5_OPERATING_MODEL.md` warns about. That authorization's campaign ended
`CAMPAIGN_STOP:HALT`; it is a **separate** authorization and does not consume
the current one.

**2026-09-16 authorization breach — occurred, fixed, tested.** Authorized for
ONE market at $0.05; traded 11+ markets and committed **$19.3295** to one.
Causes: (a) the campaign chain continued on approvals dated 2026-09-06, raising
the per-market envelope to $7.50; (b) `counts_against_market_cap()` was a
stage-name allow-list that excluded `spring_complete_*`, a BUY of the
complement. Replaced by an immutable per-authorization mandate enforced at the
transport by economic effect (`src/pmus_live_mandate.py`), with consumption
read from the durable ledger. Continuation, settlement, restart, resume,
`advance_gate` and historical rung approvals cannot enlarge or replace it.
Records: `analysis/l5/live_probe/R1_CANARY_BREACH_2026-09-16.md`,
`analysis/l5/live_probe/EXECUTION_ARCHITECTURE.md`. Tests:
`tests/test_live_mandate_{adversarial,ledger_invariant,deployment}.py` —
preserve all three.

**V1 continuous-inventory SHADOW — RUNNING, unchanged.** `l5-v1-shadow`
(`8a7bc9f4-…`), deployment `17d027dd`, commit `76061d06`. No credentials, no
transport, no order verb, no executor in the image. ~46 sports markets/cycle,
~49k rows. k = 0.00619, Envelope A. Not to be tuned during this sample. Both
the naive and the `residual / k` L5-equivalent state lookups are retained; the
lift changes the answer on 98% of rows.

**ACT vs WAIT is the current fidelity question.** V1 proposes on ~79.5% of
rows; every WAIT is a risk cap, never the model. The model's relation
vocabulary is `{open, complete, extend}` — no wait/hold/none token exists in
the artifact — so `P(act) = 1` by construction. Separately, `observe()` never
passes `last_rel`/`last_size`, so lookups are frozen at `(open, q<5)`, reaching
202 of 69,646 transitions (0.29%). Waiting IS in the artifact, as
`cadence_modal` gap buckets, which V1 does not consult. Full diagnosis:
`analysis/l5/v1_shadow/ACT_VS_WAIT.md`. **Controller unchanged pending a
decision.**

### R1 runtime safety audit (2026-09-17T22:10Z, read-only)

Closed out; see the superseding note at the top of this section.

* **No unexpected orders.** Zero submissions in 8h33m. Last order `CJ19WJ4QEVNG`
  (YES, qty 0.10, `spring_complete_8`, post_only, **filled_qty 0**) at 13:23:04Z.
  Sessions after that all `SPRING_SKIP_NOT_QUALIFYING`, `submitted: 0`.
* **Residual: ZERO.** The one non-zero residual, `aec-itfme-maxreh-alewei-2026-09-17`
  at `-0.19` (`unmatched_usd 0.0925`), **resolved at 13:36:49Z** —
  `beforeNet -0.1900 → afterNet 0.0000`, status `REALIZED`, `realized_usd
  -0.0832`, `realized_confirmed: true`, `settled_by
  position_resolution_activity`. Never more than ONE market with a non-zero
  residual at once (`total_unmatched_usd` peaked at `0.0980`) — the concurrency
  repair held.
* **Latch/stop state.** `CAMPAIGN_STOP:HALT`, not auto-restarting. Daily loss
  stop NOT reached: `daily_realized_pnl_usd -0.5619`, `combined_loss_usd
  -0.6451` against `-6.00`. Budgets `market_remaining_after 4.4678` /
  `total_remaining_after 14.4678`.
* **`venue_vs_ledger`.** One `agrees:false` (12:59:31Z, delta 0.80) — on a
  **fresh, non-resumed** session, i.e. the known scope artifact, not the HALT
  condition. Every `resumed:true` session reconciled `agrees:true`,
  `delta 0.0000`.
* **Clean on the rest.** No sampled COMPLETE recorded as an executed EXTEND
  (0 of 64 decisions). No unrecognized progress token admitted — those strings
  appear only as *refusal* reasons. No crash, no restart loop. Two read-only
  `held_absent_while_risk_bearing` alarms, `action: none taken` by design.
* **Legacy `pmus-spring-l5-probe`: still halted.** Deployment `c30724d7` /
  commit `6adcc4c43e67` unchanged since 2026-09-15T12:46Z. **Zero `PMUS_ORDER`
  entries.** Latch untouched.

Nothing reportable. Confirmed realized on this cohort stays **-$0.5619**,
entirely directional residual; matched-pair realized $0.00.

### Scaling

763,966 unique historical fills. The continuous-inventory shape is already
present in the early **2026-03-19 → 04-19** regime (~218 fills/day, ~$1,139
notional/day, ~$128.69 median window notional, ~28 fills/window, ~10-share
typical fill, ~$32.90 median peak residual). Across the later 87× capital
increase, median fill size moved ~1.4× while fills/day moved ~44.5× and
markets/day ~24×. **Working hypothesis — supported by that comparison, not a
universal rule — L5 scaled by frequency and breadth, not size.** Direction:
one controller → explicit bankroll envelope → many eligible markets → more
repetitions as realized evidence earns expansion.

**No live scaling authorization currently exists.** Candidate envelopes are
recorded unranked and unarmed.

### Realized accounting (cohorts never merge)

* **R1 cohort**: venue-confirmed realized **−$0.0916** across five markets
  (meiguo-junzha −0.0214, zavmyk-honser −0.0306, lacvic-qiasun −0.0264,
  koryev-semser +0.0318, liszaa-elekor −0.0450).
* `aec-itfme-conbra-andpet-2026-09-16`: **SETTLED_REALIZED_UNCONFIRMED**
  (the venue supplied no `realizedPnl`), venue_qty 40.88 == ledger 40.88,
  position_net 0. Held OUT of every net. Its ~$1.1105 matched-pair figure is
  **derived arithmetic, not realized profit**; its $0.1200 incentive is
  venue-credited and stays separate from trading P&L.
* **Legacy PMUS cohort**: **−$44.4898** across 54 markets / 34 venue-confirmed.
* Earlier BTC-5m q=1 live probes: cumulative realized **−$0.51** (HISTORICAL).

### Other services

* **Legacy PMUS** `pmus-spring-l5-probe` (`d84d9a68-…`): **HALTED, do not clear
  the latch.** Its Railway startCommand rewrites source with `sed -i` at boot,
  so the running code is not the reviewed artifact even when the hash matches.
* **Gemini**: **P0, execution lane.** Transport is proven and closed (authenticated WS `order.place`); the gap is a driver, not a venue question. See `L5_CURRENT_PRIORITY.md`, `analysis/gemini/CONTINUOUS_PROBE_1_FAIL_CLOSED.md` and `analysis/gemini/CONTINUOUS_CONTROLLER_WALK_FINDINGS.md`. The old "P1, collecting, untouched" line was stale and is retired.
* **Polymarket.com**: observation/reference only.

### Parallel lanes (2026-09-17, evening) — SHADOW/MEASUREMENT only

Full detail: `analysis/l5/lanes/LANE_FINDINGS_2026-09-17.md`. Nothing below is
realized profit; no order was placed; nothing here authorizes a canary.

Two ADAPTED lane specs derived from `FIDELITY_OBSERVABLE_V1` (unchanged,
`d583fa4005d1b815…`). Each inherits 4 fields as `TRANSFERRED` — a status that
requires `transferred_from` and whose `is_l5_evidence()` is False, so a
Polymarket BTC15m observation can never be cited as one about another venue.
`TTE_policy` and `secondary_rung_qty` are never transferred: those numbers are
about the instrument, not the behaviour. `observed_here` is EMPTY for both.

* `L5_ADAPTED_PMUS_SPORTS` `2237304d81ac63f5…` TTE [0, 86400]
* `L5_ADAPTED_GEMINI_CRYPTO` `62d004d80b4af8b9…` TTE [0, 300]

**LANE B P0 — ANSWERED.** The 17-fills/0-complements result is **not** an
artifact of the obsolete one-shot architecture (`gemini_l5_shadow_v2.py`,
untouched, not running) and **not** a broken complement. From the surface's own
per-side counters: SELL placed **122** vs BUY **80**, restable 368 vs 356,
book_reached 239 vs 276. At 641 observations: placed 209/89, restable 719/601,
book_reached 499/407, and **SELL trade_flow 20 against BUY 0** — flow went
through a sell quote twenty times and produced **zero** sell fills. Verdict
`BUY_ONLY_FILLS_SELL_RESTED_UNFILLED`, and the cause is the **QUEUE, not the
market**: the sell side is placed, rests, reaches the book and is traded
through, and `volume_ahead` never clears. (An earlier 2-minute read said the
venue did not trade through it; 641 observations supersede that.)

**LANE B BLOCKER — settlement.** Four expired BTC5m windows (578s–3878s past
expiry): all `status=closed`, empty book, public tape intact, and **no terminal
value published anywhere**. One last-printed 0.97 and another 0.17; a last
print is not a settlement. The finding is CANNOT, not not-yet.
`src/gemini_settlement.py` states the five requirements and reports **BLOCKED**,
`NOT_EXERCISED` rather than PASS. `src/gemini_settlement_probe.py` is the
read-only, fail-closed probe that would close it, to be run where the Gemini
credentials live — it refuses here, as it must.

Gemini venue terms, measured: tick 0.01, **`min_order_size` 0.01** (the
shadow's `UNIT=1` is 100× the venue minimum), **`status: limit_only`** (the
venue itself rejects non-limit orders on this instrument).

**LANE A — books yes, prints no.** 150 of a 602-market live slate: two-sided
BBO 23/150 = 0.153, and **23/23 of those have depth on both sides**, median 13
book levels, median spread 0.030, 10 of 23 with fee-safe pair cost ≤ 0.96. But
`/trades`, `/history`, `/ohlc`, `/price-history` and `/v1/trades?marketSlug`
**all 404** — re-verified. `sharesTraded` is cumulative *per market* and cannot
say a trade happened at OUR price, which is what `VOLUME_AHEAD_CLEARED`
requires. `src/l5_surface/pmus_prints.py` now joins the authenticated WS tape
to the ladder (with coverage rules that forfeit vouching on a wrapped 500-trade
ring or a late subscription), so what remains is wiring the live feed.

**THE RACE — neither lane is canary-ready** (`src/l5_surface/lane_gates.py`):

| lane | propose | score | blocked by |
|---|---|---|---|
| Gemini crypto | yes | **no** | SETTLED, REALIZED |
| PMUS sports | **no** | no | TRADE_FLOW, EVIDENCED_FILL, OPPOSITE_RECYCLING, SETTLED, REALIZED |

Proposal- and scoring-readiness are tracked separately because they fail
separately: a lane that can place a canary but cannot settle it spends real
money on a result nobody can read, which under settlement-only realized
labelling is an unscored loss, not a canary. Gemini is **closest**, blocked
only on settlement accounting.

Also: G-P8 had been reading **REFUSED** since `e063e54c` — A4 caught two
unclassified record kinds, exactly as designed. Both are non-terminal; it
PASSes again. And `deploy/l5_exec_shadow/RELEASE` now records every served
module's SHA256, with a test that fails on drift, after its `code_sha` was
found naming a commit whose Gemini shadow lacked the counters it was serving.

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

## 2026-09-14 — L5_EXTEND_PRESERVE: **KILLED**. Recorder cap: **stays 1500**.

Both verdicts accepted by the owner. Neither change is implemented.

**L5_EXTEND_PRESERVE is KILLED.** Preserving a model-selected EXTEND instead of
converting it to COMPLETE was already implemented twice — `SHADOW_1_ENTRY_ONLY`
(`src/l5_band_semantics.py`) and `band=entry_only|cap=6` (9d6feb46) — and both,
on both fill rules, produce **zero extra matched contracts**. Matched inventory
per market is identical with and without the arm (3.44 / 6.08 / 2.79 / 4.67)
while terminal residual grows 4.05→7.66, 3.49→6.87, 1.00→5.08. Its +$1.5994 is
one baseball game (lad-mia +1.9938 against sapole-salvas −0.3944, one market
unsettled). The residual cap of 6 refuses 64 of 67 opportunities in the
dominant market, and the residual the arm does create meets a COMPLETE fill
rate of 3.2% there. See `analysis/l5/extend_preserve/EXTEND_PRESERVE.md`.

**`PMUS_WS_STATE_MAX_MARKETS` stays 1500.** The 7.1 GB container figure is not
the Python heap: `rss_gb()` reports `ru_maxrss`, whose all-time peak moved
0.603 → 0.652 GB over 6 h 16 m while the container grew 6.48 GB. The growth is
page cache from a ~1.8 GB/h tape write against a 12 GB retention budget. A
lower cap would save 299–569 MB of a figure that is 91% page cache while
halving the residency of unseated markets — already 30.3 min against the 3600 s
`trades_1h` needs. The proposed 3900 s `bbo_history` age bound was then
**measured and declined**: the p50 seated market burns 2000 entries in 699 s,
so the count bound is already tighter in 16 of 17 measured markets. Cold-market
rates remain UNKNOWN. See `analysis/pmus/recorder_memory/`.

## 2026-09-14 — P0 COMPLETE FILL CONVERSION: we cancel our own orders

**SHADOW/FORENSIC ONLY; nothing deployed.** 260 COMPLETE attempts, 8 markets,
16 fills (6.15%), corroborated by 190 post-cutover attempts with 5 fills.

**THE MECHANISM.** The completion price is bounded by pair economics; in 47.4%
of sessions the ceiling puts our price **below our own side's touch** by
design. The `touch_moved` re-quote — armed for COMPLETE via
`PMUS_SPRING_COMPLETE_REQUOTE` — then fires on `touch != px_`, which is true at
the first poll and every poll after. The next attempt re-derives the same
ceiling-bound price. Deterministic, not probabilistic:

| rested orders | n | fills | rate | median rest | single-poll |
|---|---:|---:|---:|---:|---:|
| priced AT our touch | 76 | 15 | **19.7%** | 6.44 s | 4 |
| priced BELOW our touch | 135 | 1 | **0.74%** | 2.01 s | **134** |

**RANKED DECOMPOSITION.** M3 we cancelled early **122 (46.9%)**; M1 complement
never reached 52 (20.0%); R0 rejected before resting 49 (18.8%); M6 market
moved past us 9 (3.5%); M5 churn at an unchanged price/book 9 primary, 89
co-occurring (75% of consecutive attempts re-post the identical price);
M7 UNKNOWN 3. **M2 (queue) and M4 (size) are 0% and both are UNKNOWN, not
absent** — the complement reached our price in **0 of 205** windows, so no
queue race was ever observed, and there are **zero partial fills** in 260
attempts, so no size pressure was ever observed. Queue placement and cancel
behaviour are UNKNOWN on L5's side too: public fills cannot establish them.

**AGAINST L5's PUBLIC FILLS** (77.3% maker; queue/cancels/quotes UNKNOWN):
his maker opposite-side fill arrives at p50 **3 s**, p75 **7 s**. We cancel at
p50 **2.01 s**, and 87.4% of our requote-cancels land inside our own 3.19 s
median fill latency. He holds p50 **1** fill per price level and steps ~1 cent;
we re-post the same price 75% of the time, in runs up to 23. Clip p50 17.0 vs
our 1.39 (the learning-stage bound, not a defect).

**THE SENSITIVITY DOES NOT SAY WHAT THE QUESTION ASSUMED.** A COMPLETE can only
match inventory we already hold: total residual is **32.77 contracts**, so the
benefit saturates at a ~20% fill rate and the maximum obtainable matched margin
is **$0.1563** — against a settled residual loss of $1.3408 and an account
settlement line of −$12.5082. **No fill-rate improvement turns it positive.**
Covering the settled residual loss needs pair ≤ **0.9591**; the account line
needs ≤ **0.6183**; we quote **0.9952** on the attempts that do not fill.
**0 of 13 filled sessions were capped by the pair ceiling; 37 of 65 no-fill
sessions were.** Conversion and economics fail together, for one reason.

**HIGHEST-EVIDENCE CHANGE TO SHADOW-TEST NEXT:** suppress the `touch_moved`
cancel for a COMPLETE pinned by the pair ceiling, where the re-quote provably
cannot produce a different price. It changes no price, cap, band, size or
concurrency. **It is a fidelity/conversion test, not a profit lever** — the
prize is bounded at $0.1563 — and whether those orders would have filled is
NOT answerable from the tape we hold, because we cancelled them. The profit
lever is the pair cost we enter at, i.e. position state. See
`analysis/l5/complete_conversion/COMPLETE_CONVERSION.md`.

## 2026-09-14 — CEILING_PINNED_PERSIST PARKED; P0 is now HABITAT, not execution

**CEILING_PINNED_PERSIST is PARKED** at `ce3f0294`: not deployed, no live arm,
imported by nothing in `src/` or `deploy/`. Un-parking requires BOTH halves of
the owner's condition — economically attractive ceiling-pinned COMPLETEs AND
evidence they are lost to premature cancellation — and neither holds today
(marginal pair 0.9952, whole prize $0.1563; the complement reached our price in
0 of 53 chains). See `analysis/l5/ceiling_pinned_persist/PARKED.md`.

**P0 ECONOMIC-STATE TRAJECTORY DECOMPOSITION.** One walker
(`src/l5_state_trajectory.py`), both venues, episodes aligned at first fill,
no lookahead. The identity `pair − 1.00 = C_own_fills + B_market_move` holds at
**2.22e-16** across both.

**THE CENTRAL ANSWER IS YES.** Perfect maker COMPLETE conversion at existing
prices (each imputed fill capped by the residual in front of it) takes PMUS
from 44 fills to 74 and 12 switches to 35 — and still leaves:

| | L5 | PMUS | PMUS perfect |
|---|---:|---:|---:|
| episodes | 2,216 | 9 | 9 |
| informative states (computable gain) | 69,558 | 35 | 48 |
| gain_per_unit exactly zero | 4.0% | 45.7% | **29.2%** |
| sec between fills p50 | 2.0 | 116.9 (upper bound) | unchanged |

**7.3× L5's degeneracy after perfect conversion.** Conversion changes whether a
posted order fills, not how often we are in a position to post one.

**THE DEGENERACY METRIC IS THE POINT.** `gain_per_unit` is the held side marked
against its own running average, so a position built from ONE fill has a gain
of exactly zero by arithmetic. PMUS's tight gain distribution centred on zero
is a symptom of not cycling inventory, not of healthy positions. Any
cross-venue comparison of gain or pair LEVELS is meaningless without it.

**SURVIVORSHIP WARNING, recorded so it is not re-derived as an edge.** PMUS's
realised pair p50 (0.96) looks better than L5's (0.997). It is not an
advantage: n=9 pairs with a full decomposition against 13,048, and those 9 are
the COMPLETEs that filled — our fills land at pair p50 0.949 and our no-fills
at 0.9952. Classified UNKNOWN.

**DECOMPOSITION.** Observed (exact): ΔC +0.0023, ΔB −0.1420, sum −0.1397. **C —
what our own fills do to average cost — is statistically indistinguishable from
L5's (SHARED_L5_BEHAVIOR).** Counterfactual (associational, non-additive): A
reweighting to L5's price bands shifts +0.0178 but **51.6% of L5's mass falls
in bands PMUS never visits**; D has n=1 and L5 has no shell at all; F is
UNAVAILABLE (PMUS fills carry no per-fill TTE). G/remainder is habitat.

**GEMINI CANNOT ANSWER THIS AND THE REASON IS COUNTABLE.** 5m: 118 campaigns,
best arm **2 complement fills / 2 matched campaigns**, three of four arms ZERO.
15m: 174 rows, **6 fills, zero matched inventory**. Gemini does NOT exhibit
more L5-like distributions — it has essentially no pair formation. An
availability finding, not a performance comparison. The exact per-fill schema
and query the workstream must return is in §5 of
`analysis/l5/state_trajectory/STATE_TRAJECTORY.md` (minimum to be usable: ≥30
matched campaigns and ≥30 complement fills).

**CONSEQUENCE: stop optimising PMUS COMPLETE execution.** The binding
constraint is how often we are in an L5-like state at all.

## 2026-09-14 — L5 HABITAT SCORE, and the PMUS baseline

**ef5ef135 accepted as the PMUS habitat verdict. COMPLETE microstructure work
is FROZEN at current evidence.** PMUS remains bounded production + evidence
collection and is **not disabled**. CEILING_PINNED_PERSIST stays parked. No
admission, sizing, concurrency or production change is made on the score.

**`src/l5_habitat_score.py`** answers one question — *how much does this market
environment resemble the environments in which L5 actually generated his
inventory cycles?* — from variables observable **before any strategy acts**.
Settlement outcome, realized P&L, our fills and order state are on a
`FORBIDDEN_INPUTS` list and the scorer **raises** if one reaches it.

**THE THRESHOLDS ARE L5's.** Each calibrated feature is scored against the
empirical distribution across his **1,893 canonical windows**; his median
scores 0.5, his p90 scores 0.9. `derive_reference()` regenerates the table, and
the reference and every candidate go through the **same** feature extractor.

| feature | kind | L5 p10 | L5 p50 | L5 p90 |
|---|---|---:|---:|---:|
| price_changes_per_min | CAPACITY | 0.866 | 2.344 | 10.769 |
| share_move_ge_1c / 2c / 5c | CAPACITY | .572/.407/.286 | 1.000/0.800/0.625 | 1.000 |
| price_span | CAPACITY | 0.172 | 0.780 | 0.960 |
| bands_visited | CAPACITY | 2 | 4 | 7 |
| tte_p50_s | REGIME | 69 | 171 | 524 |

**ONE-DIRECTIONAL BY CONSTRUCTION.** L5's tape records his FILLS, which
lower-bound what his markets supplied. Below the reference is a **conclusive
habitat deficit**; above it is **not** conclusive sufficiency. The score is a
**ceiling on habitat adequacy, never an estimate**.

**THREE KINDS OF NOT-KNOWING, NEVER IMPUTED FAVOURABLY.** UNKNOWN (candidate
did not supply it, or supplied it saturated), UNCALIBRATED (L5's fills-only
tape cannot produce a reference — spread, depth, book-update rate, queue,
trade-through: reported, never scored), NOT_APPLICABLE. Missing features leave
the denominator; `coverage_discounted_score` makes the loss visible. L5's
demand side (fills/min, switches/min, switch_rate) is reported BESIDE the
score, never inside it — and a book-update rate is not a substitute for trades.

**PMUS BASELINE: habitat score p50 0.108, coverage 0.857, discounted p50
0.0926**, over 14 markets (356 XCHECK rows, 18 seen, 4 skipped for <180 s).
Best market 0.2537 against L5's median window of 0.5 by construction.

**AND THE BIAS CHECK MAKES IT CONCLUSIVE.** Coarse sampling biases rate
features DOWN but move-magnitude features UP. On the four UP-biased features —
where the error runs in PMUS's own favour — PMUS's median scores are
**share_move_ge_1c 0.0561, ge_2c 0.0491, ge_5c 0.0121, price_span 0.0991**:
roughly L5's 1st–10th percentile. `price_changes_per_min` is flagged
sampling-limited and is not needed for the finding.

`ws_trades_total` is a deque length **capped at 500 and saturated in all 14
markets**, so trades/min is UNKNOWN — never a floor read as a measurement.
`tte_p50_s` is UNKNOWN in every market, which holds coverage at 0.857.

This is independent support for ef5ef135, reached from market state **before**
any strategy acts rather than from our own trajectories. It establishes nothing
about profit, nothing about whether a better PMUS market exists (14 markets over
~1 hour is a baseline, not a census), and nothing about Gemini, which is not
scored because the workstream has returned no observation stream. The
per-venue observation spec and the still-required per-fill schema are in
`analysis/l5/habitat/HABITAT_SCORE.md` §7.

## ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 = FAIL (finalised)

The frozen cap5/cap8/cap13 experiment reached its 20th two-sided PMUS window at
00:17:30Z and returned **FAIL** on every arm. A-1, A-4, A-5 and A-7 pass; all
three arms fail A-2, A-3 and A-6. No threshold was changed and no cap3/cap4
rerun was attempted. Gemini formed 0 two-sided windows and never qualified.

| arm | matched | residual | total | matched % | maxDD |
|---|---|---|---|---|---|
| cap5 | +106.01 | -110.09 | **-4.09** | 0.7085 | -163.30 |
| cap8 | +67.78 | -330.26 | **-262.49** | 0.8130 | -397.60 |
| cap13 | -34.18 | -798.25 | **-832.43** | 0.9643 | -955.67 |

**FAILURE_MODE = BROAD_LEAKAGE.** 25 of 38 settled windows lose on residual;
top1 0.1024, top3 0.2929, top5 0.4468 of a -454.57 loss pool. PMUS alone is
BROAD_LEAKAGE at top1 0.174; GEMINI alone is MIXED at 0.2273, and GEMINI forms
matched inventory in **0 of 17** windows, so pooling it dilutes every share.

**The residual is a pure directional bet.** Its sign is determined entirely by
whether the leftover side won — 38 of 38 settled windows, no exceptions — and
that bet wins only **0.3421** of the time (PMUS 0.2381). A fair coin is 0.50.
The side a resting maker accumulates is the side that is becoming wrong.

**No observable risk state removes most of the damage.** Across 17 counterfactual
probes the largest share of residual LOSS removed is 0.3989, and that is
ORACLE_FLAT_AFTER_MATCH, which no policy can run; the best runnable probe
removes 0.3680. **0 of 17 probes raise the residual win rate to 0.50.** Residual
control changes how much of the bet you carry, never that it is adversely
selected.

**The edge and the risk are the same act.** The oracle keeps 53% of matched
QUANTITY but only **14.5%** of matched P&L (+16.33 of +112.75): the profitable
pairs are completed by the very extensions that build the residual. This
reproduces prospectively what the historical P1 decomposition already found —
residual extension carried the whole pair-cost improvement (-22.69) while match
expansion was net harmful (+9.29).

Incentives are excluded from all of the above. Retrospective L5 incentive
forensics remains closed as UNATTRIBUTABLE.

Three defects in the first run of the decomposition were found and corrected
before these numbers were read; see the header of
`analysis/l5/adapted/failure.py`. The FAILURE_MODE verdict is unaffected by
them — it is computed from the baseline, which no probe touches.

## 2026-09-19 — the running lanes had no fill mechanism (SHADOW; nothing armed)

Both Gemini lanes run continuously on the frozen Model A and the
`GEMINI_RECYCLE` adapter: **BTC5m** locally and **BTC15m** on Railway
(`gemini-maker-shadow` `a15785aa-…`, deployment `6ebd2b34-…`,
`restartPolicyType: ALWAYS`, 50 GB volume, no new service created).
`quote_between_tte_s = [0, 300]` is untouched; `lanes.py` reads it and a test
asserts it never assigns to it.

**THE DEFECT.** `shadow_soak` places no order — correct, and the safety
property — but the window record was also built with
`HB.window_summary(samples, rungs_placed=0, fills=(), …)` hard-coded, and the
recovered `Inventory` was never touched after entry. Both lanes therefore
reported **0 first fills, 0 complement fills, 0 matched** beside a real,
non-zero print count, and would have gone on doing so for ever. That reads as
a market that declined our quote. It was the absence of a fill mechanism.
`OPEN fill → inventory change → COMPLETE fill → matched inventory` was
**structurally impossible** in either lane, so watching for it was watching
for something that could not occur.

**THE FIX.** `src/gemini_execution/hypothetical.py` drives the same queue
accounting `tape_replay` uses offline, forward against the live public tape:
displayed queue eats first, a rung is reached only by the opposite aggressor
direction, a price change loses queue position, and the policy is re-asked
after every fill. Prints are walked **before** the rungs are requoted (a
cycle's tape landed while the previous cycle's rungs rested), and a re-fetched
rolling tape cannot fill the same rung twice. It reaches no write path —
asserted against the module's AST. Every number is HYPOTHETICAL and an upper
bound; none is submitted, filled, settled, realized or a cost basis.

**PROSPECTIVE TTE ≤ 300 COMPARISON**, 24 expired contracts per cohort, venue
tape only, over the interval Model A can actually quote in:

| | BTC5m | BTC15m |
|---|---|---|
| prints | **113** | 47 |
| notional | **$1,534.43** | $357.28 |
| prints / contract | **4.71** | 1.96 |
| notional / contract | **$63.93** | $14.89 |
| two-sided share | **0.667** | 0.333 |

BTC5m wins every column Model A can reach. This reproduces prospectively the
inversion found retrospectively before the whole-window BTC15m promotion was
withdrawn. Both lanes stay up; this is a measurement, not a selection.

**EARLY_15M_ADAPTATION (offline Model B research only).** 60 BTC15m contracts,
56 computable: a first fill was available in **56/56**, and external
opposite-direction flow reached a fee-safe complement price in **29 (51.8%)**,
median **124.6s** after the fill. **The 0.99 pair basis is set by the replay,
not discovered** — `replay_early()` quotes the complement at `1 − held − tick`,
so all 29 bases were exactly 0.99 by construction, and "51.8%
par-constrained" would have been a tautology. What survives is that flow
*reached* that price, not that pairs *cost* it. Upper bound: Gemini serves no
book history, so this prices from the tape and treats queue position as zero.
Model A is untouched and nothing quotes earlier.

See `LANES_PROSPECTIVE_1.md`.

---

## 2026-09-19 — the BTC15m timing correction (SHADOW; zero venue mutations)

`quote_between_tte_s = [0, 300]` on the Gemini lane **is not an L5
measurement and never was**: `OURS_ADAPTED`, `evidence: null`,
`transferred_from: null`. It is the length of a Gemini **BTC5m** contract,
chosen for the 5-minute cohort (commit `9b8a1ebe`) and inherited unchanged
when BTC15m was bound to the same lane (`fdd19d06`). L5's own measured
`TTE_policy` is `[0, 900]`. Full trace in `TTE_PROVENANCE_1.md`.

**L5's own fills, per fill rather than per window** — 63,569 deduped across
527 windows (`analysis/l5/tte/btc15m_by_fill_tte.json`):

| bucket | fills | USD share | **OPEN** | recycle | median basis |
|---|---|---|---|---|---|
| 600–900 | 18,604 | 30.3% | **560** | 0.810 | 0.9737 |
| 300–600 | 22,687 | 34.2% | **46** | 1.003 | 0.9520 |
| 180–300 | 10,100 | 16.1% | **13** | 0.925 | 0.9854 |
| 120–180 | 4,644 | 7.7% | **6** | 0.886 | 0.9924 |
| 60–120 | 4,890 | 8.5% | **1** | 0.866 | 0.9876 |
| 30–60 | 2,165 | 2.5% | **0** | 0.865 | 0.8792 |
| <30 | 472 | 0.7% | **0** | 0.765 | 1.1622 |

**560 of 626 OPEN fills (89.5%) at tte 600–900; 96.8% above tte 300; none
below tte 60.** Under `[0, 300]` the clone is present only for the tail in
which L5 has stopped opening. (`NO_NEW_OPEN_EXTEND_TTE_S = 60` is confirmed
by the same table.)

**Measured on Gemini's own tape, not argued from L5.** 96 expired BTC15m
contracts, one fetch each, replayed under both bands so treatment and control
differ in timing and nothing else. Both legs priced from prints that actually
traded; `maker_reachable` is **UNOBSERVABLE** on every row — Gemini serves no
book history — so every figure is an upper bound conditional on a fill.

| | EARLY `[300,900]` | LATE `[0,300]` |
|---|---|---|
| any print in band | 92/96 | **58/96** |
| price-observable complements | **76** | 28 |
| pairs ≤ $1 | **33** | 14 |
| fee-positive pairs | **28** | 12 |
| median basis | 1.0200 | 1.0050 |
| prints in band | 812 | 178 |

Paired: fee-positive **EARLY-only 22, LATE-only 6** (two-sided sign test
**p = 0.0037**). Conditional quality is slightly *better* late (50.0% at or
below par vs 43.4%); absolute opportunity is 2.3× greater early, and **38 of
96 contracts have no prints at all** inside the frozen band. On the stated
metric — recycling, not first-fill count — the frozen band is where the flow
is not.

**The correction.** `L5_ADAPTED_GEMINI_BTC15M_V2`
(`cd8ad6a914dc9fc0…`): one field, `quote_between_tte_s → [0, 900]`.
`L5_ADAPTED_GEMINI_CRYPTO` (`62d004d80b4af8b9…`) is **byte-identical and
preserved as the control**. A correction is not a lane — `lane_gates.SOURCES`
is keyed by `LANES`, so it is declared in `lanes.CORRECTIONS` and pinned out
of `LANES` by test, and it carries no live-progression gate. The corrected
field stays `OURS_ADAPTED`; the provenance lives in its `prior_evidence`
text, not in its status.

Both arms run in the existing `gemini-maker-shadow` process on the same tape,
book and depth every cycle, sharing nothing else. The BTC15m **entry band
moves 240–280 → 840–880** — experimental scheduling, always outside Model A —
because otherwise the process is unattached to the contract for the whole
interval the correction is about and the arm's silence would read as a
finding. BTC5m is untouched and has no arm.

**Nothing else changed:** side selection, price surface, sequential inventory
state, EXTEND/COMPLETE mechanics, size, residual bounds, the 60/30 terminal
rules, `MAX_ACTIONS`, the GEMINI_RECYCLE veto, the crossed-book refusal.
`mutations_sent = 0`.

The correction is a claim about **recycling**, so first-fill count cannot
confirm it. If the corrected arm accumulates first fills at tte 600–900 and
still shows zero complements and zero matched pairs over a comparable sample,
timing was not the binding constraint — and that is the report, not a request
for more uptime.

See `BTC15M_TIMING_CORRECTION_1.md`.
