# Batch 2: window provenance ruling, made BEFORE the frozen run

The mechanical manifest over `pmus_ws_20260930_10..14.jsonl` certified **20 of
21** windows complete and settled. Only `0945z` was excluded, for two
independent mechanical reasons: the stream starts 900.1s into it, and the market
state is `MARKET_STATE_CLOSED` with no settlement evidence.

That is comfortably above the preregistered >= 10 floor. But two of the 20 are
NOT untouched, and both are disclosed here before any economics are computed.

## 1. `1445z` -- my committed prediction was WRONG

`analysis/TWO_SIDED_FREEZE_AND_BATCH2_PREREG.md` stated that passing no 15Z file
would make the manifest "exclude the `1445z` window automatically as truncated".
It did not: hour 14 carries the whole 14:45-15:00 window AND settlement
evidence, so the manifest correctly certified it complete.

`1445z` appeared in Batch 1's manifest as an EXCLUSION ("stream starts 900.1s
into the window"), so the slug has been seen. Its economics were never computed
and it was never replayed. Nonetheless the committed rule asserted strict
disjointness and named this window as excluded, so it is excluded here. Honouring
the rule's stated intent beats honouring my mistaken prediction of its effect.

## 2. `1330z` -- development-contaminated

`scripts/l5_run_causal_ws.py:21` still defaults to
`data/pmus_ws_stream/raw_l2/pmus_ws_20260930_1330z.jsonl.gz`. That 5.01-minute
partial slice is the smoke-test tape whose MECHANICS were analysed at length
(HOLD_THE_LEVEL's no-op root cause, HOLD_OPEN_EXTEND requote/queue behaviour).
It is the same market window. Partial and under older arms, but it is
development data, so it cannot be part of a clean OOS sample.

## Ruling

**Primary OOS set = 18 windows:** the 20 certified windows minus `1330z` and
`1445z`.

This is a PROVENANCE decision, taken before any Batch-2 economics were
computed or inspected. It is not outcome selection. 18 >= 10, so the floor holds
without reaching for additional hours.

## The freeze is not touched

`49799e99` stays byte-identical. The frozen comparison runs over all five files
and reports all 20 windows per-window; the 18-window primary aggregate is
computed from that per-window output afterwards. The two contaminated windows'
figures are reported separately and explicitly, never folded into the OOS claim.
No arm, metric, threshold or selection rule inside the frozen code changes.
