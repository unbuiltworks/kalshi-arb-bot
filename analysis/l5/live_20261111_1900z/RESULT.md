# Result: cpc-btc-updown-15m-2026-11-11-1900z

Live, venue-confirmed. SHA `ff4b5291afc8a272cd5e8ae5e873120a1d4b67d6`.

| | |
|---|---|
| Armed | 18:39:23Z, deployment `66ccd7ce`, all 37 preconditions passed |
| Orders | 7, all post-only maker; no taker; no budget refusal |
| Both sides quoted together | yes, from T+9 s: UP 0.47, DOWN 0.48, 0.30 contracts each |
| First-leg fill | UP 0.30 @ 0.47 (`DNK8WP3G14F0`), commission 0.0000 |
| Completion order | repriced to 0.51 four seconds after the fill; on the book 498 of the 518 s to T+534 |
| Completion fill | none |
| Unwanted inventory | none |
| Venue settlement | DOWN won (`outcomePrices ["0","1"]`) |
| Settled net P&L | **-0.1410** |
| Locks restored | 19:12:16Z, deployment `ef559ff4`, verified |

Why no second leg: the book was 0.49/0.50 when the pair was priced, 0.42/0.43
eight seconds later, 0.08/0.09 by T+292. After the fill the DOWN touch was
never below 0.57, so no pair at 1.00 or less existed. In the last minute the
completion was not re-placed because the touch was outside the engine's price
band (`PRICE_OUT_OF_BAND`); it could not have filled.

Evidence: `data/l5/recent/live_20261111_1900z/window_lines.json` (every
order-related line the armed process logged), pulled by `pull_window.py`.

## Against the 380-window replay (real runner, recovery at 0.98)

`fills380.py`, `depth380.py`. Replay, through-only fills, not realized.

- 354 windows with a first-leg fill; 69% completed; in 13% no pair at 1.00 or
  less was ever quotable afterwards (this window is one of those).
- Completed window +1.5c, stranded window -17.9c: break-even needs 92%.
- Fill speed and the size of the move against us do not separate the two.
- Opening depth 2 / 4 / 6 / 8 ticks: -4.2 / -4.1 / -3.3 / -2.9c per window;
  no depth positive, halves disagree.
