# Why the spring probe 401s while `l5-pmus-live` authenticates

**I withdraw the credential-replacement proposal from my previous report.** It was not proven.
What follows is the comparison you asked for — runtime auth paths, request timestamps, account
identity, configuration references — and the result is that **every code-side and
configuration-side cause is eliminated, leaving only "the key material this service presents is
not accepted by the venue."** No secret was read, printed, or compared at any point.

## 1. Runtime authentication paths — IDENTICAL, verified against the probe's actual running image

The probe serves `6adcc4c43e67` (deployment `c45eb3f3`, 2026-10-14). I fetched that commit and
compared the real running code, not HEAD.

| check | probe @ `6adcc4c4` | `l5-pmus-live` @ HEAD | verdict |
|---|---|---|---|
| `sign_path` | strips query string | strips query string | **identical** |
| `signed_headers` | Ed25519 over `ts+method+sign_path`, `X-PM-Access-Key` / `X-PM-Timestamp` / `X-PM-Signature` | same | **identical** |
| `_get_with_retry` | — | — | **byte-identical (`diff` clean)** |
| env var names | `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` | same | **identical** |
| API host | `https://api.polymarket.us` | same | **identical** |

Three apparent auth surfaces collapse to one: `pmus_q1_executor_v4` does
`Transport = base.Transport`, `KEY_ENV = base.KEY_ENV`, `SECRET_ENV = base.SECRET_ENV` — aliases,
not a reimplementation. **The failing path and the working path share the same signer object.**

I also checked the two plausible path-level traps and both are clean:

- **Unsigned request.** `Transport.request` attaches auth only when `base == API`; a call made
  against `GATEWAY` would go out unsigned and return 401. The auditor calls
  `q1.base._get_with_retry(t, "/v1/portfolio/activities")` with the **default base**, so it is
  signed.
- **Query-string signing.** This family already had exactly this bug — *"every authenticated GET
  carrying a query string answered 401 while signed over path+query"* — and the fix is present in
  the probe's own running commit, not just at HEAD.

## 2. Request timestamps — eliminated

`signed_headers` sends `int(time.time() * 1000)`. A skewed clock would 401 every request, which
matches the symptom, so it had to be checked. It is not the cause:

- the code computing the timestamp is **identical** between the two services, so a unit or format
  error would break both;
- `CLOCK_REALTIME` in a container comes from the **host kernel** unless a time namespace is in use.
  Both services run in the same Railway region (`us-east4-eqdc4a`) on NTP-synced hosts, so there is
  no per-container clock to drift — a 12-day-old container reads the same clock as a 3-hour-old one.

## 3. Account identity — eliminated, and this is the sharpest single piece of evidence

**The venue returns `401 Unauthorized`, not `403 Forbidden`.**

- 401 means the request was **not authenticated**.
- A key belonging to a **different account** would authenticate fine and then either return another
  account's (empty) data or **403** on the resource. It would not 401.

So the probe is not presenting a valid key for the wrong account. **It is presenting something the
venue will not authenticate at all.** Of the two conclusions you allowed, the evidence supports
**invalid**, and specifically *not* wrong-account.

## 4. Configuration references — eliminated as a mechanism

- `get-service-config` reports **`"staged": null`** for the probe, so there is no unapplied
  variable edit sitting in the way.
- An **unexpanded Railway reference** (e.g. a literal `${{Service.VAR}}` left in the value) cannot
  produce this symptom. I tested the signer's own decoding: `base64.b64decode` on such a string
  **raises `binascii.Error`**, which would surface as that exception, not as an `HTTPError 401`.
- The variables are **present and structurally valid**, proved by the shape of the failure: the
  signer does `os.environ[SECRET_ENV]`, `b64decode(...)[:32]`, `from_private_bytes(seed)`. Absent
  would raise `KeyError` *before any HTTP call*; non-base64 would raise `binascii.Error`; a short
  seed would raise from `from_private_bytes`. We see a built, signed, sent request rejected at the
  venue.

**One real hazard found while testing this**, worth recording even though it is not the cause here:
`base64.b64decode` without `validate=True` **silently discards** non-alphabet characters, so a
wrong-but-long-enough value yields a valid 32-byte seed, signs successfully, and 401s — a
mismatched `KEY_ID`/`SECRET_KEY` pair is indistinguishable from a revoked key from inside the
process.

## 5. What is left, and what I have NOT done

Eliminated: code version, signer drift, unsigned base, query-string signing, retry wrapper, env
var naming, API host, timestamp format, clock skew, staged config, unexpanded references, missing
or malformed values, wrong-account.

**Remaining: the key pair this service presents is not accepted by the venue** — revoked, expired,
disabled, or an `ID`/`SECRET` pair whose halves came from different credentials.

**This is proof by elimination, not a positive test, and I am stating it as such.** I cannot run a
positive test: this container cannot reach `api.polymarket.us`, and I will not read the key to
compare it with the working one.

## 6. The one safe positive test — and it is not a rotation

**Point the probe's two variables at the working service's values as Railway references**, so both
services resolve the same secret from one source:

- creates no credential, revokes nothing, changes nothing at the venue, and exposes no value to
  anyone — it replaces a **duplicated copy** with a reference to the copy that demonstrably works;
- a variable change does **not** restart a running container, so the probe needs a redeploy to pick
  it up;
- **if the 401 clears**, the probe's own copy was stale or mismatched — proved positively, and
  reconciliation resumes with no further change;
- **if the 401 persists**, the cause is upstream of both services and the live service's key is
  living on borrowed time too, which is worth knowing before it takes production down.

I have not done this, because it writes to credential variables on a money-moving service and your
instruction was explicit. It needs one word from you. If you would rather not link them, the
equivalent check is to compare the two services' `PMUS_API_KEY_ID` values yourself — if they
differ, the probe is holding an older key.

## 7. Risk controls — untouched

R5FB unarmed, R5FT unapproved, no loss limit moved, `combined_loss_above_stop` still refuses at
**−$17.2602 against −$5.00**, `DEPLOY_TRIGGER` untouched, `PMUS_SPRING_SHUTDOWN` still latched.
**Settlement reconciliation is still down and I am not requesting live activation.**
