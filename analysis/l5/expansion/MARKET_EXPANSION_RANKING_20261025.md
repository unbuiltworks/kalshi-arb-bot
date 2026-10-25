# L5 market expansion — ranked recommendation (read-only research)

**Snapshot:** 2026-10-25 ~12:30 UTC · branch `claude/l5-market-expansion-research-yn1muo`
**Classification:** HISTORICAL REPLAY + SHADOW. Nothing here is submitted, filled, settled or
REALIZED. Modelled fills are not executions. No service was created, changed or deployed; no
order path was touched; the live BTC15m lane was not modified.

---

## (a) CURRENT RUNNING STATE inspected

| thing | state as read |
|---|---|
| Railway `l5-book-shadow` (project `l5-book-shadow-public`, prod `ffbe9f9b…`) | SUCCESS, deployment `e0f8d1ed…`, uptime **993,293 s (11.5 d)**, source `claude/august-l5-forensic-replay-788cml` @ `d97ba71b`, volume `/state/l5-book-shadow`. Collector + `l5_maker_shadow_runner`, `orders_submitted: 0`, `role: MAKER_ENGINE_SHADOW_NO_ORDERS`, `network_requests_issued: 0` (shadow tails the collector's files). |
| Railway `l5-clob-ws-probe` | present, not inspected further, not touched. |
| GH Actions `kalshi-l5-tape-5m` | still committing to branch `l5-kalshi-data`; latest `a3c7c1e9` 2026-10-25T06:55Z. |
| Frozen Kalshi release | `release/kalshi-btc15m-policy-d-v1-final` → `src/kalshi_btc15m_policy_d_live_v1.py` (Policy D: last-30-print share-weighted tape, quote the side opposite >10% accumulation, selected-side bid ≥ $0.30, post-only q=1, hold to settlement, no new exposure inside 90 s). |

**No new collector, service, workflow, branch deployment or credential was added for this
research.** Both datasets below already existed.

## (b) NEW EVIDENCE

### Lane A — Polymarket L5 clone (the canonical L5 premise)

Source: 4,489 unique de-duplicated `MAKER_SHADOW_WINDOW` records emitted by the running
`l5-book-shadow` maker shadow, **2026-10-13T21:29Z → 2026-10-25T12:23Z**, read from Railway
logs only. The collector's slate selector is `l5_active`, i.e. **these four families are the
markets L5 itself trades** — so this is the complete candidate set under the clone premise, not
a sample of convenience. (One day-slice returned at the 500-row API cap, so a handful of Oct-14
records are missing; it does not move any ranking.)

`pair_cost` = each side's own volume-weighted basis, summed. `< 1.00` on matched quantity is a
locked gross edge before fees; `> 1.00` is a loss.

| family | windows | L5 matched qty / observed hr | matched pairs | frac pair_cost <1.00 | median pair_cost | mean edge ¢/pair | our modelled fill/intent | our windows with a matched pair |
|---|---|---|---|---|---|---|---|---|
| **btc-updown-15m** (live lane) | 1,248 | **3,315.8** | 1,232 | **0.856** | **0.9391** | **+6.65** | 0.587 | 676 / 1,248 |
| **btc-updown-5m** | 2,219 | 278.2 | 644 | 0.488 | 1.0100 | −2.61 | 0.084 | 35 / 2,219 |
| **eth-updown-5m** | 671 | 99.1 | 68 | 0.324 | 1.1492 | −11.12 | 0.034 | 1 / 671 |
| **eth-updown-15m** | 351 | 29.7 | 53 | 0.321 | 1.1390 | −14.77 | 0.022 | 1 / 351 |

Rate ordering is identical on raw counts, on per-observed-hour normalisation, on sub-$1.00
frequency, on mean edge and on the clone's own modelled fill ratio. `btc-updown-5m` is first on
every one of those five axes.

**Observation coverage is not equal and must be stated:** median observed fraction of each
window is btc-15m **0.98**, btc-5m **0.65**, eth-5m **0.41**, eth-15m **0.19** (collector
`max_markets` cap + eviction — `markets_evicted: 2,094`). The ETH numbers are therefore a
**floor**, not a measurement, and eth-15m at 19% coverage is **not adjudicable** as it stands.
Normalising per observed hour (table above) is what keeps the comparison honest, and btc-5m
still leads ETH by 2.8×–11×.

### Lane B — Kalshi Policy D port

Source: the canonical 5-minute public tape on branch `l5-kalshi-data`,
`data/l5/kalshi_tape/tape_2026{0903..1025}.jsonl` — 53 files, **308 snapshots**, 615
snapshot-market rows, KXBTC15M + KXETH15M, full 20-level `orderbook_fp` ladders and up to 500
trade prints per snapshot. Policy D's gates were applied verbatim to the in-window rows.

| gate | KXBTC15M | KXETH15M |
|---|---|---|
| in-window rows (0 < ttc ≤ 900 s) | 304 | 304 |
| 30-print tape ready | 304 (100%) | **303 (99.7%)** |
| contra signal present (\|imbalance\| > 10%) | 287 | 280 |
| selected-side bid ≥ $0.30 | 219 (**76.3%** of signals) | 203 (**72.5%**) |
| also > 90 s before close | 190 (86.8%) | 184 (**90.6%**) |
| selected-side touch qty, median / p10 | 3,210 / 215 | **107 / 15** |
| yes spread, median | 2.00 ¢ | 2.00 ¢ |
| window volume_fp, median | 860,051 | 37,852 (≈ **23× thinner**) |
| open interest, median | 305,488 | 16,017 |

KXETH15M clears **every input gate Policy D actually tests**, at rates indistinguishable from
BTC, with the same 2 ¢ median spread and touch depth ~100× the q=1 requirement. The only large
difference is absolute size, which a q=1 release does not consume.

## (c) P&L STATUS

`historical replay` and `shadow` only. Lane A figures are the target trader's own observed
basis plus our **modelled** fills; Lane B is quote/print replay with no fill assumption at all.
**Submitted 0, filled 0, settled 0, REALIZED $0.00.** No capacity claim is made.

## (d) ONE BOTTLENECK

**Neither dataset can price the candidate's outcome.** Lane A has entry basis but this analysis
carries no settlement join, so a sub-$1.00 pair is a locked gross edge and an unmatched residual
is an unpriced directional bet (our shadow's residual share: btc-15m 1.4%, btc-5m −6.5%,
eth-5m −12.0%, eth-15m 8.1%). Lane B has **no settlement ground truth at all**: the
`kalshi-l5-tape-5m` collector intends to capture the current *and next* window
(`candidates[:2]`), but `status=open` means the next 15-minute market does not yet exist at
capture time, so **every snapshot holds exactly one market per series** (307/307 for BTC) and
`result` is `""` in 100% of rows. Consecutive-window strike pairing — which would have given
settlement for free, since window *N+1*'s `floor_strike` is the index at window *N*'s close — is
therefore impossible from this tape. GitHub cron throttling compounds it: ~6 snapshots/day
land, not the scheduled 288.

## (e) RANKED RECOMMENDATION and NEXT ACTION

1. **`btc-updown-5m` (Polymarket) — test next, shadow only.** Best candidate on all five
   measured axes; 2,219 windows already observed; same underlying, same rules family and same
   slug convention as the live 15m lane, so the port is configuration, not code. Its median
   matched pair cost is 1.0100 — **negative at the median** — so this is a dedicated
   instrumented shadow, **not** a live promotion, and the 48.8% sub-$1.00 tail is the thing to
   characterise.
   *Cheapest decisive step, no new service:* raise coverage for this one family on the
   **existing** `l5-book-shadow` service (`BOOK_SHADOW_MAX_MARKETS` / selector scope) to lift
   its 0.65 observed fraction toward btc-15m's 0.98, and add a settlement join to the window
   summary so residual is priced. Owner authorisation required to change a running service's
   variables; this session changed nothing.
2. **`KXETH15M` (Kalshi) — the Policy D port candidate.** Clears every Policy D input gate at
   BTC-equivalent rates (99.7% tape readiness, 72.5% admission, 90.6% stop-new headroom, 2 ¢
   spread, 107-contract touch vs q=1). Ranked second only because its *outcome* economics are
   unmeasured, not because its microstructure is worse.
   *Cheapest decisive step:* fix the collector gap in (d) — capture the next window regardless
   of `status`, or capture `status=settled` for the window just closed — which makes a real
   Policy D win-rate computable for both series from the tape that already runs.
3. **`eth-updown-5m` (Polymarket) — hold.** 99 matched qty/hr, 32.4% sub-$1.00, −11.12 ¢ mean
   edge, 1 matched window in 671. Under-observed (0.41), so this is an upper bound on how bad
   it is, and still clearly behind btc-5m.
4. **`eth-updown-15m` (Polymarket) — BLOCKED, not rejected.** Worst on every axis, but at 0.19
   observed fraction and 53 matched pairs it cannot be adjudicated. Do not spend engineering on
   it before coverage is fixed; do not record it as a negative verdict either.
5. **Kalshi altcoin 15m (`KXSOL15M`, `KXXRP15M`, `KXDOGE15M`, `KXBNB15M`, `KXSHIB15M`,
   `KXHYPE15M`) and `KXBTC5M` — BLOCKED, no data.** The 5-minute tape covers BTC/ETH only, this
   container's network policy denies `external-api.kalshi.com`, and the prior repo evidence that
   exists is adverse: a SOL15M hedge leg was rejected
   `fill_or_kill_insufficient_resting_volume` (`ARB_STATUS_HISTORY_2026-08-23.md`) and the XRP
   15m maker campaigns took **zero fills** across every attempt
   (`ARB_STATUS_2026-08-24_XRP_STICKY_MAKER.md`). Under the Lane A clone premise they are moot
   anyway: the `l5_active` selector shows **L5 does not trade them**. Do not open an altcoin lane
   on current evidence.

## (f) COST / DEPLOYMENTS ADDED

**Zero.** Read-only Railway log reads, read-only git reads of existing branches, and local
replay. Two throwaway analysis scripts per lane are committed under
`analysis/l5/expansion/scripts/` for reproducibility; they make no network request and submit
nothing.

## (g) OWNER AUTHORISATION

Required before **any** of the next actions above, because each one changes a running service's
configuration or a committing collector's schema. Nothing in this ranking authorises arming,
order entry, or a live probe on any market, including `btc-updown-5m`. Promotion past shadow
still needs the stage-5 gate in `analysis/l5/CLAUDE.md`.

---

## Reproduce

```sh
# Lane B — Kalshi 5m tape
git fetch origin l5-kalshi-data:l5-kalshi-data
mkdir -p /tmp/tape && for f in $(git ls-tree -r --name-only l5-kalshi-data \
    -- data/l5/kalshi_tape | grep jsonl); do
  git show l5-kalshi-data:$f > /tmp/tape/$(basename $f); done
python3 -I analysis/l5/expansion/scripts/kalshi_tape_flatten.py /tmp/tape /tmp/flat.jsonl
python3 -I analysis/l5/expansion/scripts/kalshi_tape_summarize.py /tmp/flat.jsonl

# Lane A — Polymarket maker-shadow windows
#   save Railway `l5-book-shadow` deploy logs filtered to MAKER_SHADOW_WINDOW as
#   <dir>/mcp-Railway-get-logs-*.txt, then:
python3 -I analysis/l5/expansion/scripts/shadow_window_aggregate.py <dir> /tmp/families.json
python3 -I analysis/l5/expansion/scripts/shadow_window_economics.py <dir>
```
