# Cross-service ownership accounting — sports positions in the account ledger

22 Nov 2026. Development only. Nothing deployed, no variable changed, no
ledger touched. The account ledger (`l5-pmus-live`, commit `7c3d69ba`) was
read through its logs only.

## 1. What the account ledger did today (from its logs, 16:49Z)

| Market | Ledger held (at $1) | Booked | Cash result | State now |
|---|---|---|---|---|
| `aec-nba-lac-dal-2026-11-21` | DOWN 5 | 0.00 | +0.20 | refused 3 times, final |
| `aec-cfb-utsa-uab-2026-11-21` | DOWN 5 | −5.00 | +0.15 | refused 3 times, final |
| `atc-sea-bol-udi-2026-11-22-bol` | UP 4 | −4.00 | +0.28 | refused twice, one try left |
| `atc-sea-sas-gen-2026-11-22-gen` | DOWN 4 | −4.00 | −2.68 | corrected by the ledger itself at 16:28Z |

- Day figure: −13.00 at 16:00Z, **−11.68 now**. True: −2.05. Drift now 9.63.
- Lifetime −51.26, daily limit 50, room 38.32, no halt latched.
- All three open refusals have one reason, `VENUE_QTY_NE_LEDGER_QTY`: the
  venue had paired the position (5 and 5, 6 and 6) while the ledger kept a
  one-sided snapshot.

## 2. Why

The ledger already corrects a settled market it did not open, two ways:

1. Exact: the venue's fills equal what the ledger held. This fixed Genoa.
2. Other strategy's ledger: the venue's fills and that strategy's own order
   ledger agree order by order. Built for the BTC 5-minute lane on 21 Nov.

Sports has no entry in the second path and the sports bot published no
order ledger. So a paired sports market can never be corrected.

## 3. The change (two parts, both built and tested)

**Sports bot** — dev `2b2cd0a63`; release candidate `7af13644a` (below).
A read-only document `l5/sports_fills.json` beside the order registry:
every order of the bot that has filled, by venue order id, with side,
quantity and cost, from its durable book. The cost the venue reports is now
recorded per order as it fills.

**Account ledger** — patch `patches/0002-account-ledger-sports-ownership-on-7c3d69ba.patch`.
- Sports is registered as a ledger owner for `aec-` and `atc-` markets, in a
  separate registry used only by the two cost corrections.
- A settled sports market is restated only when every venue fill is an
  order in the sports ledger with the same side, quantity and cost, and no
  fee was charged. The row is then labelled `SPORTS`.
- The result stays in the account-wide day and lifetime totals at its true
  value. No limit changes.
- Off until two variables are set on the account ledger service:
  `L5_SPORTS_FILLS_URL` and `L5_SPORTS_ATTEST_FLOOR_UTC_DAY`. Deploying the
  code alone changes no number.
- Only markets the ledger settled on or after the floor day are in scope.

Anything ambiguous keeps the $1 bound and stays `UNKNOWN`: a fill the sports
ledger does not list (for example a manual trade in the same market), a
missing order, a different quantity, cost or side, a fee, an unreadable or
unreachable feed, a feed that names another strategy.

Unchanged for a sports market: it is still adopted at $1 before settlement,
it still counts in account-wide exposure, its orders are still never
adopted or cancelled, and uncertainty about it is still treated as full
uncertainty.

## 4. Test results

- Today's four markets replayed through the ledger's real correction code:
  −13.00 → −11.68 with nothing set (what production does now), → **−2.05**
  with the feed and floor day set. Bologna −4.00 → +0.28, NBA 0.00 → +0.20,
  CFB −5.00 → +0.15.
- The same replay with the document produced by the real sports code
  (`ownership_drill/`, two processes, two code bases) gives the same
  figures: −13.00, −11.68, −2.05.
- 31 new account-ledger tests and 8 new sports tests pass. With the scope
  rule removed, 10 of the 31 fail (the tests do test it).
- Sports release candidate `7af13644a`: sports suite 1,954 passed / 6
  failed; full 5,090 / 24. Same 6 and 24 as every release today.
- Account ledger full suite: see section 7.

The NBA and CFB fills in the test use the logged totals (5 a side, cost
4.80 and 4.85) with an illustrative split between sides.

## 5. What it does not fix

- **A false loss can still appear for a while.** A paired market is booked
  at the bound at settlement and corrected afterwards. Today the ledger's
  first read came 18 minutes after settlement and retries are 10+ minutes
  apart. The size is the stale snapshot times $1 (4 to 5 contracts today).
  A one-sided position (Genoa) is now priced before settlement instead, so
  it never books a false loss.
- Removing that window means the ledger must stop keeping a one-sided
  snapshot once the venue has paired it. That is a change to the adoption
  code and is not built.
- Genoa was corrected by the exact form before this change, so its row has
  the right number and keeps the label `UNKNOWN`. From here on a one-sided
  sports position is priced before settlement and its row is labelled
  `SPORTS`.
- Rows of the BTC 5-minute lane corrected by the second path are now
  labelled `BTC5M` instead of `UNKNOWN`. Label only; no number moves.
- The trigger-file line in release `7af13644a` carries a time of 19:10Z;
  the commit was made at about 17:10Z. The line is a comment.

## 6. Decisions for the owner

1. **Floor day.** `2026-11-22` restates today's three rows through recorded
   corrections (day −11.68 → −2.05, lifetime −51.26 → −41.63). Each
   correction keeps the row as it was booked. `2026-11-23` or later leaves
   today exactly as booked. This is a ledger change either way it is set,
   so it is the owner's call.
2. **Deployments**, each needing explicit approval of the exact commit:
   the sports release (locked), then the account ledger release, then the
   two variables.

## 7. Account ledger regression

Full suite on `7c3d69ba` + the patch: 21,623 passed, 72 failed, 26 errors.
The 72 failures and 26 errors are the same tests, by name, as the earlier
run of this service's suite kept from before the change.

The patch is not on any pushed code branch. It applies to `7c3d69ba`
(`release/l5-pmus-live-sibling-value-20261121`); a release branch for the
account ledger needs the owner's go-ahead.
