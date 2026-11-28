# 28 Nov campaign: inventory quality, settlement, and the integrated release candidate

28 Nov 2026, 04:10 UTC. The campaign is in completions-only grace until
05:15Z. Close-out and relock follow at 05:17Z. Five markets settle later
(match starts 03:55–08:05Z).

## 1. All 24 fills (`fills_recon.txt`, `fills_recon.json`)

- **Order-time BBO:** the last decision quote before the order.
- **Fill-time BBO:** the venue book before the fill (FILL_MARKOUT).
- **Favourite:** the side above 0.50 at the fill.
- **Direction:** our side's mid change over the prior 60 s.
- **Markouts:** logged at 5 s, 30 s, 120 s and 300 s. **10 s and 60 s are
  not logged**, so they are reported as unavailable, not estimated.
- **Independent fair value:** **none** in all 24 (`fair: null`;
  directional `no_probability`).

| Role | Fills (contracts) | Avg px | Favourite / underdog | Direction (strengthening / weakening / flat) | Markout 30 s / 120 s / 300 s |
|---|---|---|---|---|---|
| Openings | 7 (9.1) | 0.448 | 2 / **5** | 0 / 0 / 2 (5 no prior quote) | −1.3¢ / **−3.4¢** / −2.6¢ |
| Same-side additions | 8 (6.6) | 0.467 | 3 / **5** | 1 / **2** / 5 | −1.2¢ / **−2.4¢** / −1.3¢ |
| Completions | 9 (6.0) | 0.536 | **6** / 2 | 1 / 2 / 5 | **+3.6¢ / +3.6¢ / +2.8¢** |

**The eight additions:**
- 5 added to underdogs: `beldmy` YES ×3, `bokrom` YES ×2;
- 3 added to favourites, of which **2 were weakening**: `makyar` NO at
  0.50 and 0.49, marking −8.5¢ and −5.5¢ at 120 s;
- 1 added to a strengthening underdog: `beldmy` at 0.37.

So the accumulation built adverse inventory. Openings and additions were
bought as the price moved through our bid. Completions, bought later on the
other side, marked positive. This is the residual-loss mechanism measured
over 14–27 Nov.

## 2. Settled trading P&L (venue resolution; rewards separate)

| Market | Status | Pairs | Pair edge | Unpaired | Settled P&L |
|---|---|---|---|---|---|
| atpdb aregonreyvar (live) | **settled** (NO) | 1.00 | +$0.030 | 0 | **+$0.030** |
| setkameua plivol-kovnaz | **settled** (NO) | 1.19 | +$0.036 | 0.81 YES lost | **−$0.296** |
| setkameua bokrom-priser | open | 2.19 | +$0.060 | 1.11 YES ($0.52) | pending |
| setkameua masyev-cherom | open | 0.64 | +$0.019 | 2.36 YES ($1.25) | pending |
| setkameua beldmy-kvaiho | open | 0 | — | 1.85 YES ($0.70) | pending |
| setkameua makyar-karole | open | 0 | — | 3.55 NO ($1.74) | pending |
| setkameua kanden-kotand | open | 1.00 | +$0.030 | 0 | pending (+$0.030 locked) |

- **Settled so far: −$0.266.**
- **By role, settled markets only:** openings −$0.200; completions −$0.066;
  additions $0 (none settled yet).
- **Rewards:**
  - **confirmed $0**;
  - **pending $0** (no 28 Nov rows yet);
  - **projected $0.155**, THEORETICAL: calibrated × paid fraction, credited
    to resting bids 01:45–03:15. **Not realized.**

## 3. Reward minimum (`PMUS_RN1_REWARD_MIN_RULE=threshold`)

- **Reconciliation of the sub-$1 rows to programme ids and periods: not
  possible.**
  - The earnings rows carry no programme id.
  - The venue no longer serves programme records for those settled markets
    (0 of 64 returned).
  - Our production logs from 15–18 Nov are not retained.
