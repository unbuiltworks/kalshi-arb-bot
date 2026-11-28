# Minimum viable live capital for continuous two-sided L5 trading

**Status: NOTHING ARMED, nothing submitted, no variable written, the current
mandate unchanged.** This is the capital calculation requested, from actual
PMUS mechanics read off the venue.

**Headline: capital is not the binding constraint, and no capital level
available today makes the economics measurable. Recommend keeping $5 and
re-scoping the campaign's purpose to mechanical validation.** Reasoning below;
§5 is the part that changes the decision.

---

## 1. Actual PMUS mechanics (venue metadata, 2026-10-24T21:13:41Z preflight)

| mechanic | value | source |
|---|---|---|
| tick size | **0.01** | `tick_constraint_known`, market metadata |
| **minimum order quantity** | **0.01 contracts** | `quantity_constraint_known`, market metadata |
| account buying power | **$341.19** | `/v1/account/balances` |
| fee | **θ·C·p·(1−p), rounded UP to the whole cent** | `l5_two_sided_probe.fee` |
| worst-case θ (taker) | 0.0695 | `TAKER_THETA` |
| maker θ | −0.0125 (a **credit**) | `MAKER_THETA` — never assumed |
| open orders now | 0 | reconcile |

Two things follow immediately:

- **The venue minimum is one hundredth of a contract**, so minimum size is
  *not* a constraint at any ceiling. A minimum order costs about half a cent.
  Sub-dollar notional orders are accepted — Stage 1's six confirmed fills were
  ~1 contract at ~$0.45.
- **Account buying power is $341.19.** Every limit discussed here is
  self-imposed. Nothing about the venue forces a small test.

---

## 2. The fee schedule eats most of the pair edge

A pair bought at the $0.96 cap redeems $1.00, so gross is **4¢ per contract**.
Worst-case fees on two legs are `2 · θ · p(1−p)` per contract:

| price pair | p(1−p) sum | worst-case fee/contract | **net/contract** |
|---|---|---|---|
| 0.48 / 0.48 | 0.4992 | 3.47¢ | **0.53¢** |
| 0.30 / 0.66 | 0.4344 | 3.02¢ | **0.98¢** |
| 0.30 / 0.70 | 0.4200 | 2.92¢ | **1.08¢** |

**The 4¢ pair edge survives as roughly 0.5–1.1¢ per contract.** And because
the fee rounds *up* to a whole cent per order, two legs always cost at least
2¢, so small clips are fee-dominated:

| q (contracts/leg) | gross | fees (both legs) | net |
|---|---|---|---|
| 0.01 | $0.0004 | $0.02 | **−$0.0196** |
| 0.25 | $0.0100 | $0.02 | **−$0.0100** |
| 0.50 | $0.0200 | $0.02 | **$0.0000** |
| 1 | $0.04 | $0.04 | **$0.0000** |
| 2 | $0.08 | $0.07 | **+$0.01** |
| 3 | $0.12 | $0.10 | **+$0.02** |

**q must exceed 0.5 contracts per leg before a pair can pay for its own
fees, and q = 2 is the smallest clip with a reliably positive worst case.**

---

## 3. What actually binds — and it is not capital

Every gate prices **both** resting legs as filling in full, so one resting
pair commits `q × pair_cost`.

| constraint | value | binds at |
|---|---|---|
| Cash envelope | $5 → 5.21 contracts | not binding |
| **Residual cap** | **3 contracts, absolute** | **q ≤ 2** (3 would sit *at* the cap and block any further action) |
| **Max live orders** | 1 + 1 paired slot = **2** | **one pair resting at a time** |
| Opening-leg clip cap | 3 contracts, downward-configurable only | q ≤ 3 |
| Cash release | **settlement only** | cash does not recycle inside a window |

At $5 the cash envelope permits 5.21 contracts but the **residual cap permits
2** — so **$5 already funds the largest clip the risk controls allow.**
Raising the ceiling cannot buy a bigger clip; it buys more sequential pairs
per window, because cash is released only at settlement.

Continuity is a config value, not new code: `L5_TS_ROUND_ALLOCATIONS` splits
the envelope across up to 4 sequential rounds per window (`round_allocations`,
max 4).

---

## 4. Configurations at $5 / $10 / $20 / $50

Signal = contracts/window × worst-case net per contract (1.08¢ at 0.30/0.70),
over 8 windows. Noise = the measured residual-loss scale, −$0.6454 per window
for windows ending with |residual| ≥ 3, over 8 windows.

| ceiling | pairs/window (q=2) | contracts/window | signal/campaign | noise | signal÷noise | **available?** |
|---|---|---|---|---|---|---|
| **$5** | 2 | 5.21 | **$0.45** | $5.16 | 0.087 | **yes** |
| **$8** | 4 | 8.33 | **$0.72** | $5.16 | 0.140 | **yes** (ceiling $8.3787) |
| $10 | 5 | 10.42 | $0.90 | $5.16 | 0.174 | **no** — exceeds headroom by $1.62 |
| $20 | 10 | 20.83 | $1.80 | $5.16 | 0.349 | **no** — exceeds by $11.62 |
| $50 | 26 | 52.08 | $4.50 | $5.16 | 0.872 | **no** — exceeds by $41.62 |

### Why $10, $20 and $50 are not available

