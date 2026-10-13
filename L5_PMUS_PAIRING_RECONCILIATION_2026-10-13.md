# L5 Round 9 — PMUS Sports Pairing: Accounting Reconciliation and Verdict

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `b9bd8449`
**Mode:** read-only. Source + log reads only. No orders, deploys, services, spend, no file deleted, no volume resized, no service restarted. Gate unchanged.

---

## Verdict up front

**STOP dedicated engineering on the PMUS sports sequential-pairing mechanism.** Not because residual losses were priced — they could not be — but because the **gross** paired economics at observed scale are **~$0.50/day equivalent, a 100× shortfall against the $50/day gate**, before fees and before any residual loss. No residual accounting can rescue a 100× gap.

And a correction to my own round-8 headline, which was wrong.

## Priority A — Accounting and denominators reconciled

### A.1–A.2 What each number actually counts

The two figures come from **two different modules measuring two different things**. You were right: they are not in disagreement.

**`virtual_completions_le_1 = 7` and `virtual_first_fills = 3,048`** — `src/pmus_sports_shadow_breadth.py:classify_actions`. These count **replayed policy ACTIONS**, filtered to `decision == "quote"` and `fill_class == "possible"`, bucketed by `relation`:

| Field | Counts |
|---|---|
| `virtual_first_fills` | actions with `relation == "open"` |
| `virtual_extends` | actions with `relation == "extend"` |
| `virtual_completions_le_1` | actions with `relation == "complete"` |

Deduplicated by `(slug, t, relation, target_price)`, accumulated in persistent cohort state across 25,562 selection cycles. **The `le_1` suffix is vestigial.** Its own docstring says so: the earlier version filtered on a non-existent `pair_cost` key and *"the completion count would have read 0 forever"*; `max_pair_cost` is now *"checked as an ASSERTION rather than a filter"*, because `complement_target` prices every completion at the tighter of the ≥1¢ bound (0.99) and the 0.98 cap, so a completion above $1.00 cannot be emitted. Violations are recorded separately in `pair_ceiling_violations`, not dropped.

**`VIRTUAL_MATCHED = 56`, denominator 2,011** — `src/pmus_execution_shadow.py`. These are **proposals followed forward through the tape** to a terminal status:

```
WOULD_POST  -> policy priced and posted it. A proposal, not a fill.
WOULD_FILL  -> a later trade came THROUGH the resting price in the hitting
               direction, inside the follow window.
VIRTUAL_MATCHED -> that fill would have paired inventory.
NO_FILL     -> followed to window end, market never came. A RESULT.
PENDING     -> still inside the window; never collapsed with NO_FILL.
SUPERSEDED_BY_REQUOTE -> excluded from the completion rate entirely.
```

Denominator check: 56 + 1,955 = **2,011** exactly, with the 391 `SUPERSEDED` correctly excluded — *"it neither filled nor failed to fill, and counting it either way would be a verdict the evidence does not support."*

### A.3 One explicit cohort — and the withdrawal of my round-8 headline

**I withdraw "0.230% completion / 99.770% unpaired residual."** It divided `relation=="complete"` **action** counts by `relation=="open"` **action** counts, across two different accumulation windows, in a module that counts actions rather than resolved outcomes. It is not a funnel and it is not a completion rate. It should not be cited.

The single defensible cohort is the execution ledger, which has explicit terminal-status discipline:

| Stage | Count | Notes |
|---|---:|---|
| Proposals (total) | 2,402 | |
| Resolved (terminal) | 2,011 | `VIRTUAL_MATCHED + NO_FILL`; excludes 391 `SUPERSEDED` |
| `VIRTUAL_MATCHED` | **56** | would have paired |
| `NO_FILL` | 1,955 | market never came |
| **p(completion \| first fill)** | **2.78%** | the one citable completion rate |
| Exposure | 164.29 h | |
| `risk_returned_units` | 56.0 | |

