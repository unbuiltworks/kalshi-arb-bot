# The cancellation repair, and one final bounded verification request

The repair is committed code only. Nothing deployed, no order submitted, no
new session, HALT left latched, shadow lock preserved.

## 1. What was wrong

Order `D68KZ0SG10JR` rested 530s on 2026-10-18 while the watchdog and then
the executor both posted an EMPTY body `{}` to the correct route with a
correct signature. The venue refused every attempt with HTTP 400 Bad
Request. The order left only when the market expired; `cum_qty: 0`,
`commission: 0.0000`, outflow $0.00.

`pmus_q1_executor.cancel_exact` -- the only cancel in this repository ever
used against a live PMUS order -- has always sent `{"marketSlug": slug}` to
the same route, same method, same identifier type, same signer.

## 2. The repair

**Three call sites now send a non-empty `marketSlug`:**

| site | before | after |
|---|---|---|
| `l5_pmus_transport.cancel` armed path | `{}` | `{"marketSlug": slug}` |
| `l5_pmus_transport.cancel` oracle path | `{}` | `{"marketSlug": slug}` |
| `l5_watchdog.CancelOnlyTransport.cancel` | `{}` | `{"marketSlug": slug}` |

Both transport paths now go through one `_cancel_request` helper, so the
body cannot drift between them again.

**The slug is the ORDER's, not the process's.** An orphan sweep runs after
`roll` has moved on, so `self.market_slug` can name a different window than
the order being cancelled. `cancel()` takes an explicit `market_slug`;
callers pass what their ledger row recorded (`l5_live_execution` passes the
row's `market`, `l5_cancel_mechanism` passes `self.market`, the watchdog
passes `venue_slug or claim_slug`), and the transport's own slug is only a
fallback. **An absent slug REFUSES** rather than posting `{}`, because
posting `{}` is the precise failure being removed.

**Signing is untouched.** PMUS signs ts+method+path and does NOT sign the
body, as `l5_watchdog._send` documents. I stated the opposite in the
diagnosis commit; that was wrong and the code confirms it.

**Sanitized error capture.** `http_error_detail()` in both modules reads the
response body once, redacts it, truncates to 400 chars, and records status
and reason. The transport attaches it to `self.last_cancel_error` with the
order id, slug, endpoint and method; the watchdog puts it in each sweep row
as `cancel_detail`. Every step is guarded -- a diagnostic must never replace
the error it describes. The watchdog carries its OWN copy because its image
deliberately excludes the transport (6 `src/` files only), and importing it
would have broken the credential-free closure.

**A pre-existing credential leak, found by the new tests.** Both redactors
matched a secret's value with `\S+`, which stops at the first space, so
`Authorization: Bearer abc123` redacted only `Bearer` and left `abc123` in
the log. Both now consume the value to a structural delimiter, and JSON
around it survives. This was live in `l5_pmus_transport.redact`, used on
every error path, not only cancellation.

## 3. Tests

26 new deterministic tests in `tests/test_l5_cancel_body_marketslug.py`
asserting the REQUEST rather than a double's return value: exact body on
both transports, exact route and method, venue-order-id passthrough,
order-slug-wins-over-transport-slug, fallback, missing-slug refusal with
nothing on the wire, missing-id refusal, ownership refusal still preceding
the wire, route regex still admitting only the cancel route, sanitized
detail contents, redaction of header/signature-shaped text, truncation,
unreadable and undecodable bodies, and errors with no body.

17 existing test doubles were updated to the real contract, plus two tests
that call the real transport. One of those now asserts the body positively.

## 4. One final bounded verification -- FOR SEPARATE AUTHORIZATION

**Not activated. Not scheduled.** Same shape as before, and deliberately
nothing more:

- one post-only order, **0.01 contracts**, nonmarketable, one BTC15m window;
- maximum gross outflow **<= $0.02**, inside the $5 authorization;
- every existing limit unchanged; watchdog and HALT unchanged.

**What the owner must supply** (the only blockers):

1. a **fresh, unused** `PMUS_Q1_SESSION_ID` plus identical `PMUS_Q1_ARM` --
   `sub1.MECH-CANCEL-20261018T1743Z-1438AF` is spent and the 2026-10-18
   session is now spent too;
2. `L5_LIVE_PROBE_MARKET` for the chosen window;
3. `L5_LIVE_AUTH_NOT_AFTER` covering it;
4. **an owner HALT reset** -- the latch is held by
   `UNMANAGED_RESTING_ORDER` and the watchdog refuses to clear it. The
   mechanism's `halt_not_engaged` gate is terminal, so without the reset the
   test refuses before placing.

**What is already settled and must NOT be re-debugged:** the preflight
passes, arming works and was positively evidenced, the market binding
mechanism is understood, and the activation timing is known -- flip AFTER
the window opens, never before, because `mech.market` follows the transport
and an expiring window terminally refuses `window_has_room_for_the_watchdog`.
The book 404s for roughly the first 5 minutes, so the placement band is
~T+5:00 to T+7:30.

**Pass condition, unchanged:** venue order id, durable ownership record,
cancellation **initiated by the independent watchdog**, and a venue-confirmed
**terminal** status for that id. A `SELF_FALLBACK` is a failure. The new
`cancel_detail` field means a further 400 would now name the field the venue
objects to, instead of reading as an opaque `HTTPError`.

## 5. Honest limit on the evidence

The fix is derived from a code diff against the only cancel that has worked
on this venue, not from the venue telling us which field was missing -- the
400 body was never captured, which is the other half of this repair. The
next cancel attempt, success or failure, will produce that body. Until one
runs, "the empty body caused the 400" remains a well-supported inference.
