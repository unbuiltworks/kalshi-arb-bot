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

## Slice 2 (14:09:07-14:19:09Z)
- 25 ORDER (all 2 contracts, post-only; stages rn1_open, rn1_extend, two_sided_complete), 3 FILL.
- Fills (venue-reported):
  14:09:27.98 Bologna win NO 0.47 x2 (intl mid 0.515 -> fair for NO 0.485, +1.5c at fill)
  14:12:31.21 Genoa win NO 0.66 x2 (intl mid 0.325 -> fair 0.675, +1.5c; +3.5c two minutes later)
  14:14:33.45 Bologna win YES 0.49 x2 (intl mid 0.505, +1.5c at fill; -1.5c 30 s later)
- PAIR_LOCKED 14:14:39: Bologna win, 2 pairs at 0.96, +$0.08.
- Positions 14:19Z: Bologna win 2 YES / 2 NO (paired); Genoa win 0 YES / 2 NO (unpaired, cost $1.32).
  Unmatched $1.32 (caps $3 per market, $6 campaign).
- Venue cash (l5-pmus-live) 320.4238 at 14:18:47Z = 321.6638 + 0.08 (pair) - 1.32 (unpaired leg). Exact.
- Pulls: 7 reactive (3 no_closer_than_1t, 2 guard, 1 jump, 1 yes_closer), 2 guard pulls;
  8 jump pauses. Withheld: refill cooldown 12, depth gate 4, guard 3.
- 13 market_error, all request-budget deferrals. No rejects, no loop errors, no new 429s.
- Watchdog 14:18:48Z: 3 open orders, all owned, no halt. BTC ledger: utc_day_realized -5.00
  (unchanged), would_halt false.
- Safety check: all clear.
- Recorder note: the two per-match recorders stopped at 14:16:01Z (killed, no error); one combined
  recorder restarted 14:20:24Z as a tracked background task. Gap 14:16-14:20Z.

## Slice 3 (14:19:09-14:25:33Z)
- 15 ORDER (2 contracts, post-only; rn1_extend 8, two_sided_complete 7), 1 FILL.
- Fill 14:24:26.53 Bologna win YES 0.45 x2 -> Bologna win now YES 4 / NO 2
  (one pair at 0.96 done; 2 YES unpaired). Completion bid resting: NO 0.50 x2 (pair would be 0.95).
- Genoa win unchanged: NO 2 unpaired (cost 1.32). Completion bid YES 0.26-0.28; a same-side
  extension NO 0.68 x2 has rested since 14:20:57 (a fill would take Genoa unpaired to 2.68).
- Unmatched total 2.26 (bot's figure: Genoa 1.32, Bologna 0.94). Caps 3 / 6. No cap block.
- Venue cash 319.5238 at 14:25:00Z = 320.4238 - 0.90. Exact.
- Pulls: 4 reactive (2 jump, 2 yes_closer_than_1t); 9 jump pauses, all Bologna (0.52 -> 0.44).
- 8 market_error (7 budget deferrals, 1 stale post-only refuse, resubmitted). No rejects, no 429s.
- Watchdog 14:25:26Z: 3 open orders, all owned, no halt.
- BTC ledger: at 14:13:39Z l5-pmus-live adopted NO 2 in each market at the $1 bound (risk floor
  $2.00 each), as expected; utc_day_realized still -5.00; would_halt false. It still shows
  Bologna NO 2 that the venue has netted away.
- Safety check: all clear.

## Slice 4 (14:25:33-14:32:49Z) - Genoa goal; bot stopped new exposure itself
- Genoa win jumped 0.27/0.28 -> 0.53/0.54 (21 ticks), logged 14:28:34Z.
- Fills: 14:28:39 Bologna win YES 0.43 x2 (extension); 14:28:43 Genoa win NO 0.68 x2 - the
  same-side extension that had rested since 14:20:57, filled INTO the goal (NO was worth 0.465:
  -21.5c per contract at the fill). The venue had both fills by ~14:28:07; the bot logged them
  ~30 s later.
- 14:28:33Z PILOT_PNL / INVENTORY_RECONCILE ran inside that lag: venue net 4 / -4 against book
  2 / -2 -> both markets position_unconfirmed, new_openings_block pilot_fills_unconfirmed.
- From 14:29:43Z RN1_QUOTE block = pilot_loss_at_risk_book (campaign worst case -4.36 <= -4):
  no new openings or extensions for the rest of the window; completion bids only.
- Positions 14:32Z: Bologna win YES 6 / NO 2; Genoa win NO 4 (cost 2.68). Unmatched 4.51 (cap 6).
  Spent 6.36 (cap 16). Cash 317.3038 = 321.6638 - 6.36 + 2.00. Exact.

## Supervision failure (this session)
A reconciliation mismatch was a stop condition. The relock (lock=1) was issued by this session at
~14:35Z but the session stalled and Railway received it at 15:20:45Z. The bot ran the full
approved window under its own controls; it was not relocked early and was not monitored
14:33-15:20Z. Nothing exceeded the window, markets or limits.

## 14:32:49-14:50:00Z (from logs, read 15:26Z)
- 14:40:52 FILL Bologna win NO 0.51 x2 (completion; YES was 0.42/0.43, so NO fair 0.575: +6.5c)
- 14:40:53 ORDER Bologna win NO 0.51 x2 (completion)
- 14:44:08 FILL Bologna win NO 0.51 x2 (completion; +8.5c) -> Bologna win YES 6 / NO 6, flat.
- ~14:44:30 Bologna scored (0.41 -> 0.69). The market was already flat.
- Genoa completion bid YES 0.30 x2 never filled (YES 0.54 -> 0.83).
- 14:50:00Z window ended: cancelled_all_own why submission_lock:window_ended, still_open empty;
  every later placement refused (window_ended). No ORDER after 14:44:08Z.

## Campaign totals (venue-reported fills; settlement pending)
- 65 orders, 8 fills, 16 contracts, $8.40 spent (cap $16). Two markets, one per match.
  All 2 contracts, post-only, maker. No taker order.
- Bologna win: 6 YES (2.74) + 6 NO (2.98) = 6 pairs at 0.9533 -> +$0.28, venue-netted, in cash.
- Genoa win: 4 NO, cost $2.68, UNPAIRED. Genoa win trades 0.91 at 15:27Z. Settles +$1.32 if
  Genoa do not win, -$2.68 if they do.
- Venue cash 319.2638 at 15:25Z = 321.6638 - 8.40 + 6.00. Exact. Reconciled, 0 open orders.
- BTC-service ledger (separate, not rebased): utc_day_realized -5.00 unchanged; it holds
  Bologna UP 4 (stale - venue is flat) and Genoa DOWN 4 at its $1 bound; would_halt false.

## 15:20:45Z relock (late) - verified 15:26Z
Deployment 57b21885, commit 08b1a7c93, lock "locked", 0 ORDER / 0 FILL, watchdog open_orders 0.
With the window gone the campaign marker reverted to the stale 01:15Z value, so the bot's meter
now shows both campaigns together (realized 0.63 = 0.35 last night + 0.28 today; 12 fills).
