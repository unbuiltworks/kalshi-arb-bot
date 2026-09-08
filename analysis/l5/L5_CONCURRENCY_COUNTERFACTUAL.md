# Concurrency counterfactual — NOT_YET_ENOUGH_FORWARD_EVIDENCE

_OWNER #167 comment 5586921649. scores a frozen gate; enacts nothing. Concurrency, per-market capital and every cap are owner decisions._

Gate `L5_CONCURRENCY_SCALING_GATE_v1` sha `b86fe09178ea78a2`, frozen 2026-09-08T14:47:00+00:00.

## Sample minimums

| minimum | required | have | met |
|---|---:|---:|---|
| completed_sequences_at_c1 | 40 | 30 | False |
| distinct_leagues | 4 | 21 | True |
| distinct_markets | 40 | 57 | True |
| forward_hours_of_tape | 72 | 68.19 | False |
| settled_markets_with_venue_confirmed_realized | 20 | 6 | False |

## Arms

| arm | observed | verdict |
|---|---|---|
| c1 | yes | scored below |
| c2 | no | UNVERIFIABLE |
| c3 | no | UNVERIFIABLE |

### c1 (observed)

- completed sequences: 30 of 49 started (p=0.6122)
- completed per bankroll-dollar-hour: 0.9272
- settled venue-confirmed: 6 markets, -12.5082 USD
- capital locked: 10004.1 market-seconds

### Why c2 and c3 are UNVERIFIABLE rather than failing

- cycles with a ranked universe: 93
- cycles held with NO ranked universe: **284**
- share of cycles unreconstructible: 0.7533

_run_campaign logs resume_start and calls the session runner directly when a market is held; select_candidate is never reached, so no admitted list, ranking or opportunity score exists for those cycles_

**What would fix it:** rank the universe during holds too and log it WITHOUT acting on it -- a shadow selection row. That is a log-only change: no order path, no cap, no gate, no concurrency change. It makes the c>=2 arms reconstructible from real observed candidates instead of assumed ones. It needs an owner go because it touches the live campaign loop, even though it cannot trade.
