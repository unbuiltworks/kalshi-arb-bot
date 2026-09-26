# PMUS BTC15m joined-tape coverage

Running record of windows collected by the combined read-only collector
(service `9ce058ce`, volume `b51edade` at `/state/l5-pmus-obs`). One row per
15-minute BTC window. No trading, no treatment, no threshold work here: this
file exists only to say how much usable tape exists and on how many
independent days.

Primary-eligibility gate, frozen before any data arrived
(`src/l5_window_collector.py`):

    observed_fraction >= 0.95     max_gap_s <= 20.0
    prints >= 1                   L5 feed reachable

## Windows

| window | observed | max gap s | prints | book states | raw frames | L5 actions | primary | excluded for |
|---|---|---|---|---|---|---|---|---|
| 2026-09-26 1300z | 0.000000 | 900.000 | 0 | 0 | 0 | 235 | no | OBSERVED_FRACTION, GAP, NO_PRINTS |
| 2026-09-26 1315z | 0.000000 | 900.000 | 0 | 0 | 0 | 101 | no | OBSERVED_FRACTION, GAP, NO_PRINTS |
| 2026-09-26 1330z | — | — | — | — | — | — | no | no record; lost to the credential redeploy |
| 2026-09-26 1345z | 0.499688 | 450.281 | 500 | 2318 | 4599 | 140 | no | OBSERVED_FRACTION, GAP |
| 2026-09-26 1400z | 0.994019 | 5.383 | 500 | 5231 | 10697 | 153 | **yes** | — |

Causes, so the gaps are not later mistaken for quiet markets:

* 1300z / 1315z ran before the collector had `PMUS_API_KEY_ID` /
  `PMUS_API_SECRET_KEY`. The raw feed never connected. These are
  `FEED_NEVER_CONNECTED` in substance; the `NO_PRINTS` reason predates the
  loudness fix that separates the two.
* 1330z has no record at all: the credential redeploy replaced the container
  mid-window.
* 1345z opened twice (`lead_s +11.2`, then `lead_s -450.2`) because a push
  landed 450s into it. The 450.3s gap and the 50.0% fraction are that push,
  not the venue.
* 1400z opened at `lead_s -5.2` and ran to close with no interference. The
  0.6% it misses is those 5.2s.

**Coverage status: 1 primary-eligible window, 1 independent day.**

## Known limitation: `prints` is capped at 500

`pmus_ws_evidence_recorder.apply_events` keeps a ring buffer of the last 500
trades per market (`del s["trades"][:-500]`). Both 1345z (half a window) and
1400z (a full one) report exactly 500, so the cap is binding in both. The
`prints` array in `window.json` is therefore a CEILING, not a count, and for a
busy window it holds the LAST 500 prints rather than all of them.

This does not corrupt any record, because `RawTapeFeed.ingest` writes the
frame to the sink BEFORE folding it into state. `frames.jsonl` holds every
frame verbatim and uncapped -- 10,697 of them for 1400z.

Two consequences, recorded rather than acted on:

* `primary_eligible` is unaffected; it only asks `prints >= 1`.
* The frozen 9fa00b9 study reads `auth_book_tape.jsonl`. When that tape is
  rebuilt from these windows it must be derived from `frames.jsonl`, not from
  `window.json`'s folded `prints`, or every window silently loses its early
  prints. The study's own definitions do not change.
