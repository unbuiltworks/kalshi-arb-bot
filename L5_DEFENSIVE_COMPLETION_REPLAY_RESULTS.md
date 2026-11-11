# Inventory-aware completion — actual replay results and GO/NO-GO

Tool: `src/l5_strand_policy_replay.py` (existing). Data:
`data/l5_strand_replay/residual_books_20261025.jsonl` — **290 authenticated
residual frames**, 8 episodes, the 20261025 PMUS campaign. Production
**disarmed**. No new service, no money-path change.

**Anchor reproduced:** the no-policy baseline returns **+0.1256**, matching
the realized campaign net exactly. A replay that cannot reproduce the past
is not evidence about the future.

Per-episode baseline: 0030z@0.55 +0.2898 · 0045z@0.50 +0.0830 ·
0100z@0.20 +0.0800 · 0100z@0.75 +0.0344 · 0115z@0.40 +0.1246 ·
0115z@0.48 +0.4704 · **0130z@0.62 −1.1594** · 0145z@0.35 +0.2028

## The candidate already exists — do not build it

`src/l5_defensive_completion.py` implements the requested policy, and it is
disabled by flag (`L5_DEFENSIVE_COMPLETION=0`,
`defensive_execution_enabled: false`):

| requested | existing |
|---|---|
| evaluate taker / maker / partial / hold | `alternatives(qty, first_leg_cost, exit_price, fee, …)` |
| separate ceiling for completion | `DEFENSIVE_PAIR_CEILING = 1.20` vs `MAKER_COMPLETE_CEILING = 0.96` |
| bounded loss | `DEFENSIVE_LOSS_BUDGET_USD = 2.00` |
| time remaining | `DEFENSIVE_ARM_S 60`, `DEFENSIVE_MIN_EXECUTION_S 15`, `binding_terminal_s`, `arm_window_open` |
| no double completion | **`cancel_confirmed(result, coid)`** — cancel by exact coid, confirm terminal |
| dwell before crossing | `DEFENSIVE_DWELL_S = 30` |

The replay's `cross_decision` likewise already enforces all six gates asked
for: depth from the engine's own walk, frame freshness (<=0.5 s), economics
strictly positive after the published taker fee priced on a VWAP **sized to
the whole residual**, the pair ceiling, an optional horizon, and EV-of-
waiting against the maker quote the engine would actually place. `execute`
fills an **IOC limit that does not chase** at the first frame at or after
decision + λ.

## Result 1 — completion capped at 0.96: REJECTED (reproduced)

| λ (s) | total | vs baseline |
|---|---:|---:|
| 0.000 *(physically impossible)* | +1.1190 | +0.9934 |
| **0.500** | **−0.1570** | −0.2826 |
| 1.000 / 2.000 / 5.000 | −0.1570 | −0.2826 |
| **8.818** *(observed)* | **−0.0445** | −0.1701 |

Worse than doing nothing at every realistic latency.

**Your premise about 0130z, corrected in one detail that changes the
conclusion.** The 0.96-capped policy did **not** miss the completion — it
*decided to cross* at 0130z and the order **did not fill**. Its limit was
0.3300 against a book that had moved; the episode still reads
`STRANDED, settled worthless` at −1.1594. The completable pair was not at
0.95–0.96. It was at **0.9656–0.9752 — just above the cap.** The cap is what
caused the strand.

## Result 2 — completion capped at the venue break-even 1.00: POSITIVE

Separately measured, as asked. The economics gate still requires net > 0
after the taker fee; only the ceiling moves, and only for a **risk-reducing**
completion. Justified by the venue schedule: PMUS has **no maker-fee term**,
a matched pair redeems exactly $1.00, so any pair under $1.00 is net
positive.

| λ (s) | total | vs baseline |
|---|---:|---:|
| 0.000 | +0.9849 | +0.8593 |
| **0.500** | **+1.1199** | **+0.9943** |
| 1.000 / 2.000 | +1.1199 | +0.9943 |
| **8.818** *(observed)* | **+1.1492** | **+1.0236** |

**Robust across latency, and positive at every realistic λ** — the opposite
of Result 1. Per-episode at λ=8.818:

| window | own | fills | total | baseline | delta | detail |
|---|---|---:|---:|---:|---:|---|
| 0030z | 0.55 | 1 | +0.1290 | +0.2898 | **−0.1608** | crossed at pair 0.9012; pre-empted a better maker fill |
| 0100z | 0.20 | 0 | +0.0800 | +0.0800 | 0.0000 | maker completion at 0.96 kept |
| 0100z | 0.75 | 1 | +0.0251 | +0.0344 | **−0.0093** | crossed at 0.9416 |
| 0115z | 0.40 | 0 | +0.1246 | +0.1246 | 0.0000 | decided, `BOOK_LEFT_THE_LIMIT`, fell through |
| 0115z | 0.48 | 0 | +0.4704 | +0.4704 | 0.0000 | no decision |
| **0130z** | **0.62** | **1** | **+0.0343** | **−1.1594** | **+1.1937** | **crossed at pair 0.9656, limit 0.3600, FILLED 1.87** |
| 0145z | 0.35 | 0 | +0.2028 | +0.2028 | 0.0000 | decided, `BOOK_LEFT_THE_LIMIT`, fell through |

**The mechanism: paying one to two extra ticks buys fill certainty.** The
0.96 limit of 0.3300 was unreachable; the 0.9656 limit of 0.3600 filled at
both λ=0.5 and λ=8.818.

## Result 3 — the EV-of-waiting gate, and why no horizon

Pc sweep at λ=8.818, cap 0.96: harmful at Pc 0.50–0.90 (−0.1701), +0.1163 at
0.95, exactly baseline at 0.98 with **0 crossings** — harmful below 0.95,
inert above. Unchanged from the prior finding.

