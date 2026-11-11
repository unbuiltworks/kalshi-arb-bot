# CONTINUOUS-8 — proposal, and the authorization boundary

**STATE: READY_TO_MUTATE_BUT_UNAUTHORIZED.** Constants are `None`, the
workflow carries the sentinel, `authorized` is `false`. Nothing here can arm.

```
executable_sha  e32e31f2711c10d99a499717d654bee76468d55171b3f5f13d5bff8439cd369f
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      bfc4ec1e3a7efcff19778ae0f9f3c0f8dc7752c949d256c59eb7f5d0fb2d9fe6
```

**The envelope has not moved for an eighth window.** Executable and bundle
moved, and the diff that moved them is below.

## Two changes. Nothing else.

Unchanged and verified unchanged: the frozen L5 controller, maker-only
semantics, pricing behaviour, 0.01 sizing, the absolute risk envelope, the
coherent authoritative-state gate, terminal cleanup, no pair-cost ceiling, no
taker crossing, no SELL, no forced flatten, no scaling.

### P0 — the book each decision was made against

The controller always had the book. `state_from_venue` fetches it when the
caller passes none, hands it to `WindowGuard.step`, and **throws it away**. So
C7's record could say what we quoted and never what we were quoting against,
and "why did nothing fill" had to be read backwards out of our own prices plus
`ticks_behind_bid: 0`. That inference was sound. It is still an inference, and
it is the substitution this program refuses everywhere else.

* **`ws_continuous.decide` now returns the market state it decided on.**
  Additive; nothing reads it back; no decision, bound, price, size or ordering
  depends on the key existing. This is what moved `executable_sha`.
* **Per cycle, recorded:** timestamp · TTE · YES bid/ask · NO bid/ask · both
  spreads · passive and crossing pair cost · last trade · tick · the
  complement-identity check and its residuals · source · readability — beside
  the controller inventory, working orders, newly reconciled fills, desired
  OPEN/EXTEND/COMPLETE/HOLD rungs and the KEEP/CANCEL/PLACE plan that were
  already captured.
* **Size comes from a second, public read.** `GET /v1/book/{symbol}`, proven
  against these symbols over 8,186 samples
  (`analysis/l5/gemini_depth/INVESTIGATION.md`). It has **its own clock**, so
  it is reported *beside* the presented book and never merged into it, and the
  claim that its touch **is** the presented YES touch is **checked every
  cycle** rather than asserted once in a comment.

**Nothing is synthesized.**

| what | how it is recorded |
|---|---|
| presented sizes | `null` **with a reason** — the contract object publishes prices only. Never `0`, which would claim "no resting interest". |
| NO-side depth | `null` with a reason — the venue publishes **one** ladder. An ask size is **not** relabelled a NO bid size. |
| unreadable book | `readable: false` + the venue's own `why`, and **no prices at all** in the record |
| identity-inconsistent book | reported as not-a-book, with the residuals, because the controller quotes nothing into it |
| depth read failed | `readable: false` + the error; the presented book still recorded |

**Behaviour-neutrality, proven as before** — byte-equivalent across the
driver's report, the frames that reached the session, and the append-only
ledger:

* with the observer on and the size read **on**;
* with a size reader that **raises** (recorded as unreadable, window
  identical, error kept);
* with **no** size reader (the row says so rather than going missing).

The size read is taken *before* `inner_decide` and is **not passed to it**, so
it cannot reach the decision. Its elapsed time is measured per cycle and
totalled in the report, because that cost comes out of maker resting time and
an unmeasured cost is one nobody can weigh. Measured live: **RTT 0.24–0.55s**.

### P1 — when the experiment begins

C4 and C7 entered at 129.1s and 136.3s and filled nothing across 19 cycles. C5
and C6 entered at 281.8s and 206.8s and took one fill each.

**n=4 is a correlation and is not encoded as strategy.** Nothing the
controller reads knows this number exists. `--entry-tte-band 240:280` chooses
only **when** the experiment starts — which until now was decided by when the
workflow happened to reach the armed step. C7 got 136s by accident of the
dispatch clock.

Properties that carry it:

* **It can only narrow.** The band is intersected with the standing
  `[90, 300]` rule, so it can never enter a contract the normal rule refuses.
  Pinned by test with a band of `10:3000`.
