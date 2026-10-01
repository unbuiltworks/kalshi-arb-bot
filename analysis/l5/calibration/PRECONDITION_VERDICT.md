# Calibration campaign precondition: **FAILS — DO NOT SUBMIT**

Authorization granted for `c8d01c9a` carried one hard precondition: before the
first order, prove that authenticated private-order transitions are durably
captured with order ID and timestamps precise enough to JOIN them to the public
market tape. I checked the existing executor against that requirement and it
does not meet it. **No order has been submitted and none will be until this is
closed.**

## What exists today

`X.persist_intent()` (`src/pmus_q1_executor.py:261`) is genuinely durable: it
writes `campaign_state.json` atomically (tmp file + `os.replace`) and records the
intended order BEFORE placement, including `submitted_utc`.

## The three gaps, each sufficient on its own to fail the precondition

**1. There is no private-order event stream.** `subscribe_frames()` subscribes to
MARKET DATA for a slug. Order state is obtained by REST polling
(`/v1/orders/open`, `_get_with_retry`). A poll tells us the state at poll time,
not when the venue changed it. A touch-through observation needs the transition
ordered against book updates arriving every ~0.101s; poll cadence cannot do that.

**2. The acceptance record carries no timestamp.** The submit path persists
`{"stage": stage + ":accepted", "order_id": order_id}` and nothing else
(`src/pmus_q1_canary.py:546`). The ACK time — the moment our quote joined the
queue, which sets `placed_t` and therefore every causal fill decision — is not on
disk at all.

**3. Fills carry no venue trade id and no venue fill timestamp.** Fill detection
reads `cumQuantity` / `filledQuantity` / `cumQty` off a polled order payload and
extracts a price. The public tape records a per-trade `id` (e.g. `CTJAC9HSWYAJ`)
on every print, so a venue trade id on our fill would give an EXACT join to the
public stream. We capture neither it nor a venue fill time, so our fill can only
be located to within a poll interval.

Consequence: a live episode collected with today's executor would be exactly as
unscorable for timing as the q1 episode already is — which is the specific
weakness the campaign exists to remove. Submitting would spend real money and
real windows to reproduce a known blind spot.

## What would close it

1. Determine from the venue whether a private order/fill WS channel exists. If it
   does, subscribe and persist every frame verbatim with receipt and venue
   timestamps. If it does not, say so explicitly rather than assuming.
2. Timestamp EVERY persisted transition — ACK, each poll result, fill, cancel
   attempt and result — with monotonic and wall clock, and keep any
   venue-supplied timestamp verbatim.
3. Capture the venue trade id (and venue fill time) for each fill, so the join to
   the public tape is by identity rather than by time proximity.
4. Prove the join end-to-end on a NON-MONEY dry run: submit nothing, replay the
   recorder plus a synthetic order ledger, and show that a transition can be
   placed between two specific book updates by id or venue timestamp.
5. Only then arm, with every limit in `c8d01c9a` unchanged.

## What I did instead of submitting

Repaired Defect C in the development simulator (`39d78f22`), which was the other
authorized workstream and needs no live exposure.

## Status

Campaign NOT armed. Zero orders submitted. Limits unchanged. Crossed-resting
semantics remain unresolved, and no assumption has been made about them.
