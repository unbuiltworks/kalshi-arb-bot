# E1 Phase 2 — the PMUS-native switchover

> **E1 IS DISARMED.** `PMUS_E1_LIVE`, `PMUS_E1_ARM`, `PMUS_E1_SESSION_ID`,
> `PMUS_E1_GATE_EVIDENCE` and `PMUS_E1_AUTHORIZED_FINGERPRINT` are blank on
> the service. No owner value is set. Cohort `E1-COHORT-20261007T1849Z`
> stands at **4/12 windows, 0 submitted, 0 filled, $0.00 exposure, $0.00
> settled, $0.00 realized**, untouched.

## 1. What was wrong

E1 discovered its market on **`gamma-api.polymarket.com`**, pinned it as
**two CTF token ids**, streamed **Polymarket.com's public CLOB
websocket** for prices — and then serialized that identity into an order
to **`api.polymarket.us`**.

Polymarket.com and Polymarket US are **different venues**. PMUS names a
BTC15m window `cpc-btc-updown-15m-YYYY-MM-DD-HHMMz` and an outcome
`OUTCOME_SIDE_YES`/`OUTCOME_SIDE_NO`. It has **no CTF token ids at all**.
The venue was right: **Market not found**, five times, window 3 of the
cohort at 19:15:47Z.

A second, independent defect in the same window: an **absent first-leg
bid became `0.00`**, which the `<= $0.60` ceiling read as the cheapest
possible maker opportunity.

## 2. Phase 1 — identity, proven by asking the venue

Read-only, one guarded `POST /v1/order/preview`, no `/v1/orders`, no
latch, no retry.

| step | result |
|---|---|
| `pmus_identity.slug_for(window_start)` | `cpc-btc-updown-15m-2026-10-07-2100z` |
| PMUS metadata (`/v1/events/slug/…`) | resolved |
| PMUS BBO/book | served |
| `POST /v1/order/preview` | **HTTP 200 ACCEPTED** |
| venue echo | `marketSlug` as sent, `outcomeSide: OUTCOME_SIDE_YES`, `quantity: 0.13`, `ORDER_STATE_PENDING_NEW` |
| `marketMetadata.eventSlug` | `btc-updown-15m-2026-10-07-2100z` |

The venue **recognised the PMUS-native slug**, so P0-1 is proven and
Phase 2 was authorized to proceed. No Gamma host, no CTF token id and no
`ws-subscriptions-clob.polymarket.com` appeared anywhere in that proof,
checked via `sys.modules` after the work was done.

Note the last row: PMUS's own metadata carries the Polymarket.com-style
string as an **event** slug, not as the market identifier. That is
exactly the kind of near-match that made the original defect look
plausible, and it is **not** a translation we may rely on.

## 3. Phase 2 — what changed

### Identity and feed

| was | is |
|---|---|
| `selector.py` → `src.l5_market_binding` → Gamma | `pmus_selector.py` → `pmus_identity.slug_for` → `src.l5_pmus_transport:resolve_market` |
| `live_book.py` → `src.l5_clob_ws`, two CTF token books | `pmus_book.py` → `src.l5_pmus_auth_book.AuthBook`, **unmodified** |
| `provider.py`, both-tokens readiness | `pmus_provider.py`, pinned-market readiness |
| timing authority `websocket_strict` | `pmus_authenticated_ws_strict` |
| boundary 2 checked slug + **two token ids** | slug + **both outcome sides** |
| NO side = a second token's book | NO side = the venue's arithmetic complement (`NO bid = 1 − YES ask`) |

AuthBook is **reused, not vendored**: `pmus_book.py` contains no
websocket core, no `wss://`, no `WebSocketApp`, asserted by test. Its
stall/resubscribe semantics (`STALL_S = 10.0`,
`RESUB_SLOW_S = 30.0`) are AuthBook's own, from the 2026-09-24 1215z
incident.

### The absent-bid defect

A first-leg price that is absent, empty, zero, negative, non-finite,
out of `(0, 1)`, crossed or stale is now **INELIGIBLE** — an explicit
refusal before preview, not a cheap maker price. The `<= $0.60`
comparison was *not* the fix; it is still there, and it is now only ever
reached by a price that already proved itself executable.

### REST is not timing authority

Three independent refusals, at three layers:

* the **feed** refuses a snapshot whose `source` is not
  `authenticated_ws`;
* the **provider** refuses, on every snapshot, a health record whose
  `timing_authority` is not the PMUS authenticated book, or which claims
  `rest_is_sufficient_timing_authority`;
* the **recorder** refuses to write an attempt row against such a book
  state.

An AuthBook failure has no REST branch to fall into.

## 4. Two defects my own tests found while migrating

Both are real, both predate the test that caught them.

