# Authorization packet: corrected controller, second campaign of 28 Nov 2026

**This is a request for explicit authorization. Nothing in it has been done.** The current campaign (authorization 87, release `9e6c2039f`) runs to 19:15Z, with completions-only grace to 21:15Z and close-out at 21:17Z.

## 1. Release

| | |
|---|---|
| Release SHA | **`97a22688d92e765fa62093ad08a5b462a5c8da9e`** |
| Branch | `release/pmus-sports-tf-evict-97a22688d` (code `738af20f3`, dev `dev/l5-integrated-tf-soccer`) |
| Contains | `9e6c2039f` (armed now), the eviction fix `ce0b13510`, the reselection backoff `01248d03b`, and soccer three-way `646d591d3` (soccer flags default off) |
| Regression | 6,344 passed, 27 failed. 26 are the baseline set; the 27th is a port collision from a concurrent suite and passes alone |
| Image | Built from scratch; 64/64 modules import |

**What changes against the armed release, with the same variables:**
- Under trading-first, a flat market is no longer released because its quote earns no reward. The economic idle release (120 s with no wanted bid) still applies.
- Repeated reselection of the same market backs off 60 / 120 / 240 / 300 s.
- These two changes act through `PMUS_RN1_TRADING_FIRST` with no separate flag.

## 2. Why: today's campaign, 16:33–18:04Z

| Measure | Today (`9e6c2039f`) |
|---|---|
| Reward-only evictions | 125, against 11 economic idle releases |
| Reselections of the same markets | 128 |
| Order rest time before eviction | median 335 s, p25 178 s |
| Orders / fills | 87 / 3 (all post-only, in scope) |
| Rate-limit and read errors | 30, plus 13 deferred orders |
| Same-side additions | 0 |
| Hedges (completions) | 1 pair locked at 0.970 (+$0.03) |

## 3. Soccer: excluded (validation gate not passed)

- **Replay on recorded soccer tapes:** 17 closed outcome markets from 6 matches; both the current controller and trading-first made **0 trades**, so there is no evidence either way. The tapes cover only about 30–45 minutes per match because the recorder stopped at 14:13Z.
- **Three-way price consistency:** the international mids summed to 1 within 0.04 in 94–100% of 5-second buckets, but two matches showed brief breaks up to 0.27 and 0.48.
- **Provider soccer keys and team spellings:** still unverified.
- **Decision:** `PMUS_RN1_SOCCER` and `PMUS_SHARP_SOCCER` stay off and the soccer league list stays empty.

## 4. Proposed sequence

1. **21:17Z close-out** of the current campaign: lock = 1, reconcile, record.
2. **Disarmed deploy of `97a22688d`** right after close-out, lock held at 1, variables unchanged. *(Needs authorization.)*
3. **Verify locked:** boot, `why: locked`, watchdog, reconcile 0/0, audit, guard connected, `RN1_TF_SELECT with_independent_fair` > 0, no `released_unqualified_market` events.
4. **Arm at 22:15Z** for **22:15Z 28 Nov – 01:15Z 29 Nov**, completions-only grace to 03:15Z. No automatic repeat. *(Needs authorization.)*

## 5. Configuration

Identical to the armed campaign: packet §5 of `CAMPAIGN_PACKET_EXPANDED_20261129.md` plus `PMUS_RN1_NET_RANK=0`, with these exceptions only:

| Variable | Now | Proposed |
|---|---|---|
| Image | `9e6c2039f` | `97a22688d` |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | window 16:15–19:15Z | `window:2026-11-28T22:15:00Z/2026-11-29T01:15:00Z` |
| `PMUS_RN1_PILOT_LOSS_USD` | 6.50 | **the lower of 6.50 and the lifetime room at close-out, rounded down to $0.25** |

Unchanged: NBA, NHL, CBB and CFB moneylines only; maker-only; 2 markets; 2-contract orders; 0.97 pair cap; $12 per market; $60 per campaign; $10 daily stop; $20 drawdown limit; independent-fair-only selection; 2¢ edge; hedge at fair + 1¢; 2¢ falling-fair filter; watchdog; crypto disarmed; combos in shadow.

**Loss bound.** Lifetime room was $6.88 before today's campaign. Today's campaign worst case at 17:30Z was about −$0.95, so room at close-out is expected between about $5.90 and $6.90. The stop is set from the reconciled figure and never above it.

## 6. What the comparison will report

Order rest time, fills per order, rate-limit errors and deferrals, same-side additions, hedges and their cost against fair, and settled trading P&L before incentives, each against the table in §2. Rewards and rebates are reported separately.

## 7. To authorize

Reply with:
- disarmed deployment of `97a22688d92e765fa62093ad08a5b462a5c8da9e` after close-out;
- the window 28 Nov 22:15Z – 29 Nov 01:15Z plus 2 h grace;
- the campaign stop rule in §5.
