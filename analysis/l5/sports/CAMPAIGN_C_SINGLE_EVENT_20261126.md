# Campaign C — one event at a time (for authorization)

26 Nov 2026, 02:05 UTC. Nothing deployed or changed.

## Release

`1b496656c45c2b38664166a912ee518073fc781a`, branch
`release/pmus-sports-rn1-1b496656c` (code `fb1248d17`). It is `b8693dcf4`
(running now) plus two changes:

1. **Full-ladder reward scoring** (dev/chatgpt-rn1-ladder-score,
   `8137a82eb`, reviewed and integrated):
   - the score walks multiple owned levels per side;
   - a two-sided full-ladder estimate;
   - a same-snapshot marginal reward for adding one order.

   The resting-reward measurement now scores every owned level older than
   the book lag (5 s), each capped at the size the public book shows. It
   used to score only the nearest bid. Marginal reward is a measurement and
   authorizes nothing; it carries no fill cost.
2. **Hold through a dip** (`PMUS_RN1_NET_HOLD_S`, off by default). When the
   fresh net estimate turns not positive, nothing new is placed (no
   opening, extension or replenishment). Bids already resting are kept for
   up to the hold, so they keep their queue position, unless one would now
   set its side's price. Any other block (pause, kickoff, late, cap, lock)
   cancels as before. Completions are unaffected.

Tests: 5,719 passed, the same 24 unrelated failures
(`regression/full_suite_ladder_score.txt`). 8 new tests are in
`tests/test_pmus_rn1_ladder_score.py`, and one existing test was rewritten
for the full-ladder estimator.

ChatGPT's branch had no tests of its own. The added ones check:
- a single level equals the old score;
- two owned levels each count once;
- marginal = after − before;
- only mature, visible levels count;
- the whole level that reaches the target size is walked, as in the venue
  rule.

## Settings: FINAL TERMS, with these changes only

| Setting | Campaign B | Campaign C | Why |
|---|---|---|---|
| `PMUS_TWO_SIDED_MAX_MARKETS` | 2 | **1** | one event at a time |
| `PMUS_TWO_SIDED_MAX_TRACKED` | 4 | **6** | tracking only: markets carried with inventory (Garcar–Borvic, `slomyk`, `jaskub`) occupy tracking places, and at 4 no new event could be selected. Not a capital limit |
| `PMUS_RN1_NET_HOLD_S` | 0 | **300** | keep resting bids and queue priority through a temporary dip |
| `PMUS_RN1_RELEASE_UNQUALIFIED_S` | 120 | **600** | do not drop an event on a short fluctuation; drop it after 10 minutes without a positive estimate |
| `PMUS_RN1_LADDER_LEVELS` | 2 | **3** | three price levels per side, two ticks apart, 1–2 contracts each; the caps still bound the ladder |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | window B | `window:<start>/<start+2h>` | |

**Unchanged:**
- **Capital and loss limits:** account limit $20, loss stop $10 (counting
  resting bids), daily halt $10, sports lane $20, unpaired $5 per market and
  $10 for the campaign, $12 per market, spend $60, at-risk $20.
- **Quoting:** sizes 1/2, net ranking, maker-only, both slates (pre-match
  and in play), band 0.20–0.80, minimum touch 0, competitions as in
  FINAL TERMS.
- **Off:** esports and crypto.
- **Protections:** watchdog, reconciliation, kickoff pull.

## How one event is managed

- **Selection:** the best positive-net market by net per dollar, and only
  one at a time. A market holding inventory from earlier keeps its
  completion bids but takes no slot.
- **Both sides:** a YES and a NO ladder (3 levels). Each fill clears the
  estimate, re-prices it on the new book, and replenishes after the
  10-second refill cooldown while the estimate is positive.
- **Inventory:** repeated same-side buys up to the $5 unpaired cap for the
  market. The fit rule sizes down to fit. Completions are allowed but not
  required.
- **Before and after the start:** opening bids are withdrawn 5 minutes
  before the start (kickoff pull) and resume in play once the match is
  confirmed live and calm. The event keeps its slot through kickoff (a
  kickoff block does not release it).
- **Rotation:** to another event only after 10 minutes without a positive
  estimate, or when a block (late, ended, cap, lock) applies.

## Measured during the run (existing logs)

Fill prices and markouts (`FILL_MARKOUT`, by role), order persistence
(ORDER intervals), qualifying vs resting contract-seconds (heartbeat),
capital in use vs room (heartbeat), inventory and pairs (`PILOT_PNL`),
settlement, and paid rewards (`REWARD_CHECK`; rows post 1–7 days later).

## Sequence

1. After Campaign B's window ends at 02:10 UTC, deploy `1b496656c`
   (completions continue across the restart).
2. Verify the deployed SHA, settings, latch, reconciliation and watchdog.
3. Set the Campaign C settings with the window. If any check fails,
   relock and roll back to `b8693dcf4`.

## Authorization line

> Authorized: after 02:10 UTC deploy 1b496656c45c2b38664166a912ee518073fc781a,
> then single-event campaign C for two hours, ____ to ____ UTC 26 Nov, with
> FINAL TERMS plus exactly the changes in CAMPAIGN_C_SINGLE_EVENT_20261126.md
> (one market, tracking 6, hold 300 s, release 600 s, three ladder levels);
> all capital and loss limits unchanged; esports off; crypto unchanged and
> disarmed.
