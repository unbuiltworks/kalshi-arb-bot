# E1 obligation ledger — contract changes and modified test fixtures

Every behavioural contract this patch narrows, and every test fixture it
changes, listed explicitly. Nothing here was changed to make a test pass.

Branch `claude/e1-obligation-ledger`, cut from the deployed commit
`b88ec5eecbb77151c07a10928b3687fd60c77cd4`. Not deployed, not armed.

## 1. Contract changes in production code

### 1.1 `order_state.read_order_state` — absence no longer resolves an
### id the venue has never admitted to

**Before:** a confirmed venue id absent from a successful read was
`pending_ids_resolved_by_this_read`, i.e. not outstanding. The reasoning
was sound at steady state: "`/v1/orders/open` is the venue's statement of
what rests."

**After:** absence resolves such an id only once a successful read has
POSITIVELY SHOWN it at least once (`observed_ids`). An acknowledged id the
venue has never listed is counted, like a `LOCAL_INTENT`, and reported
under `acknowledged_but_never_observed_ids`.

**Why:** the old rule is false immediately after a POST. A venue that
acknowledges an order and has not yet published it omits it from
`/v1/orders/open` and 404s its own record — both endpoints agreeing, so
nothing looks inconsistent and nothing fails closed.
`test_e1_burst_1215z.py` places a second first leg straight through that
gap, and the count grows with the lag, bounded above only by the attempt
latch at twenty.

**New field:** `ids_observed_this_read`, so observation can be accumulated
durably. This exists because the first version of the patch created a
DEADLOCK: with nothing recording observation, a confirmed id the venue
later stopped listing would have blocked E1 for ever. Fail-closed must not
become fail-permanently.

### 1.2 Release requires order-specific attribution, not an aggregate match

An obligation releases only when its OWN per-id venue row is terminal, and
then only when either its own fill is zero or an INDEPENDENT inventory
snapshot covers the SUM of every fill we believe we hold and have not
matched.

Three rules, each from a defect found by a test:

| Rule | Rejected alternative | Why the alternative was wrong |
|---|---|---|
| attribution comes from the obligation's own per-id row | aggregate inventory | aggregate cannot attribute |
| the comparison is against `total_unmatched`, not this order's fill | `independent >= filled` | satisfied by coincidence — another order's 0.13 fill would release an obligation whose own 0.13 nobody had accounted for |
| `derived_net_qty` is never consulted | accept it as inventory | circular: it is computed from the same per-id `cumQuantity` the fill was read from, which made `TERMINAL_FILL_UNACCOUNTED` unreachable |

**Freshness boundary:** an inventory snapshot must not predate the instant
the fill was first observed (`view["as_of"]` vs `fill_observed_unix`), and a
snapshot with no `as_of` is not evidence. A positions read taken before we
saw the fill cannot contain it, however large the number.

**Offsetting fills do not net away.** Two fills on opposite sides are two
fills; a venue net of zero does not mean nothing filled.

### 1.3 The single first-leg reservation is an `O_EXCL` token, not a count

A blocking count is read-then-write and bounds nothing under a race — the
same reasoning the attempt latch records for refusing to be a counter. Two
processes that start before either reserves both see an empty ledger.

The reservation is one file, created by `os.link` from a temp file whose
content is already complete and fsynced, so the holder is always nameable.

**Two defects found by crash tests, both in this patch's own design:**

1. The first version created the final name with `O_EXCL` and then wrote
   into it, leaving an empty file between create and fsync. A five-worker
   race read it mid-write and could only report its holder as
   `UNREADABLE`. It refused, so nothing was unsafe, but a lock whose holder
   cannot be named cannot be audited.
2. `os.link` makes the temp name a SECOND NAME FOR THE SAME INODE. While
   the temp name was only pid-scoped, a later attempt reused it, opened it
   with `"w"`, **truncated the live reservation through the hard link**,
   rewrote it with its own intent id, and was then told it already held the
   token — two workers, one reservation, no error raised. The temp name is
   now unique per attempt and created `O_EXCL`, so a stale one is inert.

**Crash at each filesystem transition:**

| Crash point | Surviving state | Safe? |
|---|---|---|
| before the temp file | nothing held, no obligation row | yes — no order can have been sent |
| temp written, before `os.link` | content exists, the NAME does not, so nothing is held | yes — the name is the reservation |
| after `os.link`, before temp cleanup | held, and the holder is nameable | yes — the safe direction |
| reserved, before the obligation row | held, with no row | yes — fails closed; we may be about to POST |
| directory entry not yet durable | reservation simply absent | yes — same as "before the link"; the row is the LAST thing written, so a reservation can never exist while its obligation does not |

### 1.4 Retirement is restricted

`retire_never_sent` refuses any obligation that carries a venue id or has
been observed. The only safe retirement is the attempt latch refusing
strictly before the POST, where the `O_EXCL` create failed and no order can
exist. Cancellation and shutdown cannot retire an obligation; they act
through the venue's verified terminal state or not at all.

### 1.5 The snapshot merge only ever raises the floor

`our_outstanding_orders` and `aggregate_exposure_usd` become the maximum of
the venue's value and ours. Every existing refusal fires on at least the
input it fired on before; nothing that refused can now permit. The truthy
fail-closed sentinel is passed through untouched.

## 2. Modified test fixtures

### 2.1 `test_e1_live_path.clean_order_state_reader` — now mirrors posts

**Changed, not weakened.** The helper answered "you have no orders" even
for orders it had just acknowledged through `FakeTransport`: a POST
returned `order-1` and the next read denied `order-1` existed. That is not
a clean venue, it is an inconsistent one — and it is precisely the
delayed-visibility shape this patch exists to refuse. Three tests were
therefore asking E1 to keep placing first legs while the venue disowned
every one.

The reader now reflects what was POSTed. `mirror_posts=False` restores the
literal behaviour for a test that needs to script absence deliberately.
**Every assertion in that module is unchanged.**

### 2.2 `test_e1_live_path.FakeTransport` — now honours cancels

The fake acknowledged a cancel and then kept reporting the order as
resting for ever, so a window's handover never cleared its order and the
next window read it as still outstanding. `FakeTransport.cancels` records
the ids and the reader reports them `ORDER_STATE_CANCELED`. Reset
alongside `posts` in every fixture that resets `posts`, so venue state does
not leak between tests.

### 2.3 `test_e1_outstanding_orders.test_a_confirmed_id_absent_from_a_successful_read_is_resolved`

The only existing assertion whose EXPECTATION changed. It now records both
halves: an id **once observed** is still resolved by absence (its original
concern — an order that filled and paged out of history must not block E1
for ever), and an id the venue has **never** admitted to is not. The
original concern is preserved as the first case.

### 2.4 Synthetic views in this patch's own tests gained `as_of`

Views constructed by hand in `test_e1_obligations.py` and
`test_e1_obligation_race.py` now carry `as_of`, because a snapshot with no
timestamp can no longer evidence a fill. These are this session's own
tests, written against the weaker rule.

## 3. What is NOT claimed

- No protection against SIGKILL, OOM, power loss or a container hard stop.
  Those are the shutdown patch's territory and it does not claim them
  either; only the durable latch, the obligation ledger and startup
  reconciliation cover them, by refusing rather than recovering.
- The ledger never cancels, submits, amends or reads the venue. It holds no
  credentials. A bad read can make it refuse; it can never make it act.
