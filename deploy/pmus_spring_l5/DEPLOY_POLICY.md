# The live probe does NOT auto-deploy on push

Its Railway watch set was changed to a sentinel path that never matches
(`deploy/pmus_spring_l5/DEPLOY_AT_BOUNDARY_ONLY`), so pushing a file the probe
used to watch — `pmus_l5_continuous_session.py`, `pmus_sports_l5_decision.py`,
`pmus_status_report.py` and the rest — **no longer restarts it**.

## Why

This process holds real inventory. Auto-deploy on push means any commit
touching a shared module restarts a live money process at a moment nobody
chose. The campaign has been in `SPRING_RESIDUAL_HOLD` all night and the
standing instruction has been not to touch it mid-hold; under auto-deploy,
honouring that meant never pushing probe-side code at all, which is why the
boundary reader could not be staged.

With deploy decoupled from push, probe-side changes can be **staged on the
branch** and applied at the clean boundary, which is the only moment a restart
is free.

## The hazard this creates, stated plainly

A push that *should* reach the probe now silently will not. That is the same
class of failure that bit this project twice on 2026-09-10 (`9912e9a8` read
SKIPPED and left broken code serving; `d2624218` deployed and collected
nothing). **Anyone changing probe code must deploy it explicitly and then
verify the deployment**, not assume the push did it.

## How to deploy the probe

Explicitly, via the Railway API — `redeploy`, or `create-deployment` against
the service — and then CONFIRM: read the deployment status AND the probe's own
status row (commit hash, `errors`, campaign state). SUCCESS alone is not
health.

## When

At a clean boundary only: `held: []`, status no longer `SPRING_RESIDUAL_HOLD`,
`flat_verified: true`, and the seven-check preflight green. That sequence is
pre-approved and needs no further owner turn.

## Deploy log

Each explicit probe deploy is recorded here, because "we deployed it" is a
claim and this file is where it becomes checkable.

- **2026-09-11 02:02Z — `5efc9a2f`, deployment `92d127ad`, SUCCESS.**
  Per-trade quantity evidence. Restarting a probe that held inventory tripped
  `boot_assertions → flat_held_state` and it went to `REARM_REFUSED`; the
  arming gate only arms from flat. No orders were lost (fresh venue read
  `{'ours': 0, 'foreign': 0}`). The lesson is in the gate's favour: it did
  exactly what it exists to do.
- **2026-09-11 02:16Z — `258dfb2e`, deployment `be0c0fec`, SUCCESS.**
  Moved the quantity reconciliation onto the `shutdown_idle` pass, because a
  halted probe never reaches `session_end`. It answered the question on the
  first cycle: venue 45.0100 against ledger 45.0100 on BOTH measures, and 12
  of 13 trades with `qty` disagreeing with `qtyDecimal`. The 774.62 phantom
  was a unit fallback, not a position.
- **2026-09-11 — one-shot migration close.** `PMUS_MIGRATION_CLOSE_SLUG` is
  set to the exact cricket slug, so the boot runs the reduce-only close
  BEFORE the arming gate and a single boot can go residual → flat → armed.
  Unset the variable once the close has reported `CLOSED_FLAT`: leaving it
  set is harmless (a zero residual has no reducing side and refuses) but it
  is not meant to be a standing configuration.