Horizon sweep at cap 1.00 — **a cliff, not a dial**:

| horizon | λ=0.5 | λ=8.818 | fills | 0130z |
|---|---:|---:|---:|---:|
| none | +1.1199 | +1.1492 | 3 | +0.0343 |
| **300 s** | **+1.3111** | **+1.2028** | 2 | +0.0261 |
| **240 s** | −0.2632 | **+0.1256** | **0** | **−1.1594** |
| 180 / 120 / 60 / 30 s | +0.0244 … +0.1256 | +0.1256 | 0 | −1.1594 |

300 s is the best number in the whole study; 240 s makes the rule **totally
inert**. A 60-second parameter change flips it from best to nothing. **Do not
ship a horizon** — that is the overfit signature, and the no-horizon version
is already positive.

## Comparison against current ARGMAX

Not run on identical windows, and I will not imply otherwise: ARGMAX is
evaluated on 38 live BTC15m shadow windows (2026-10-29), the replay on the 8
recorded residual episodes of 20261025. They share no windows, so the only
honest comparison is directional:

| | ARGMAX (38 shadow windows) | this candidate (8 replay episodes) |
|---|---|---|
| total settled net | **−3.1400** (−0.0826/window) | **+1.1492** (+0.1437/episode) |
| pair surplus | +10.1837 | positive, unchanged by the rule |
| residual settlement | **−13.3237** | the rule converts the one strand to +0.0343 |
| worst single loss | not isolated | **−1.1594 → +0.0343** |
| completed pairs | 38 units / 11 windows | 3 completions forced, 4 maker fills preserved |
| opportunities missed | complement unreachable, median queue 316× need | 2 maker fills pre-empted (−0.1701) |

ARGMAX's deficit is **−$13.32 of residual settlement** against **+$10.18** of
pair surplus. This candidate attacks exactly that term, which is the reason
to take it seriously.

## Honest limits

- **The entire gain is one episode.** 0130z is n=1. The recurring **cost** is
  real and recurs: 2 of 8 episodes lose −0.1701 to pre-emption.
- So the rule is **insurance**: pay ~0.17 per campaign to avoid a 1.16
  strand. It pays if strands are more frequent than ~15%. L5's own
  out-of-sample curve puts strand probability at own 0.62 at **23.8%** (408
  windows) and the whole 2,216-window set at **27.3% unpaired**, so the
  insurance is favourably priced on out-of-sample evidence — which is the
  only reason I am not calling n=1 disqualifying.
- **Two episodes were spared by luck, not design.** 0115z-A and 0145z
  decided to cross and were saved only by `BOOK_LEFT_THE_LIMIT`. At λ=0 more
  pre-emption succeeds and the total falls to +0.9849. A faster venue makes
  this rule worse, not better.
- **It requires leaving MAKER_ONLY.** Defensive completion crosses;
  `maker_only_forbids_defensive_crossing` is currently true. That is a
  strategy-scope change and needs new authorization.
- **This is not the near-$1 tolerance `CLAUDE.md` locks.** That lock concerns
  *opening* pairs near $1 on incentive-era rebate economics. This is
  *completing an existing residual* — risk reduction — and the codebase
  already draws that line itself: `DEFENSIVE_PAIR_CEILING 1.20` versus
  `MAKER_COMPLETE_CEILING 0.96`. The proposal sits at 1.00, well inside the
  existing defensive ceiling and below it.

## GO / NO-GO

**GO — prepare a bounded live validation of the EXISTING defensive
completion path, risk-reducing only, ceiling at 1.00.**

Rationale: it is positive at every realistic latency (unlike the 0.96
version), the mechanism is understood and physical, it attacks ARGMAX's
dominant loss term, the implementation already exists with its own bounded
loss budget, and the insurance is favourably priced against a 23.8%
out-of-sample strand rate.

**NO-GO on:** any horizon parameter; raising the *opening* pair ceiling;
taker completion at the 0.96 ceiling; any increase in size, exposure or
concurrency.

### The smallest bounded live validation

Explicit authorization required — this changes execution scope:

1. **New approval document** granting `execution_mode` beyond MAKER_ONLY,
   scoped to `DEFENSIVE_COMPLETION_ONLY`. `ac855ce3…` does not cover it and
   must not be reused. New experiment id.
2. **Config, all existing knobs:** `L5_DEFENSIVE_COMPLETION=1`,
   defensive pair ceiling **1.00** (below the 1.20 code constant),
   `DEFENSIVE_LOSS_BUDGET_USD` stays **2.00**, no horizon.
3. **Unchanged limits:** exposure $20, `max_live_orders` 1, cash $12.00,
   worst-case deficit $3.00, residual cap 3, daily loss $5, account-wide
   $18.6325, halt latch, campaign budget. All upstream of the crossing.
4. **No double completion:** use the existing `cancel_confirmed(result,
   coid)` — reconcile outstanding maker orders, cancel by exact coid, confirm
   terminal state, re-read inventory, then cross. Already implemented.
5. **No new opening while unmatched exposure exceeds budget:** already
   enforced by the residual cap and `exempt_open` logic; assert it in a test
   rather than add a rule.
6. **Automatic halt:** existing halt latch plus
   `DEFENSIVE_LOSS_BUDGET_USD 2.00`; stop after the **first** defensive
   crossing and reconcile before permitting a second.
7. **Scope:** 4 windows, one market, crossing permitted **only** when
   unmatched inventory exists and the completion is risk-reducing.

**Acceptance:** at least one defensive completion that is venue-confirmed
filled and settled, with pair cost < 1.00 and realized net positive on that
window, and no strand worse than the $2.00 defensive budget.

I have not changed the money path, requested authorization, or enabled
anything.
