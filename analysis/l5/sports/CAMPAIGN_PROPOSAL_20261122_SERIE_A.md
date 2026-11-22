# Proposed live sports test - Serie A first half, 22 Nov 2026 (NOT APPROVED, NOT ARMED)

Prepared 12:45Z. Production: `08b1a7c9356ad6e090eb74dbff31542ea13f91bb`, locked.

## Proposal
- Window: **2026-11-22T14:05:00Z to 14:50:00Z** (45 minutes; first half of two matches).
- Sport: Serie A only. Markets the bot may select (in price band now, deep, mapped):
  Bologna-Udinese: Bologna win 0.55/0.56 (28,233 x 58,301), draw 0.25/0.26 (36,655 x 11,436)
  Sassuolo-Genoa: Sassuolo win 0.34/0.35 (15,371 x 49,354), draw 0.29/0.30 (28,729 x 4,899),
  Genoa win 0.35/0.36 (31,881 x 45,617)
  International quotes within 1c on all five. One market per match -> at most two markets.
- Variables (one change, one restart, by 13:50Z; nothing else):
  PMUS_TWO_SIDED_SUBMISSION_LOCK = window:2026-11-22T14:05:00Z/2026-11-22T14:50:00Z
  PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS = cfb,cs2,nba,nfl,wta,sea
- Limits unchanged: $16 / $6 campaign, $8 / $3 per market, $4 campaign stop, $5 daily halt,
  2 contracts, 2 markets, maker-only (taker qty 0), pair cap 0.97, 10-contract depth,
  international guard required, watchdog, automatic relock at 14:50Z (cancel all own orders).

## Why soccer and not ATP for this window
- ATP at 12:37Z: 8 live, 0 eligible (5 lopsided, the rest with an empty touch: 0.01-0.13
  contracts on one side). At 11:57Z two were eligible. Books flip between deep and depleted.
- Serie A 14:00 matches: five in-band outcome markets with 4,900-58,000 contracts at each
  touch on our venue and 1c spreads on both venues.

## What was tested (read-only / no credentials)
- Discovery funnel 12:37Z (74 live events, 126 markets) with atp and sea treated as allowed:
  1 eligible (Juventus win), 0 ATP.
- Engine dry run 12:39Z (`engine_dryrun.py`): the deployed commit's engine with allowlist
  + sea, real public data, a fake order sink and no venue credentials. It selected
  atc-sea-juv-ven-2026-11-22-juv, resolved the international market, and would have placed
  one order: NO 0.46 x 2 (rn1_open, post-only). YES was withheld by the depth gate (0.27
  contracts at the bid).
- Production open gate 12:27Z: every sport's historical EV is negative, so no sport outside
  the allowlist can open. With `sea` added, Serie A is the only allowlisted sport live at
  14:05Z (NFL 18:00Z, NBA 20:30Z; CS2 blocked by the esports rule).

## Juventus-Venezia recording (36 min, 12:02-12:38Z) - what it does and does not show
- Juventus win: both touches >= 10 contracts 82% of the time; our mid within 1c of
  international 73% of the time; 48,869 contracts traded; price 0.59 -> 0.52.
- Draw: both touches >= 10 only 47% of the time. Venezia win: out of band throughout.
- Replay of the bot's quoting rule counting only fills where the market trades through a
  resting bid (the adverse kind): 0 fills with the guard, 1 losing unpaired fill without it.
  The guard removed the adverse fill. The replay cannot see ordinary fills, so it gives NO
  evidence that pairs complete profitably. Only live fills can.

## Remaining concerns
1. No fill, pairing or residual evidence exists for soccer. This test is how we get it.
2. A goal moves these prices 10-30c at once. A resting bid can be filled into the move;
   the unpaired leg is then bounded by the $3 per-market / $6 campaign unmatched caps and
   the $4 stop, and after 14:50Z it rides to full time (no completion after the deadline).
3. First live use of: the window lock, the depth gate, the soccer resolver, soccer markets
   on the order path. All are tested; none has traded.
4. The bot's own history shows negative value per opened market in every sport it has
   traded (-$0.10 to -$0.33), from before the price guard. Expect a small loss as the base case.
