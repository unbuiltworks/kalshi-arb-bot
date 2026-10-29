# P11: is our configuration suppressing L5 on BTC5m/BTC15m?

> **CORRECTED 2026-09-22T23:35Z after challenge. Three claims in the first
> version of this file were wrong and are struck through below. Read
> `P11_CORRECTIONS.md` first.** The surviving findings are marked. The
> headline claim — "the risk envelope is not what is suppressing L5" — is
> **withdrawn**: the envelope was never *tested*, which is a different
> statement and a much weaker one.

**READ-ONLY DIAGNOSTIC.** No parameter is changed, no threshold is tuned, no
order is placed, nothing is armed, and the pre-registered P10 decoder gate is
untouched and still running. This audit answers "are our settings interfering
with L5 behaviour", not "what is a better backtest".

Run in parallel with the P10 decoder gate at the owner's instruction, because
the short-window crypto hypothesis is to be preserved regardless of how the
aggregate gate lands.

## The headline, before the detail

**~~The risk envelope is not what is suppressing L5. It has never once bound.~~**
**WITHDRAWN. Corrected conclusion: the envelope is UNTESTED AT RELEVANT
INVENTORY DEPTH.** No window in the sample reached four fills, and
`MAX_IMBALANCE` needs four net same-side fills to fire, so the zero below was
guaranteed by construction and carries no information. On L5's own history a
cap of 4 truncates about a third of windows (peak imbalance p50 2.76, p75
5.21; k=4 coverage 66.9%), and in the P2 replay CAP3 already altered the
sequence where CAP4 did not. See `POST_DECODER_EVALUATION_PREREG.md` for the
measurement that would settle it.

Across **151 consecutive live-shadow windows** (2026-09-22 13:59Z–23:19Z,
policy `FROZEN_L5_NATIVE`, deployment `d6a23b9d`), the per-window
`action_census` splits every withheld rung by the constraint that withheld it.
The result:

| constraint | OPEN | EXTEND | COMPLETE | total | windows |
|---|---|---|---|---|---|
| OSCILLATOR (L5 surface, behaviour) | 0 | 0 | 0 | **0** | 0 |
| ENVELOPE_MAX_IMBALANCE (CAP4 imbalance) | 0 | 0 | 0 | **0** | 0 |
| ENVELOPE_MAX_LEG (CAP4 per-leg / spend) | 0 | 0 | 0 | **0** | 0 |
| END_OF_WINDOW (<60s OPEN/EXTEND floor) | 58 | 22 | 0 | **80** | 21 |

4,396 actions were requested. **80 (1.82%) were blocked by any constraint at
all, and every one of them by END_OF_WINDOW.** `policy_vetoed` is 0 in all 151
windows, as it is by construction under `FROZEN_L5_NATIVE`.

So the answer to the question this audit was asked to prioritise — *do
CAP4 / the 12-action budget / one-rung / max-working-orders terminate the
oscillator before it can recycle residual?* — is **no. They never fire.** The
oscillator is starved upstream, at fill acquisition, not terminated downstream
by a cap. A cap cannot truncate a run that never reaches two.

Where the funnel actually narrows:

| | BTC5m | BTC15m | all |
|---|---|---|---|
| windows | 113 | 38 | 151 |
| OPEN requested | 3,090 | 922 | 4,012 |
| EXTEND requested | 168 | 13 | 181 |
| COMPLETE requested | 183 | 20 | 203 |
| OPEN filled | 25 | 2 | 27 |
| EXTEND filled | 6 | 0 | 6 |
| COMPLETE filled | 2 | 0 | 2 |

**35 fills from 4,396 requests — 0.80%.** 125 of 151 windows (82.8%) end with
zero fills. An EXTEND is even *requested* in only 24 of 151 windows (15.9%),
because an EXTEND requires inventory and inventory requires a fill.

Reproduce with `python3 analysis/gemini/audit/p11a_blocked_census.py` over the
transcribed census in `l5f_actions_20260922.tsv` (provenance in its header).

### The one caveat that matters, stated up front

These fills are `SHADOW_LIVE_HYPOTHETICAL`, assigned by `tape_replay.reaches`,
which tests **both** the print's aggressor `type` **and** a mirrored price
(`tp >= 1 - p` for a NO leg). Both are precisely the H1/H2 question P10 exists
to settle. **So the fill counts above are decoder-dependent, and so, at one
remove, is the claim that the caps never bind** — a different decoding would
produce different inventory, and more inventory is the only thing that could
make CAP4 fire. The *direction* is knowable: caps can only start binding if
fills go **up**. At the observed 0.80% they are nowhere near binding.

The blocked/requested counts themselves do not depend on the tape at all.

## Constraint inventory: every setting on the BTC5m/BTC15m live call graph

