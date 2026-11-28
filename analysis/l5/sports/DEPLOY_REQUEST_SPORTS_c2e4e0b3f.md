# Sports release candidate `c2e4e0b3f`: account-limit correction

24 Nov 2026, 19:10 UTC. **Not approved. Not deployed.** Production runs
`12d5d3daf` on the plain lock.

| | |
|---|---|
| Release commit | `c2e4e0b3fe9cef43690f104cbaef636b03422e74` |
| Branch | `release/pmus-sports-rn1-c2e4e0b3f` |
| Code commit | `6dc706c4434d3d3f3b3eb4e4d4eb399560d4ef6c` on `dev/pmus-rn1-replica` |
| Changed against the deployed build | `src/pmus_sports_two_sided.py` (+133 −8), the deploy trigger note, two test files |
| Rollback | `release/pmus-sports-rn1-12d5d3daf` |

## The defect (live, 18:27–18:50 UTC)

The account-limit verdict is cached for 10 s. Its capacity counted only
the bids resting when it was computed, and each new bid was checked
against that same capacity, so several opening bids inside one cache
period each passed. Account figure plus resting opening bids read 3.06,
3.36, 3.17, 3.34, 3.33 (repeatedly) and 3.23 against 3.00. The next
reading refused, every opening bid was pulled, and the cycle repeated
about every 25 s. The account figure itself peaked at 2.2644.

## The correction

1. A placement is judged on a verdict computed at that moment from the
   last ledger snapshot and the book as they are now (no network call).
2. Every submission is reserved before it is sent. On success the book
   holds the order and the reservation ends. A send that fails without a
   definite answer keeps its reservation until an orphan sweep has run
   over an account read at least 5 s newer than the send; a definite
   venue rejection releases at once. Reservations count in the resting
   worst case, the lane exposure and the final gate, and are saved, so
   they survive a restart.
3. A bid must leave one cent of room. A bid that exactly fits would put
   the reading at the limit, and a reading at the limit pulls every
   opening bid: that was the place-and-cancel cycle.

Nothing else changes: no limit, no default, no quoting rule. Two new
settings with defaults (`PMUS_TWO_SIDED_ACCOUNT_RISK_ROOM_MARGIN_USD`
0.01, `PMUS_TWO_SIDED_RESERVATION_HOLD_S` 5); none needs setting.

## Regression results

- New file `tests/test_pmus_sports_account_reservation.py`: **26 pass**,
  all at the production 10 s cache period. **21 of the 26 fail on the
  deployed code.** (The earlier tests of this limit set the cache period
  to zero, which is why they could not see the defect.)
- Covered: the exact 18:34 burst and the other live bursts; twelve
  markets against one reading; simultaneous submissions from eight
  threads; an order raised while another is on the wire; an unanswered
  send (held, counted, released only by a newer reconcile, not by a
  failed sweep, kept across a restart); a definite rejection; concurrent
  fills before the ledger has seen them; a fill between two placements;
  partial fills; stale, missing and unreadable account readings,
  including one that goes stale inside the cache period; twenty rounds
  of the live loop with no pull and no re-placement; completions never
  refused; the other ceilings unchanged; limit off = unchanged.
- Full suite: **5,555 passed, 24 failed** — the same 24 as before the
  change (none in this code).

## Not proven by tests

Behaviour at the venue: how often a send fails without an answer, and
whether holding its reservation for one reconcile cycle (about 30 s)
noticeably reduces quoting. The first locked run and the first window
will show it (`ORDER_RESERVATION_HELD` / `_RELEASED` log lines).

## Next-stage configuration (separate authorization; NOT part of this deployment)

| Setting | Service | Now | Proposed |
|---|---|---|---|
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` (account limit) | `l5-pmus-live` (restarts the ledger) | 3 | 5 |
| `PMUS_RN1_TOTAL_SPEND_USD` (campaign spend) | `pmus-spring-l5-probe` | 16 | 30 |
| Order size, markets, unpaired caps, per-market spend, loss stop, daily halt | | | unchanged |

## Superseding candidate `766a965ba` (24 Nov, 19:43 UTC) — not approved, not deployed

`c2e4e0b3f` was deployed locked at 19:27 UTC with the owner's approval and is
running. One gap remained: an unanswered order that the venue accepted and
filled in full is in no open-order list, so a sweep of open orders alone
could release its reservation before the book held the position.

| | |
|---|---|
| Release commit | `766a965ba03330dcb1dbb05db517b59d8131e33a` |
| Branch | `release/pmus-sports-rn1-766a965ba` |
| Code commit | `6a9a3e20a4252bc3c3c606cac0d3bfb51cc6446a` |
| Change against `c2e4e0b3f` | `src/pmus_sports_two_sided.py` (+70 −13), one test file |
| Rollback | `release/pmus-sports-rn1-c2e4e0b3f` |

An uncertain reservation is now released only when ALL hold: an orphan sweep
has run over a fresh account read at least 5 s newer than the send; every
order the venue shows open in that market is in the book; a COMPLETE
positions walk shows the venue's position in that market equal to the
book's. Anything unread, incomplete or unequal holds it. No ledger reading
takes part. If the venue holds a position the book does not, the
reservation stays and the existing position-mismatch latch stops the bot.

Tests: 32 in the reservation file (7 new, including the fully filled
unanswered order, a newer reading alone, an incomplete walk, a venue order
the book does not know). Full suite 5,561 passed, 24 failed (the same 24).