The account-wide cumulative loss ceiling is **$20** and all-time realized is
**−$11.6213**, leaving **$8.3787**. That gate refuses first, every time. A
campaign budget above $8.3787 would be decoration — exactly the defect I found
and fixed in the $11.10 constant earlier today.

**So the real menu is $5, or up to $8.3787. $10 and above requires raising the
account-wide cumulative ceiling, which is a separate and much larger decision
than this campaign.**

---

## 5. The finding that decides it: no available ceiling is measurable

Signal scales with capital; the residual-loss noise does **not** scale down,
because the residual cap is denominated in *contracts*, so a bad residual draw
costs roughly the same at $5 as at $50.

**For the campaign's signal to equal the residual noise over 8 windows would
need about $57 of exposure — which would need the account-wide ceiling raised
from $20 to roughly $69.**

At every available ceiling the expected worst-case gain is under $1 against a
residual noise scale of about $5. **A bounded 8-window campaign cannot
distinguish a real edge from a lucky or unlucky residual draw at any capital
level available today.** Stage 1 already demonstrated this in the other
direction: −$3.7194 over 8 windows, which the 21.2% all-loss probability says
was not individually significant.

This is not an argument for more capital. It is an argument that **economic
measurement is not an achievable goal for a bounded campaign**, and that
asking capital to deliver it will buy risk without buying evidence.

---

## 6. Recommendation

**Keep the ceiling at $5 and re-scope the campaign's stated purpose to
mechanical validation.** Specifically, the campaign should be approved to
demonstrate — with real orders and real fills — that:

1. UP and DOWN rest **concurrently** from flat;
2. confirmed fills are reacted to promptly, and inventory recomputed;
3. EXTEND / REDUCE / REQUOTE / WAIT are chosen from live inventory;
4. multiple and **partial** fills are handled inside one window;
5. individual orders cancel and replace **without disturbing the other side**;
6. it repeats across windows without intervention;
7. reconciliation, exact-order cancellation, the watchdog and the halts all
   hold under real two-sided load.

Those are all verifiable in 8 windows. **Net P&L is not**, and the mandate
should say so rather than implying the campaign will prove profitability.

**If you want maximum continuity within the existing account-wide ceiling,
$8.00 is the largest defensible figure** (4 pairs/window instead of 2) and I
would support it. I would not recommend $10+, because it cannot be authorized
without raising the account-wide cumulative gate, and even $50 would not make
the economics measurable.

### Proposed final scope

| parameter | value |
|---|---|
| **Maximum total capital at risk** | **$5.00** (or $8.00 if you prefer continuity) |
| **Order size** | **2 contracts per leg** (smallest clip with positive worst case) |
| **Maximum simultaneous orders** | **2** — and only ever the two legs of one pair, one market, one round |
| Pairs per window | 2 at $5 / 4 at $8 |
| Rounds per window | `L5_TS_ROUND_ALLOCATIONS` up to 4 |
| Concurrent markets | 1 |
| Execution | maker-only, post-only both legs |
| Pair cost cap | ≤ $0.96 per pair |
| First-leg own-price floor | $0.20 |
| **Residual limit** | **3 contracts absolute; working clip 2 so a single-side fill cannot reach the cap** |
| **Duration** | **≤ 8 windows and ≤ 4 hours**, whichever first |
| **Daily realized loss halt** | **$5.00** (on the UTC-day figure) |
| **Cumulative account-wide** | **$20.00** — $11.6213 used, **$8.3787 remaining** |
| **Campaign worst-case budget** | **$5.00** (= the ceiling; already re-scoped from $11.10 today) |

### Automatic halt conditions

1. Watchdog **HALT latch** — durable, over HTTP; the executor cannot clear it.
2. Daily realized loss ≤ −$5.00.
3. Campaign worst-case budget exhausted.
4. Account-wide cumulative −$20.00 (refuses first, every time).
5. Residual ≥ 3 contracts.
6. Authorization expiry.
7. Window budget spent (8).
8. Per-window timing: entry cutoff 600 s, complete cutoff 720 s, cancel margin
   **300 s** before close, order TTL 120 s, minimum entry runway 300 s.
9. Unresolved accounting blocks live authorization outright.
10. No automatic relaunch after a completed or crashed money-moving session.

### Required configuration changes

| change | from | to | why |
|---|---|---|---|
| `L5_TS_TWO_ORDER_PAIR` | off | `1` | **without this the campaign is not two-sided** |
| `L5_TS_ROUND_ALLOCATIONS` | 1 | 2–4 | sequential rounds per window = continuity |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | 3 | 2 | keeps a single-side fill off the residual cap |
| `submission_budget_open` | **32** | **16** | restores the approved rate (a tightening) |

---

## 7. What I am not doing

- Not raising any limit. $5 stands unless you choose $8.
- Not writing the approval document. The blocker in the mandate's §6 is
  unchanged: the only live authorization path reads a Railway variable I can
  write, `MANDATE_PUBKEYS` is empty, and `mandate_enforcing` is **false** in
  production telemetry. Your approval still cannot be distinguished from mine
  by the system.
- Not submitting anything until you approve the capital figure and scope.

## Accounting labels

Every figure above is a **limit** or a **worst-case model**, not a forecast.
The only venue-confirmed realized result remains **Stage 1: −$3.7194 over 8
windows**, six confirmed fills. The one-sided policy's modelled edge is
**negative** (−$0.0827 per filled contract, 91.6% adverse over 972 modelled
fills), and the two-sided ladder has **no** economic result yet.