Provenance vocabulary: **L5 evidence** = measured from L5's own behaviour;
**safety overlay** = ours, to bound loss; **Gemini adaptation** = ours, to fit
this venue; **legacy artifact** = inherited from an earlier experiment and not
supported by evidence for its current use.

| setting | current value | provenance | blocked in 151 windows | preferentially blocks |
|---|---|---|---|---|
| `rung_count` | 1 | **L5 evidence** — 108 Polymarket BTC15m parents, established offset set has 1 rung | 0 | — (shapes surface, never blocks) |
| `tick_offsets` | `[0]` (quote **at** the bid) | **L5 evidence** — 73 of 108 parents (0.68) rested at offset [0] | 0 | — (shapes surface) |
| `both_sides_policy` | `['Up','Down']` | **L5 evidence** — both-outcome share 0.7278 over 2,788 snapshots | 0 | — |
| `refresh_policy` | `STANDING` | **L5 evidence** — opposite-already-present 0.9208 over 101 reversals | 0 | — |
| `minimum_rest_time` | 30.0s | **Gemini adaptation** (`OURS_ADAPTED`) | 0 directly; sets both end-of-window floors | — |
| `TTE_policy` (control) | `[0, 300]` | **LEGACY ARTIFACT** — see below | not counted as a block; withholds the whole surface | **all three, on BTC15m** |
| `TTE_policy` (V2 arm) | `[0, 900]` | Correction, matches L5's measured [0, 900] | — | — |
| `max_residual` (engine) | 3.00 frozen → scaled `0.04 × k` | **Safety overlay**, `OURS_ADAPTED` | 0 | would hit EXTEND first |
| `MAX_SAME_SIDE_RUN` / CAP4 | 4 | **Safety overlay**, sized from L5's run distribution (modal 2.9, run-4+ OOS-validated) | 0 | EXTEND |
| `max_total_spend_usd` / `max_committed_usd` | $4.00 | **Safety overlay**, owner-authorized 2026-09-21 | 0 | OPEN then EXTEND |
| `max_residual_usd` | $4.00 | **Safety overlay** | 0 | EXTEND |
| `MAX_ACTIONS` | 12 | **Safety overlay** — "enough to see a run, a suppression and a flip" | 0 | whichever comes 13th |
| `max_working_orders` | 2 | **Safety overlay**, deliberately not widened with CAP4 | 0 | — |
| `max_concurrent_markets` | 1 | **Safety overlay** | n/a | — |
| `action_size` (shadow) | 0.01 = venue `quantityMinimum` | **Gemini adaptation** | 0 | — |
| `action_size` (q=1 live) | 1 | **Safety overlay**, with all bounds scaled by `k = q/0.01` so decisions are invariant | 0 | — |
| `NO_NEW_OPEN_EXTEND_TTE_S` | 60.0 = 2 × rest | **Safety overlay** derived from the lane's own rest time | **80** | **OPEN 58 / EXTEND 22 / COMPLETE 0** |
| `WITHDRAW_ALL_TTE_S` | 30.0 = 1 × rest | **Safety overlay** | withdraws, not counted as a hold | all |
| `DEADMAN_S` | 20.0 | **Safety overlay** | 0 observed | all |
| `STALE_ORDER_S` (cancel cadence) | 30.0 | **Gemini adaptation** = lane `minimum_rest_time` | 0 | — |
| `MIN_ENTRY_TTE_S` | 90.0 = 60 + 30 | **Safety overlay** | scheduling only | — |
| entry band BTC5m | (240, 280) | **Gemini adaptation** | scheduling only | — |
| entry band BTC15m | (840, 880) | Corrected to follow [0,900] | scheduling only | — |
| `maker_only` | `True` | **Gemini adaptation**; no taker cross is ever used to flatten | 0 | — |
| crossed-book refusal | refuse cycle | **Gemini adaptation** — found 2026-09-19, 2 of 86 cycles published a bid above its own ask | not a hold | all |
| `inventory_price_skew` / `size_skew` | 0 / ×1 | `OURS_ADAPTED`, deliberately inert | 0 | — |
| pair-cost ceiling | **none on this path** | — | — | — |
| spread gate | **none on this path** | — | — | — |
| cooldown | **none on this path** | — | — | — |

Three entries deserve emphasis because they run against the intuition the audit
was commissioned on:

* **One rung per outcome at offset [0] is L5 evidence, not our overlay.** It is
  the single best-supported parameter in the set (73 of 108 parents). It is not
  a candidate for removal on suppression grounds.
* **Every dollar cap, the action budget and the working-order cap are inert.**
  Not "rarely binding" — literally zero holds in 151 windows.
* **The only overlay doing any blocking is the end-of-window floor**, and its
  design intent is visible in the data: it blocked 58 OPEN and 22 EXTEND and
  **zero COMPLETE**, which is exactly what `WINDOW_COMPLETE_ONLY` is specified
  to do. It suppresses residual creation and never suppresses residual
  reduction.

## The legacy artifact, and what removing it is already measured to do

