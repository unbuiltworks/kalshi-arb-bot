# TTE — measured coverage, and what the next sample should do

**Correcting an estimate I gave before measuring it.** I judged `endDate`
coverage from `PMUS_SPRING_WATCH_LIMIT`'s *default* of 10 and reported that TTE
would reach "a minority of the universe", roughly 10 of ~55 markets. The
deployed recorder does not run the default.

Measured on the live handoff, 2026-09-16 13:35Z, from `tte_observed` on the
V1.1 cycle line:

| cycle | markets | with an `endDate` | coverage |
|---|---|---|---|
| 4 | 50 | 47 | 94% |
| 5 | 52 | 49 | 94% |
| 6 | 49 | 45 | 92% |
| 7 | 51 | 47 | 92% |

**~92–94%, not ~18%.** The conclusion to hold this sample on the uniform
sentinel is unchanged and still right — a 92%-covered TTE is still a *mixed*
population, and changing the state key mid-sample would break the comparison.
But the reason is now "8% missing", not "82% missing", and that materially
improves the case for real TTE in the next version.

## Still unverified, and it gates the next version

Coverage is not fidelity. `endDate` is the venue's **scheduled** end; the venue
serves no settlement timestamp for live sports, so whether `endDate` tracks
actual resolution is **not yet established**. A sports market that runs long,
goes to overtime, or is suspended will resolve away from its scheduled end, and
the historical state key's TTE buckets (`tte<2m`, `tte2-5m`, `tte5-10m`,
`tte>10m`) are fine-grained enough that a few minutes of error moves the bucket.

The evidence to settle it is already accruing: `observed_end_date_tte_s` is
recorded on every observation and universe row, and `aec-…` markets resolve
within the sample. Comparing recorded `endDate` against observed resolution
times answers it directly, with no new collection.

## What the next version does

1. Measure `endDate` vs actual resolution over this sample's own rows.
2. If the error is small relative to the bucket boundaries, feed real TTE and
   re-run the comparison as **v1.2 with its own maturity clock** — never mixed
   into a sample already in flight.
3. If it is not, keep the sentinel and say so; a wrong TTE is worse than a
   constant one, because it moves the state key toward states L5 was never in.
4. Either way, the 6–8% with no `endDate` need an explicit branch. They must
   not silently take the sentinel while their neighbours take a measured value —
   that is the mixed population in miniature.

Nothing here changes the running sample. This sample runs on the sentinel.