- **So the documented $1 minimum per programme and period is applied:**
  - our projected total for that programme and period (accrual, plus every
    active market paying from it, plus the candidate) must reach $1, or the
    bid's expected payable reward is $0;
  - score share stays per contract;
  - the paid fraction is only a calibration multiplier;
  - an unknown paid fraction or pool means no credit.
- **Tests:** 7 in `tests/test_pmus_reward_threshold_rule.py`.
- **Tonight's production rule:** `paid_fraction`. It does NOT apply the
  minimum, so tonight's projected reward may not be payable.

## 4. Fair-value coverage

- **prediction-market-ev-engine** (`03db746a0`):
  - **no licence**: ideas only, no code reuse;
  - Pinnacle guest API; multiplicative de-vig plus a haircut; odds older
    than 30 s rejected;
  - both participant orders tried, ambiguous matches skipped, ±6 h window;
  - **no tennis, no table tennis**.
- **polymarket-tennis** (`32b0dd9fb`, MIT):
  - Live Tennis API (paid, 100 requests a day free);
  - **scores only, no probabilities**; doubles rejected; no table tennis
    found.
- **Our provider (The Odds API):**
  - its documentation is unreachable from here (DNS);
  - **coverage of Setka, ATP and WTA is unverified**;
  - added a read-only `OddsClient.coverage()`, logged once per process with
    `PMUS_SHARP_COVERAGE_LOG=1`: the provider's sport list, tennis and
    table-tennis keys, configured keys offered or missing, and the measured
    quota cost of the list call.
  - **Polling is unchanged** (3,600 s). The fair-feed branch's 300 s default
    and MAX_AGE/2 refresh were reverted until coverage and quota are
    reported; stale and future-dated odds are still refused.

## 5. Completion safety class vs cancel and halt priority

- **What is unaffected:** cancels and order-status reads are critical, are
  always sent, and may overdraw the bucket. The emergency halt cancels
  through them, and the independent watchdog is a separate process.
- **Finding:** a safety request waits through a 429 cool-down for up to the
  20 s safety wait. On the single trading thread, which also issues cancels
  and reactive pulls, a completion could hold the loop for 20 s.
- **Fix:** `RequestBudget.safety(max_wait_s)`. A completion waits at most
  the normal wait (3 s), ahead of openings, so it holds the loop no longer
  than today. Audits keep the full safety wait. Rate, burst and cool-downs
  are unchanged. Tests are in `test_pmus_completion_safety_class.py`.

## 6. Integrated release candidate (disarmed)

- **Release:** `ac1c1bbafb007107b592a4a5732b94c4403be899` on
  `release/pmus-sports-integrated-ac1c1bbaf`; code `cc0a222c9`.
- **Contents:** 6e6cc2d5d plus:
  - the threshold rule;
  - `PMUS_RN1_MAX_LIVE_MARKETS`;
  - `PMUS_RN1_COMPLETE_KEEP_TICKS`;
  - the completion safety class with the capped wait;
  - fair-feed freshness bounds (polling unchanged);
  - the provider coverage report;
  - the fixed time-of-day tests.

  Every new flag is off by default.
- **Regression:** 6,126 passed, 26 failed. The **26 failure names are
  identical to a51cfdbd2**.
- **6e6cc2d5d itself, same hour:** 30 failed. The extra 4 are the three
  time-of-day reward tests (fixed here) and one feed-port test that failed
  only while two suites ran in parallel.
- **Image:** built from scratch. 63 `src` files, all identical to the tree
  except the build-patched spring session file (identical to production).
  62/62 modules import with every new flag on, and it boots locked
  (`why: locked`) with no errors.

## 7. What this means for profit

Execution now accumulates. The inventory it accumulated tonight is adverse:
openings and additions mark −1.2¢ to −3.4¢, and the first settled unpaired
leg lost its full cost. Until an independent fair value tells the
controller which side to add to, additions should not be widened. The
release adds no accumulation; it corrects reward crediting and request
priority.

The next profitability lever is a fair value for these markets. Setka has
none from any inspected source. Next is the measured provider coverage,
then a decision whether to restrict accumulation to fair-valued markets.
