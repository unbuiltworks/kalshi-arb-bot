# P2: the first 180 seconds — recorder running, transition described

**Gemini BTC5m primary. Controls frozen as accepted; no further
UP/DOWN/favorite/underdog permutation was run on the T+180-and-later corpus. No
threshold optimization. No live Gemini orders.**

---

## 1. The recorder, and what two live partial tapes already taught us

Running in CI via `.github/workflows/gemini-btc5m-recorder.yml` (settlement-gate pattern,
own file because that workflow is at the 25-input ceiling; started by a request-file push
because the API won't dispatch a workflow absent from `main`). Read-only is proved over
the AST before anything runs, and that guard has passed on every run.

### Two checkpoint families, chosen not to overlap

| family | grid | anchor |
|---|---|---|
| **EARLY** | **T+5 / 15 / 30 / 45 / 60 / 90 / 120 / 150 / 180** | `availableAt` — the moment the venue captures the reference strike |
| LATE | tte 90 / 60 / 45 / 30 / 20 / 15 / 10 / 5 / 2 / 1 | expiry |

For a 300s life, T+60/T+120/T+180 **are** tte 240/180/120, so those three left the late
grid rather than being emitted twice under two names. Every checkpoint carries
`checkpoint_label`, `checkpoint_family`, `elapsed_since_t0_s`, `t0_epoch` and
**`t0_source`** — which reads `availableAt` or `expiry_minus_life`, so no row can
silently claim a T it inferred. T is upgraded to the published value the moment the venue
publishes it.

### What the live tapes measured (and one of them was a real defect)

The cancelled run left a 524-row tape (`btc5m_rec_20261005T133214Z.jsonl.gz`). Four
findings, two of which changed the code:

1. **The venue leaves `sourceDetails` NULL.** Live BTC5m contracts return
   `source_agency: null`, `source_index: null`, and only the **deprecated flat**
   `source: "LS-LRRR-XBTUSD"`. My index task required *both* agency and index, so it
   subscribed to nothing: **requirement 1 produced zero `index_price` rows, silently.**
   That is the part that mattered. Now the agency is resolved from the index prefix
   (`LS-` → `lukka`, `GRR-` → `kaiko`; a declared agency always wins) and **every
   candidate spelling is sent in one frame** — `lukka:LS-LRRR-XBTUSD@indexPrice`, the
   upper-case variant, and the bare form — with the venue's ack or error recorded as
   `index_ctrl`. The venue tells us which spelling it accepts instead of us guessing once
   and failing quietly.
2. **The early book is reachable and was TWO_SIDED**: a T+180 checkpoint came back
   `book_state: TWO_SIDED`, `book_integrity: OK`, `quote_age_s: 0.99`.
3. **Ladders carry real multi-level sizes** — and the first one is instructive:
   asks `0.22×192`, `0.98×1`, `0.99×1` against bids `0.02×1`, `0.01×1`. A **20-cent
   spread whose bid side is 1-lot dust.** TWO_SIDED is a structural label, not an
   economic one; this is exactly why full ladders with sizes are recorded rather than the
   touch.
4. **Venue `E` is nanoseconds, ~2s behind our receive.** The ms/ns unit is now decided by
   magnitude per value — guessing wrong turns a 40 ms age into 40 000 s — and the raw `E`
   stays on every `p_depth` row either way.

### Validation is window-aware, and will fail rather than flatter

The old validator proved rows of each *kind* existed somewhere in the file, which is not
the question. It now requires **the first completed BTC5m window** to carry: an EARLY
checkpoint whose `book_state` is TWO_SIDED; an EARLY ladder with sizes on **both** sides;
a checkpoint whose `reference.value` is populated (the index stream and the checkpoint
actually met); and a T from the **published** `availableAt`. Stage 2 — the 9-hour
continuous run — is unreachable until all of that is on disk.

If TWO_SIDED turns out to be absent from the early region, **that is the finding**, and
the validator reports it instead of passing. 16 unit tests cover each requirement failing
closed, plus the book, gap detection, the complement derivation, the unit test for event
age, and the agency resolution.

**Run status**: runs 1–3 were cancelled or failed on my own defects (missing `pytest`;
then the old grid; then the null-`sourceDetails` index bug, caught by its own tape).
**Run 4 (`37318383391`) carries all fixes and is starting now.** Captured per window:
both outcome ladders with sizes, trades, strike, expiry, contract and reference
identifier, `availableAt`, raw `indexPrice` messages verbatim, timestamps and provenance,
and explicit book state. Raw rows are written alongside the derived ones; the NO ladder
is labelled DERIVED; book gaps are reported as `book_integrity: GAP`, never repaired.

---

## 2. The transition study — descriptive, label quarantined

`scripts/gemini_transition_study.py`. The side that **later** becomes the persistent
one-sided leader is identified retrospectively; the earlier checkpoints are then described
against it. **The future label lives only in a `label` block, and the script asserts that
no `causal_features` block names it** — an assertion, not a convention. Causal features
are preserved separately so the no-leakage prediction question can be asked later from the
same artifact without rebuilding it.

On the existing 133 windows (which begin at tte 120s = T+180s):

| | |
|---|---|
| windows with a persistent one-sided leader | **121** of 133 |
| that leader matched settlement | **78.51%** |
| its ask was **NEVER** executable at any checkpoint | **55** of 121 (**45.5%**) |
| its ask was executable at least once | 66 |
| its ask **reappeared** after vanishing | **15** |
| last tte its ask was present | median **75s**; distribution bimodal — **19** at tte 120, **20** at tte 1 |
| pre-disappearance span | median **30s**, p10 **0.0s**, max 119s |

**So a tradable pre-disappearance interval exists — in about half the windows, and it is
short.** Three caveats carried on the rows themselves: the grid cannot prove continuity
*inside* a span; a p10 of 0.0s means a tenth of those windows were executable at exactly
one checkpoint; and the 19 spans ending at tte 120 are **truncated by the corpus's own
first observation**, so they are lower bounds. That truncation is precisely the gap the
recorder is now filling.

Per window the artifact preserves: whether the eventual leader was executable at each
earlier checkpoint, its executable ask and VWAP (flagged: this tape records one level per
side, so VWAP *equals* the touch — the recorder's full ladders will make it a real VWAP),
depth, spread, eventual settlement, the presence/absence timeline, and the reappearance
flag.

---

## 3. P1 — LS-LRRR narrowed, Z still **NOT IDENTIFIED**

Progress, and it is real progress:

- **`LS-LRRR` is the Lukka Real-time Reference Rate**, and the stream is spelled
  `lukka:LS-LRRR-XBTUSD@indexPrice`. That confirms both the agency token and the
  subscription form my recorder now sends.
- **Lukka's published approach is a different family from Kaiko's.** Lukka Prime ranks a
  **principal exchange** at intervals and prices from that primary source — not a
  partitioned volume-weighted median across venues. So the Kaiko reasoning does not carry
  over at all, and any Z built on it would have been wrong in kind, not just in parameter.
- **The LRRR calculation window remains unestablished** in anything reachable from here;
  `lukka.tech`, `kaiko.com` and `developer.gemini.com` are all egress-denied in this
  container.

**Nothing fabricated. Z stays NOT IDENTIFIED.** The decisive route is now the recorder
itself: a published reference series needs no reconstruction. The first tape that carries
`index_price` rows will say whether that route works, and `index_ctrl` will say why if it
does not.

Sources for the narrowing: [Gemini stream reference](https://developer.gemini.com/prediction-markets/websocket/streams) · [Lukka index calculation services](https://lukka.tech/index-calculation-services/) · [Lukka Prime deep dive](https://lukka.tech/lukka-prime-deep-dive/)

---

## 4. PERSIST4 → bounded q1 cohort: the exact action required

**PERSIST4 is accepted as PASS. No PERSIST5 exists, is proposed, or is needed.** The
cohort profile, budget, durable latch and acceptance all exist and are tested; **no new
code is required.**

What remains is one authorization message from you containing exactly these five, none of
which I may supply myself:

1. **A new authorization id** — e.g. `OWNER-2026-10-05-GEMINI-CI15M-PERSIST4-LIVE`.
   CI15M-1/2/3/4 are retired and the build asserts a retired grant cannot be re-armed, so
   the previously approved mechanics cohort cannot run under its old id.
2. **The execution SHA, as a full 40-character SHA.** A shorter ref is resolved by
   `actions/checkout` as a branch and the probe step skips silently — that has already
   happened once in this programme.
3. **Explicit confirmation of the envelope** (or its correction): BTC15m, 4 windows,
   **q=1**, ≤2 submissions per window and 8 total, **one outstanding order**, Treatment T,
   acquisition ≤ 0.90, pair ceiling ≤ 0.96, EXTEND ≥ 5¢, max 2 EXTENDs, ≤3 unmatched
   shares, ≤ $2.70 unmatched principal, **$2.00 realized-loss stop**, one market at a
   time, deadman enabled.
4. **The words that this is a LIVE, money-moving authorization** — the probe refuses to
   construct an order unless `arming.EXPECTED_AUTH_ID` equals the live grant.
5. **Acknowledgement of the known residual behaviour**: 3 of PERSIST4's 4 shadow windows
   ended holding an unmatched leg. That is the complement-availability constraint, not a
   defect, so a live run should be expected to carry residual within the ≤3-share /
   ≤$2.70 caps rather than complete every pair.

On receipt I pin the SHA, run the no-credential gate, and dispatch. **Nothing is armed
until then.**

## Artifacts

`transition_study.json` · `scripts/gemini_transition_study.py` ·
`src/gemini_research/btc5m_recorder.py` · `.github/workflows/gemini-btc5m-recorder.yml` ·
`tests/test_gemini_btc5m_recorder.py` (16) ·
`analysis/gemini/btc5m_rec/btc5m_rec_20261005T133214Z.jsonl.gz` (the partial tape that
exposed the index defect).
Accounting label: historical replay on Gemini's own recorded books and settlements, plus
live public market-data recording. No order was sent.
