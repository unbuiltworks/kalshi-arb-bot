# August L5 forensic replay

**DIAGNOSTIC HISTORICAL REPLAY ONLY.** This study is **not validation**, is
**not** part of the prospective 25-market Shadow S1 cohort, and its artifacts
**must not be used to modify Candidate S1** while that cohort is running.

Candidate S1 is read, never written. Every entry point asserts the frozen
fingerprint and aborts if it differs:

```
441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe
```

## The question

Run frozen S1 through one complete, real August BTC15m window, chronologically,
using only what was knowable at each second, and put it side by side with what
L5 actually did — then say what **observable mechanism** made them diverge.

## Isolation from the live run

This study has its own branch, its own workflow, its own concurrency group, its
own request file and a two-directory commit allow-list. The workflow **refuses
to run on any branch but this one**, so it cannot push to the shadow branch or
interact with the prospective Shadow S1 run.

## Provenance of the inputs

| input | source | verification |
|---|---|---|
| 1 s L2 ladders | Zenodo record **22084977**, `updown_15m.parquet`, CC-BY-4.0 | sha256 `d86191506e…`, a value recorded in this repository by prior analysis **before** any acquisition workflow existed |
| L5's own fills | Polygon archive logs, joined **by CTF token id**, never by market name | a failed chunk is a **HOLE**, never an absence of fills |
| fill timestamps | real `eth_getBlockByNumber` timestamps | never interpolated; an unresolvable block stays missing |
| all-trader prints | the same block range with **no** address filter | each economic trade counted once (maker-order legs); taker aggregates reported beside, never summed in |

Both sources need CI: `zenodo.org` and `polygon.drpc.org` each answer
`CONNECT -> 403` from the research container.

## The three rules, enforced structurally

* **No crediting S1 with L5's fills.** Separate ledgers. S1's inventory moves
  only when the frozen classifier returns an `EVIDENCED_*` verdict on S1's
  **own** hypothetical order.
* **No touch = fill.** `UNCERTAIN_PARTIAL` and `QUEUE_CLEARED_FRONT_OF_BOOK`
  are recorded and **excluded** from economics, exactly as the frozen module
  treats them.
* **No future information.** `Book.at(t)` returns the latest row at or *before*
  `t`; prints come from `(t-1, t]`; the tape is walked strictly forward.

Each rule has a test in `tests/test_august_replay.py` that fails if it is
broken.

## Window selection never mentions S1

`select_window.py` imports no candidate and has no notion of a hypothetical
order or a P&L. Its gates read only the parquet and the chain backfill:

| gate | rule |
|---|---|
| G1 | all 900 seconds resolvable on **both** tokens, carry ≤ 60 s (the dataset's own REST resync bound) |
| G2 | L5 fills in **every** one of five equal 180 s sub-intervals |
| G3 | L5 fills in window ≥ 50 (also the ranking key) |
| G4 | **both** `RESTING_BID_CONSISTENT` and mint-consistent fills present |
| G5 | zero chunk holes in the chain scan |

## Two arms

In the frozen module `last_prints` is initialised empty and **never assigned**,
so `cum` is always 0 and `EVIDENCED_TRADE_THROUGH` cannot fire — live S1 polls
`GET /book`, which carries no trades.

* **Arm A** reproduces that faithfully (prints always empty). **This is the
  headline**, because it is what the frozen code actually does.
* **Arm B** supplies the real chain prints the historical tape does have, as a
  sensitivity test.

## Artifacts

| file | what it is |
|---|---|
| `WINDOW_SELECTION.json` | every window, every gate, and why the selected one won |
| `l5_backfill.json`, `block_times.json` | L5's canonical fills and their real block timestamps |
| `window_prints.json` | all-trader prints for the selected window |
| `TRACE_<arm>.csv` / `.jsonl` | **one row per second**: timestamp, TTE, UP/DOWN BBO and depth, L5's fills and ledger, S1's resting orders, evidenced fills and ledger |
| `REPLAY.json` | both arms' totals, divergence markers and the rules in force |
| `MECHANISM.json`, `SUMMARY_<arm>.md` | each candidate mechanism reduced to a number, and the compact write-up |