1. **`_candidate_starts` applied the already-consumed filter only to the
   computed list.** An injected list could re-open the same window start
   twelve times: **one market observed, twelve windows of the budget
   spent.** The filter now applies on every path.
2. **Nothing refused a candidate whose window had already ended**, so a
   dead window could consume one of the twelve. It is now refused before
   authorization.

## 5. The accepted hazard, and why it is bounded

Reusing AuthBook drags its **eighteen-file transitive closure** into the
image, including **`src/pmus_q1_executor.py`**, which can place and
cancel real orders from its own `PMUS_Q1_*` namespace. This is a second
order-capable stack in E1's container, and it is **deliberately
accepted** rather than worked around by re-implementing a websocket.

It is bounded three ways, each **tested**, two of them **at build time**:

1. **Importing it does nothing.** A fresh interpreter replaces
   `socket.socket`, `socket.create_connection`,
   `SSLContext.wrap_socket`, `urlopen` and both
   `HTTPConnection.connect` with raisers, then imports E1's entrypoint,
   AuthBook and the executor. Statically, every top-level node in the
   executor is a definition, import or assignment — no call runs on
   import.
2. **E1 refuses to boot beside an armed Q1.**
   `arming.assert_no_foreign_executor_authorization()` raises
   `SystemExit` if **any one** of the five Q1 authorization keys is set,
   and `boot()` calls it before the arming gate and before any network.
   A blank value reads as absent (Railway blanks by setting empty);
   `PMUS_Q1_STATE_DIR` is **not** authorization — it points at Q1's
   historical evidence, which must stay mounted.
3. **E1 calls nothing in it**, and it is **absent from `sys.modules`**
   after importing E1 — AuthBook's import is lazy and scoped to
   `start()`, so a disarmed boot does not load the closure at all.

The Q1 arming variables on this service are **blanked**.

## 6. The fingerprint changed, honestly

```
superseded  481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486
current     9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920
invariants  139 -> 153
```

The old fingerprint is recorded in
`SPEC['supersedes_e1']['must_not_be_rearmed']` with its reason: *it
ordered Polymarket.com identity to Polymarket US, and it treated an
absent first-leg bid as 0.00.* It was **not** preserved artificially to
make the diff look smaller — this is a genuine executable semantic
change and the spec says so.

**No economic or risk parameter moved.** 0.13 shares, $0.60 first leg,
$0.96 pair, 20 attempts/window, 12 windows / 240 placements,
$1.56/window, $5.00 cohort ceiling, 13 complement tranches, maker-only,
no chase, no requote, no taker rescue.

## 7. Differential against the pre-switchover build

`20013fa8b` → HEAD: **278 tests → 314**. Forty removed, sixty-five
added, plus eleven restored.

Thirty-four removals are correct — they test `price_change` frame
shapes, CTF token ids and one-token-silent races, all facts about a
venue E1 does not trade on.

**Six were not.** A differential of the test inventory showed six
removed tests covering concerns that are *not* venue-specific, with
nothing replacing them — verified by grep before the claim was made:
zero occurrences anywhere in the package's tests of the frozen-clock
bound, the terminal-sweep classification, or the no-latch-while-waiting
guarantee. Restored in `test_e1_pmus_readiness.py`:

1. the wait is bounded **against a frozen clock** — `now_fn` is
   injected, so a wall-clock deadline alone is not a bound;
2. the shipped bound is 20.0s/0.25s, asserted against the **source
   literals** (conftest shrinks the live attributes for every test here,
   so reading them would assert the test value and prove nothing);
3. a readiness success carries its own proof;
4. a feed failure is **terminal for the sweep** — twelve candidates, one
   dead socket, exactly one refusal and **zero** windows consumed;
5. no latch slot is spent while waiting (the latch has no release path);
6. a failed wait leaves no pinned window behind, and the book is never
   read before health is ok.

## 8. What is still open

**The PMUS best-bid parse is not yet pinned by test.** The window-3
probe recorded `price_source: PMUS_BEST_BID, price_sent: 0.1300`, while a
`"value": "0.4900"` also appears in the same BBO/book payload. I am not
asserting which field is the true best bid. No E1 decision may depend on
that parse until a test pins it against a captured PMUS payload — and
since the first leg's price *is* that parse, **this is a prerequisite to
arming**, not a follow-up.

## 9. Accounting

Strict labels, unchanged:

**historical replay → shadow → live probe → submitted → filled →
settled → realized**

| | |
|---|---|
| windows consumed | 4 / 12 |
| submitted | 0 |
| filled | 0 |
| open exposure | $0.00 |
| settled | $0.00 |
| **realized** | **$0.00** |

Nothing above is realized profit or loss. Nothing has been submitted.