Reachability (1,956 measured, a near-identical but **not identical** population — 1,956 ≠ 2,011, so it is a third denominator and I do not merge it): `reached_our_price` 63, `never_reached` 1,893, `filled` 55, `reached_but_unfilled` 8.

**And I withdraw "fill mechanics are fine."** You are right: 55/63 is a ratio of *virtual* fills under an upper-bound rule. The module itself states the rule *"is an UPPER BOUND — queue position is unproven"* and that *"quantity is bounded by what actually traded through the price… We cannot be ahead of a queue we cannot see."* Nothing here establishes our allocation.

### A.4 Queue-ahead provenance — your caution was correct, and the module agrees

`median_queue_ahead` comes from `pmus_shadow_proposal_log.queue_position`, reading `state_at_post.book_levels`. It is **side-aware** (a YES buy queues on `bids` beaten by higher prices; a NO buy at *p* occupies *1−p* on `offers` beaten by lower yes-prices) and explicitly *"never estimated from top-of-book: a guess here would answer the owner's sharpest question with a number we invented."*

Its own `QUEUE_NOTE` bounds it further:

> *"the collector's normalizer keeps only bids[0].qty / asks[0].qty … and discards every level behind them. Until the ladder is retained, queue_ahead_qty is computable ONLY when our price IS the touch."*

So: it is **true displayed cumulative better-priced size where the ladder was retained, and null — never guessed — where it was not.** Critically, it answers *"how much better-priced size must clear before anyone reaches us"*, **not** our FIFO position at our own price level. **Queue priority remains unknown**, and the zero values at the $0.995/touch arms mean "our price is the touch," not "we are first in line."

### Bonus correction: `risk_created_units = 0.0` is not a bookkeeping omission

`risk_created_units = sum(risk_created for p in matched)` — summed over **matched proposals only**. It measures residual *created by completions*, and 0.0 correctly reports that completions created none. **Unpaired first-leg residual was never in this field.** My round-8 framing implied an accounting error; there isn't one. The residual is genuinely unpriced, but by absence of a settlement join, not by a zeroed field.

## Priority B — Residual pricing: BLOCKED, and precisely why

`src/pmus_execution_shadow.py` tracks `residual_before`, `residual_after`, `pair_cost` and `matched_after` — but contains **no settlement, outcome, resolution or payout field anywhere**. There is no PMUS sports settlement join in this pipeline, consistent with the gap I flagged in round 7.

Therefore: **first-leg exposures cannot be valued against official settlement from existing evidence.** Completed-pair P&L, unmatched-residual P&L and net cohort P&L are **unavailable**, and I am not substituting a proxy. The exact missing evidence is a PMUS market-resolution record joinable to market slug.

## Priority C — Economic decision, on gross alone

Because B is blocked, I tested whether the decision even depends on it. It does not.

Gross paired profit at observed scale = `risk_returned_units × (1 − median_pair_cost)`, per sport, over its own exposure-hours. No fees, no residual losses, no adverse selection:

| Sport | Resolved | p(completion) | Risk returned | Exposure h | Median pair cost | Gross $ | $/day equiv | Sample sufficient |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| NFL | 1,521 | 2.37% | 36.0 | 124.62 | $0.9167 | $3.00 | **$0.58** | Yes |
| UFC | 312 | 2.56% | 8.0 | 25.44 | $0.9800 | $0.16 | **$0.15** | Yes |
| Boxing | 151 | 4.64% | 7.0 | 12.30 | $0.9700 | $0.21 | **$0.41** | Yes |
| CFB | 27 | 18.52% | 5.0 | 1.94 | $0.8400 | $0.80 | $9.90 | **No** |

**Sufficient-sample sports: $3.37 gross over 162.36 exposure-hours = $0.0207/exposure-hour = $0.50/day equivalent.**
**Excluding the best sufficient sport (NFL): $0.24/day equivalent.**
**Shortfall against the $50/day gate: ~100×.**

