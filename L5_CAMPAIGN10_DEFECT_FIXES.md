# Campaign 10 — the two defects: one fixed, one needing your decision

## Defect 2 — adopted-order timestamp. FIXED.

### What happened

```
02:00:16.414  SUBMIT  l5f-e68bbd642d44a1190afd16fd  DOWN own 0.4100 qty 1.94
02:00:16.779  ACK     venue_order_id: NULL
02:00:21.565  L5_LIVE_DISARMED ORDER_STATE_UNCERTAIN
              "accumulation order submit time unparseable: DDDFX4X012VP"
02:00:36.293  DDDFX4X012VP  ORDER_STATE_CANCELED  cum 0
```

The ack carried no `venue_order_id`, so when reconciliation read the open
order back it could not match our client id and adopted the row keyed by
the **venue** id. The adoption dict's first field is
`"t": mine.get("t")` — and `mine` was `{}` for a key we never submitted
under, so `t` was `None`. `maybe_acc_deadline` parses `t` with
`fromisoformat(str(raw))`, read `"None"`, called the row unusable, marked
the store UNCERTAIN and disarmed. Five seconds after the order reached the
book.

### The fix

`recover_submit_time()` establishes the time in descending order of
authority:

1. our own record under this key, if we have one;
2. a **durable client-order record** whose `venue_order_id` matches this row
   — `link_venue_order_id` writes that link the moment the venue discloses
   the id, so the order we sent stays identifiable even when the ack did not
   name it;
3. the venue's own creation timestamp, parsed strictly.

**Nothing is fabricated.** A naive timestamp is refused — assuming UTC for a
venue that did not say so is exactly what the deadline rule exists to
prevent — as is anything unparseable. `None` means not established, and the
caller leaves `t` absent so the fail-closed path behaves exactly as before.
The row records `submit_time_source` (`OURS` / `RECOVERED`) or
`submit_time_unrecoverable`, so an audit can tell them apart.

Exact-order cancellation, uncertainty handling, and the
`mark_uncertain` → `disarm` rule are untouched; tests pin all three.

In the 0200z case this is decisive: our `l5f-…` record **did** carry
`venue_order_id: DDDFX4X012VP`, so path 2 would have found `t` and the
engine would never have disarmed.

---

## Defect 1 — a window spent on an arm that cannot happen. NOT WIRED.

### What happened

```
01:45:01.572  claim written for 0145z
01:45:18.007  L5_LIVE_DISARMED "owner arm token absent or does not match"
01:45:21.698  L5_LIVE_DISARMED AUTHORIZATION_ALREADY_USED, same lock
```

`authorize_arming` **step 2** accepts **either** a valid campaign approval
**or** the Q1 handshake. That is deliberate and documented at length: *"The
campaign document is an authorization, not merely a bound on one... Requiring
the Q1 handshake ON TOP of a valid campaign bought nothing... and cost
exactly what this layer exists to abolish: a Railway edit per campaign."*

But `PMUSTransport.arm()` calls `_require_armed()`, which requires
`Q1.armed()` **always**.

So a process holding a valid campaign and no handshake passes the
boundary, **spends a window at the step-11 claim**, and is then refused one
call later by the transport. One of three authorized windows, consumed with
no order and no possibility of one.

### Why the tests never caught it

`test_PMUS_Q1_LIVE_NO_cannot_block_a_campaign` asserts exactly this:

```python
monkeypatch.setenv(Q1.LIVE_ENV, "NO")
assert Q1.armed() is False
r = _Runner(env, market=W1); r.try_arm()
assert r.transport.armed is True
```

It passes because `_Transport.arm()` in the harness just sets
`armed = True`. **The fake transport does not model
`_require_armed()`.** The real one does. The two layers have disagreed since
the campaign became a first-class authorization, and nothing noticed until
`0145z` paid for it.

### What I built, and did not turn on

`authorize_arming(transport_armable=...)` — a caller-supplied fact, the same
idiom as `halted` and `reconciled`. `False` refuses
`TRANSPORT_CANNOT_ARM` **before** the claim; unstated behaves exactly as
today, so no existing caller changes. Tested both ways, including that
duplicate arming is still refused and that the window survives a refusal.

**I did not wire it,** because passing `Q1.armed()` from the runner makes
`PMUS_Q1_LIVE=NO` block a campaign window — contradicting that pinned
invariant and four of its neighbours. That is a change to the authorization
scope, which you told me not to make.

### The two coherent resolutions — your call

**Option A — align the transport with the documented design.**
Let `PMUSTransport.arm()` accept a valid campaign approval the way
`authorize_arming` already does. The arm then succeeds, so no window is
wasted, and the design intent ("no Railway edit per campaign") is finally
real. **Cost:** the engine can arm on the campaign document alone, without
the Q1 handshake. That is strictly less authorization than today requires.

**Option B — require both, and refuse early.**
Wire `transport_armable=Q1.armed()`. The handshake becomes necessary
alongside the campaign, and a process lacking it is refused before spending
a window. **Cost:** contradicts the pinned invariant and restores the
per-campaign Railway edit that the campaign layer was built to abolish —
though that cost is now nil in practice, since you have authorised me to
manage those variables.

**My recommendation: Option B.** It keeps two owner-held objects required
for arming rather than one, which is the stronger safety property, and the
ceremony it reintroduces is the ceremony I now perform. Option A trades a
real authorization requirement for convenience that is no longer needed.

Either way the pinned tests and the fake transport need updating so the two
layers can never silently disagree again.

### Meanwhile the waste is avoidable without deciding

The path only triggers when a **valid campaign is installed while the Q1
handshake is absent**. Keeping the handshake set whenever an approval is
live makes it unreachable. That is what I will do when resuming: set
`PMUS_Q1_LIVE=YES` and clear the halt in the same change, so there is no
interval in which the approval is live and the handshake is not.
