# Bounded live-validation packet: expanded trading-first, 29 Nov 2026

**This is a request for explicit authorization. Nothing in it has been done.** Production is locked and unchanged on `8f7cb2c36`.

## 1. Release

| | |
|---|---|
| Release SHA | **`9e6c2039fd1b31f281b5cbd1545b036b8604651b`** |
| Branch | `release/pmus-sports-expanded-9e6c2039f` (code `046a63775`, dev `dev/l5-integrated-expanded`) |
| Contains | `8f7cb2c36` (deployed, locked), trading-first v2 `7133117e1`, PR #253 head `36c0e030c` |
| Regression | **6,224 passed, 26 failed. The failure set is identical to the `ac1c1bbaf` baseline.** 98 tests were added since `ac1c1bbaf` |
| Image | Built from scratch; 63/63 `src` modules import, including the new `pmus_sports_line_fair`. `Dockerfile` and `railway.json` updated |
| Flags | All new flags default off. With them off, behaviour is that of `8f7cb2c36` |

### PR #253 review (critical)

| Commit | Verdict |
|---|---|
| `bac2468`, `63f3318` (duplicate or empty outcome names) | Correct; kept |
| `74fd187`, `36c0e03` (non-finite odds) | Correct; kept |
| **`9408c40`** (non-moneyline types refused) | **Defect.** It refused every real venue moneyline (`basketball_team_full_game_winner`, `hockey_team_full_game_winner`, …) and failed 3 existing sharp tests, which would have switched off the working NBA Pinnacle fair. Replaced by `is_moneyline()`: V2 `MONEYLINE` plus full-game or match winner types only. 16 tests |
| `01cceda`, `b5e0290` (spread and total pricing) | Correct as pure functions; kept. They were unconnected: they had no venue mapping, and the venue's V2 field labels **team totals** as `TOTAL`. Adapter added (below) |
| `3a33dd5`, `a613a6c` (player props) | Kept as a pure function and **not connected**. See §3 |

**Integration added:**
- `line_spec` and `line_market_fair` map full-game spreads and game totals only. Exact identity comes from the sibling moneyline: same event slug, teams and start time. Pinnacle only. The line must agree between the slug and the description, and only half-point lines are accepted.
- Shadow pricing from odds already fetched (`PMUS_SHARP_LINES`). Event line markets come from a background refresher, so the trading loop never blocks on it.
- `PMUS_SHARP_MARKETS`, `PMUS_RN1_LINE_MARKETS`.
- `PMUS_COMBO_INDEPENDENT`: cross-game combo legs priced from the independent fair. Same-game combos are still refused.

## 2. Production, verified 13:13–13:15Z

| Check | State |
|---|---|
| Deployment | `8f7cb2c36`, deployment `26b47654` |
| Submission lock | `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`, `why: locked` (new exposure and completions) |
| Watchdog | Sweep every 20 s, `open_orders: 0`, all 3 registries OK, cancel enabled |
| Inventory | 0 venue positions, 0 mismatches, $0 unpaired, $0 resting |
| Last campaign | Settled. **Realized net −$1.521** (85d) |
| Rewards, confirmed (PAID) | Account $24.09; campaign $0. Pending $0.86 (account); skipped $4.00 |
| Rebates | $0 |
| Lifetime realized | −$68.1207, against the $75 ceiling: **room $6.8793** |
| Account drawdown | $7.64 of $20: room $12.36 |
| UTC-day realized | −$1.55 (resets at 00:00Z, before the window) |

## 3. Market universe and pricing coverage (28 Nov)

**Venue catalog.** There are 50,749 non-futures sports markets.
- The event listing that discovery reads appears to embed only each event's primary (moneyline) market. Spreads, totals and props are reachable per event (`/v1/events?slug=…`, e.g. 423 markets for one NBA game).
- Today's selection concentrates on Setka, UTR and ATP doubles because of the owner scope (`STRICT_SPORTS` with `allow_sports` = atp, atpdb, setkamemd, setkameua, utr, wta, wtadb), reward-only ranking, and reward pools with small target sizes. Coverage is not the reason.

