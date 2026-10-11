# Can we acquire the leading side before its ask disappears? **No, on both taker routes.**

**Gemini BTC5m primary, BTC15m control. No threshold optimization on the 133 windows.
No live orders.** The $21–26/window one-sided figure is treated as unreachable
descriptive value throughout; this document is the attempt to reach it.

---

## 1. The recorder is wired and running in CI

`src/gemini_research/btc5m_recorder.py` + `.github/workflows/gemini-btc5m-recorder.yml`,
following the settlement-gate pattern (pinned tree, long timeout, no-cancel concurrency,
evidence commit under an allowlist) in a file of its own — `gemini-settlement-gate.yml`
is **at the 25-input `workflow_dispatch` ceiling** and a 26th input makes the whole file
unparseable, which already cost this programme one run. Started by touching
`.github/btc5m_recorder_request`, because the API refuses to dispatch a workflow absent
from the **default** branch and this research does not belong on main — the same
mechanism the pmus research workflows use.

**The four additions**, over a collector that already preserved raw depth diffs with
`U`/`u` sequence ids, venue `E` stamps, raw snapshots, raw trades and window metadata:

1. **`{agency}:{index}@indexPrice`**, subscribed **per contract from that contract's own
   `sourceDetails`**, so the index a window actually settles against is the one
   recorded. Every message is persisted **verbatim** in `raw`; the parsed
   value/timestamp sit beside it and are explicitly best-effort, because the stream's
   schema could not be read from this container (`developer.gemini.com` is egress-denied
   here). **The raw row is the authority.**
2. **`availableAt`** under its own name, with `sourceDetails` agency+index rather than
   only the deprecated flat `source`, plus the whole raw contract object.
3. **Checkpoint rows at 240/180/120/90/60/45/30/20/15/10/5/2/1s** carrying the **full
   YES ladder — price AND size, every level** — plus the derived NO ladder and the
   reference value as of that instant.
4. **`book_state` TWO_SIDED / ONE_SIDED / EMPTY written explicitly**, with
   `quote_age_s` from our receive stamp and `event_age_s` from the venue's own `E`.