* **It sends nothing.** Its whole signature is `(band, max_wait_s, poll_s,
  now_fn, sleep_fn, scan_fn)` — no session, no signer, no frame is reachable.
* **It runs before the latch** — before the preflight, recovery and the
  authorization gate — so a wait that never reaches the band refuses with the
  authorization **unspent**.
* **It is bounded**: 420s, one contract cycle plus margin. From the worst
  arrival (just under the band) the next band opens ~260s later.

## A defect this found before it cost anything

The job-level timeout was **15 minutes** against a 12-minute armed step —
correct for a window that began immediately. With a bounded wait ahead of it,
the runner would have been **killed mid-window**. A killed runner never
reaches the terminal withdrawal, so the orders stay live with nothing left to
cancel them: CONTINUOUS-5's post-stop fill, with no recovery path.

Job **30**, armed step **18**, and a test now pins
`job > step > wait + window`.

## The eight items

| # | item | result |
|---|---|---|
| 1 | full suite | **6,423 pass**, 27 skipped, 1 xfailed |
| 2 | exact hashes | above; envelope unmoved, 8/8 |
| 3 | fresh cohort chained to C7 | `COHORT-2026-09-18-C8B`, budget **12**, predecessor C7 `3e8399a3…` at 6/6, `intact: true`. C8 (6 slots) is **abandoned at 0/6**, byte-for-byte empty, pinned by test. |
| 4 | C5/C6/C7 ledgers preserved | C5 `b2404035…` 6 rows (3/6, UNKNOWN intact) · C6 `3996eb94…` 12 rows · C7 `3e8399a3…` 12 rows · historical `89cf9de6…` 12/12. Last commit touching any of them is C7's own evidence commit. |
| 5 | production-shaped UNARMED verification | run 35410681217, **at the armed window's own 240:280 band**: waited 126.4s over 12 polls, entered at **TTE 271.3s**, then `READY_TO_MUTATE_BUT_UNAUTHORIZED` — latch **not** consumed, **0 orders, 0 wire frames**, preflight `agrees: true`, recovery true, coherence readers both true, authorization the sole blocker (three reasons) |
| 6 | book capture present | live read-only proof below, plus the real-`_build` integration test |
| 7 | entry-TTE scheduling proven, no order | live read-only proof below, plus 20 unit tests |
| 8 | stop at the boundary | **stopped here** |

### 6 — the capture, live and read-only

`analysis/gemini/book_schedule_proof_20260919T003029Z.json`, public reads
only, no credential, no session, zero orders:

```
PRESENTED  GEMI-BTC05M2609190035-UP  tte 278.5s
           YES 0.56 / 0.80  spread 0.24      NO 0.20 / 0.44  spread 0.24
           passive pair 0.76   crossing pair 1.24   last 0.48   tick 0.01
           identity consistent, source = the events contract prices (PUBLIC)
           sizes null -- "this source publishes PRICES ONLY"
DEPTH      bids best 0.56 @ 10.0 across 5 levels
           asks best 0.80 @ 760.0
           rtt 0.243s
AGREEMENT  bid_agrees true, ask_agrees true, agrees TRUE
```

Note in passing what C7 could not have shown: at TTE 278 this contract was
**genuinely two-sided**, 0.56 against 0.80, with 10 resting on the bid and 760
on the ask. C7's book at TTE 125 was 0.01 against 0.95. That is one
observation, not a result — but it is the *kind* of observation the capture
exists to make, and it is now measured rather than reconstructed.

The `decisions` key is **legitimately absent** from the unarmed artifact: the
refusal precedes Driver construction, so the observer is never built. That was
true for C7 too and it is why the capture is proven by an integration test
that drives the real `_build` through a real window, asserting the book
reaches `rep["decisions"][0]["book_state"]` — on the **asks and spreads**,
which a maker never quotes and which therefore cannot have come from our own
rungs.

### 7 — the scheduling, live and read-only

Same artifact, real public scan, no order path:

```
waited 225.0s over 24 polls -> GEMI-BTC05M2609190035-UP at TTE 278.5s
```

Inside the band, well inside the 420s bound. The unarmed workflow run also
exercised the scheduling path **production-shaped** at the standing band
(`90:300`, which narrows nothing): `in_band true`, `waited_s 0.36`,
`polls 1`, handed off into the rest of `_build` normally.