| Type (venue) | Example sports | Independent fair available | In this campaign |
|---|---|---|---|
| Two-way moneyline | NBA, NHL, CBB, CFB, NFL, tennis, table tennis | International book: CBB, CFB, NHL, NBA (not tennis or table tennis). Pinnacle: NBA, NHL pre-game | **LIVE: NBA, NHL, CBB, CFB** |
| Full-game spread / game total | NFL, CFB, NBA, CBB, NHL | Pinnacle main line only, with the integrated adapter. Requires `h2h,spreads,totals` = 3 calls per refresh | Not live (no tape evidence; quota) |
| Period, half or quarter lines; team totals | NFL, CFB, NBA, NHL, soccer | None mapped (would need provider period/alternate keys) | No |
| Player props | NBA, NFL, NHL, soccer | Pure helper only. **Settlement differs:** if the player doesn't play, the venue settles at the last fair price while books void | No |
| Drawable soccer | All soccer leagues | None (3-way) | No |
| Combos (RFQ) | Cross-game and same-game | Cross-game legs only, with independent leg fairs. Same-game has no joint model | No (combo maker stays in shadow) |
| ETH, SOL, XRP, BTC up/down | — | 0 of 68 windows carry a reward programme (#250) | No |

**Window opportunities.** Moneylines starting 21:15–03:15Z, so either live or pre-game during the window:

| Sport | Moneylines | Internationally listed | With reward programme |
|---|---|---|---|
| NBA | 5 | 5 | 5 |
| NHL | 12 | 7 | 12 |
| CBB | 18 | 17 | 0 |
| CFB | 16 | 15 | 16 |

So **44 markets with an independent fair** compete for 2 slots.

**Odds provider.**
- 262 of 500 calls remain; h2h costs 1 per call.
- With `PMUS_SHARP_REFRESH=on_demand` and `PMUS_SHARP_DAILY_CALLS=8`, use is at most 8 calls a day.
- NHL now matches on nicknames (`PMUS_SHARP_NAME_MATCH=words`).

## 4. Evidence (THEORETICAL, before incentives)

21 Nov two-venue tape (CFB, NBA, NHL moneylines):

| Policy | Touch | Strict |
|---|---|---|
| Current controller | −5.87 | −7.74 |
| Trading-first, edge 2¢ | +0.97 | +0.82 |
| Edge 2¢, hedge repriced to fair + 1¢ | +1.61 | +1.06 |

- Edge 1–1.5¢ loses; 2¢ and 2.5¢ cannot be told apart. Requiring positive EV makes entries favourite-heavy without a favourite-only rule; the favourite-only variant without an edge was −$9.73.
- **The threshold was selected on this same tape.** Out-of-sample evidence comes from tonight's recording, in progress for CBB, CFB, NBA, NHL and AHL.
- The interim replay of games closed by 23:35Z is the arming gate in §6.

## 5. Proposed campaign

**Window: Sunday 29 Nov 2026, 00:15–03:15Z** (three hours), then a completions-only grace period to 05:15Z. No automatic repeat.

**Activation variables** (all other variables unchanged; the release's other new flags stay off):

| Variable | Now | Campaign |
|---|---|---|
| Image | `8f7cb2c36` | `9e6c2039f` |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | atp,atpdb,setkamemd,setkameua,utr,wta,wtadb | **nba,nhl,cbb,cfb** |
| `PMUS_TWO_SIDED_STRICT_SPORTS` | 1 | 1 (unchanged: exactly these four) |
| `PMUS_RN1_REWARD_ONLY` | 1 | **0** (CBB has no programme; trading first) |
| `PMUS_TWO_SIDED_INTL_GUARD` / `_REQUIRE` | on / 1 | 1 / 1 (confirm on at boot) |
| `PMUS_RN1_TRADING_FIRST` | off | **1** |
| `PMUS_RN1_TF_EDGE_USD` | — | 0.02 |
| `PMUS_RN1_TF_HEDGE_GIVE_USD` | — | 0.01 |
| `PMUS_RN1_TF_WEAKEN_USD` | — | 0.02 (300 s) |
| `PMUS_RN1_TF_SELECT` | off | **only** (no slot without an independent fair) |
| `PMUS_RN1_TF_BYPASS_NET` | off | **1**: the pre-reward edge gate, not the reward-net estimate, decides openings and additions. Without it, CBB (no programme) could never open, and rewards would still admit bids elsewhere. The tape results were produced without the reward-net block, so they match this setting |
| `PMUS_RN1_DIR_SHARP` | 0 | 1 (sportsbook fair when no international fair has settled) |
| `PMUS_SHARP_NAME_MATCH` | exact | words |
| `PMUS_SHARP_REFRESH` / `PMUS_SHARP_DAILY_CALLS` | poll / — | on_demand / 8 |
| `PMUS_RN1_PILOT_LOSS_USD` | 8 | **6.50** (tighter; see risk) |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | 1 | `window:2026-11-29T00:15:00Z/2026-11-29T03:15:00Z` |
| `PMUS_SHARP_COVERAGE_LOG` | 1 | 1 |

**Effective risk limits.** Nothing is weakened; one limit is tighter.
- Maker-only, no taker.
- 2 markets; clip 2 contracts; pair cap 0.97.
- Per market: $12 spend, $5 unpaired. Campaign: $60 spend, $10 unpaired.
- **Campaign loss stop $6.50.** It must fit under lifetime room ($6.88). At $8 it would not, and the previous packet's preflight rule ("do not arm unless lifetime room ≥ stop") would block.
- $10 lane daily stop; $20 at-risk and account drawdown (room $12.36); lifetime ceiling $75.
- Jump and volatility pauses, safety latch, shared halt, and the independent watchdog with exact-ID cancels.
- 2 h completions-only grace.
- Crypto disarmed; esports, cricket and soccer off; combos in shadow; lines and props not live.
- **Maximum feasible campaign loss: $6.50.**

**Reporting.** Settled trading P&L before incentives, by market and by role. Favourite and underdog entries; markouts; hedge economics; fair source and age per fill. Rewards reported separately as paid, pending and skipped, and rebates separately.

## 6. Sequence and gates (each must pass, or nothing is armed)

1. **23:35Z, evidence gate:** interim out-of-sample replay on tonight's closed games (scheduled). Trading-first settled P&L before rewards must be ≥ 0 at both fill models, with a paired gain over the current controller. If not, the packet is withdrawn.
2. **23:40Z, deploy locked:** point the service at `9e6c2039f`, lock held at `1`, with every variable above except the window.
3. **Verify within 30 min:**
   - `boot` shows the release and flags; `why: locked`;
   - no loop or import errors for 5 min;
   - watchdog OK, venue reconcile 0/0, lifetime room ≥ $6.50;
   - `RN1_TF_SELECT with_independent_fair` > 0;
   - `intl_guard.matched` > 0 and `ws.connected` true;
   - `fair_coverage.any` on at least 80% of candidate quotes.
   - Any failure means no arming, and the lock stays at 1.
4. **00:15Z, arm:** set the window. 03:15Z: completions only. 05:15Z: lock at 1. Then reconcile and record.

**To authorize, the owner replies with:** release `9e6c2039fd1b31f281b5cbd1545b036b8604651b`, the activation variables of §5 (including the $6.50 stop), and the window 29 Nov 00:15–03:15Z plus 2 h grace, subject to the gates in §6.
