# Morning decision sheet — 2026-10-07

One page. Sections marked **PENDING** are lanes still running at the time
of writing; each will be filled from its own verdict commit, never from my
summary of it.

---

## 1. Collector health and coverage — HEALTHY

Service `l5-book-shadow`, project `l5-book-shadow-public`, pinned by SHA to
`84e277297`. Public-data-only project: no credentials, no order-capable
module, mutation-incapable boot gate passing 4/4.

All five required live-proof items, from the deployed heartbeat:

| requirement | observed |
|---|---|
| independent two-token rows continue | `clob_rest_book` 74 → 296 → 516; `tokens_tracked` 72–74 across 36–37 markets; **`single_token_markets: []`** |
| L5 activity rows advance | **627 → 636 → 643** |
| a known public L5 activity item joins to its market/token | **`joined_any: true`**, join key = the venue's own token id |
| `http_429` remains zero | **0** |
| request pacing ≤ 240 RPM | **238.8 / 225.0 / 223.0** measured (not the cap) |

Durable storage verified read-only: `both_tokens_ok: true`,
`defective_markets: []`, and pre- and post-restart raw parts coexisting
(`tape_20261007T03Z.jsonl` + `tape_20261007T04Z.jsonl`, 39.7 MB) —
append-only across six restarts, `TapeWriter.MODE` still `"a"`.

**Honest caveat:** 37 activity rows have no recorded book. That means we
did not poll those tokens, **not** that their books were unchanged. The
collector reports this rather than dropping it.

### The one data-integrity defect you must know about

`BOOK_ONLY_ACTIVITY_UNAVAILABLE` ran **03:33:10Z → 04:17:51.286Z (44m41s)**.
The tape's own interval starts at 04:03:01.868Z, because the OPEN edge
could not be written before the code that writes it shipped. So
`derived/activity_windows.json` **understates the outage by 29m52s**.

> Exclude the TRUE bound, not the derived file's narrower one. Activity
> inside it is UNMEASURED, never a zero-fill observation.

The tape was not backfilled: manufacturing provenance for rows never
observed would be worse than the gap. Books were healthy throughout.

Root cause of the outage: `data-api` keys activity on the **proxy wallet**,
not the profile handle, and reports a non-address `user` as *absent*
(HTTP 400 `required query param 'user' not provided`) — so a wrong identity
was indistinguishable from a missing parameter. Repaired GET-only, plus a
blocking boot-gate row so it cannot recur silently.

Also built: the dual-token CLOB websocket strict-timing arm
(`src/l5_clob_ws.py`), separate schema and tape stream,
`timing_authority=websocket_strict`, both tokens on one socket for a shared
receive clock. It refuses to accumulate until a live proof shows both
tokens streaming. **Not yet proven live.** REST rows remain
reachability/same-second evidence and were not reinterpreted.

---

## 2. New L5 activity since the canonical corpus — **PENDING** (Lane 3)

The decisive question: the August reward program disappeared — did L5
abandon or change the subsidy-harvesting posture afterward? This governs
whether the *latest* behavior is worth copying at all.

---

## 3. S3 verdict — **INSUFFICIENT**, and the instrument is the finding

`markets_COMPLETE = 0` against a required **25**. Run `37536917745`
finished on its own at 04:21:36Z, untouched, all 15 steps green.

Evidence integrity held, in the required order: both artifacts uploaded
`if: always()` before any git op; the fingerprint assertion passed; the
data commit `5cec58cc` is a verified ancestor of the branch head. **The old
race recurred** (`5d24217..e6a2d11` moved underneath) and the new rebase
absorbed it on attempt 1 — so the four workflow fixes earned their keep.
Green CI here does not mean the evidence disappeared successfully.

Two defects matter more than the verdict, and both say *broken instrument*,
not *absent opportunity*:

1. **The complement side was never observed** — `complement_never_observed:
   true` in all five markets. Direct cause of the gate breach
   (`two_sided_evidenced_acquisition_below`, observed 0.0 vs threshold
   0.20). A two-sided instrument fed a one-sided tape cannot produce
   matched inventory. The gate caught precisely what it was built to catch.
2. **`markets_COMPLETE` is unreachable by construction** — window spans
   537–787 s against an 840 s criterion, with healthy 4.6–6.0 s gaps. Edge
   truncation, not dropout. The 25-market bar cannot be met however long
   collection runs.

Nothing was tuned; no threshold was relaxed to reach a verdict. Zero orders
submitted or signed. Nothing realized. Unexplained and worth a check: the
tape is ~50× smaller than the lost earlier run at comparable duration,
consistent with the missing complement.

**S3's substantive question is unanswered.** This is not a negative result
about opportunity.

---

## 4. Stage-6 candidate protocol and fingerprint — **PENDING** (Lane 4)

Built, not armed. Spot-checked for safety state only: `armed: False`, its
own `PMUS_S6P_*` arm keys (no collision with A6's `PMUS_Q1_*`), a separate
fingerprint-pin env, and derived from XR2W by deep-copying its `SPEC`
rather than duplicating it — a duplicate serializer is what caused the A5
HTTP 400.

---

## 5. Historical / shadow evidence for or against — **PENDING** (Lane 5)

Bound to report three buckets — WOULD_FILL, WOULD_NOT_FILL, **UNKNOWABLE**
— never merged, with the UNKNOWABLE fraction prominent. If it dominates,
the honest verdict is INCONCLUSIVE rather than an optimistic point
estimate. No parameter fitting.

---

## 6. Maximum dollars at risk — **$0.00 live tonight**

No live order was placed overnight, by instruction. No owner value is set;
no order-capable image is deployed. **A6 remains FILLED but NOT REALIZED** —
settlement unresolved. At 0.01 shares the PMUS maker rebate is structurally
$0.00 after the hard cent floor (below 1.62 shares), so A6's liquidity-role
ambiguity does not affect its rebate economics.

---

## 7. Exact live authorization proposed — **PENDING** on lanes 4 and 5

I will not propose an authorization until Lane 5's historical evidence is
in. The honest sequencing: a frozen protocol is necessary but not
sufficient; what decides authorization is whether the policy's first leg
becomes matched ≤ $0.96 often enough, and how bad the residual gets when it
does not.

---

## 8. The single highest-value thing to do next

**PENDING** the remaining lanes. The leading candidate, on evidence already
in hand: S3's "complement side never observed" defect is the same class of
problem that the new two-token collector was built to solve, and the new
collector demonstrably does *not* have it (`single_token_markets: []`,
`both_tokens_ok: true`). If that holds, re-running S3's question against
the new collector's tape is worth more than any new strategy — it would
convert a measurement failure into an actual answer.

---

### Standing boundaries honored overnight

No live orders. No parameter tuning. No evidence deleted. S3 untouched
until it terminated on its own. XR2W, its spec and its fingerprint
untouched. A6 untouched.
