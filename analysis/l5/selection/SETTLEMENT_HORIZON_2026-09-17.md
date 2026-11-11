# Settlement horizon as an eligibility constraint — findings and proposal

Read-only investigation, 2026-09-17T01:55Z. Nothing here is deployed. The live
probe, its mandate, the controller, sizing, freshness gate, maker behaviour and
risk controls are all untouched, and the held tennis position is preserved
exactly as live inventory.

## 0. Correction to my previous report — the premise is wrong

I reported the held position as settling "~2026-10-01" on the strength of
`hours_to_end 334.5`. **That was my error.** `hours_to_end` is derived from
`market.endDate`, which is a contract expiry, not a resolution time. Measured at
01:55Z:

```
aec-itfwo-erishi-jiahan-2026-09-17
  gameStartTime 2026-09-17T01:08:24Z   (the match began 0.79 h before selection)
  endDate       2026-10-01T00:00:00Z   (= gameStartTime + 13.95 d)
  status        MARKET_STATUS_OPEN
```

We did **not** buy into a two-week market. We bought into a live ITF women's
tennis match in its **first set** (`game_proximity 0.20` = period token `S1`),
which will resolve in hours, not weeks. The two-week figure was an artifact of
the field I quoted.

The concern behind the instruction is nonetheless real, and sharper than the
premise — see §4. The selector repeatedly admitted a market whose game had **not
started and does not start for 2.9 days**.

## 1. What Polymarket US actually exposes

From `GET /v1/events?live=true&active=true&closed=false&categories=sports` and
`GET /v1/markets?slug=…` (keyless, GET, read-only).

**Market row:** `gameStartTime`, `endDate`, `startDate`, `status`
(`MARKET_STATUS_OPEN` / `MARKET_STATUS_RESOLVED`), `closed`, `active`,
`ep3Status` (`OPEN` / `EXPIRED`), `ep3SyncedAt`, `outcomePrices`, `outcomes`,
`feeCoefficient`, `marketSides`.

**Event row:** `startTime` / `startDate`, `endDate`, `ended`, `period`, `score`,
`live`, `sportradarGameId`, and `eventState.{type, period, ended, periodScores,
score}`.

## 2. Which of them represent actual resolution timing

| field | forward-looking? | verdict |
|---|---|---|
| `endDate` | no | **Useless.** Fixed per-sport contract expiry |
| `gameStartTime` | **yes** | **Reliable.** Says whether play has begun and when |
| `period` / `eventState.period`, `ended` | yes | **Reliable as position, not as time** |
| `status`, `ep3Status`, `closed` | no | Terminal state, after the fact |
| — | — | **No field anywhere gives an expected resolution time** |

### `endDate` is a fixed expiry, measured

Across **18/18 live sports events** (soccer, cricket, baseball), the offset
`endDate − gameStartTime` was **exactly 14.00 days** — the counter had a single
key. It does not discriminate at all:

```
baseball, Bot 9th  (minutes from resolving)  endDate in 333.2 h
baseball, Bot 4th                            endDate in 334.7 h
```

In our own admitted set, a **1st-inning** game read 335.77–335.94 h and a
**9th-inning** game read 333.13 h. The ~2.7 h spread is just the difference in
first pitch, not game progress.

`endDate` is also not stable. On a market that resolved mid-investigation
(`aec-setkamemd-ghifio-meraur-2026-09-17`, table tennis) it had been **rewritten
backwards to before `gameStartTime`**:

```
gameStartTime 2026-09-17T01:30:00Z   endDate 2026-09-16T21:55:09Z  (offset −0.15 d)
status MARKET_STATUS_RESOLVED        ep3Status EXPIRED
```

`endDate` there equals `ep3SyncedAt` minus exactly 4 h, which looks like an
Eastern-time value labelled as UTC.

This confirms `src/l5_sports_clock_study.py`, which already recorded remaining
tradeable life as *"NOT SERVED by the venue … Neither is a resolution time."*
This is fresh measurement agreeing with prior work, not a new discovery.

**Caution for gate design:** the expiry convention is *per sport*, not global.
Table-tennis markets carried a **+2 d** expiry (`hours_to_end` 47.6–47.9) while
everything else carried +14 d. A threshold on `endDate` would therefore select
table tennis — the right answer for entirely the wrong reason, and it would break
the moment the venue changed a convention. **Do not build the gate on `endDate`.**

## 3. Distribution across markets the selector is admitting

29 admitted rows, 13 distinct markets, 6 live selection cycles (01:32–01:52Z).

**By `hours_to_end` (endDate):**

| bucket | rows | share |
|---|---|---|
| ~333–336 h (+14 d expiry) | 21 | 72.4% |
| ~405–406 h (+14 d expiry) | 6 | 20.7% |
| ~47.6–47.9 h (+2 d expiry) | 2 | 6.9% |

Minimum ever observed: **47.63 h**. The floor never approaches same-day even
for a game in its final innings.

**By `game_proximity` (venue-served position — the honest signal):**

| bucket | rows | share |
|---|---|---|
| game **not started / unknown** | 6 | **20.7%** |
| live, early (<0.25) | 12 | 41.4% |
| live, mid (0.25–0.5) | 2 | 6.9% |
| live, late (0.5–0.75) | 7 | 24.1% |
| live, ending (>0.75) | 2 | 6.9% |

All six "not started" rows are the same market.

## 4. Why the selector admitted what it did — root cause

Two separate facts, and only the second is a defect.

**(a) The tennis market was a correct admission.** Live, first set, resolving in
hours. Nothing to fix.