**Nothing is transformed away.** Raw depth/snapshot/trade/index rows are written as
received *alongside* the derived checkpoint rows. The NO ladder is labelled **DERIVED**
(NO ask = 1 − YES bid at the YES bid's size) with the raw YES ladder beside it as the
authority. Book integrity is **reported, never silently repaired**: a sequence gap sets
`resync_pending` and every checkpoint emitted while it is set carries
`book_integrity: "GAP"`, because "we could not see the book" is itself the finding this
lane cares about.

**BTC5m is primary**: the checkpoint tick scans `BTC05M` first, each duration has its
own grid, and `--series-regex` can exclude 15m outright. BTC15m rides the same
connection and the same REST listing at no cost to 5m.

**Read-only is a checked property, not a claim.** A pre-run AST guard asserts the module
imports nothing from `gemini_execution`/`arming`/`transport`/`payload`/`sell_path` and
mentions no credential or mutation string. It **passed on the first run**. 13 unit tests
cover the book, zero-size deletion, gap detection, ladder ordering with sizes, the
complement derivation, the checkpoint grids, and a validator that fails closed on each
missing requirement.

**Collection starts only after validation.** Stage 1 records 480s and then proves *from
the file on disk* that books-with-sizes, the index reference, venue timestamps,
checkpoint rows, book-state labels and `availableAt` were all persisted; a missing
requirement fails the job and stage 2 never runs. Stage 2 is the 9-hour continuous run.

**Run 1 (`37317162502`) failed in 48s at the unit-check step: `No module named pytest`** —
my install line named only `httpx` and `websockets`. The read-only guard had already
passed. Fixed and re-triggered; the run is live.

---

## 2. The transition, characterized

133 windows, within the recorded ≤120s grid:

| shape | windows |
|---|---|
| **NEVER_TWO_SIDED** | **71** |
| TWO_SIDED → ONE_SIDED | 48 |
| INTERLEAVED | 12 |
| NEVER_ONE_SIDED | 2 |

**Only 80 of 133 windows ever had an executable ask on the leading side at all** within
this grid. Where that last ask sat:

| tte of the last executable leader ask | 120s | 90s | 60s | 45s | 30s | 20s | 15s | 10s | 5s | 2s | 1s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| windows | 16 | 11 | 4 | 7 | 9 | 3 | 2 | 4 | 1 | 3 | 20 |

The grid cannot date disappearance finer than "absent at every later checkpoint", and
the artifact says so on every row rather than implying a precision it does not have.
Per transition the artifact preserves the last executable leader ask, its depth, the YES
bid, the spread, the TTE, the proxy reference and distance, the subsequent winner, and
the state/leader sequences.

---

## 3. Mechanism A — taker before disappearance: **negative at every checkpoint**

Strictly causal: at each checkpoint, if the leader's own side has an executable ask, buy
it and hold.

| tte | n | ask | win % | breakeven | **edge** | CI95 | CI low − BE | net/trade | depth |
|---|---|---|---|---|---|---|---|---|---|
| 120s | 58 | 0.862 | 70.69% | 87.01% | **−16.32** | [58.6, 81.0] | −28.39 | −$0.162 | 43.5 |
| 90s | 36 | 0.887 | 63.89% | 89.42% | **−25.53** | [47.2, 77.8] | −42.20 | −$0.255 | 26 |
| 60s | 13 | 0.845 | 61.54% | 85.38% | **−23.84** | [30.8, 84.6] | −54.61 | −$0.238 | 1250 |
| 45s | 16 | 0.896 | 50.00% | 90.22% | **−40.22** | [25.0, 75.0] | −65.22 | −$0.401 | 1181 |
| 30s | 21 | 0.821 | 9.52% | 83.08% | **−73.55** | [0, 23.8] | −83.08 | −$0.734 | 58 |
| 15s | 26 | 0.800 | 7.69% | 81.12% | **−73.43** | [0, 19.2] | −81.12 | −$0.732 | 76.5 |
| 1s | 20 | 0.791 | 10.00% | 80.26% | **−70.26** | [0, 25.0] | −80.26 | −$0.701 | 76.5 |

**And the hindsight upper bound fails too.** Buying at the *last* checkpoint the ask
existed — which is **not knowable at the time** and is labelled hindsight-selected —
gives 80 trades at an average 0.859 ask winning **45.00%** against an 86.76% breakeven:
**−41.76pp**, CI [33.75, 56.25], net **−$0.416/trade**.

**This is the decisive finding of the turn.** The 94% leader accuracy belongs to the
leader when it has **no** ask. When the leader *does* have an ask, it wins 45–70%. So
the existence of an offer on the leading side is not merely "the wrong side is quoted" —
**it is evidence the leader is weaker than the one-sided population suggests.** Mechanism
A is closed, including its own best case.

---

## 4. Mechanism B — resting maker: **specified, blocked on the recorder**

Not estimated, because this tape cannot evidence it. An 11-point checkpoint grid carries
no queue position, no per-level arrival sequence and no trade-by-trade flow, so no fill
can be established conservatively. The existing 960-cycle census measured 0.6–1.6% fill
with a +0.12 adverse move but **did not condition on leading side or book state**, which
is exactly the conditioning that matters.

What the recorder must capture, and now does: every depth diff with `U`/`u` so
queue-ahead at our price is reconstructible; every trade with venue `E` so a reaching
print can be matched to a resting level; `book_state` and leader identity at the instant
of each print; and **the leading side's own ask depth, which the old tape never
recorded**.

---

## 5. Mechanism C — earlier state entry: the precursor works, the price already knows

| tte | two-sided windows | leader → eventual winner | leader → later ONE_SIDED leader | buying it |
|---|---|---|---|---|
| 240s | **NOT COLLECTED** | — | — | — |
| 180s | **NOT COLLECTED** | — | — | — |
| 120s | 56 | 69.64% | **83.33%** (n=54) | 0.870 ask, −18.18pp |
| 90s | 30 | 70.00% | **75.00%** (n=28) | 0.889 ask, −19.59pp |

So a causally available precursor — the two-sided book's own leader — **does** identify
the side that later becomes the one-sided leader, 83% of the time at 120s. That is real
information. It is also already in the price: the leader costs 0.870 where it settles
69.64%, an 18-point loss. 240s and 180s were never collected and are not substituted.

---

## 6. The controls

Already run and reported in `BTC5M_CONTROLS_FINDINGS.md` (commit `3508a399`), at the
earliest consistently available genuinely two-sided checkpoint, **tte = 120s (T+180s)**:

- **UP-only**: 36.47% win at 0.5031, **−15.59pp**, −$27.69/day. **DOWN-only**: 31.07% at
  0.4806, **−18.74pp**, −$40.40/day. Unconditional UP base rate 55.64%, CI
  [47.2%, 64.1%] — straddles 50%, so no directional advantage is established.
- **Initial underdog** (two-sided only): 30.36% vs 49.46% breakeven, **−19.10pp**.
  **Initial favorite**: 69.64% vs 87.83%, **−18.18pp**.
- The symmetric result: a side settles **36.47%** where it has an ask and **89.58%**
  where it has none (UP); **31.07%** / **90.00%** (DOWN).

Nothing in this turn changes them.

---

## 7. PERSIST4 → q1 live: the exact authorization required

PERSIST4 passed (`ci15m_shadow_persist_20261004T120508Z.json`: `pass true`, 4/4 windows,
all nine checks, 7/8 slots, writes disabled, clean `COHORT_EXPIRED`). **No further
shadow gate is proposed or needed.** To advance, I need one message containing exactly
these, and nothing here is assumed:

1. **The authorization id**, e.g. `OWNER-2026-10-05-GEMINI-CI15M-PERSIST4-LIVE` — a new
   id, because CI15M-1/2/3/4 are retired and the build asserts a retired grant cannot be
   re-armed.
2. **The execution SHA** this grant is given against, as a **full 40-character SHA** (a
   shorter ref is resolved as a branch and the probe step skips silently — that has
   happened once).
3. **Explicit confirmation of the envelope already proposed**, or its correction:
   BTC15m, 4 windows, **q=1**, max 2 submissions/window and 8 total, **one outstanding
   order**, Treatment T, acquisition ≤ 0.90, pair ceiling ≤ 0.96, EXTEND ≥ 5¢, max 2
   EXTENDs, ≤ 3 unmatched shares, ≤ $2.70 unmatched principal, **$2.00 realized-loss
   stop**, one market at a time, deadman enabled.
4. **The words that this is a LIVE money-moving authorization**, since the probe refuses
   to construct an order unless `arming.EXPECTED_AUTH_ID` equals the live grant.
5. **Acknowledgement of the known residual behaviour**: 3 of the 4 shadow windows ended
   holding an unmatched leg. That is the complement-availability constraint, not a
   defect, and it means a live run should be expected to carry residual within the
   ≤3-share / ≤$2.70 caps rather than complete every pair.

Given those five, no new code is required: the cohort profile, the budget, the latch and
the acceptance all exist and are tested. I will pin the SHA, run the no-credential gate,
and dispatch. **I am not arming anything until that message arrives.**

## Artifacts

`leader_acquisition.json` (transitions, A, B spec, C) ·
`scripts/gemini_leader_acquisition.py` · recorder at
`src/gemini_research/btc5m_recorder.py` + `.github/workflows/gemini-btc5m-recorder.yml`
· `tests/test_gemini_btc5m_recorder.py`.
Accounting label: historical replay on Gemini's own recorded books and settlements, plus
live public market-data recording. No order was sent.
