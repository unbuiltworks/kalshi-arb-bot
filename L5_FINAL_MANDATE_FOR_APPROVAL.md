# $5 L5-style two-sided live campaign — FINAL mandate

**NOT ARMED. Nothing submitted. No variable written. No loss limit, capital
figure or risk control changed. Strategy, research and inventory engine
unchanged.**

One decision is needed before the approved watchdog deployment can happen
(§2). Everything else is done.

---

## 1. Regression comparison — ONE regression, found and fixed

HEAD vs HEAD~2, full suite, run in a **git worktree** so the working tree was
never stashed.

| | failed | errors | passed |
|---|---|---|---|
| baseline `HEAD~2` | 58 | 23 | 13,152 |
| `HEAD` | 60 | 23 | 13,179 |
| **new, attributable to this patch** | **2 tests, 1 root cause** | 0 | — |
| no longer failing | **none** | | |

The two new failures were
`test_l5_live_probe_image_closure::test_image_import_closure_is_complete` and
`::test_removing_any_closure_module_fails_the_check` — one root cause, now
fixed and re-verified passing.

**The one regression:** `l5_live_authorization` imported
`l5_mandate_signature` at module level, which pulled it into the import
closure of every image carrying the authorization module. `l5_live_probe` and
`l5_halt_reset` do not COPY it, so both images could no longer import their
own entrypoint — `ImportError: cannot import name 'l5_mandate_signature'`.
`test_l5_live_probe_image_closure` caught it. **An ImportError at boot is
worse than a missing feature.**

**Fixed with a lazy import**, not three new COPY lines: the gate runs once per
arming attempt, so a local import costs nothing, each image's surface stays at
what it runs, and no future Dockerfile has to be chased. Same pattern the
module uses for `cryptography` and the verifier uses for this module.

**Everything else classified against the baseline, not assumed:** all four
`test_r1_source_lineage` failures, both `test_deploy_fallthrough_guard`
failures, both `test_l5_live_runner_graft` byte-identity pins and both
`test_lane_a_probe_v0` lineage checks **fail on the baseline too** — the
baseline in fact fails one more. None is mine.

---

## 2. Watchdog deployment — BLOCKED on one decision

The fix is committed and reviewed. **It cannot reach the running watchdog**,
and not for a reason either of us could have known without looking.

**The two services build from different, unrelated-history branches:**

| service | branch |
|---|---|
| `l5-pmus-live` | `claude/l5-defensive-8window-prod` |
| `l5-halt-watchdog` | **`claude/gemini-btc-economic-viability-h3u7zl`** |

`git merge-base` reports **no common ancestor**. The watchdog's branch does
not contain `src/l5_mandate_signature.py` at all, and its watch pattern is
`deploy/l5_halt_watchdog/DEPLOY_TRIGGER` **on its own branch** — so appending
to that file on my branch deploys nothing.

### Review completed (all of it passes)

- **Module surface:** imports only `__future__`, `datetime`, `decimal`,
  `json`, `os`, `typing`, `urllib.request`, and `cryptography` **lazily**. No
  transport, no PMUS module, no order path. Its one `urlopen` is the GET-only
  document client; the image already reads a URL for the emergency halt marker.
- **Boot safety:** `cryptography>=42` is already installed in the image, and
  the import is inside `_verify_ed25519`. Both pinned by new tests.
- **Image closure:** new transitive test over 11 modules, **proven to have
  teeth** (deleting the COPY fails it). The watchdog had no such test before;
  the executor has had one for a while.
- **Halt behaviour unchanged:** `HALT_KEY` path byte-identical,
  `do_POST`/`do_PUT`/`do_DELETE` still undefined, served keys an **exact
  allowlist** (which also closes a traversal shape, since `FileStore._path`
  maps a key straight onto a filesystem path).
- **A redeploy will not clear a halt.** The last `L5_HALT_RESET` event
  (2026-10-19T01:47:49Z) was `REFUSED — NO_LATCH_PRESENT`, with "a spent reset
  id refusing here is the expected steady state". The reset id is spent and
  the hook exits 0 without touching the latch.
- Targeted tests: **35 passed** (18 arming gate, 13 watchdog, 4 probe closure).

### What repointing would carry

I diffed it rather than assuming. `l5_halt_gate.py` and `l5_watchdog.py` are
**byte-identical** between branches. The delta is three files:

1. my mandate publisher + served-key allowlist + the COPY;
2. an **emergency-halt-marker** subsystem from earlier work on my branch — a
   second halt path that can only **assert** a halt, never clear one, and is
   **inert** without `L5_EMERGENCY_HALT_URL` (reports `configured: false`).

Both are safety-additive. Nothing loosens.

### Your options

- **D1 (recommended)** — repoint `l5-halt-watchdog` to
  `claude/l5-defensive-8window-prod`, then deploy. One call, reversible with
  one call. It also ends the split-brain between the two services. **But it is
  a source/provenance change to a safety service, not the bounded deployment
  you approved — and Railway notes it recreates the service's deployment
  triggers and re-resolves GitHub authorization — so I have not done it.**
