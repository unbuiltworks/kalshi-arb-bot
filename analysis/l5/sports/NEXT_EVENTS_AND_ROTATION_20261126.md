# Next events and continuous rotation (26 Nov 2026, 22:21 UTC)

Sports `5cbca4d3c` (deployment `d03af928`, boot 22:20:39): pin null,
lock `1`, edge gate off. Nothing is armed.

## Ranking (the bot's own net estimate, theoretical, not a payment)

- 70 markets have a paying programme. The bot estimated 6 in its first
  passes.
- The estimate is reward per hour minus expected fill cost per hour,
  using the deployed table: fill rate 3.0 at the best bid, 1.5 behind;
  fill loss 5c / 3.5c, and 8c / 6c for doubles.

| Rank | Market | Phase | Best placement | Reward/h | Fill cost/h | Net/h | Price YES/NO |
|---|---|---|---|---|---|---|---|
| 1 | `aec-atp-lucsil-diedur-2026-11-25` (ATP singles) | live | best bid, 2 | 5.93 | 0.60 | **+5.33** (level 1: +1.70) | 0.39 / 0.54 |
| 2 | `aec-setkamemd-urevad-chevic-2026-11-26` | — | one behind, 1 | 0.049 | 0.30 | −0.25 | 0.58 / 0.39 |
| 3 | `aec-atp-masima-davsan-2026-11-26` | — | one behind, 1 | 0.001 | 0.30 | −0.30 | 0.49 / 0.48 |
| 4 | `aec-setkamemd-dupale-plevit-2026-11-26` | — | one behind, 1 | 0.001 | 0.30 | −0.30 | 0.76 / 0.21 |
| 5 | `aec-atpdb-bayldofancut-falcksuarez-2026-11-26` | — | one behind, 1 | 0.001 | 0.48 | −0.48 | 0.59 / 0.38 |
| 6 | `aec-atpdb-puttersweeny-becrofshepp-2026-11-26` | — | one behind, 1 | 0.001 | 0.48 | −0.48 | 0.44 / 0.53 |

**Rank 1, `lucsil-diedur`.**
- Programme `atp_wta_moneyline_live_20261018`: a $184 pool per 3-hour
  live period (0.017 $/s) with a target size of 19,000.
- The bot SELECTED it at 22:21:05.
- The book was moving (bid 0.25–0.30, ask 0.31–0.32), so it is not
  joining the best bid for now.
- ATP singles has an international price (the paper cross-venue monitor
  lists this market).
- Measured ATP opening loss is 6.3c at 300 s, against the 5c touch
  setting. The net estimate is therefore somewhat optimistic, but not
  enough to change its sign at this reward level.
- **Remaining duration:** unknown. It is a live singles match (the slug
  date 25 Nov is the scheduled day in another time zone), typically up to
  1–2 hours more. The window should be sized so the late cut-off, not the
  lock, ends quoting.

Ranks 2–6 are negative: their reward does not cover the expected fill cost.
The selection rule correctly would not pick them.

## Continuous rotation: no new code needed

The existing controller already rotates when it is not pinned:

- `MAX_MARKETS=1` keeps one market at a time. It is chosen by the same
  ranking, which requires a fresh positive net estimate.
- A market is left after 600 s without a positive estimate
  (`PMUS_RN1_RELEASE_UNQUALIFIED_S`), at the late cut-off, or when it
  closes or settles. The next positive market is then selected.
  - Covered by `tests/test_pmus_sports_reselect.py`: a released market is
    selected again when eligible, and everything gone then resumes by
    itself.
  - Shown live on 25 Nov (Campaign B).
- **Limits per window are unchanged and apply across every market in it:**
  - campaign loss stop $10 (counting resting bids) and daily halt $10;
  - spend $60; unpaired $5 a market and $10 total; $12 a market;
    at-risk $20;
  - lane $20 and the account $20 drawdown;
  - ledger daily $50 and cumulative $75; loss room now $12.69;
  - maker-only; watchdog.
- **Bounded by the authorized window:** the lock opens only inside the
  named window, followed by a completions-only grace. Nothing relaunches
  after the window, a crash or a halt.

**To run continuously:** leave `PMUS_RN1_PIN` empty and set
`PMUS_TWO_SIDED_SUBMISSION_LOCK=window:<start>/<end>`, with every limit
unchanged. That needs the owner's authorization of the window.

**Note:** the account's cumulative loss room ($12.69) is close to the
campaign's $10 stop. A longer window does not raise any limit.
