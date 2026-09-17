# Gemini BTC5m bounded live canary — authorization package

**Status: PREPARED, NOT SUBMITTED. Nothing is armed. This requires explicit
owner authorization and nothing here constitutes it.**

Prepared 2026-09-17 because `L5_ADAPTED_GEMINI_CRYPTO` reached `canary_ready`
in `src/l5_surface/lane_gates.py` — every ladder stage a canary needs is
evidenceable, and every account endpoint needed to score one answers.

---

## 1. What this canary is FOR

**It is not a profit test, and it must not be judged as one.** The Gemini
surface is `QUEUE_BOUND`: flow went through the sell side 20 times and cleared
zero fills. Expected P&L from a minimum-size canary is approximately zero and
could be slightly negative. That is fine, because the question it answers is
the one nothing else can:

> Does `/v1/prediction-markets/positions` populate a terminal outcome for a
> settled contract, and does that outcome reconcile against `/v1/balances` and
> `/v1/transfers`?

The endpoint is proven to **exist and be well-formed** (200, `{positions[],
total}`). It returns 0 rows because we hold nothing. Whether it **populates**
at settlement is unobservable until a position exists through an expiry, and
holding the canary back for that proof is circular.

**Success = the acceptance chain completes end to end. Not P&L.**

---

## 2. Venue terms, measured not assumed

From `/v1/symbols/details/GEMI-BTC05M<expiry>-UP`, read 2026-09-17:

| term | value | consequence |
|---|---|---|
| `status` | **`limit_only`** | the VENUE rejects anything but a limit order; maker-first is enforced by the venue, not only by us |
| `min_order_size` | **`0.01`** | one hundredth of a contract — the shadow's `UNIT=1` is 100× this |
| `tick_size` / `quote_increment` | `0.01` | |
| `product_type` | `Unsupported Instrument` | the details endpoint does not model prediction contracts, which is why it carries no expiry or settlement metadata |

---

## 3. The bounded envelope — TIGHTENED for the FIRST probe (owner, 2026-09-17)

| bound | value |
|---|---|
| windows | **exactly one** BTC5m window |
| orders | **exactly one** passive limit order |
| quantity | **0.01 contracts** (the venue minimum) |
| automatic second order | **NO** |
| automatic complement | **NO** |
| automatic repeat | **NO** |
| max residual | **0.01 contracts** |
| order type | **limit only** — enforced by the venue's own `limit_only` status |
| risk / deadman | **existing protections remain in force, unmodified** |
| queue priority | **absolutely no change intended to improve it** |

Enforced in code, not described: `src/gemini_canary_preflight.py` holds
`QTY=0.01`, `MAX_ORDERS=1`, `MAX_WINDOWS=1`, `MAX_RESIDUAL=0.01`,
`TICK_OFFSET=0`, with tests pinning each.

**On the last row, which is load-bearing.** The order quotes *at the touch*,
tick offset 0 — the same placement the shadow used for every observation that
justified running this. Changing placement to chase priority would make the
first live result incomparable with that evidence. Window selection ranks on
**instrument quality** (genuinely two-sided, then tightest spread), never on
where we would sit in the queue; depth at the touch is reported and not
optimised.

**Size is not a queue fix.** 0.01 is the venue minimum, chosen for risk and
partial-fill reasons: a lot at the minimum cannot partially fill, which removes
a source of unmatched residual. It does **not** reduce the volume already ahead
of us — the queue is unchanged and a smaller order waits behind the same depth.

### The cost of this envelope, stated before authorization

**The accounting half of this canary needs a fill.** With no fill there is no
position, so `/v1/prediction-markets/positions` stays empty and the settlement
question sits exactly where it is today.

The lane is **queue-bound** — flow went through the sell side 20 times and
cleared zero fills — so an **unfilled first order is the likely outcome**.

- **If it fills:** the durable-intent path, `/v1/mytrades`, the *population* of
  `positions` at settlement, and cash reconciliation are all exercised. That is
  the whole question answered.
- **If it does not fill:** `SUBMITTED → UNFILLED → EXPIRED` and the
  authoritative order-state read path are exercised; the settlement and
  position chain is **not**.

This is a cost of the envelope rather than a defect in it: improving queue
priority is the only thing that would raise the fill probability, and the
envelope forbids it. **It is the owner's call to accept.**

## 4. The acceptance chain this canary must complete

Per `src/gemini_settlement.py`, each link **PRESENT** or the run is not
accepted. The gate reports the **first break**, never a score.

| link | source | status today |
|---|---|---|
| contract | symbol under account | PROVEN |
| expiry | parsed from the venue's own symbol | PROVEN |
| authoritative outcome | `/v1/prediction-markets/positions` | **source proven (200, 0 rows)** — population at settlement is what this canary tests |
| position | `/v1/mytrades` | source proven (200, 0 rows) |
| settlement cashflow | `/v1/balances` + `/v1/transfers` | source proven (200; transfers has 9 real rows with `purpose`) |
| fees | `/v1/mytrades` fee fields + `/v1/notionalvolume` | source proven (maker/taker bps) |
| ledger reconciliation | venue cash vs arithmetic over our legs | implemented + tested, **unexercised** |

`REALIZED` is the venue's cash movement. Our arithmetic is reported beside it
as `reconstructed_cash`, and if they disagree **the venue is right** and the
delta is reported rather than reconciled away.

---

## 5. Kill conditions — any one halts, no exceptions

- any order that is not `limit_only` maker
- residual exceeding 0.01 contracts
- more than one outstanding order, or more than one market
- total notional exceeding $0.05
- daily loss stop $0.25 reached
- `venue_vs_ledger` disagreement on a resumed session
- any settlement endpoint that stops answering mid-run
- any unreadable order state that cannot be resolved to a terminal status

Fail closed: the session does **not** relaunch itself after a crash or a
completion.

---

## 6. Accounting labels — binding

Realized is **settlement-confirmed venue cash only**. Never counted as
realized: fills, theoretical or evidenced; `VOLUME_AHEAD_CLEARED`; modeled
pair economics; `synthetic_pair_realized`; mark-to-market; a cost basis;
estimated rebates or rewards. Maker rebates, taker rebates and liquidity
rewards stay in **separate ledgers** and enter nothing until credited and
source-accounted.

Report separately, never summed: matched-pair P&L, directional-residual P&L,
fees, incentives, total confirmed realized.

---

## 7. What is NOT in this package

- No authorization. **Do not submit without explicit owner authorization.**
- No arming. No credential is created, copied, printed or rotated.
- No PMUS canary — that lane cannot yet evidence `TRADE_FLOW`.
- No change to `FIDELITY_OBSERVABLE_V1` (`d583fa4005d1b815…`) or to
  `L5_ADAPTED_GEMINI_CRYPTO` (`62d004d80b4af8b9…`).
- No R1 involvement of any kind.