- **D2** — I port the three watchdog files onto
  `claude/gemini-btc-economic-viability-h3u7zl` and deploy from there. Leaves
  the service config untouched; keeps the two histories split.

**§3 (independent endpoint verification) is blocked on this.** The endpoints
are on Railway private networking, so I will verify them through the
watchdog's own boot telemetry — which now reports
`mandate_documents_served`, `mandate_doc_max_age_s` and
`mandate_trusted_keys` — plus a live freshness and revocation check, as soon
as a deployment happens.

---

## 4. `submission_budget_open` 32 vs 16 — resolved, and it is not drift

I was wrong to call this drift. It is **documented and reasoned**:

> `L5_CAMPAIGN_PROPOSAL_8WINDOW.md`: *"`L5_LIVE_AUTH_MAX_OPEN` | `32` (from
> 16) | the one budget 8 windows would exhaust. 2015z and 2030z spent 5 OPEN
> between them; at that rate 8 windows need ~20, against a ceiling of 16."*

The arithmetic for **this** campaign:

| rounds/window | windows | OPEN needed | ceiling that fits |
|---|---|---|---|
| 2 | 8 | 16 | 16 |
| **4** | 8 | **32** | 32 |

So 16 and 32 are both correct — for different campaign shapes. The verifier's
`APPROVED` table holds a bare `16` that was never updated when the figure was
reasoned up.

**I have changed nothing, and I am not raising the approved count silently.**
The resolution is for the OPEN budget to be an **explicit term of this
mandate**, sized to the rounds you approve:

- approve **4 rounds/window** → `L5_LIVE_AUTH_MAX_OPEN=32`, and the
  verifier's `APPROVED["submission_budget_open"]` becomes 32 **by your
  decision, recorded here**;
- approve **2 rounds/window** → leave 16 and lower the runtime to match.

---

## 5. B7 — the 2026-10-23 forfeiture, exact impact

The campaign settled **+$0.1336** over 8 windows, 5 of 8 completing. Had it
rested both legs simultaneously:

| component | amount |
|---|---|
| actual net | **+$0.1336** |
| forfeited complement improvement | **−$0.5335** |
| dear-side stranding | **−$1.2982** |
| **would have been** | **−$1.6981** |

A **−$1.8317 swing, 12.7× the campaign's entire net.**

**Forfeiture, per window:** the complement was priced against the *current*
book from the *realised* first-leg cost, so four of five completions beat the
planned 0.95 — 2145z 0.91, 2215z 0.76, 2300z 0.74, 2330z 0.89. At plan prices
those five earn $0.2685 instead of $0.8020, i.e. **$0.0993 per contract
forfeited** over 5.37 contracts.

**Stranding:** today the *cheaper* leg is placed first, so an unmatched window
can only strand the cheap side. With both resting, the **dear** side can fill
alone:

| window | qty | cheap | dear | extra loss | ratio |
|---|---|---|---|---|---|
| 2230z | 1.47 | 0.12 ($0.1764) | 0.83 ($1.2201) | **+$1.0437** | **6.92×** |
| 2245z | 1.10 | 0.45 | 0.50 | +$0.0550 | 1.11× |
| 2315z | 1.05 | 0.38 | 0.57 | +$0.1995 | 1.50× |

### Does it affect the loss limits?

**It does not breach them, and it does expose a real weakness in one.** The
$5 campaign budget is worst-case and already prices every resting order as
filling in full, so the campaign still cannot lose more than $5.

**But the residual cap is denominated in CONTRACTS, which is price-blind:**

| 3 contracts at own price | dollars at risk |
|---|---|
| 0.12 | $0.36 |
| 0.45 | $1.35 |
| **0.83** | **$2.49** |

The same unchanged "3-contract cap" admits a **6.9× range in dollars**
depending on which side strands — and two-sided resting is precisely what
makes the dear side reachable.

**Mitigation, using a control that already exists and is currently disabled:**
`max_worst_case_deficit_usd` (`L5_MAX_WORST_CASE_DEFICIT_USD`) bounds the
worst-case deficit on **either** outcome, priced with taker fees plus a cent
per order. At **$2.00**:

| clip | own price 0.80 | verdict |
|---|---|---|
| 2 contracts | $1.64 | allowed |
| 3 contracts | $2.45 | **refused — binds before the contract cap** |

So it permits the proposed clip even on the dearest side and refuses the
dear-side accumulation that is B7's actual hazard. **This is a tightening and
I am proposing it as part of the mandate.**

### Does it affect reconciliation?

Mechanically no — reconciliation stays venue-authoritative with exact-order
cancellation. But a dear-side residual is larger in dollars, so the **49 legs
in `leg_cost_basis_unknown`** matter more: an unpriced dear leg misstates
realized P&L by more than an unpriced cheap one. That is why B6 should be
cleared before results are quoted as realized.

---

## 6. Owner signing procedure — prepared