CFB is excluded from the headline as you required — it is the best cohort *and* flagged `sufficient_sample: false` on 27 observations.

So: opportunity frequency is real (2,601 markets tracked, 633 distinct markets entering the funnel), execution uncertainty is high (2.78% completion, queue priority unknown), quote depth is q=1-certified only, fill sensitivity is dominated by reachability (96.8% never reached, median gap 2¢, median favourable excursion 0.0¢), and the downside — unpaired residual — is unpriced. **No NFL/UFC/boxing/CFB subgroup survives.** The gap is not a residual-accounting question; it is two orders of magnitude.

**Gate: `< $50/day ⇒ archive`. STOP dedicated engineering on this mechanism.** I did not fit new thresholds, change the pairing horizon, or revise the mechanism to rescue the result.

**ITF / ATP / WTA tennis and cricket remain explicitly UNMEASURED.** They are outside this funnel — it covers NFL, UFC, boxing and CFB only. Their economics must **not** be assumed to match.

## Operational triage — `l5-pmus-observer` disk-full (read-only)

```
OSError: [Errno 28] No space left on device:
  '/state/l5-pmus-obs/windows/cpc-btc-updown-15m-2026-10-13-1630z'
```
Repeating every ~5s, service `9ce058ce-1cbc-423b-80b1-7fd07544f499`, volume `l5-pmus-obs-evidence` (50 GB) at mount `/state/l5-pmus-obs`.

| Question | Finding |
|---|---|
| Is the data unique? | **No.** It collects `cpc-btc-updown-15m` window records — crypto, not sports. |
| Does a healthy collector cover it? | **Yes, partially.** `pmus-ws-evidence-recorder` is live and writing the same market family (1,945 book observations on the `1630z` window alone), on a volume with headroom. Granularity differs (window records vs. book/trade rows), so coverage is overlapping, **not provably identical**. |
| Preservation risk | The process **stays running while persistence fails**, so health signals can look normal while the historical record silently gaps. Existing files are intact; new writes are lost. |
| Sports discovery impact | **None** — it is crypto-focused. Restoring it would not advance sports discovery. |

**No storage action taken**: nothing deleted, no volume resized, no service restarted, no unrelated infrastructure touched. For the infrastructure owner: the data loss is ongoing but overlapping, and the actionable risk is the misleading-health-signal pattern, not the content.

## Next priority

This discovery lane has now characterised every venue available to it: Polymarket (edge exists, not our venue), PMUS BTC 15m (1¢ static spread; sequential case belongs to replication), Kalshi (fee-adverse, 5.8 snapshots/day), Gemini (stopped for resource allocation), PMUS sports pairing (**$0.50/day gross, stopped**).

**I recommend pausing new-venue discovery rather than starting a sixth lane.** The single highest-value remaining measurement is not another venue — it is the one dataset in this repository with *confirmed settled cash flow*: the Polymarket L5 wallet at $1,766/day realised. Everything else has been theoretical. If you want this lane to continue, the question worth asking is which *venue-native, executable* action on a lawful venue most closely reproduces what that wallet actually does — and that question sits at the boundary with the replication session, so it needs your call on ownership rather than my initiative.

**Cost: $0.** All reads.

## Handoff to replication (informational; no change requested)

A gross paired edge is economically meaningless without the probability and cost of the complementary fill. Measured here, on **sports** cohorts: p(completion | first fill) 2.78% over 2,011 resolved proposals; complement never reached in 96.8% of 1,956 measured cases; median gap 2¢ with median favourable excursion 0.0¢; completions concentrate at 13× median flow ($204 vs $15/min); the 300s horizon forgoes 337 later fills (19.5% by 1,800s) at the cost of longer unpaired exposure; displayed better-priced size ahead at a $0.98 pair cost ~274k units, with **FIFO priority unknown**; and unpaired residual unpriced for want of a settlement join. **These are sports cohorts (NFL/UFC/boxing/CFB) and must not be applied as assumptions about BTC 15-minute behaviour.**
