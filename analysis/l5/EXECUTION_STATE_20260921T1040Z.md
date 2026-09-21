# Execution state, 2026-09-21T10:40Z

Labels are strict: historical replay → shadow → live probe → submitted → filled
→ settled → **realized**. Nothing below is called realized.

## 1. Collector hygiene patch — DEPLOYED AND WORKING

Commit `769f5bcd`, deployment `18a0cd2e-24d8-4c6a-8e32-8a5dddd959ef` (SUCCESS).
Production before (`6553d6dd`, 10,764s uptime) vs after (`18a0cd2e`, 326s):

| counter | before | after | reading |
|---|---|---|---|
| `book_errors` | 11,229 | **0** | genuine fetch-health failures |
| `book_gone` | (did not exist) | 120 | expected rollover 404/410 |
| `tokens_retired` | 3,718 | 40 | 36.1/min → ~7/min |
| `habitat_readd_suppressed` | (did not exist) | 6→46→86 | the tombstone working |

`intl_flow` disambiguation, live across three heartbeats:
`sweeps 10 → 12 → 16` while `selected 0 → 119 → 0`, `tape 1000`,
`fetch_errors 0`. A flat row counter is now provably a quiet slate rather than
a stalled loop.

All four streams advancing; sweeps 0.20–0.29s (pmus) and 0.33–0.39s (intl).

## 2. Prospective evidence — BELOW THE FLOOR, NOT SCORED

Durable `prospective.events` 1,232 at 10:31Z. Per-market scorable
**precondition** (venue-labelled MAKER, pre-fill book present, ≥1 share ahead),
reconstructed from 501 durable event log lines spanning 09:43–10:30Z:

| market | events | maker | +pre_book | +queue≥1sh |
|---|---|---|---|---|
| `btc-updown-15m-1789983900` | 152 | 151 | 151 | **103** |
| `btc-updown-15m-1789984800` | 149 | 147 | 147 | 96 |
| `btc-updown-15m-1789985700` | 79 | 79 | 79 | 36 |
| `btc-updown-15m-1789983000` | 52 | 50 | 50 | 29 |
| 11 others (5m windows) | 69 | 9 | 9 | 5 |

**Best single market 103 against the preregistered floor of 200. NOT SCORED.**
`pooled()` refuses, as preregistered.

### A structural problem with the floor, reported not resolved

A `btc-updown-15m` window lives 900s and yields ~100 scorable fills, then the
market ends. **No single market in this habitat can reach 200.** The floor is
not merely unmet, it may be unreachable here.

I am not moving the floor — changing a preregistered threshold after seeing the
data is exactly the manoeuvre the preregistration exists to prevent. This needs
an owner decision between: a longer-lived market type, a market-clustered
alternative stated in advance, or accepting that H-QUEUE-MISMEASURED cannot be
scored in the updown habitat.

The 2026-09-21 03:55:13Z–06:07:17Z PMUS interval remains **CENSORED**.

## 3. PMUS executable L5 states — 43 of 61

`analysis/pmus/executable_state_census.py`, 12 sweeps at 8s over the live slate,
running the existing decision layer unchanged (`analyze_market` → `_proposal` /
`plan_action` → `open_admission`). No gate relaxed, no fill invented.

```
ELIGIBLE                  43
model_no_open             17
one_sided_book (no bid)    1
```

Sample: `aec-cs2-sashi-fokus` 0.28/0.29, `aec-dota2-nem-mouz` 0.39/0.40,
`aec-setkamecz-kukzde-hrstom` 0.53/0.54 — all `q1_depth=True`.

**A FIRST RUN OF THIS CENSUS REPORTED 0 EXECUTABLE AND WAS MY BUG.** I read
touch depth from `bestBidQuantity`, which does not exist on the gateway payload;
the real fields are `bidShares` / `askShares` (31,000 shares at the touch in the
first market sampled). Every market therefore failed `no_q1_touch_depth`. The
same field-name class of error is called out in `pmus_status_report` itself.
Reported because a zero from a typo is indistinguishable from a zero from the
market.

## 4. Gemini q=1 lane — see GEMINI_Q1_STATE_20260921.md

---

# Addendum, 2026-09-21T11:2xZ

## PMUS: the 43 characterized (re-measured 40/69 on a later slate)

`analysis/pmus/executable_state_profile.py`, 20 sweeps at 8 s. Full table in
`analysis/pmus/EXECUTABLE_STATE_PROFILE_20260921.txt`. The 43/61 census is
preserved; a later slate gave **40 ELIGIBLE / 27 model_no_open / 2 one_sided**.
The count moves with the slate — it is a live census, not a constant.

| market type | n | median spread | median bid shares | shadow reach |
|---|---|---|---|---|
| tennis_match_winner | 18 | **2.0c** | 56,000 | 8/18 |
| esports_match_winner | 8 | **1.0c** | 31,000 | 2/8 |
| table_tennis_match_winner | 4 | 1.5c | 48,500 | 3/4 |
| tennis_match_total_games | 3 | 11.0c | 27 | 0/3 |
| esports_series_total_maps | 2 | 20.5c | 34,500 | 0/2 |
| tennis_match_games_spread | 2 | 35.0c | 300 | 0/2 |
| tennis_match_sets_spread | 1 | 39.0c | 76 | 0/1 |
| hockey_team_full_game_winner | 1 | 21.0c | 26,000 | 0/1 |
| tennis_match_total_sets | 1 | 1.0c | 100 | 0/1 |

**Every proposed action is `open`** — correct, since the census evaluates a flat
inventory. Sides split YES/NO by the cheaper touch, as the convention requires.

**The NFL anti-correlation does not hold here.** On the NFL slate, tight spreads
and good recycling were anti-correlated by market type. On this live
esports/tennis slate the **match-winner** types carry *both*: 1–2c spreads
**and** 26k–190k shares at the touch **and** all 13 of the shadow reaches.
Totals and spreads markets carry 10–39c spreads and 27–500 shares, and reach
nothing. That is a different habitat from the one the NFL study rejected.

**SHADOW/HYPOTHETICAL: 13 of 40** proposed resting prices were reached by the
opposing touch within the ~160 s observation. Several reach at 0.0 s — the
touch was already there at first observation, so the price was immediately
marketable rather than requiring a wait.

**This is not a fill.** It is whether the opposing touch visited the price. It
assumes nothing about queue position, and today's prospective evidence says
depth ahead is exactly the quantity we cannot yet interpret. Not submitted, not
filled, not settled, not realized.
