# Universal prediction quant — deployment / research boundary

Railway service: `universal-prediction-quant`

This branch/service is intentionally **read-only** research across Gemini, Polymarket US, and Kalshi. It has no order-entry credentials or execution path.

## Safety boundary

- `read_only=true`
- `order_entry=false`
- no automatic merge or production activation
- no real-money order placement/cancellation
- no remote live-arm path
- fuzzy counterpart discovery never grants execution eligibility
- exact identity is not enough: settlement rules must also be equivalent

## Current research lanes

### Three-venue crypto discovery

Normalizes same-window crypto markets across Gemini, Polymarket US, and Kalshi. Polymarket joins remain rule-audit candidates and Gemini/Kalshi index-basis labels are preserved.

### Polymarket US ↔ Kalshi sports counterparts

Strict adapters currently cover:

- MLB player hits → `KXMLBHIT`
- MLB hits + runs + RBIs → `KXMLBHRR`
- MLB total bases → `KXMLBTB`
- MLB pitcher strikeouts → `KXMLBKS`

Strict identity requires exact family, game, scheduled start, player, statistic, and threshold. Scheduled-time agreement is mandatory to prevent consecutive-game / doubleheader false matches.

## Critical MLB player-prop settlement caveat

Current Polymarket US and Kalshi MLB player-prop participation rules are **not unconditionally equivalent before participation**.

For the covered batter props, Kalshi can settle to fair market price when the player is not in the starting lineup even if he later enters, and also requires a starter to record at least one plate appearance before the statistic governs settlement. For strikeouts, Kalshi requires the named pitcher to be the starting pitcher and to record at least one pitch; relief appearances do not count.

Polymarket US uses a broader no-participation LFMP rule. Therefore these player-prop families are classified `CONDITIONAL_PARTICIPATION` until an independent game-state source proves the required gate:

- batter props: `CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE`
- pitcher strikeouts: `CONFIRMED_STARTING_PITCHER_AND_FIRST_PITCH`

After that gate, research may classify the participation branch as `EQUIVALENT_AFTER_PARTICIPATION`, but this branch still does **not** grant execution eligibility or contain order entry.

## Economics snapshot

`src.strict_mlb_prop_snapshot` is a one-shot public/read-only pricer. It discovers strict identities, enforces scheduled-time agreement, reads displayed depth, reads current Kalshi series fee metadata, evaluates both complementary directions, and reports positive conservative pricing only as participation-conditional research.

The modeled Kalshi quadratic fee is deliberately rounded up to a whole-cent upper bound per order so rounding details cannot manufacture a positive signal. This is not a fill simulation.

## Next research priorities

1. collect repeated live-shadow snapshots and OPEN/MOVE/CLOSE episodes for exact pairs;
2. measure duration/survival, quote age/skew, displayed depth, capital usage, and non-double-counted theoretical $/day;
3. add trustworthy first-PA / first-pitch proof only if player-prop economics justify it;
4. prioritize cleaner unconditional sports counterparts in parallel: strict MLB game totals / YRFI, tennis total games, NFL totals;
5. expand exact audited counterpart work into weather and scheduled macro markets;
6. rank all sleeves centrally by net edge × executable probability × depth ÷ capital lockup, with settlement confidence and basis risk.

Treat any $/day result as research capacity evidence, never a forecast or guarantee.