**(b) There is no horizon eligibility constraint anywhere.** In
`rank_candidates` (`src/pmus_l5_continuous_session.py:709-735`) admission is
decided by exactly one call:

```python
reason = entry.strict_evidence_reason(slug, ev, now=now)
...
if reason is None:  admitted.append(item)
else:               refused.append({**item, "reason": reason})
```

`strict_evidence_reason` tests evidence quality. It has no horizon clause.
`hours_to_end` and `horizon_basis` are computed and logged but **never gated
on** — disclosure only. Horizon reaches behaviour solely through the
`residual_duration` scoring component, weight **0.5 of 12.5 = 4.0%**.

The consequence is concrete. `aec-cfb-lsu-miss-2026-09-19` was admitted in
**every one of the six cycles**, ranked as high as **3rd**:

```
gameStartTime 2026-09-19T23:30:00Z   -> kickoff in 69.6 h
game_proximity null                  -> not in the live feed at all
residual_duration 0.0                -> costs it only 4% of its score
```

A 4% penalty is not a constraint. Had the ranking above it thinned by two
markets, the probe would have opened inventory in a game that cannot begin, let
alone settle, for **2.9 days**. That is the real horizon blindness.

## 5. Proposal — the smallest fail-closed horizon gate

### Where

One clause at the single existing admission point in `rank_candidates`. Nowhere
else.

```python
if reason is None:
    reason = horizon_reason(progress_by_slug.get(slug))
```

### What

```python
HORIZON_CLASS = "venue_execution_adaptation:settlement_horizon"

def horizon_reason(progress):
    """None = admit. A string = refuse, with the venue's own words for why.

    Fail closed: we admit only when the venue positively tells us the game is
    under way and we recognise where in it we are. Not-started, already-ended,
    unrecognised period token and feed-unavailable are all UNKNOWN horizon,
    and unknown is refused -- never admitted on the assumption it is short."""
    if not progress:
        return "horizon:not_in_live_feed"          # not started, ended, or no feed
    prox = progress.get("game_proximity")
    if prox is None:
        return "horizon:unknown_progress:%s" % progress.get("proximity_basis")
    if progress.get("ended"):
        return "horizon:event_ended"
    return None
```

### Why this is the narrowest possible version

- **No new request and no new field.** `progress_by_slug` is already fetched at
  line 704 and already passed to `pair_score` at line 725. The gate reads what is
  in hand.
- **No new venue semantics.** It reuses `pmus_event_progress`, which is already
  disciplined about reporting only tokens the venue serves and never
  manufacturing a settlement time.
- **It does not touch `endDate`**, so it cannot be broken by an expiry-convention
  change.
- **It is one boolean on the admitted set.** It removes candidates. It cannot
  add one, cannot move a price, cannot change a size.

### What it changes in practice

On the measured 29 rows it would have refused the 6 "not started" rows (20.7%,
all the CFB market) and admitted the 23 live ones. The tennis market we actually
traded stays admitted. Same-day sport — baseball, tennis, table tennis, soccer —
stays admitted.

### Known cost, stated plainly

`not_in_live_feed` conflates *not started*, *already ended* and *feed
unavailable*. Fail-closed is correct for all three, but a total outage of the
live-events endpoint would refuse **every** market and idle the probe. That is
safe (it cannot lose money) and visible (`progress_diag` already carries
`error` and `live_events`), but it is an availability cost and should be
recorded as one. Refusals must be logged with counts, exactly as the freshness
gate's skips are, so the opportunity removed is measurable.

Esports would also be refused today: `"Map 2"` yields
`unknown_series_length` because the venue does not serve series length. That is
the fail-closed rule working as intended, not a bug to patch around.

### What this is NOT

- **Not an L5_FIDELITY change.** Classify as **L5_ADAPTED** —
  execution/venue-selection. A horizon refusal must be recorded like a freshness
  skip and **must not be scored as a model WAIT**, preserving
  `MODEL WANTED ACTION ≠ EXECUTION COULD SAFELY SUBMIT ACTION`.
- **Not a change to trading behaviour.** No price aggressiveness, rest duration,
  sizing, pair ceiling, ACT/WAIT, k, or controller logic.
- **Not a ranking change.** `residual_duration` keeps its 4% weight and
  `pair_score` keeps ordering the survivors. The gate decides eligibility only.
- **Not deployed.** This is a proposal for the next authorization.

### Deliberately excluded

A per-sport expected-duration ceiling ("admit only if the game should end within
N hours") would need a duration the venue does not serve, and the game clock
cannot supply it — `pmus_event_progress` records that `elapsed` counts **down**
and is time remaining in the period. Adding that would mean manufacturing a
settlement estimate, which is the exact failure this document is about.

## 6. Held position — preserved, and marked honestly

`aec-itfwo-erishi-jiahan-2026-09-17`, 0.36 shares NO at avg 0.44, cost
**$0.1584**. Untouched: no force-close, no manual hedge, no manufactured
COMPLETE.

At 01:55Z the venue quoted `outcomes ["Eri Shimizu","Jiangxue HAN"]` at
`["0.9400","0.07"]`. We hold the Shimizu-NO side, quoted near **0.07** against
our 0.44 average, so the position is currently marked well against us. The match
is still `MARKET_STATUS_OPEN` and unresolved.

Strictly: this is a **mark-to-market** figure and is **not realized P&L** — not a
loss, not a gain, until the venue resolves the market and the settlement audit
confirms it. Worst case if NO settles at zero is the **$0.1584 already spent**,
which is inside the $5.00 market cap, the $15.00 campaign cap and the $6.00 daily
stop. No halt condition, and no reason to abandon the position.
