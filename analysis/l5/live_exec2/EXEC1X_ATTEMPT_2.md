# LIVE-L5-EXEC-1-X ATTEMPT 2 — SKIP. NO SUBMISSION.

Classification: **SKIP (authorized artifact has no submission runner).
NOT a trading result.**

No order. No preview. No signing. No venue mutation. No arming keys set.
The durable latch for any session is **UNSPENT**. Capital at risk **$0.0000**.
matched-pair economics **N/A**.

## The blocking gate

The authorization is limited to fingerprint `d2f32276…6c79` running from SHA
`536aa40732d7880195ac7d8eb47b0a29a47bf6d8`. **At that SHA there is no code
path that submits an order.**

Verified against the commit itself, not from memory:

* `git grep submit_one 536aa407 -- analysis scripts src` returns exactly one
  hit: the **definition** in `analysis/l5/live_exec2/orders.py:161`. Nothing
  calls it.
* The deployed image copies exactly one script:
  `scripts/l5_exec2_boot_preflight.py`, and its `CMD` runs that module.
* That module never calls `submit_one`, never POSTs to `/v1/orders`, and
  never consumes the owner's latch. Its only `L.consume` calls are the
  self-test under a throwaway session id.

So EXEC-1-X as built is an order-**capable** library with a read-only
entrypoint. The four routes, the latch and every refusal are real and
proven; the thing that would *decide* a side, price and moment, place the
order, watch its lifecycle, time the markouts and run the deadman does not
exist yet.

I did not write one. The authorization says: *"If any gate fails, SKIP. Do
not modify anything to force execution."* Adding a submission runner changes
the artifact and the SHA, which is exactly the modification that sentence
forbids — and it would mean submitting a real order from code this
authorization never saw.

## Why no keys were set either

Arming a build that cannot submit achieves nothing and would leave live
arming keys sitting on a credentialed production service. So all four
`PMUS_EXEC1_*` keys remain unset.

Noted for the record: the authorized spec itself asserts
`arming.claude_may_not_set_these: True`, and that field is inside the hash
`d2f32276…6c79` that was authorized. The instruction to set the keys and
that frozen assertion disagree. That is the owner's call to resolve, not
mine to resolve silently — and it is moot this round, because there is
nothing to arm.

## What the other gates said

Re-verified at the current head of the service (deployment `a0fdce1c`,
boot 15:32Z), all PASS: fingerprint `d2f32276…6c79`; derived-from
`b1dbe7f1…2e06`; 20 envelope invariants; credentials present by presence
only; slug `cpc-btc-updown-15m-2026-10-06-1530z`; `minimumTradeQty` 0.01;
`orderPriceMinTickSize` 0.01; AuthBook 8/8 two-sided at 0.016–0.094 s;
spread exactly 0.0100; no competing order-capable PMUS process; durable
latch unspent for any owner session.

**Every gate the authorization names passes except the one that matters:
there is nothing at this SHA that can place the order.**

## No lifecycle to report

There is no decision timestamp, placement BBO, side, price, depth ahead,
preview, latch consumption, POST, ACK, rest duration, fill, liquidity role,
commission, markout or settlement. Writing any of them would be
fabrication. Attempt 2 produced **zero observations** — it is not a no-fill
and not an ambiguous POST; it never reached the venue.

Attempt 3 was not launched. S3 untouched.

## What Attempt 2 needs

A submission runner — one new module and a start-command change — covering:
window selection and TTE, waiting for a fresh two-sided AuthBook, the
near-touch price at offset 0 from the touch, the local preflight, the
preview echo, latch consumption, the single POST, lifecycle capture through
the seven states, the +1/+5/+10/+30/+60 s markouts, the TTE-120 s
withdrawal, the deadman cancel-all in a finally block, and settlement
follow-up.

That produces a **new fingerprint and a new SHA**, so it needs its own
authorization. I can build, test and deploy it DISARMED and return with the
fingerprint, exactly as EXEC-1-X was done.