- **2026-09-11 — residual quarantine promotion.** Deploying the CI-green
  boot-closure build. `92b39ca6` is the validated commit: GitHub Actions run
  `34591355951` (pmus-collector-ci run 418) concluded SUCCESS at 10:55:34Z;
  2,829 tests pass locally.

  An empty commit cannot trigger a Railway build — Railway matches CHANGED
  PATHS against the watch set, and an empty commit changes none. So strict
  tree identity and a push-trigger are mutually exclusive, and this entry is
  itself the trigger. The deployed commit differs from `92b39ca6` by exactly
  this documentation block: every path under `src/`, `tests/`, the Dockerfile
  and `railway.json` is byte-identical, which is the property that matters —
  the image built is the image CI validated.

  After the serving process is verified, `PMUS_RESIDUAL_QUARANTINE=1` is set
  and nothing else. The existing gates then decide: `blocking_held()` requires
  the cricket residual to be genuinely dormant (zero resting orders, venue
  quantity reconciling against the ledger, real inventory, still in `held`,
  still settlement-monitored), and `combined_loss_above_stop` separately
  prices its full downside against the −$15.00 stop. Either it qualifies and
  R5F re-arms, or the probe stays halted and names the assertion.

  **Outcome, 11:40Z.** Deployment `e48dcf90`, commit `383a69631848`.
  `boot_assertions ok: true` — all eleven pass. The one line that changed:

      quarantine OFF   flat_held_state ok=false got ['aec-county-not-ham-…']
      quarantine ON    flat_held_state ok=true  got []

  `combined_loss_above_stop` stayed `true` throughout; cricket's downside is
  still charged. Campaign reports `mode: ARMED`.

  **But it still does not acquire.** The very next row:

      resume_start  selection_skipped: "CAMPAIGN_HOLD:POSITION_HELD
                    (1/1 markets held, worked one session at a time)"
                    funnel: {admitted: 0, selection_skipped_position_held: 1}

  The BOOT gate is quarantine-aware; the RUNTIME acquisition path is not.
  Three coupled places still count a quarantined market as occupying the
  slot, all in the live order path:

    1. `capacity = (len(held_all) < concurrency and not _allowance_spent())`
       — cricket consumes the single acquisition slot.
    2. `over_concurrency_action(held_all, filled_markets, concurrency)` —
       freeing (1) alone would open a second market and then immediately trip
       `CAMPAIGN_DRAIN:OVER_CONCURRENCY` on the next cycle.
    3. `micro.account_block_reason` via `set_concurrent_owned`, which is
       passed `()` under every concurrency-1 rung. A NEW open's preflight
       requires an account-wide flat, so cricket blocks it as
       `nonzero_positions` regardless of (1) and (2).

  (3) is the sensitive one: it is the account-wide flat lock. Making
  quarantine work end-to-end means teaching that lock to accept a declared
  quarantined position — which is what quarantine is for, and is still a
  change to a safety lock on the money path. Not attempted here.

- **2026-09-11 — quarantine's runtime half.** The refusal above named the
  condition; this is the condition fixed, not routed around.

  Dormancy now has ONE definition, `dormant_held()`, read by both halves:
  the BOOT gate through `blocking_held()` (unchanged behaviour) and the
  RUNTIME scheduler directly. The slot arithmetic has one definition too,
  `acquisition_state()` — a pure function, specifically so it can be tested,
  because the three places that disagreed disagreed precisely by each
  recomputing it inline inside a 900-line loop where nothing could reach it.

  What changed, in the scheduler:

    1. `capacity` counts ACQUIRING markets, not held ones.
    2. The over-concurrency DRAIN counts acquiring markets. Without this,
       freeing (1) opens a second market and the next cycle declares
       `CAMPAIGN_DRAIN:OVER_CONCURRENCY` — quarantine would buy exactly one
       market and re-deadlock. The BREACH branch is untouched and still
       reads every held market: a position this campaign did not open halts
       it, quarantine or not.
    3. A market established as dormant is DECLARED to the account preflight
       through `set_concurrent_owned` — the same audited `allow_slugs`
       channel the concurrency>1 rung has always used. Nothing else about
       the preflight changed, and `open_orders:N` still refuses a first leg
       for ANY resting order in the account regardless of what is declared.

  A short-lived cache on `dormant_held` was written and then removed. It
  saved a handful of read GETs and bought a window in which `venue_confirmed`
  could already be false while it still answered "dormant" — the 774.62
  shape, on the money path, in the function whose whole job is establishing
  that a position is safe to stop watching.

  Still true, each with a test that fails if it is removed: quarantine OFF is
  byte-identical to the pre-quarantine loop; a dormant market stays in
  `held`, so combined_loss, settlement monitoring and the round-robin all
  still see it; it is still worked for COMPLETE; it is never re-selected as a
  new first leg; no cap, ceiling, concurrency, residual bound or daily stop
  moved.

  **Deploy trigger, 12:0xZ.** CI-green commit for the runtime half is
  `6d097516` — GitHub Actions run `34596461473` (pmus-collector-ci run 421),
  conclusion SUCCESS at 2026-09-11T11:59:31Z; 2,850 pass / 0 fail / 27
  skipped locally. As before, an empty commit cannot trigger a Railway
  build (Railway matches CHANGED PATHS), so this deploy-log entry is itself
  the trigger, and against `6d097516` it changes exactly one documentation
  file — every path under `src/`, `tests/`, the probe Dockerfile and
  `railway.json` is byte-identical, which is the property that matters: the
  image built is the image CI validated.

  **Outcome, 12:03–12:11Z. Deployment `6cfbd53c`, commit `319bd682ce92`.**
  `boot_assertions ok: true`, all eleven, `mode: ARMED` — and this time the
  new row fired:

      quarantine_slot  quarantined ['aec-county-not-ham-2026-09-08']
                       held        ['aec-county-not-ham-2026-09-08']
                       acquiring   []

  Four minutes later the campaign had opened, filled and PAIRED a different
  market — the structural proof, from the venue's own numbers:

      12:03:16  boot_assertions ok: true          (11/11)
      12:03:20  quarantine_slot  acquiring []
      12:04:07  session_end  aec-itfwo-kaycro-sofjoh-2026-09-11
                status SPRING_MATCHED   filled_actions 2   submitted 5
                spring_open_4     BUY YES 5.60 filled 5.6  CE4K0JQEETN3
                spring_complete_5 BUY NO  5.6  filled 5.6  CE4K0S6A2TN5
                inventory up 5.6 / dn 5.6 / matched 5.6 / RESIDUAL 0.0
                avg_up 0.4200  avg_dn 0.5700  spent 5.5440
                pair_at_chosen 0.9900  above_par false  fee_safe true
                venue_vs_ledger venue 11.2000 ledger 11.2 delta 0.0000
                                agrees true    flat_verified true
      12:07:31  session_end  aec-cs2-wc-nem-2026-09-11        SPRING_NO_FILL
      12:10:39  session_end  aec-itfme-rustiu-dmipop-…        SPRING_NO_FILL

  Three new markets worked in seven minutes, where the same engine holding
  the same residual opened nothing in 3h20m. No `concurrency_drain`, no
  `concurrency_breach`, no `CAMPAIGN_DRAIN:OVER_CONCURRENCY`. Cricket stayed
  in `held` and was still resumed between them, and its full downside stayed
  charged against the −$15.00 stop throughout.

  LABELLING. That pair is SUBMITTED → FILLED → PAIRED. It is **not**
  realized: the market has not settled, no rebate has been credited, and
  the +$0.056 implied by a 0.99 pair cost on 5.6 units is gross of fees and
  of nothing else. Realized comes from the venue's settlement number or it
  does not exist.

