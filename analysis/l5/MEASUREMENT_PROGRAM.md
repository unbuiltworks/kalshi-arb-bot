# Adverse-selection measurement programme

**Objective: measure, live, whether our execution model has a real edge.** The
81% adverse-selection figure is a replay estimate and the only number the
decision turns on. This programme measures it. It is not a profit attempt and
its success criterion is statistical.

Status: PREPARED, NOT ACTIVE. HALT asserted. Fidelity mode absent from the
running image. No authorization installed.

## 1. It needs no code deployment

Both inputs are records the **deployed engine already emits**:

| record | carries |
|---|---|
| `L5_FILL_BOOK_EVIDENCE` | per fill: side, own price, qty, executable book before/after, `order_resting`, depth ticks, `venue_event_t`, `processing_t` |
| `L5_WINDOW_ACCOUNTING` (CONFIRMED) | the venue's own settlement lines, actual fees, matched/residual |

Nothing joined them, which is the only reason "did this fill land on the
winner" was unanswerable. `analysis/l5/fill_outcome_join.py` does the join
**offline**. That is deliberate: the programme must measure the execution
model we already run, and instrumenting the money path would measure a
different one.

**Deployment SHA stays `2d74847f`** (running as deployment `3b02b5ce`).
`l5_fidelity_mode.py` is not in that image at all, so fidelity mode is off by
construction rather than by configuration.

## 2. What is recorded per fill

Against the six requirements:

| required | source | status |
|---|---|---|
| market state + executable book at submission and fill | `L5_FILL_BOOK_EVIDENCE` `before`/`after` ring + `book_at_processing` | **have** |
| maker price, size, queue/depth evidence | `fill_own_px`, `fill_qty`, `depth_ticks`, `two_sided_queue` (`queue_ahead`, `level_depth`) | **have** |
| latency | `venue_event_t` − `processing_t` | **NOT AVAILABLE** — see below |
| fill lands on winning or losing outcome | new join → `on_winner` | **have (new)** |
| whether a complementary fill occurs | new join → `complemented`, `complement_qty` | **have (new)** |
| actual fees, settlement cashflows, realized net | `execution.actual_fees_usd`, `settled.*`, `realized_net_usd` | **have** |

**Latency is unmeasurable and is not estimated.** `venue_event_t` is null on
every fill we hold. The runner already passes
`venue_event_t=ev.get("venue_event_t")` straight through, so this is the venue's
fill record omitting a timestamp, not a gap in our code. The join reports
`latency_observations` and refuses to interpolate. If the venue begins
supplying it the field flows through with no change.

**Validation, not assertion.** The join reproduces Campaign E exactly from its
real records: matched 1, residual UP 1, `residual_side_lost True`, and the
per-fill gross figures sum to **−0.57 + 0.58 − 0.30 = −$0.29**, the venue's own
`realized_net_usd`. Modelled rows are excluded on their `basis` label, so a
replay fill is never counted as a confirmed fill.

## 3. The durable sequence already exists

`l5_campaign_approval.WindowLedger` is a "durable, per-nonce record of which
windows an approval has spent", stored under `/data` on the Railway volume. One
approval with `max_windows: 8` and an enumerated `window_starts` runs **8
windows back-to-back with no intervention and survives a restart**. Campaign E
used `max_windows: 1`, which is why it was one window.

**One variable silently caps it.** `L5_LIVE_AUTH_MAX_WINDOWS` is currently `1`,
and the preflight's own note says "a SET value can only tighten it, so a stale
1 silently caps a four-window campaign". It must be `8`.

**The limit on a single owner act.** `MAX_CAMPAIGN_S` is 4h and an
`issued_utc` in the future is refused (`approval_issued_in_the_future`). So one
document can cover 8 windows (2h) comfortably, but it cannot cover 14h of
windows. **40–60 windows therefore needs 5–8 documents, one installed per
block.** Within a block there are no manual restarts; across blocks there is
one owner install each. That is a property of the staleness rule, not
something to engineer around.

## 4. Sizing, corrected

The unit of observation is a **window ending with residual**, because that is
the metric the 81% replay figure and L5's 57% both use. At today's config only
**58%** of windows end with residual (304 of 522 in replay; 85% produce any
fill at all).

| authorized windows | residual observations | 95% half-width | σ separating 57% from 81% | expected cost |
|---|---|---|---|---|
| 40 | ~23 | ±20pp | **1.83** | −$2.64 |
| 48 | ~28 | ±18pp | **2.01** | −$3.17 |
| **56** | **~32** | **±17pp** | **2.15** | **−$3.70** |
| 80 | ~47 | ±14pp | 2.59 | −$5.28 |
| 100 | ~58 | ±13pp | 2.90 | −$6.60 |

**Correction to an earlier note of mine:** I previously said 40–60 windows gave
"≥30 residual observations, ~2.9σ". That conflated windows with observations.
The true figure for the requested range is **1.83–2.15σ**. 40–60 windows can
detect a large shift; it cannot settle 81% vs 57% at 3σ. Reaching ~2.9σ needs
~100 windows, which exceeds the $5 campaign budget and needs more days.

**Recommended: 56 windows = 7 blocks of 8, one block per day.** Top of the
requested range, divides evenly by `MAX_WINDOWS`, and its −$3.70 expected cost
sits inside one campaign budget per block with large margin.

## 5. Statistics