## The budget question, answered

> **Will the existing 6-slot budget terminate an early-entry window before it
> has a reasonable opportunity to observe a fill?**

**Yes — very likely.** Every observed mutation rate projects above 6.

Measured from C5/C6/C7's own decision records. "mid" is mutations that are
neither the two opening places nor the end-of-window cancels:

| window | entry TTE | cycles | span | slots | open | mid | end | mid per 100s | stopped on |
|---|---|---|---|---|---|---|---|---|---|
| C5 | 281.8s | 2 | 7.5s | 3 | 2 | 1 | 0 | 13.33 | cancel rejected |
| C6 | 206.8s | 8 | 64.8s | 6 | 2 | 4 | 0 | 6.17 | **budget, 6/6** |
| C7 | 136.3s | 12 | 101.7s | 6 | 2 | 2 | 2 | 1.97 | end of window |

A window entering at 240–280s runs **~210–250s** before the 30s withdraw
floor. Projecting each observed rate onto 250s, plus 2 opening places and the
2 end-of-window cancels C7 actually paid:

| at this window's rate | slots needed |
|---|---|
| C7 — the calmest observed, six consecutive KEEPs | **~8.9** |
| C6 | **~19.4** |
| C5 — only 2 cycles, weakest evidence | ~35 |

**Even the calmest observed rate exceeds 6.** And C6 is not a projection: it
*already* stopped on budget, at 6/6, after 65 seconds of window. An
early-entry window is roughly 4× longer than that.

The fixed costs alone are 4 of the 6: two opening places, two end-of-window
cancels. That leaves **two slots** for ~210s of re-pricing.

### Authorized and created: C8B at 12 slots

**`COHORT-2026-09-18-C8B`, budget 12.** C8 at 6 slots is abandoned before it
ever ran, kept byte-for-byte at 0/6, and never to be used.

* 12 is `CP.MAX_ACTIONS` — **the frozen envelope's own ceiling**.
  `cohort.create` refuses anything above it, so this moves **no bound and no
  `envelope_sha`**. It is lifecycle capacity for a window that runs roughly
  twice as long, not financial risk.
* It covers C7's projected 8.9 with margin and does **not** cover C6's 19.4,
  so a C6-rate window still stops on budget. That is the correct failure
  direction.
* Every absolute bound is untouched: $0.15 spend, 0.04 residual, 0.04
  imbalance, 0.10 per leg, 0.01 size, MOC maker-only, no pair-cost ceiling.

**The lineage, and why it is not the immediate predecessor.** C8B chains to
**C7's** ledger, not to C8's.

`verify_predecessor` is **single-hop** — it checks only the ledger a cohort
names, and does not walk back through that ledger's own predecessor. So
whichever file C8B names is the *only* one the live path enforces before the
latch. C8's ledger is **empty**: it carries no mutation history to protect,
and chaining to it would have traded C7's six spent slots for a file whose
hash is `e3b0c442…`, the sha256 of nothing.

C5, C6 and C7 each chained to the last cohort that had actually consumed
slots. Until C8 that always coincided with the immediate predecessor; C8 is
the first cohort that is created and never used, so the two notions diverge
for the first time. The canonical reading of the chain — *the mutation
history that must not be rewritten* — selects C7.

C8 is therefore pinned by **test** rather than by the chain: its ledger must
stay byte-for-byte empty, and no workflow step may name it. The manifest
records all of this in full.

## If you authorize

```
Authorization ID  OWNER-2026-09-19-GEMINI-CONTINUOUS-8
executable SHA    e32e31f2711c10d99a499717d654bee76468d55171b3f5f13d5bff8439cd369f
envelope SHA      b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle SHA        bfc4ec1e3a7efcff19778ae0f9f3c0f8dc7752c949d256c59eb7f5d0fb2d9fe6
cohort            COHORT-2026-09-18-C8B   (budget 12)
entry band        240:280
```

The experiment is unchanged and remains solely:

> **REAL FILL → COHERENT AUTHORITATIVE REFRESH → INVENTORY CHANGE → CHANGED
> L5 ACTION.**

Not profit, and no quote is altered to manufacture a fill.

**STOPPED AT THE AUTHORIZATION BOUNDARY.** Nothing is armed and no window is
scheduled.
