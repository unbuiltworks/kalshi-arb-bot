# Index-stream verdict: **the Lukka reference stream is live.** Z still not identified.

**Scope unchanged. No new strategy experiments. No live Gemini orders.**

---

## 1. The verdict, per spelling, individually attributable

| subscription | venue response | |
|---|---|---|
| `LS-LRRR-XBTUSD@indexPrice` | `status 400`, `code -1013`, `"Invalid stream name: LS-LRRR-XBTUSD@indexPrice"` | **REJECTED** |
| `LUKKA:LS-LRRR-XBTUSD@indexPrice` | `status 200` | **ACCEPTED** |
| `lukka:LS-LRRR-XBTUSD@indexPrice` | `status 200` | **ACCEPTED** |

So the **agency prefix is required** and the venue is **case-insensitive** about it. Each
verdict is attributed by the request id `idx|<candidate>`, recorded on the row, so none of
this rests on inference.

A prefixed stream succeeded, so per your instruction the recorder went straight into
**validated long BTC5m collection** — run `37321367522`, stage 2, 32,400s.

## 2. Validation: **9 of 9 PASS**

| check | |
|---|---|
| `a_completed_btc5m_window` · `early_checkpoints_present` · **`early_two_sided_book`** · `early_ladder_has_sizes` · `t0_from_availableAt` · `availableAt_seen` · `venue_timestamps` · **`index_reference_stream`** · **`index_reference_at_a_checkpoint`** | all **PASS** |

Early book states on that tape: **TWO_SIDED 13 of 13**. Late: TWO_SIDED 17, ONE_SIDED 4,
EMPTY 1. 3,065 rows, 478 `index_price` rows, 35 checkpoints carrying a populated
`reference.value` — the stream and the checkpoints actually met, with reference ages of
**0.29–1.45 s**.

### One more defect of mine, and it cost a cycle

The step still reported **failure** on a 9-of-9 pass. Stage 1's *informational echo* named
`v["book_states"]`, a key my validator rewrite had renamed to
`early_book_states`/`late_book_states` — `KeyError` → exit 1 under `bash -e` → stage 2
skipped **for a cosmetic reason**. The echo is now `.get()`-based with `|| true` and
cannot fail the job; the real gate stays in its own step asserting `v["pass"]`. A passing
validation can no longer be lost to its own logging.

---

## 3. What the published reference series actually is

Recorded verbatim, 478 messages:

```json
{"e":"thirdPartyPrice","E":1791208280,"s":"btcusd","I":"LRRR_XBT",
 "p":"86325.31683333333","P":"","x":null,"u":226894}
```

Established by measurement:

| property | value |
|---|---|
| cadence | **1 Hz** — all 477 consecutive `E` deltas are exactly 1 |
| `E` units | **epoch seconds** (10 digits), not ms or ns |
| venue's internal index id | **`LRRR_XBT`** |
| symbol | `btcusd` |
| event kind | `thirdPartyPrice` |
| sequence field | `u`, monotonic |
| age at a checkpoint | 0.29–1.45 s |

## 4. A hypothesis I formed, tested, and must report as **refuted**

The first three values I looked at all satisfied "value × 60 is an exact multiple of
0.01", which would have meant a 60-observation mean of two-decimal prices — and would have
been a clean answer to the settlement-window question.

**It is wrong.** Across all 478 values:

- ×60 gives an exact 2-decimal product in only **165 of 478 (34.5%)**
- **313 of 478 have no minimal denominator at all for N ≤ 240**
- the minimal-N distribution is scattered (20, 5, 10, 4, 1, 2) and **does not divide 60**

The three I checked were coincidences. I nearly reported them as a finding, and the only
reason I did not is that I tested the whole series instead of the sample that suggested
it. So the fractional structure **does not support a simple equal-weight mean of
two-decimal inputs** — the inputs are not 2-decimal, or the statistic is not that mean.

**Z remains NOT IDENTIFIED. No alternative settlement model is proposed or guessed.**

What has genuinely changed is that **this no longer blocks the research the way it did.**
The published series is now being recorded at 1 Hz with provenance, so a causal state
variable can be built from **observed reference values** rather than from a reconstructed
statistic. The remaining unknown is narrower and separable: how Lukka *computes* `LRRR_XBT`
(needed to project the settlement value forward from a partial window), versus what it
*published* (needed to know the state, and now recorded).

---

## 5. Early L2 collection is preserved, and the shortest path if the reference had failed

Four tapes are committed under `analysis/gemini/btc5m_rec/`, including the two that
exposed the `sourceDetails`-null and batched-frame defects. The early region is captured
with full multi-level ladders; one window (`BTC05M2610051350`) was TWO_SIDED at **all
nine** T+ checkpoints:

| | T+5s | T+15s | T+30s | T+60s | T+120s | T+150s |
|---|---|---|---|---|---|---|
| bid | 0.02 ×2501 | 0.32 ×25 | 0.46 ×25 | 0.19 ×815 | 0.63 ×25 | 0.92 ×5 |
| ask | 0.98 ×2499 | 0.58 ×25 | 0.60 ×12 | 0.39 ×8 | 0.80 ×25 | 0.98 ×1 |
| spread | 0.96 | 0.26 | **0.14** | 0.20 | 0.17 | **0.06** |

Observations on n=2 windows, not claims: the book is unpriced dust at both extremes at
T+5s and prices in by T+15s; thereafter spreads run **6–26¢** with real depth on both
sides, against the **33¢** median of the ≤120s corpus. The second window was the dust
regime instead (bid pinned 0.02×1), so both regimes exist and the sample cannot say how
they divide.

**The shortest path, as requested — and it is now contingency rather than plan, since the
stream succeeded.** Had both prefixed spellings failed, the one-line change is to move
`index_reference_stream` and `index_reference_at_a_checkpoint` out of the blocking set
into a **reported-but-non-blocking** set in `REQUIRED_FOR_VALIDATION`. Everything else
stays: the recorder still subscribes, still writes every `index_ctrl` verdict, and the
reference fields on each checkpoint simply read `null` with the rejection recorded beside
them. L2 collection then proceeds immediately while the reference question is resolved on
its own track — authoritative Lukka methodology, or a separate probe of other stream
spellings — with no tape discarded and no settlement model guessed. I have **not** made
that change, because it is not needed.

## Artifacts

`analysis/gemini/btc5m_rec/` — 4 tapes + `validation.json` (9/9) ·
`src/gemini_research/btc5m_recorder.py` · `tests/test_gemini_btc5m_recorder.py` (17).
Accounting label: live public market-data recording. No order was sent.
