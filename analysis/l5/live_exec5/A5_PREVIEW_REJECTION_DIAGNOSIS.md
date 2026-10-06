# Attempt 5's HTTP 400: root cause, field-by-field

**Attempt 5 is formally PREVIEW_REJECTION / NO SUBMISSION / LATCH UNSPENT /
ZERO EXECUTION OBSERVATIONS.**

**Root cause — established, not hypothesised: a duplicate serializer.**
`analysis/l5/live_exec2/orders.order_body()` hand-rolled the wire body
instead of delegating to `src/l5_order_body.py`, the serializer that has
actually traded on this venue. EXEC-1-X was the **only** PMUS order path in
the repository that did not delegate, so it was the only one that could
drift — and it drifted in three ways.

## Field-by-field diff

Both payloads below are for Attempt 5's exact state: semantic side DOWN,
own-side price 0.49, qty 0.01, tick 0.01, min qty 0.01, post-only.

| field | A5 sent | proven path | verdict |
|---|---|---|---|
| `marketSlug` | `"cpc-btc-updown-15m-2026-10-06-2300z"` | same | SAME |
| `type` | `"ORDER_TYPE_LIMIT"` | same | SAME |
| `price.value` | `"0.51"` | `"0.51"` | **SAME — the 1−X mapping was never wrong** |
| `price.currency` | *absent* | `"USD"` | **DEFECT 1** |
| `quantity` | `"0.01"` (JSON **string**) | `0.01` (JSON **number**) | **DEFECT 2** |
| `tif` | `"TIME_IN_FORCE_GOOD_TILL_CANCEL"` | same | SAME |
| `participateDontInitiate` | `true` | `true` | SAME |
| `outcomeSide` | `"OUTCOME_SIDE_NO"` | same | SAME |
| `action` | `"ORDER_ACTION_BUY"` | same | SAME |
| `manualOrderIndicator` | `"MANUAL_ORDER_INDICATOR_AUTOMATIC"` | same | SAME |
| **preview envelope** | bare body | `{"request": {…}}` | **DEFECT 3** |

Exactly three deviations. Nothing else differs, and no field is missing or
extra beyond `price.currency`.

### Which one caused the 400?

**Not established, and deliberately not guessed.** `urllib` raises
`HTTPError` before the caller can read the response, so A5's record held
only `HTTP Error 400: Bad Request` — the venue's own account of what it
rejected was lost. All three are deviations from the proven path; any one
could be sufficient. Defect 3 is the only *preview-specific* one, which
makes it the most likely single cause of a failure at the preview
endpoint, but that is an ordering argument, not evidence.

This is why the observability correction comes first: the next rejection,
if any, will carry the venue's reason.

## Comparison against each requested axis

1. **The previously successful PMUS order path.**
   `pmus_micro_executor._body` delegates to `src.l5_order_body.build`, and
   calls `POST /v1/order/preview` with `{"request": body}`
   (`pmus_micro_executor.py:354-355`). `l5_pmus_transport`,
   `pmus_preview_oneshot` and `pmus_auth_preflight_readonly` all do the
   same. A5 matched none of them.
2. **Current evidenced schemas.** `price` is an object carrying `value`
   **and** `currency`; `quantity` is `number<double>`. `l5_order_body`'s
   docstring records this as hard-won, and records that this exact class of
   bug happened once before, when `l5_pmus_transport` "came to send
   `outcome`/`side`/`LIMIT` and a bare-string price, none of which the
   venue accepts."
3. **UP vs DOWN wire semantics.** Correct in A5. See the chain below.
4. **Quantity / price / tick formatting.** Price value and tick were
   correct (`0.51`, on the 0.01 grid). Quantity's *value* was right and its
   *JSON type* was wrong. Fractional quantity depends on the numeric type.
5. **Post-only / order-type fields.** Correct:
   `participateDontInitiate: true`, `ORDER_TYPE_LIMIT`,
   `TIME_IN_FORCE_GOOD_TILL_CANCEL`, `ORDER_ACTION_BUY`.
6. **Market identifier / outcome representation.** Correct:
   `marketSlug` plus `outcomeSide`. No token id, no condition id, no
   separate outcome index is used or needed on this venue.

## The complement chain, written out once and for all

This is the ambiguity that cost **24 of 24** live NO-side orders on
2026-09-05/06. It is now pinned by fixture and by an involution test, so it
cannot be re-derived from memory or inferred from a price.

```
semantic side        DOWN
  -> wire outcome    outcomeSide = OUTCOME_SIDE_NO      (DOWN is NO)
  -> wire action     action      = ORDER_ACTION_BUY     (always BUY: this
                                   venue is ONE instrument, so DOWN is
                                   BUY NO, never SELL YES)
semantic price       0.49  in DOWN's OWN terms
  -> wire price      price.value = 1.00 - 0.49 = "0.51"
                     price.currency = "USD"
                     because price.value is ALWAYS the long (YES) side's
                     price, whatever outcomeSide says.

preview payload      {"request": { ...the body above... }}
```