## The boundary standard, revised 2026-09-11

The "When" section above says `held: []`. That was written when a restart
holding inventory tripped `flat_held_state` and went to `REARM_REFUSED` —
the 02:02Z entry below is exactly that. With residual quarantine live, a
restart holding a DORMANT market boots and arms: proven at 12:03:16Z, all
eleven assertions green with cricket held and quarantined.

So the operative boundary is now:

- no session actively working inventory (between sessions, not mid-order);
- zero resting orders at the restart moment;
- every held market expected to satisfy `rq.eligibility` — zero resting
  orders, venue-confirmed quantity, real inventory, still settlement-
  monitored — so `blocking_held()` returns `[]`.

`held: []` remains the *safest* boundary and is preferred when available.
It is no longer the only one, because a continuously acquiring campaign may
not reach it for hours and that would mean never deploying observability.

**This is a change to when a live money process restarts, so it was not made
unilaterally: OWNER decision 2026-09-11, on the specific state below.**

- **2026-09-11 — read-only risk-set audit.** CI-green `854e3b4d`
  (pmus-collector-ci SUCCESS; 2,906 pass / 0 fail). Deployed at a boundary
  where `held: 1` — `aec-cs2-rev-is-2026-09-11`, 5 units unmatched YES at
  0.35 (~$1.75), static book 0.35/0.36, in `resume_wait` between sessions
  for 25+ minutes with no session working it.

  WHY THIS SHIPS. `aec-county-not-ham-2026-09-08` left `held` between
  12:03:20Z and 13:18:11Z and its state could not be established from the
  logs — not because the evidence was ambiguous but because there was none:
  the running probe never emitted its risk set. `unsettled` appears only in
  `rung_markets_reached`, which a continuous campaign never reaches, and in
  the `shutdown_idle` re-price, which needs a halt.

  ACCEPTANCE, narrow and owner-set: same risk limits, same R5F logic, no
  order-path change, clean boot, then the first emitted row shows BOTH the
  scheduler set and the ledger-backed `unsettled` risk set. If cricket is
  still in `unsettled` it is still being charged; if it is absent, a terminal
  ledger transition occurred even though the settlement row was missing from
  the logs we inspected.

  Additive logging only. No cap, ceiling, budget, bound or gate changed.
