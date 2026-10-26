# The settlement 401 — diagnosis, and what the combined-loss gate actually says

**Conclusion: the credential on `pmus-spring-l5-probe` is present and structurally valid, and the
venue is rejecting it.** Every other cause is ruled out by evidence below. The remedy is an owner
action on that service's key pair. I did not read, print, or modify any credential.

## 1. What the failure looks like

Every signed call the probe makes returns `HTTP Error 401: Unauthorized` — all five settlement
audits in each batch, every batch, plus `migration_close`, which is a **different endpoint**.
`qty_evidence.available: false` — *"the venue could not be asked for its trades."* No signed call
of any kind succeeds. 96 markets pending, `pending_after: 96`, zero progress per cycle.

## 2. Ruled out: a signing or code-path difference

This was the leading hypothesis, because the two services run different entrypoints and
`pmus_auth.py` carries its own warning that the signature "must stay byte-identical to the
executor's or [it drifts]". There are three apparent auth surfaces. **They are one.**

| module | used by | signing |
|---|---|---|
| `pmus_auth` | read-only preflight | `sign_path` strips the query string; Ed25519 over `ts+method+path` |
| `pmus_q1_executor` | `l5-pmus-live` (**works**) | identical implementation, same header names |
| `pmus_q1_executor_v4` | `pmus_settlement_auditor` (**401**) | `Transport = base.Transport`, `KEY_ENV = base.KEY_ENV`, `SECRET_ENV = base.SECRET_ENV` — **aliases, not a reimplementation** |

All three read `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`, all three post to
`https://api.polymarket.us`, and all three strip the query string before signing — the one bug
this family already had and fixed (*"every authenticated GET carrying a query string answered 401
while signed over path+query"*). **The failing path and the working path share the same signer
object. Code is not the cause.**

## 3. Ruled out: missing or malformed credentials

The failure arrives as `HTTPError: HTTP Error 401`, which means a request was built, signed, and
sent. The signer does `os.environ[SECRET_ENV]`, `base64.b64decode(...)[:32]` and
`Ed25519PrivateKey.from_private_bytes(seed)`. So:

- **absent** would raise `KeyError` before any HTTP call;
- **not base64** would raise `binascii.Error`;
- **wrong seed length** would raise from `from_private_bytes`.

None of those appear. The variables are present and the key material is structurally valid.

## 4. Ruled out: endpoint-specific scope

`migration_close` fails identically, and it is a different endpoint from the activity walk. A scope
problem confined to `/v1/portfolio/activities` would leave other signed calls working. None work.

## 5. What remains

**The venue is rejecting a well-formed signature.** That is: revoked, expired, disabled, or
belonging to a different account than the one holding these positions — or a clock skew large
enough to invalidate `X-PM-Timestamp` on every request. I cannot separate those further without
either reading the key (which I will not do) or reaching the venue (this container cannot:
`api.polymarket.us` is not an allowed host here, so I cannot test a key even if I had one).

**`l5-pmus-live` authenticated 2,092 venue requests with zero errors at 23:29Z on the same day,
from the same project, through the same signer.** So the venue and the signing scheme are both fine;
the difference is which key pair that service carries.

## 6. A CORRECTION to what I told the owner about the floor

I described `unsettled_floor_usd: −17.2602` as *"a $1/share upper bound"* and as an estimate that
would resolve on settlement. **That was wrong, and the real definition is stricter.**

```python
def guaranteed_settlement_floor(inv):
    """min(up, dn) - total cash paid: what this market settles to in its
    WORST branch. ... NOT mark-to-market. It reads no price, no book and no
    valuation model -- only contract counts and cash actually paid."""
    return min(inv.up, inv.dn) - (inv.cost_up + inv.cost_dn)
```

`unsettled_floor_total` sums **only the negative** floors, so one favourable position cannot
subsidise another's guaranteed loss. So −$17.2602 is the **worst-branch outcome across held
markets, computed from cash actually paid** — no prices, no marks. It goes negative when a position
is **one-sided** (then `min` is 0 and the whole cash outlay is the floor) or when a pair was bought
**above par**.

**It is a real risk figure, not a placeholder.** R5FB's −$5.00 stop refusing against it is
substantively correct, not an artifact of missing data.

## 7. The combined-loss gate, recalculated — without resetting or bypassing it

```python
combined_loss = realized_today + unsettled_floor_total(held)
```

Current, from the service at `2026-10-26T00:13:16Z`:

| term | value |
|---|---|
| `realized_today_usd` | **$0.00** |
| `unsettled_floor_usd` | **−$17.2602** |
| **`combined_loss_usd`** | **−$17.2602** |
| R5FB stop | **−$5.00** → **REFUSES** |
| R5FT stop (not approved) | −$30.00 → would permit |

**Which direction settlement moves it, exactly.** The docstring settles this: *"a market that
settles leaves the HELD set and its result enters realized_today in the same transition. Nothing is
in both."* The floor is the **worst** branch, so a settling market's realized result is **≥ its
floor**. Therefore:

> **Settling the 96 markets can only move combined loss toward zero, or leave it unchanged. It
> cannot make it worse.**

Whether it clears −$5.00 depends on how much better than worst-branch those positions actually
resolve, and that is unknowable until the venue can be asked. **It is not a reset and not a
bypass — the gate recomputes from the same formula on better inputs.**

## 8. What I did and did not do

Done: ruled out signing drift, code-path divergence, missing/malformed credentials, and
endpoint-scope, each by evidence rather than assumption; corrected my own mischaracterisation of
the floor; recalculated the gate and established the direction settlement can move it.

**Not done, because it is not mine to do:** replacing the key pair on `pmus-spring-l5-probe`.
I did not read, print, compare, or overwrite any credential value, and I did not touch
`deploy/pmus_spring_l5/DEPLOY_TRIGGER`.

## 9. The owner action, and the order it has to happen in

1. **Replace `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` on `pmus-spring-l5-probe`** with a working
   pair for the account that holds these positions — the same account `l5-pmus-live` is
   authenticating against, which is what "reuse the working authentication infrastructure" means
   here. A variable change alone does **not** restart a running container, so the service also needs
   a redeploy to pick it up.
2. **Let the settle cycle drain.** It audits 5 markets per cycle against a durable cursor, so 96
   markets is ~20 cycles once reads work.
3. **Re-read `combined_loss_usd`.** If it is better than −$5.00, R5FB's gate passes on its own
   arithmetic.

**I am not requesting live activation.** The gates do not pass: `combined_loss_above_stop` refuses
at −$17.2602 against −$5.00, and settlement reconciliation — the thing that would produce the
realized net P&L this campaign exists to measure — is down. R5FB stays unarmed.