`L5_ADAPTED_GEMINI_CRYPTO.TTE_policy = {'quote_between_tte_s': [0, 300]}` is
`OURS_ADAPTED` — never measured from L5 — and **300 is the length of a Gemini
BTC5m contract.** It was chosen for the 5-minute cohort and then inherited
unchanged when the same lane was bound to BTC15m. On a 5-minute contract it
never binds. **On a 15-minute contract it means the control quotes only the
final third of the market.** L5's own measured TTE policy is [0, 900].

This is the clearest legacy artifact on the call graph, and it is already being
counterfactualled prospectively by `L5_ADAPTED_GEMINI_BTC15M_V2`, which differs
from the control in **exactly one field**. Cumulative hypothetical counters at
2026-09-22T23:14:40Z, 94 BTC15m windows:

| BTC15m arm | band | first fills | COMPLETE fills | windows ending matched |
|---|---|---|---|---|
| control (frozen) | [0, 300] | 5 | 0 | 0 |
| `L5_ADAPTED_GEMINI_BTC15M_V2` | [0, 900] | **31** | **4** | **4** |

Correcting one inherited constant takes BTC15m from 5 first fills to 31, and
from zero completions to four. That is a **6.2× change in participation from a
number that was never a finding about L5 at all.**

The same shape appears on placement, via `EXEC_A_PLUS_B` (one tick better,
refused if it would reach the ask; plus keeping a rung that still holds its
place when the touch moves by at most a tick). It changes no decision Model A
makes — not side, not size, not whether to quote — only how the order carrying
that decision is placed:

| arm | first fills | COMPLETE fills | windows ending matched |
|---|---|---|---|
| BTC5m control | 47 | 6 | 6 |
| BTC5m `EXEC_A_PLUS_B` | **155** | **52** | **46** |
| BTC15m control | 5 | 0 | 0 |
| BTC15m `EXEC_A_PLUS_B` | **25** | **11** | **9** |

**These are hypothetical shadow counters, not venue state and not economics.**
`windows_ending_matched` counts windows, not pairs; the venue position
representation is UNRESOLVED; nothing here is submitted, filled, settled,
credited or realized, and no cash movement is attributed to any of it. They are
also decoder-dependent in the same way the fill counts above are.

## What this means for the two families, separately

**BTC5m.** The caps are inert; the binding constraint is that a rung resting at
the bid in a 0.240-wide book is not reached. 25 OPEN fills in 113 windows.

**BTC15m.** Worse on fills (2 OPEN fills in 38 windows, 0.22% of requests
against BTC5m's 0.81%) and **better on decodability** (14.9% ambiguous against
BTC5m's 27.1%, median book 0.080 against 0.240 — see `P10_STATUS.md`). Those
two facts are the same fact seen twice: a narrow book is easy to decode and
hard to be filled from at the touch. But BTC15m's control is *also* the family
carrying the inherited [0, 300] band, and lifting it is measured to raise first
fills 6.2× and produce the first completions the family has recorded. **BTC15m
is not a lane that failed; it is a lane that has been quoting one third of its
market because of a constant borrowed from a different contract length.**

## What is NOT concluded here, and why

The user asked for a one-at-a-time counterfactual replay of each restriction
measuring matched P&L, residual P&L, completion rate, two-sided fill rate,
imbalance, actions/window, effective pair cost and rebate-qualifying maker
volume. Two honest limits:

1. **For every cap in the envelope the counterfactual is exactly zero, by
   construction rather than by simulation.** A constraint that blocked 0 of
   4,396 actions changes no fill, no P&L component and no pair cost when it is
   removed. Simulating it would add noise, not information. Only END_OF_WINDOW
   (80 holds) has a non-trivial counterfactual, and it is the one overlay whose
   removal would manufacture residual with no time left to complement it —
   which is the leg with measured negative OOS expectancy.
2. **Every P&L-valued counterfactual runs through `reaches`, which reads the
   aggressor side and the mirrored price.** Until the P10 decoder gate settles
   which of H1/H2 the tape speaks, a matched-P&L or pair-cost number from this
   path carries the same −46/+47 bound P10-D established. Producing one now
   would be exactly the provisional-number-presented-as-a-finding this
   programme has already had to withdraw twice.

So the P&L half of the replay is **deferred to the decoder gate, not
abandoned**, and the participation half — which does not need the tape — is
reported above.

## Bottom line

Our settings **are** interfering with L5, but not through the controls the
audit expected. The dollar caps, action budget, working-order cap, one-rung
restriction and q=1 are all inert. The interference is:

1. an inherited BTC5m-length quoting window applied to BTC15m (legacy artifact,
   measured 6.2× effect on participation);
2. passive placement at the touch with no improvement (Gemini adaptation,
   measured 3.3× effect on BTC5m first fills);

and the dominant constraint is neither of those: it is that **99.2% of
requested actions never become a fill at all.**