`analysis/l5/adverse_selection_stats.py`, no scipy.

- **Headline, window-level.** One observation per window (did the residual land
  on the loser), Wilson score interval, z-tested against both 81% and 57%.
  Independence holds across windows by construction, and this is the **only**
  estimator comparable to those two figures.
- **Fill-level, cluster-robust.** Every uncomplemented fill, with a sandwich SE
  clustered on the window plus the **design effect**, so the cost of
  within-window correlation is visible. Tighter, but explicitly labelled **not
  comparable** to 81%/57%.
- The two agree by construction when each cluster holds one trial, which is
  pinned by test rather than asserted.
- An unresolved window contributes to **neither** denominator. Missing data is
  never a 50/50.

## 6. Authorization scope requested

**Block A (first of seven).** Everything below is unchanged from Campaign E
except `max_windows`.

| | |
|---|---|
| campaign_id | `camp1.0e2bf88d761a4ee0a5b5fac1` |
| document_sha256 | `aa2127615ca9fa45d477e0163512b07b982a8ad5c00a0408aef7eac14d9721fb` |
| bytes | 549 |
| series | `cpc-btc-updown-15m-` |
| windows | 8: 05:00, 05:15, 05:30, 05:45, 06:00, 06:15, 06:30, 06:45 UTC |
| expiry | 2026-11-05T07:15:00Z |

**Minted at authorization time.** `issued_utc` must be in the past and within
4h of its windows, so if this block's 05:00Z start has passed by the time you
authorize, I must mint a fresh document (new nonce, new digest) for the next
8-window block. The digest above is for this exact document.

### Capital at risk — unchanged

k=1, clip 1, maker-only, band 0.30–0.60, pair ceiling 0.96, cutoffs 480/600,
max 2 live orders, $20 total exposure, **$12 max cash deployed**, $1.50
worst-case deficit per market, OPEN 4 / COMPLETE 6 / REPLACE 6 / backstop 16.
No exposure increase, no taker crossing, fidelity mode absent from the image.

### Loss limits and halt conditions

| control | value | scope |
|---|---|---|
| campaign budget | **$5** | per block, owner-gated baseline per nonce |
| daily realized loss halt | **$5** | per UTC day, account-wide settled |
| cumulative loss ceiling | **$20** | **the binding programme-level stop** |
| worst-case deficit | $1.50 | per market |
| `L5_LIVE_HALT` | local env | blocks arming via `try_arm` and `authorize_arming` gate 3 |
| durable halt latch | remote | independent, in-path at `HALT_GATE` |
| authorization expiry | `L5_LIVE_AUTH_NOT_AFTER` | refuses at step 26 |
| watchdog / deadman | unchanged | independent of the trading process |
| reconciliation, exact-owned-ID cancellation | unchanged | startup and per-tick |

Worst case is not the expected case: 7 blocks × $5 daily halt would be $35, so
**the $20 cumulative ceiling stops the programme first**. Expected −$3.70.

### Variables to change at activation

1. `L5_LIVE_AUTH_MAX_WINDOWS`: `1` → `8` *(without this the block runs one window)*
2. `L5_CAMPAIGN_APPROVAL`: the 549 exact bytes above
3. `L5_LIVE_AUTH_NOT_AFTER`: ≥ block expiry
4. `L5_LIVE_HALT`: cleared **last**, only on your explicit go
5. `L5_FIDELITY_MODE`: leave unset

A Railway `redeploy` of an existing deployment **restores that deployment's env
snapshot** and will not pick these up — the variables must be set and then a
fresh deployment created (the watched `DEPLOY_TRIGGER` path).

## 7. Readiness evidence

- **Exact parity**: 80/80 failing ids identical to baseline; 16,367 passed
  (+70 new tests across fill-outcome, statistics and join).
- **Gate-validated, not asserted**: the Block A document was run through the
  real `CA.approval_gate()`. `approved=True`; the 8 enumerated windows
  PERMITTED; `0445z` → `WINDOW_BEFORE_AUTHORIZED_START`; `0700z` and the next
  day → `WINDOW_NOT_IN_AUTHORIZED_SET`; a foreign series →
  `approval_series_not_allowed`. An earlier draft was caught by the same gate
  with `approval_issued_in_the_future` (`age_s −66.9`) and corrected.
- **Join validated against a real settlement**: Campaign E reproduced to the
  cent, −$0.29.
- **Modelled fills excluded by label**, pinned over five label spellings.
- **Current live state** (deployment `3b02b5ce`): `halt_local_env true`,
  `armed false`, `submission_gate blocked NOT_ARMED`, 0 live orders, 0 pending
  terminal, 0 quarantined, `orders_uncertain null`, no market exposure.

## 8. Decision rule, fixed in advance

- **p̂ near 81%** (CI excludes 57%): the replay is right, passive maker fills
  are adversely selected, and this policy family has no edge at our size. Stop.
- **p̂ near 57%** (CI excludes 81%): the replay overstates adverse selection,
  and the restrictions ranked #2 and #3 in the divergence audit (entry cutoff,
  the second opening slot) become worth authorizing — both are cheap, one needs
  no code ceiling change and the other uses an already-authorized slot.
- **CI covers both**: inconclusive at this n. Continue to ~100 windows or stop;
  do not relax anything on an inconclusive result.

No relaxation is authorized by this programme. It changes one number.
