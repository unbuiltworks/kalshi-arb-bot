# Stored submit time vs log time vs rest_s — reconciled

**The 0145z discrepancy is a log-ingestion lag, not a gate defect. `rest_s` is
correct. But the same lag makes every fill-latency figure I derived from log
timestamps unreliable, and the patience conclusion I drew from them has to be
withdrawn until the engine's own clock is on both ends.**

## The 0145z case, exactly

| quantity | value |
|---|---|
| UP complement pre-submit, **internal `utc`** | **01:46:51.293** |
| same record, Railway log timestamp | 01:46:58.881 |
| ingestion lag on that line | **7.588 s** |
| `L5_ACC_RECOVER`, Railway log timestamp | 01:47:08.023 |
| 01:47:08.023 − **01:46:51.293** | **16.730 s** |
| `rest_s` as the engine reported it | **16.785 s** |
| residual difference | **0.055 s** |

My earlier 9.142 s was log-timestamp minus log-timestamp
(01:47:08.023 − 01:46:58.881), which silently omitted the 7.588 s lag on the
pre-submit line. The engine computes `rest_s` from the **stored** submit time,
which matches the internal `utc`. **The gate was compliant: 16.785 s against
its 15.0 s floor.**

## The lag is real, variable and bimodal

Ingestion lag on live `L5_FILL_CALIB_PRESUBMIT` lines, all eleven orders:

0.029, 0.047, 0.069, 0.106, 0.129, 2.905, 3.675, 5.337, 7.588, 8.925, 9.641 s

Six of eleven land under 0.15 s; five land between 2.9 and 9.6 s. There is no
usable average to correct by.

## Which clocks exist

| record | internal clock | trustworthy for timing |
|---|---|---|
| `L5_FILL_CALIB_PRESUBMIT` | **yes** (`t`, `utc`) | yes |
| `L5_FILL_CALIB_TERMINAL` | yes (`t`, `utc`) | yes — **but emitted for shadow coids only; 0 of 11 live orders have one** |
| `L5_LIVE_LIFECYCLE` | **no** | no — log timestamp only |
| `L5_ACC_RECOVER` | no, but carries `rest_s` computed internally | **`rest_s` yes** |

So for a live order we have a trustworthy **submit** time and no trustworthy
**terminal** time.

## What this invalidates

Every fill latency in `TICK_DISTANCE_AND_FILL_LATENCY.md` is
log-timestamp-to-log-timestamp, with an independent unknown lag of up to ~9.6 s
at each end. Consequences:

* **Withdrawn: "4 of 8 fills (50%) arrived after 16.8 s."** Several fills sit
  between 10.8 and 23.5 s, a band narrower than the possible error, so the
  comparison against the 16.6–16.8 s cancel point cannot be made from these
  data at all.
* **Survives, weakly:** the 73.4 s fill is still the longest by a margin far
  exceeding the lag, so the fill distribution does have a long right tail.
* **Survives fully:** the three `rest_s` values — **16.62, 16.79, 44.13** —
  because the engine computed them. The two complements were cancelled at
  ~16.7 s of true rest and the one first leg at 44.13 s.
* **Unaffected:** every price, tick distance, queue figure and settlement
  number, none of which depend on inter-record timing.

The direction of the earlier conclusion may well survive measurement. It is not
measured yet, and I am not going to carry it into the A/B/C replay as though it
were.

## Smallest fix, telemetry only

`L5_LIVE_LIFECYCLE` should carry the same internal `utc` the pre-submit record
already does. That is an additive field on one emit — no gate, no limit, no
execution path touched — and it makes submit→terminal latency measurable from
the log stream alone. Until then the alternative is reading
`/data/l5-live-20261012z/decisions.jsonl` directly, which is authoritative and
already written, but not reachable from outside the container.

**Required before the A/B/C replay**, because policy C is a rest-threshold
question and every rest figure except `rest_s` is currently unmeasurable.

Nothing changed. `MAKER_ONLY`, `max_live_orders: 1`, cap 0.96, both halts live,
03:00Z expiry intact.
