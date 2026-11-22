# Serie A live campaign, 22 Nov 2026 - running log

Owner authorization: explicit, in session, ~12:46Z; reconfirmed ~13:15Z ("proceed from
14:05-14:50 UTC ... arm only if every safety check passes ... If anything fails, halt and
relock without waiting for me"). Owner unavailable 14:00-16:00Z.

Scope: window 2026-11-22T14:05:00Z - 14:50:00Z; SHA 08b1a7c9356ad6e090eb74dbff31542ea13f91bb;
Serie A outcome markets of Bologna-Udinese and Sassuolo-Genoa, one per match; $16 / $6
campaign, $8 / $3 per market, $4 campaign stop, $5 daily halt, 2 contracts, 2 markets,
maker-only, pair cap 0.97, 10-contract depth, international guard, watchdog, auto-relock.

## 13:23Z final preflight - PASS
Commit 08b1a7c93 (deployment 76b5ffb1), locked, 0 ORDER / 0 FILL, reconciled (12:27Z line),
PILOT_PNL ok / new_openings_block null / realized 0.35, no loss stop, cash 321.6638,
watchdog healthy, l5-pmus-live would_halt false (utc_day_realized -5.00 known placeholder),
BTC lanes not submitting, no foreign orders. One HTTP 429 on an account read at 13:18Z.
Target markets 13:23Z: all in-band outcomes open, two-sided, internationally matched.

## 13:25Z armed (one set-variables call, one restart)
PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-22T14:05:00Z/2026-11-22T14:50:00Z
PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS=cfb,cs2,nba,nfl,wta,sea

## 13:30Z armed-window verification - PASS
Deployment 776b7cd4 SUCCESS 13:25:06Z, commitHash 08b1a7c9356ad6e090eb74dbff31542ea13f91bb.
Old container cancelled_all_own, still_open empty.
SUBMISSION_LOCK_STATE: new_exposure_locked true, completions_locked true, why
window_not_started, start 14:05:00Z, end 14:50:00Z, complete_grace_s 0, campaign_since 14:05:00Z.
Boot: rn1 qty 2, 16 / 6 / 8 / 3, pair cap 0.97, daily 5, markets 2, max_taker_qty 0,
min_touch_qty 10, esports fail closed, intl_three_way [sea], one_market_per_game.
Allowlist as run: cfb, cs2, nba, nfl, sea, wta. ORDER 0, FILL 0.
INVENTORY_RECONCILE 13:28:31Z reconciled, 0 open, 0 unmatched.
PILOT_PNL 13:28:32Z: ok, new_openings_block null, loss stop 4, campaign markets 0,
realized 0, cash baseline 321.6638.
Watchdog: one sweep at 13:25:01Z saw the registry unreachable during the container swap
(no halt); OK since; open_orders 0. One HTTP 429 account read at 13:29:39Z.

## 14:05:00Z window opened; slice 1 (14:04:00-14:09:07Z)
- SUBMISSION_LOCK_STATE 14:05:00.170Z: open (why null). Shared halt PERMITTED. Guard and venue
  websockets connected; guard matched 2 markets.
- Markets: atc-sea-bol-udi-2026-11-22-bol and atc-sea-sas-gen-2026-11-22-gen (one per match).
- 20 ORDER (all 2 contracts, rn1_open, post-only), 0 FILL, 9 reactive pulls, 3 guard pulls,
  1 venue-rejected order (no fill), 1 local POST_ONLY_STALE_REFUSE (not sent).
- Pair sums quoted: 0.97 (0.52+0.45, 0.33+0.64, 0.51+0.46).
- Why sides were withheld: depth gate (touch 0.70, 5.70, 0.71, 0.10 contracts), international
  guard "not cheap", jump pauses after price moves.
- Pulls: 6 of 9 were "closer than 1 tick / near cross" - the market came to our bid and the
  bot cancelled rather than be the best bid. That is the mechanism by which it gets no fills.
- 7 market_error: 6 request-budget deferrals on reactive repricing, 1 stale post-only refuse.
  One reactive pull took 5.2 s (4.3 s queued behind the request budget).
- Safety check: no order outside the two matches, none above 2 contracts, no taker stage,
  2 markets, one per match, no watchdog halt (4 open orders, all owned), nothing unconfirmed.
- Recorders started 14:07:35Z (the queued ones had not started); orders before that have no
  book comparison.
