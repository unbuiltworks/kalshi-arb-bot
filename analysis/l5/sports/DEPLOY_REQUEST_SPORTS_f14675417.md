# Release candidate `f14675417` — sports bot, for approval (not deployed)

25 Nov 2026, 02:55 UTC. Prepared during the 00:30–03:30 UTC campaign;
production was not touched.

| | |
|---|---|
| Release SHA | `f146754177a04fe57b08fac9015c339ff7032a55` |
| Branch | `release/pmus-sports-rn1-f14675417` |
| Code commit | `3caf5dfc63225482108d38c423a6616f247e850e` (`dev/pmus-rn1-replica`) |
| Running now | `766a965ba03330dcb1dbb05db517b59d8131e33a` (code `6a9a3e20a`) |
| Rollback | `release/pmus-sports-rn1-766a965ba`, then `-c2e4e0b3f` |
| Service | `pmus-spring-l5-probe` only. Ledger and watchdog unchanged. |

## Regression comparison

| | Passed | Failed |
|---|---|---|
| Running build line at `4c25d9150` (previous full run) | 5,622 | 24 |
| Candidate `3caf5dfc6` | 5,640 | 24 |

The 24 failures are the same 24 test ids in both runs (compared by name;
all in files unrelated to the sports bot, e.g. `test_spread_scanner`,
`test_orderbook`, `test_deploy_fallthrough_guard`). 18 tests added by the
last commit, 108 since the running build; none removed or skipped.

## What changes with no setting touched (deploying the build alone)

1. **A send that never left the bot reserves nothing.** An order deferred
   by the bot's own request budget, or that failed before the request was
   made, releases its capital reservation at once. Every submission whose
   fate is uncertain keeps its reservation until venue orders and
   positions reconcile, exactly as now. (Live 25 Nov: $3.76 of capacity
   was held by sends that were never transmitted.)
2. **Order reconciliation and the heartbeat continue during the hourly
   audit.** The audit itself is unchanged. (Live: about 10 minutes with no
   heartbeat and delayed reconciliation each hour.)
3. **Reward qualification is measured in every pricing mode.** Measurement
   only; it decides nothing. (Live: with the fair-value test off, no
   estimate was recorded.)

## What is added but off (each needs its own approval to switch on)

| Setting | Effect when on | Default |
|---|---|---|
| `PMUS_TWO_SIDED_FILL_LIST_POLL=1` | Fills detected from one open-order list read per pass; an order's own status is read whenever the list leaves any doubt, at least once a minute, and on every cancel. Cuts signed requests from about 0.84 to about 0.3 a second at 4 markets. | 0 |
| `PMUS_RN1_SLOTS_COUNT_INVENTORY=0` | A market holding only carried inventory no longer occupies a quoting slot. Its exposure still counts against every cap. | 1 |
| `PMUS_RN1_RANK_SHARE=1` | Markets ranked by the modelled reward of our own quote per dollar committed, not by pool size. Ranking only. | 0 |
| `PMUS_RN1_JOIN_TOUCH=1` | Bids rest at the best bid while the spread is at least two ticks, the mid moved at most 1c in 30 s and there was no jump in 60 s; one tick back otherwise. Post-only, never crossing; pair-cost limit and all caps apply. | 0 |
| `PMUS_RN1_BALANCED_IS_OPEN=1` | A position with equal YES and NO is a fresh entry, not an extension: no ceiling at the lifetime average price + 2c. The heavier side keeps that ceiling. | 0 |
| `PMUS_RN1_REFILL_COOLDOWN_S=5`, `10` or `15` | Already an existing setting (60 now); no code change. Per side, from that side's last fill; completions never wait. | 60 |
| `PMUS_TWO_SIDED_ESPORTS_UNKNOWN_FIRST_MAP=1` | See Dota 2 below. | 0 |

## Batched fill detection — what was verified

By test, with the list on (46 tests in `tests/test_pmus_sports_fill_list.py`):

- **Exact fill accounting.** A fill is booked only from the order's own
  status, never from the list. The same venue events give the same
  position, cost and filled quantities with the list on and off. A fill
  is booked once, including one that raced a cancel.
- **Cancellation safety.** Every cancel is by exact own id and is followed
  by a read of that order's own final state, whatever the list said.
- **Reservation integrity.** The list never releases or changes a
  reservation; only reconciliation over a fresh account read does.
- **Doubt falls back to today's behaviour.** A missing row, a row without
  a filled quantity, a changed quantity, any cancel/pending/partial state,
  a list older than 8 s, an order newer than the list, a failed list read:
  each forces the exact read.

Not verified, because it needs the live venue: whether the venue's
open-order rows carry a filled quantity. If they do not, the bot keeps
reading every order exactly and nothing is saved. The build logs the
row's field names once (`fill_list_row_keys`), which settles it on the
first run with the setting on.

## Join-the-best-bid — limits to know before switching on

- Depth is one value for both sides and for completions, so a completing
  bid joins as well (still capped by the pair-cost limit).
- On a two-tick spread the pair-cost limit (0.97) lets only the cheaper
  side join; the dearer side sits one or two ticks back. Both join only at
  three ticks or more.
- A market read for the first time counts as calm (no price history yet).
- When the calm test flips, the bid is replaced one tick back, then
  forward again: more order replacements in a choppy market.
- It has no live evidence. Joining takes fills sooner and takes more of
  the adverse ones; opening fills are where the money was lost so far
  (−3.4c a contract 14–18 Nov).

## Dota 2 / esports blocker — finding

Checked on the venue at 02:44 UTC 25 Nov (a live Valorant match: event
list, event state, market record, market text). The venue publishes a map
score (`"1-1"`) and a period (`"Map 3"`) and no best-of anywhere. The
bot's esports rule refuses any match whose format it cannot read, so every
esports market, Dota 2 included, is refused. It is the safeguard working
as written, not a fault.

Option in this build, off: allow an unknown-format match only before any
map is won (score 0-0, first map). That is the one state a best-of-3 rule
also allows. **Risk:** a best-of-1 is decided by that first map, and a
best-of-2 can end level, which the venue settles at $0.50; neither can be
told from the first map of a best-of-3. My recommendation is to leave
esports refused unless the owner accepts that risk for named competitions.

## Overlapping restrictions — verified

- **Legacy $4.50 cap** (`PMUS_TWO_SIDED_TOTAL_USD`): it does gate RN1
  opening bids in code, but production sets it to 60, so it cannot bind
  under the $6 campaign unpaired cap. Not a constraint today. It would
  bind at the 4.50 default if the variable were ever removed.
- Heartbeat `caps` show legacy constants: `per_market` 1.50 and
  `pair_cap` 0.98 are not used on the RN1 path; `total` and `markets` are.
- Duplicated by design: the final gate repeats the per-market, pair-cost
  and campaign caps on worst-case committed exposure.
- Both bounding the same quantity: the at-risk budget (16) and the lane
  cap (20) each cap unpaired cost plus resting orders. The smaller binds.
- Residual $3 a market × markets against campaign unpaired $6: from three
  markets up, the campaign cap is the one that binds.

## Not in this build

- The double-count of a fill for a few seconds after the ledger catches up
  (conservative; pulls and re-places opening bids once per fill).
- Completing-bid replacement churn when the fair-value test is on.
- Signed emergency stop: still unverified; needs the owner to sign a
  marker.

## Approval needed

Nothing is deployed. To deploy: explicit approval naming the release SHA,
locked. To switch on any setting in the table: separate approval.
