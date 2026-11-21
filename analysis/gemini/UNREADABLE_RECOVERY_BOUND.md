# The unreadable-recovery bound: 5 cycles / 60 seconds

**Why this file exists.** EP013 spent an authorized live window on one crossed
tick. Turning that terminal stop into a bounded HOLD needs a bound, and a bound
invented from nothing is just a different arbitrary number. This is the
measurement the bound came from.

Reproduce with `python3 scripts/gemini_unreadable_census.py` (read-only).

## What was counted

Every per-cycle recording in the tree that carries a book readability verdict:

```
analysis/gemini/btc15m_v1/continuous_v1.jsonl          46 cycles
analysis/gemini/btc5m_v2/continuous_v1.jsonl          325 cycles
analysis/gemini/continuous_v1/continuous_v1.jsonl     950 cycles
analysis/gemini/continuous_run_*.json (5 armed runs)  222 cycles
                                                    -----------
                                                    1,543 cycles
```

The predicate is `inventory_known`, which is exactly what
`continuous_probe.state_from_venue` refuses on: not readable, OR
identity-inconsistent, OR crossed. So a run counted here is a run the driver
would now HOLD for — no translation, no proxy.

Runs are cut at a **symbol change**: a contract transition is terminal in its
own right and may never be absorbed into a recovery bound.

## The result

225 unreadable cycles (14.6%) in **40 distinct runs**. Split by whether the run
ever came back on the same contract:

| run length | runs that recovered | runs that never did |
|---:|---:|---:|
| 1 cycle | 7 | 4 |
| 2 cycles | 2 | 3 |
| 3 cycles | 0 | 4 |
| 4 cycles | 0 | 2 |
| 5 cycles | **2** | 2 |
| 7–21 cycles | **0** | 14 |

```
recovered in 1 cycle    7 runs    63.6%
recovered in 2 cycles   2 runs    81.8%
recovered in 5 cycles   2 runs   100.0%
longer than 5 cycles    0 runs   NEVER, not once in 40
```

**Every run that recovered recovered within five cycles.** The longest
recovered run spanned **41.9 s** of wall clock; the median inter-cycle gap is
9.4–9.6 s, so five cycles is ≈47 s.

## Why the long runs are not evidence for a longer bound

Every run past five cycles has the same shape, and it is not a transient:

```
  n=21  tte 229 -> 36   GEMI-BTC05M2609191615-UP
  n=15  tte 176 -> 38   GEMI-BTC05M2609191545-UP
  n=14  tte 162 -> 37   GEMI-BTC05M2609191610-UP
  n=13  tte 147 -> 36   GEMI-BTC05M2609191320-UP
  n=12  tte 137 -> 35   GEMI-BTC05M2609191255-UP
```

They start in the last few minutes of a 5-minute contract and stay dark to the
end of it. That is a market going out, not a publication glitch. Waiting longer
would not have recovered any of them; it would only have kept a driver alive in
the dark next to an expiring contract.

## Why-class, for completeness

| refusal | runs | recovered |
|---|---:|---:|
| CROSSED book (a bid at or above its own ask) | 32 | 7 |
| contract does not publish both outcomes' prices | 5 | 1 |
| read timed out | 3 | 3 |

EP013's own tick is in the first row, and in the `n=1` bucket.

## The bound

```python
UNREADABLE_MAX_CONSEC = 5      # the entire observed recovery distribution
UNREADABLE_MAX_S      = 60.0   # 41.9s longest recovered span; ~47s at cadence
```

Whichever trips first ends the hold. The cycle bound is the one that normally
binds; the wall-clock bound exists because a loop that stalls could otherwise
sit inside five cycles for a very long time.

`tests/test_unreadable_recovery.py::test_the_bound_matches_the_measured_recovery_distribution`
pins both numbers to this census, so the bound cannot move without the
measurement moving.
