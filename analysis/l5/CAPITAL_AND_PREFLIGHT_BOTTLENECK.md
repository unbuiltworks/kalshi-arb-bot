# What is held until settlement, and what the preflight ladder costs

## Item 5 — capital accounting, verified in code. No recycling anywhere.

`InventoryStore.committed` is per-market **gross cash out**, and
`SafetyLimits`' cash gate charges it through `unsettled_for_gates`
(`l5_live_execution.py:5433`). That method marks an entry `settled` only
when the market is in `retired_markets`, and `retire_settled_market` is
its only writer — it runs solely on a venue-terminal state **with a final
settlement price**, popping `committed` in the same step.

Its docstring is explicit about the boundary:

> "deliberately narrow: `retired_markets` only, **never a quote, never a
> `matched` flag, never an unsettled leg**. A market that has CLOSED but
> not SETTLED keeps holding its cash in full."

**So a completed pair releases nothing.** Matching is not a release event.
Only a confirmed settlement is.

What DOES change on matching is the *size* of the charge, and this
corrects my earlier "$1.50 per matched contract remains committed":

| state | charged per contract | basis |
|---|---:|---|
| round in flight, one leg resting | **$1.32–$1.64** (measured; $1.50 typical) | `1 + up_px` — a resting DOWN is a `BUY_SHORT` and the gate charges it a dollar a share |
| both legs filled | **$0.9231** (measured mean achieved pair cost) | realised cost, held in `committed` |
| after confirmed settlement | $0 | `committed` popped |

1500z, from its own decision record: `cash_gate_obligation_usd 2.9848` on
`qty_per_leg 1.82` = **$1.640/contract** in flight, against
`gross_cash_usd 1.719` = **$0.944/contract** once filled.

So the $1.50 is the **placement-time obligation**, not a permanent hold.
The permanent hold until settlement is ~$0.92/contract.

## The ceiling, and why re-splitting cannot move it

Sizing is done on the obligation basis, so the number of matched contracts
an envelope can place is

    matched = round_allocations x (envelope / round_allocations) / obligation
            = envelope / obligation

**invariant to `round_allocations`.** Checked across n=1..19: every split
of $12.00 yields exactly **8.00 matched contracts**. More rounds at
smaller clips buys nothing.

| | matched contracts | $/window |
|---|---:|---:|
| $12 envelope, obligation $1.50 | **8.00** | **$0.615** |
| same, at the cheapest observed obligation $1.32 | 9.09 | $0.699 |
| same, at the dearest observed $1.64 | 7.32 | $0.563 |
| **$1.00/window requires** | **13.00** | — |

13.00 matched contracts hold **$12.00 of committed cost at settlement** —
exactly the envelope — with a **$12.24 peak** while the last round is in
flight.

**That is the whole gap.** $0.615 → $1.00 is almost entirely the
difference between charging $1.50 at placement and $0.92 once matched.
Closing it requires either the envelope raised ~1.6x, or the gate
recognising that a matched pair holds only its realised cost — which is
capital recycling within the window and is exactly what the code refuses.
**Both are owner decisions. Neither is an optimisation.**

**$1/window is NOT feasible under the existing $12 envelope.** It is
feasible under the $20 exposure ceiling ($12.24 peak < $20), so the
binding constraint is the $12 envelope and not the ceiling.

## Item 1–2 — the preflight ladder, and what is still unknown

Reconstructed from code, not guessed: `roll()` resets
`preflight_attempts = 0` and `last_preflight_t = 0.0`, so the first
attempt runs on the first tick after the roll, and `PREFLIGHT_BACKOFF_S =
(3, 6, 12, 24)` with `idx = min(attempts, 3)` puts attempts at about
**+2, +5, +11, +23, +47, +71, +95s**.

The nine campaign-2 passes cluster at **+80.1/+82.5/+83.0/+84.2/+84.2**
and **+105.8/+106.0/+107.4/+107.8** — which is attempts 6 and 7
completing. The ladder is behaving as designed. **Attempts 1 to 5 failed
and left no record**, because `maybe_preflight` logs a FAIL only when the
blockers changed, and at a roll every early attempt fails identically.

Two candidate causes remain, and neither is established:

* **(a)** `/v1/markets/{slug}/book` does not serve until **+83.2s**
  (measured) and the WS substitute is not firing on the early attempts;
* **(b)** the substitute is available but something else blocks first.

The engine's own records make (a) suspicious rather than settled: four of
nine windows show `ws_fallback:accepted` with `subscribed_before_open:
true`, `book_updates: 816`, `stream_age_s: 0.061` — a ready socket — and
still passed at +84 to +108s. Three book attempts at 1s backoff cost 3s,
so an attempt starting at +3s should have reached the substitute by +6s.

`emit_preflight_attempt` now records, per attempt: the offset in the
window, the attempt's own duration, the next backoff interval, the FIRST
BLOCKING gate in preflight's evaluation order, the book retry ladder, and
the socket's readiness as that attempt saw it (snapshot, two-sided,
`subscribed_before_open`, `stream_age_s`, `book_updates`) against
`WS_BOOK_FALLBACK_MAX_AGE_S`. That last group is what separates "the
socket was not ready" from "the socket was ready and the fallback still
did not fire" — today those are the same observation.

**No fix is proposed yet, because no cause is demonstrated.** Item 3 is
deliberately not attempted on a hypothesis.

## Item 6 — earliest safe preflight, and the honest arithmetic

* **Venue-side earliest**: the market is tradable at **+0.6s** and its
  metadata is served from **−5.9s**. There is no venue floor above that.
* **Earliest our gates could pass**: bounded below by whichever of (a)/(b)
  is true. If the WS substitute can satisfy `book_readable` on attempt 1,
  the floor is roughly **+6s** (one tick plus the 3s book-retry budget).
  That is a hypothesis, not a measurement.
* **Eligible trading time recovered, if so**: ~**75–100s** of a 900s
  window, about **one round** at the measured ~99s/round.
* **Worth**: one round is ~**1.84 matched contracts ≈ $0.14/window** of
  matched surplus — and it only matters because it is a step toward the
  **$0.615** the envelope allows at all, against **$0.18** today.

**It does not reach $1/window.** Even with a perfect boundary and four
full rounds, $0.615 is the arithmetic ceiling of the $12 envelope. The
bottleneck for $1/window is capital, not latency; the bottleneck for
$0.18 → $0.615 is rounds per window, of which the boundary is one part.
