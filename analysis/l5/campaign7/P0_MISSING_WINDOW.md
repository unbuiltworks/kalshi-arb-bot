# P0: 2045z produced no decisions. It is not a defect.

## What was ruled out

`L5_LIVE_WINDOW` is emitted at a window's CLOSE, summarising the window that
just ended, so 2045z's record is the one timestamped 21:00:02. It reports:

    market        cpc-btc-updown-15m-2026-10-28-2045z
    decisions     {arm_refused_window_not_usable: 18,
                   PMUS_TRANSLATION:NOT_CURRENT_WINDOW: 1}
    observability subscribed_before_open TRUE, first_book_offset_s 0.3,
                  book_updates 7122, trades_seen 500

- **Not a feed problem.** The book arrived 0.3 s after open and streamed 7,122
  updates; `subscribed_before_open` is true.
- **Not a process problem.** Heartbeats ran continuously through the silence
  (20:52:31 … 20:59:50) and the shadow controllers kept deciding.
- **Not a scheduling problem.** The window armed at 20:45:00.785 and spent its
  claim; `arm_refused_window_not_usable: 18` is the ordinary post-entry-cutoff
  arming refusal and appears in every window (17-21 times each).
- **Not market selection.** The slug is correct throughout.

## The actual cause

Every window that traded carries `first_l5_action_offset_s`:

| window | first_l5_action_offset_s | traded |
|---|---|---|
| 1945z | **absent** | no |
| 2000z | 8.0 | yes |
| 2015z | -7.0 | yes |
| 2030z | 1.0 | yes |
| **2045z** | **absent** | **no** |
| 2100z | 5.0 | yes |
| 2115z | 23.0 | yes |

Five for five, and two for two the other way. **The engine is
intent-triggered**: it evaluates a probe when it observes an L5 action to
replace (`replaced_l5_intent` appears in 190 of the campaign's decisions).
No observed L5 action in the window means nothing to replicate, so no
decisions, no quotes, no orders.

A window in which L5 did nothing and we therefore did nothing is CORRECT
behaviour. It is not a fault and must not halt: halting on a quiet L5 window
would convert a normal state into an outage.

## What this says about throughput, which matters more

Our submission rate is bounded above by OBSERVED L5 ACTIONS, not by the order
slot and not by capital. Two measurements:

- the campaign's ingest admitted **122** L5 actions across eight windows,
  about **15 per window**;
- the L5 action we react to is already stale: latency median **19.6 s**
  (p25 9.9, p75 26.4, max 37.3) over 387 decisions.

The historical tape has L5 at **176.6 fills per window**. Either we observe
roughly 9% of his actions, or his current rate is far below the August
sample. THESE ARE DIFFERENT PROBLEMS WITH DIFFERENT FIXES and the engine's
own counters cannot separate them; resolving it requires comparing L5's
public activity for these specific windows against what the ingest admitted.

## Consequence for P1

`MAX_LIVE_ORDERS=2` raises the ceiling only when two intents are live at
once. It cannot manufacture intents. The 24 blocked episodes are real, so
the change still has a target, but its upside is bounded by intent arrival
and should not be modelled as if the slot were the only limit.

## Recommended P0 change (small, read-only)

Emit an explicit `NO_L5_INTENT_OBSERVED` attribution on a window that arms
and sees no L5 action, so a silent window is visibly accounted for rather
than looking like a defect -- which is exactly how it was first read here.
No behaviour change, no halt.
