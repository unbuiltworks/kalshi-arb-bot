# Authorization packet: ledger release 9b5f4995a (l5-pmus-live)

27 Nov 2026. **Not deployed.**

## Release

- **SHA:** `9b5f4995ad4f1a58574ffce3862bc4061997d678`, branch
  `release/l5-pmus-live-sports-settle-37afdff9a`.
- **Contents:** code `37afdff9a` (`dev/l5-ledger-sports-settlement`) on the
  running `c111abd67e3de543c6eacb4ddc1f8345006239a5`, plus one
  deploy-trigger line.

| File | Change |
|---|---|
| `src/l5_live_execution.py` | +446 |
| `src/l5_pmus_live_runner.py` | +79 |
| `src/l5_venue_cost_attestation.py` | +112 |
| `tests/test_l5_sports_feed_settlement.py` | +551, 36 tests |

## Regression

Full ledger suite, run sequentially from clean worktrees:

| Commit | Failed | Errors | Passed |
|---|---|---|---|
| `c111abd67` | 72 | 26 | 21,785 |
| `37afdff9a` | 72 | 26 | 21,821 |

- The failing and erroring names are identical; the +36 are the new tests.
- The related ledger, settlement, attestation and unresolved modules pass
  (790).

## Activation

1. Set the service source to `9b5f4995a`; the ledger restarts.
2. Set `L5_SPORTS_SETTLE_FROM_FEED=1`; the ledger restarts again. This can
   be set together with the source change for one restart.

The required scope variables `L5_SPORTS_FILLS_URL` and
`L5_SPORTS_ATTEST_FLOOR_UTC_DAY` are already present on the service (names
confirmed). Every other variable and limit is unchanged.

## Restart behaviour

- The ledger restarts in about 1–2 minutes.
- **Sports bot during the restart:** it reads the account-risk record every
  5 s with a 30 s maximum age, so for about 1–2 minutes it refuses new
  openings and extensions (fail closed). Completions continue; nothing is
  cancelled by this.
- **Ledger state:** the state file on `/data` is unchanged in format; new
  fields are added only when the flag acts.
- **Venue-cost attestation:** the walk restarts from the beginning.
- **Already-retired rows:** untouched. `timleg-karpol` was already restated
  to +0.18 at 02:06.

## Verification after deploy

1. Boot is clean, `L5_LIVE_HB` resumes, the account-risk publication is
   readable, and the sports `ACCOUNT_RISK` check is ok.
2. Day, lifetime and drawdown are unchanged against the pre-deploy readings.
3. On the next bot settlement:
   - `L5_SPORTS_FEED_SETTLEMENT retired=true`;
   - the row carries `SIBLING_FEED_ATTESTED_AT_SETTLEMENT`;
   - `unresolved_cost_basis` count stays 0;
   - account risk stays readable throughout.
4. Later: `sibling_feed_settlements.CONFIRMED` once the venue walk reads the
   market.

## Rollback

- **Fastest:** unset `L5_SPORTS_SETTLE_FROM_FEED` (or set it to `0`). After
  the restart, behaviour equals `c111abd67`.
- **Full:** set the source back to `c111abd67e3de543c6eacb4ddc1f8345006239a5`
  (branch `release/l5-pmus-live-anchor2-c111abd67`).
  - Rows already booked by the new form keep provenance
    `SIBLING_FEED_ATTESTED_AT_SETTLEMENT`.
  - The old code treats them as ordinary non-bound rows: not unresolved, not
    re-attested. No double booking.
- **If any check fails after deploy:** roll back first, then relock sports
  (`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`) if reconciliation fails.

## Risks

- A manual pair netting exactly to zero inside a bot market is not visible
  at settlement. The later venue walk catches it and the row fails closed.
- A residual market whose venue position row is missing falls back to
  today's behaviour (placeholder, unresolved, and openings paused until the
  correction).
- **Not in this release:** the null-drawdown case under order uncertainty
  for sports markets. It needs separate sign-off.

## Authorization line

> Authorized: deploy ledger release 9b5f4995ad4f1a58574ffce3862bc4061997d678 to l5-pmus-live and set L5_SPORTS_SETTLE_FROM_FEED=1; all other settings and limits unchanged.
