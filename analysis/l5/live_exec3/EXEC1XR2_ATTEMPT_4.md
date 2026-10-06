# LIVE-L5-EXEC-1-XR2 ATTEMPT 4 — PRE-SUBMISSION SKIP

**Classification: PRE-SUBMISSION SKIP (frozen timing gate).**
Not a hang, not a crash, not a rejection, not ambiguous. Zero execution
observations.

No preview, no POST, no signing of an order body, no venue mutation.
**Durable latch UNSPENT.** $0.0000 at risk. matched-pair economics **N/A**.

## Timeline, boot to last observable action

| time (UTC) | event |
|---|---|
| ~20:00:00 | redeploy issued (`6f825f8b-8070-4d50-a74b-77eb5ee30fa7`) |
| 20:54:36.546 | previous container stopped |
| 20:54:38.639 | volume mounted, **container started** |
| 20:54:39.537 | `EXEC1XR2_PRE` — fingerprint `0f334019…042f`, `xr2_frozen: true`, `armed: true`, pin matches, latch `spent: false`, credentials present, no legacy executor importable, no other order-capable process |
| 20:54:39.537 | `EXEC1XR2_RECORD` / `EXEC1XR2_SUMMARY` — `stopped_at: "TIMING"`, `refusals: ["PAST_PLACEMENT_CUTOFF: TTE -2378.718s <= 120.0s"]`, `orders_submitted: 0` |
| 20:54:47.169 | **container stopped** — process exited ~8.6 s after start |

Deployment status **SUCCESS**. The whole run lasted under a second of work.

## The ten questions

1. **Still running / exited / crashed / hung?** — **Exited cleanly.** Started 20:54:38.639, "Stopping Container" 20:54:47.169, deployment SUCCESS, `restartPolicyType NEVER`, one-shot CMD. Nothing is running.
2. **Last log line / timestamp** — `EXEC1XR2_SUMMARY … "VERDICT": "NO ORDER / TIMING"` at **20:54:39.537Z**; container stop at 20:54:47.169Z.
3. **Latch consumed?** — **No.** At boot `spent: false` for `/state/pmus-ws/exec1x/xr2-a4-2026-10-06-334e3c14.08f33888df2014a5.submitted`. The run stopped at the run-start TTE check, which is *before* the side rule, the book read, the preflight, the preview and the latch. No code path to `latch.consume()` was entered.
4. **`/v1/order/preview` called?** — **No.** It is called only inside `submit_one`, which was never reached.
5. **`/v1/orders` called?** — **No.** Same reason. `orders_submitted: 0`.
6. **ACK / order id / rejection / timeout / ambiguous?** — **None of those exist.** There is no `submission` key and no `ambiguous_submission` key in the record. There is nothing to be ambiguous about: an ambiguous outcome requires a POST to have been attempted, and none was.
7. **Any order from this session open on the venue?** — **Not independently verified, and I am not able to verify it from here.** This container has no PMUS credentials and the venue is network-denied at the egress proxy; an authenticated open-orders read requires the credentialed host, which would mean a deploy, which this instruction forbids. What *is* established: nothing in this process ever opened the order route, so there is no mechanism by which an Attempt-4 order could exist. I am reporting that as inference from the process record, not as a venue confirmation.
8. **Did any quantity fill?** — Same answer and same limits as (7). `filled_qty` does not appear in the record because no `OrderRecord` was created.
9. **Venue inventory for the 20:00Z market?** — **Not readable from here**, for the reason in (7). The market has also since settled.
10. **Deadman / `finally` executed?** — **Yes.** `deadman: {"attempted_utc": "2026-10-06T20:54:38Z", "may_create_an_order": false, "skipped": "no submission was attempted, so there is nothing that could be resting"}`. The `finally` block ran and correctly skipped cancellation.

## Why it skipped

TTE was **−2378.718 s**. The frozen 20:00Z window opened at 20:00:00 and its
placement cutoff passed at 20:13:00; the container did not start until
20:54:38 — **39.6 minutes after the window had ended entirely**.

The cause is deploy latency, not the experiment. I issued the redeploy at
~20:00:00 with the window opening 79 s later, on the basis of 13–25 minute
latencies observed earlier today. This redeploy took **~54 minutes** to
start the container. That is my misjudgement: I sized the margin against
observed latency rather than against the worst case, and a one-shot
container that must land inside a 13-minute placement window has no business
being launched 79 seconds before it opens.

The gate did exactly its job — it refused rather than placing a late order
into a window that no longer existed.

## State left behind

* The five owner values are **still installed and still armed**
  (`armed: true`, pin matches). They are currently **inert**:
  `PMUS_EXEC1_GATE_EVIDENCE` names the expired 20:00Z slug, so any further
  boot can only repeat `PAST_PLACEMENT_CUTOFF`. I did not clear them, because
  this instruction is read-only diagnosis.
* The Attempt-4 latch for `xr2-a4-2026-10-06-334e3c14` remains **unspent**,
  so that session id still has its one submission available.
* The Attempt-3 latch record is untouched.
* Nothing was redeployed, restarted, revariabled or submitted during this
  diagnosis.

## The operational lesson

XR2's pre-open wait is the right mechanism and it works — it was proven in
production at 2450 s / 490 polls. The failure mode left is **getting the
container started at all** in time. A future attempt should boot against a
window **at least 90 minutes out**, so that even a ~55 minute deploy lands
the process inside the wait rather than past the cutoff. That is a scheduling
choice, not a code change.