**Both fields are sent and they are not redundant.** `outcomeSide` says
*which outcome is bought*; `price.value` says *where on the YES axis*.
Sending the NO price in NO terms alongside `OUTCOME_SIDE_NO` is the
historical bug: the venue reads it as NO at 1−X, which crosses.

The inverse holds internally: a wire value `W` on `OUTCOME_SIDE_NO` is a
semantic DOWN price of `1 − W`. **Internally every price stays in its own
outcome's terms; only the wire value maps.** `1 − (1 − x) = x` is tested
for five prices, so the mapping cannot silently double-apply.

## The two diagnostic corrections

**1. The venue's HTTP error is captured in full.** `VenueHTTPError` reads
the body before the handle closes and carries status, reason, body and
response headers. Headers are an **allowlist**, not a denylist — a header
the venue starts returning tomorrow is dropped until it is listed, which is
the direction that cannot leak. Request-header **values are never
recorded**, for any header, because a signed request has none worth the
risk; only the names go into a record, with the sensitive ones marked.

**2. The lifecycle classifier is decided by exception type, not by the
latch.** `PreviewRejected` and `SubmissionAmbiguous` each state
`crossed_order_boundary` as a fact about control flow:

- the preview is a what-if; the venue creates nothing, and it runs
  strictly before latch consumption → `PREVIEW_REJECTED`;
- a failure at or after `POST /v1/orders` → `AMBIGUOUS_SUBMISSION`;
- an exception naming neither phase → `UNCLASSIFIED_SUBMISSION_FAILURE`,
  treated as possibly having submitted and **never resolved by guess**.

The latch is still recorded, as *evidence that agrees*, not as the decision
procedure. A5's diagnosis no longer has to be read backwards out of a
missing file.

The deadman still runs on a preview rejection. No order can exist, but
cancelling cannot create one, so the safety net stays wider than the proof.

## A latent defect found while fixing this one

The XR2 image did **not** `COPY src/l5_order_body.py`. Delegating to it
would therefore have been an `ImportError` at container boot — found, in
the worst case, 897 seconds into a 900-second window. The `COPY` is added,
with a test that parses the imports of `orders.py` and `runner.py` and
fails if any is absent from the Dockerfile. Copying that module adds **no**
order path: it holds serialization only, with no credentials, no transport,
no endpoint and no executor import, which is why it was factored out.

## Supersession

The defect was in **shared** code, so the earlier fingerprints no longer
describe the code that would run. **EXEC-1-X `d2f32276…6c79`, XR
`f3e54d82…c5e9` and XR2 `0f334019…042f` are SUPERSEDED and must not be
re-armed.** XR2W declares this in its own spec.

## Is another live probe necessary?

**Not to establish the wire format.** The corrected payload is now pinned
offline against the proven serializer, byte for byte, including the
envelope and both JSON types. That check runs in CI in under a second for
$0. A live probe would tell us nothing about the format that the fixture
does not.

**A live probe is still required to establish what the format was blocking:**
whether a 0.01-share post-only order at the touch is *accepted*, *rests*,
and *obtains evidenced maker execution*. No offline test can answer that.

So the honest answer is: one more live probe is necessary, but **not yet**
and **not for this**. The sequence that makes it worth spending a window:

1. review and authorize XR2W's fingerprint (below);
2. deploy DISARMED and confirm the image boots with the new import;
3. **then** one bounded live attempt, under the unchanged envelope.

The outstanding wiring item for step 1: the runner's fingerprint pin still
checks `XR2_FINGERPRINT`, while XR2W's spec says that value must not be
re-armed. Reconciling the pin is a small change that itself alters the
fingerprint, so it belongs in the authorized build, not in this diagnosis.

## Envelope: unchanged

Quantity 0.01, max loss **$0.0099**, the sha256(slug|session_id) side rule,
offset-0 at the same-side best bid, post-only, the 2.0 s AuthBook bound,
the venue-derived tick, the preview/echo requirement, the durable
one-submission latch, all three TTE checks with the 120.0 s cutoff, the
pre-open wait, no retry/replacement/complement, markouts, lifecycle and the
deadman: all inherited unchanged and re-asserted at every boot.

## New fingerprint

**LIVE-L5-EXEC-1-XR2W**
`716e86ec92012d4cfd3ef8a9ac6a0c9de63a9c596f7d0a4a2542c50269e07064`

Derived from XR2 `0f334019…042f`. `correction_count` 2. Not deployed, not
armed, no owner values set.
