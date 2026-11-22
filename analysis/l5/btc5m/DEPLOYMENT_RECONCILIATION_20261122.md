# BTC5m deployment and authorization reconciliation — 2026-11-22

Assignment: reconcile the existing BTC5m deployment and authorization
history; no duplicate implementation. Read-only. Nothing was modified,
rolled back, re-pinned or deployed, and nothing was pushed to the deployed
branch.

## 1. Did this session authorize the 2026-11-20 12:10Z deployment?

**No.** This session did not deploy it, did not create
`release/l5-btc5m-venue-size-20261120`, did not set the variables that
appeared on `l5-btc5m-live`, and did not authorize any of it. The last action
this session took on that service was the disarmed configuration of
2026-11-19, and it has pushed only to
`claude/btc5m-favorite-accumulation-wzh2xo`.

**A correction to this session's own earlier report.** It verified a changed
deployment and then inferred "a third actor making money-capable changes".
The inference was wrong and overstated. The commit record shows another
Claude session, which is what the owner read from the same evidence.

## 2. There is no duplicate implementation

| | |
|---|---|
| merge base of the two lines | **`3f847f4af`** — this session's own head |
| divergence | theirs **+21**, mine **0** |
| `src/l5_venue_constraints.py` in their lineage | **present** |

Their line is a strict descendant: **this session's work is already in the
deployed lineage**, extended rather than replaced. `16216c6b9` adds 34 lines
to `l5_btc5m_runner.py`, 52 lines to *this session's own test file*
`tests/test_l5_btc5m_venue_gate_end_to_end.py`, a `LIVE_WINDOW_PACKET.md`,
and a `DEPLOY_TRIGGER` bump. Across all 21 commits
`l5_btc5m_controller.py` gains 284 lines and `l5_btc5m_execution.py` 97.

Nothing needs reconciling in the "two competing versions" sense, and there is
nothing for this session to merge, revert or re-land. The correct action is
none.

## 3. Ownership of the current release

Authored by `Claude <noreply@anthropic.com>`, 2026-11-19 18:05Z →
2026-11-20 04:18Z, in one continuous run. The commit record does not name a
session id, so the owning session cannot be identified from git alone. It is
probably not the sports session, which asked this session whether BTC5m was
live — a question its own author would not need to ask.

Its 21 commits, in its own words, include `#247` work on requote-by-exact-id
and heavy-side growth at the limit, a direct bound on a window's worst-case
settlement loss, a release review that "close[d] two gaps it found", and
read-only loss-ceiling diagnostics.

## 4. The two accounting artifacts are explained by that session's commits

Both were reported to this session as unexplained. Neither is.

* **The +$97.32 restatement** (attributed all-time −$136.78 → −$39.468,
  `UNKNOWN` −$211.87 → −$114.55) is `a0e524eee`, "venue-superset correction
  applied to the 40 listed markets", preceded by `152bee2a7` recording
  "INSPECT matches the manifest 40 of 40; ledger unchanged". Bookkeeping, not
  money earned.
* **The $321.4782 "balance drop"** is `e7e6f1fb1`, "new venue-equity baseline
  set at **321.4782** with a 1.00 allowance; published record and gate
  check". It is a deliberately published baseline, not a loss. The sports
  session read 336.68 → 321.48 as an unexplained $15.20 fall.
* Related: `f02f9752d` finds "the $78.70 between the loss gate's total and
  its rows is the 2026-11-15 re-anchor".

## 5. The missing commit pin — a narrower hazard than first reported

`l5-btc5m-live` carries `watchPatterns: ["deploy/l5_btc5m_live/DEPLOY_TRIGGER"]`,
so a push to its tracked branch does **not** deploy unless that one file
changes. `16216c6b9` bumps it, so the 12:10Z deploy went through the
project's intended mechanism and was deliberate. This session's earlier
framing — that the absent pin leaves the service exposed to any push — was
too strong.

The residual risk is real but specific: with no `commitSha`, the artifact
built is whatever branch HEAD happens to be **at the moment DEPLOY_TRIGGER is
next touched**. So if two sessions have pushed to that branch and one bumps
the trigger, the other's unreviewed commits ship with it. That is the
coordination failure to prevent, and per the owner's decision it is corrected
through that service's own approved release process — not from here.

## 6. No conflict on the live question

That session's own packet reaches this session's conclusion independently:
`a9aecf66b` is "owner review packet for the one-window probe — **safe to
bound, not authorizable**" and `7182a43a1` is a behaviour-probe class with
"**EV stated unproven**, code only". This session's position is unchanged:
mechanics, safety and sizing are built and verified; no entry rule has
defensible positive expected value; arming is not justified.

## 7. One coordination rule that would have prevented all of this

A service's tracked branch should be written to by exactly one session, and a
deployable branch should carry a `commitSha` pin so that what ships is what
was reviewed. This session will not push to
`release/l5-btc5m-venue-size-20261120` and has not.

## Not re-verified this turn

The Railway MCP server requires re-authorization in this session, so live
service state (armed flag, variables, deployment status) could not be read
again. Every service fact above is from the 2026-11-20 12:10Z–12:46Z reads
already reported; the git facts are from a fetch today.