Full steps in `L5_SIGNED_MANDATE_FOR_APPROVAL.md` §6 and
`docs/OWNER_MANDATE_SIGNING.md`. **I do not create, store, request or ever see
your private key.** It is generated by you, on your machine, and must never
enter this repository, Railway, a log, a chat or any assistant.

1. Generate the Ed25519 pair offline (script in §6).
2. Send me the **64-hex public half only**. I commit it to `MANDATE_PUBKEYS`
   as `owner-2026-10` in a reviewed change.
3. Fill `not_before` / `not_after` in `MANDATE_PAYLOAD_5USD.json` (≤4 h apart)
   and nothing else.
4. Sign with `tools/owner_sign_mandate.py` — it refuses a key inside the repo
   tree or a group/world-readable key file, and dry-runs the real verifier.
5. Paste the signed document into `L5_MANDATE_DOC` on `l5-pmus-live`.

Revoke by: halt latch (fastest, executor cannot clear) · `L5_MANDATE_REVOKED`
on the watchdog · raising `seq`.

---

## 7. The mandate

| parameter | value |
|---|---|
| **Max aggregate exposure, incl. outstanding orders** | **$5.00** — every gate prices a resting order as filling in full |
| **Max simultaneous orders** | **2**, only ever the two legs of one pair, one market, one round |
| **Per-order quantity** | **2 contracts** — the venue minimum is 0.01, but a maker print below ~1.6 earns $0.00 credit |
| **Rounds per window** | **4** (`L5_TS_ROUND_ALLOCATIONS`), sized from the $5 envelope |
| **Residual limit** | **3 contracts absolute**, clip 2, **plus a $2.00 worst-case deficit bound** (new, §5) |
| **Daily realized loss halt** | **$5.00** on the UTC-day figure |
| **Cumulative account-wide** | **$20.00** — $11.6213 used, **$8.3787 remaining** |
| **Campaign worst-case budget** | **$5.00** |
| **Duration** | **≤ 8 windows and ≤ 4 hours**, whichever first |
| Series | `cpc-btc-updown-15m-` only |
| Execution | **maker-only, post-only both legs**; no action name expresses crossing |
| Pair cost cap | ≤ $0.96 per pair |
| First-leg own-price floor | $0.20 |
| Concurrent markets | 1 |
| **OPEN submissions** | **32** (4 rounds × 8 windows), §4 — your explicit decision |

**Automatic halts:** watchdog HALT latch (executor cannot clear) · daily loss
≤ −$5 · campaign budget exhausted · account-wide −$20 · residual ≥ 3
contracts · **worst-case deficit > $2.00** · authorization expiry · window
budget spent · per-window timing (entry 600 s, complete 720 s, cancel margin
**300 s**, TTL 120 s, runway 300 s) · unresolved accounting · no auto-relaunch
after a completed or crashed session.

**Reconciliation:** venue-authoritative on every uncertainty and periodically;
exact-order cancellation by venue id; an order absent from the venue's open
orders is terminal but **not released until an authoritative `/v1/order` read
books its final `cumQuantity`**. Capital is released on **settlement evidence
alone**, never on a `matched` flag — unsettled inventory is never freed
capital.

### Configuration changes this mandate authorizes

| change | from | to | why |
|---|---|---|---|
| `L5_TS_TWO_ORDER_PAIR` | off | `1` | without it the campaign is not two-sided |
| `L5_TS_ROUND_ALLOCATIONS` | 1 | `4` | continuous inventory-aware rounds |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | 3 | `2` | keeps a single-side fill off the residual cap |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | unset | `2.00` | **new** — makes the residual bound price-aware (§5) |
| `L5_MANDATE_STORE_URL` | unset | the watchdog's private URL | the signature gate refuses without it |
| `L5_LIVE_AUTH_MAX_OPEN` | 32 | **32, confirmed** | §4 — not a silent raise |

---

## Outstanding blockers

| # | blocker | owner |
|---|---|---|
| **D** | watchdog branch split — choose **D1** or **D2** (§2) | **you** |
| **B2** | `MANDATE_PUBKEYS` empty — send the public key | **you** |
| **B3** | campaign approval stale; `L5_LIVE_AUTH_NOT_AFTER` expired | **you** |
| **B6** | 49 legs with no cost basis — degrades realized-P&L measurement | **me**, next |
| **B7** | accept the forfeiture, or run sequential (§5) | **you** |
| — | sign the mandate | **you** |

Once D is chosen I deploy the watchdog and verify §3; once the key arrives and
the mandate is signed, arming becomes possible and I run the campaign within
these limits and report **actual filled, settled, realized** results.

## Accounting labels

Venue-confirmed realized results to date: **+$0.1336** (2026-10-23, 8 windows,
sequential two-sided) and **−$3.7194** (Stage 1, 8 windows). Maker credits
above are **OBSERVED** venue credits — $0.42 over 25 prints. Every figure in §7
is a **limit**, not a forecast. At $5 the campaign cannot measure the edge
(signal ÷ residual noise ≈ 0.087), so its honest purpose is **mechanical
validation** of continuous L5-style execution.
