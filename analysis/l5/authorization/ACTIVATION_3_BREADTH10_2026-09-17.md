# CP1 execution-measurement probe — 10 markets, ARMED

**Authorization:** owner, 2026-09-17 — "NEXT CP1 LIVE EXECUTION PROBE".
Breadth only, on the accepted freshness-gate execution baseline.

## The eight pre-arming verifications

| # | Check | Result |
|---|---|---|
| 1 | Exact tested artifact or authorization-only descendant | **Execution path byte-identical** to serving `0edd1eb061f9`: `git diff` over `pmus_spring_l5_session.py`, `pmus_live_mandate.py`, `pmus_l5_continuous_session.py` is empty. Only the mandate, ladder breadth, image env, tests, lineage and records differ. |
| 2 | New mandate loaded, unexpired, accepted | `OWNER-2026-09-17-CP1-BREADTH10-EXECUTION-PROBE`, refusal `null`, service exact, `expired()` false with 23h49m left, dry `admit()` → `admits: true`. **Not the exhausted id.** |
| 3 | Exact envelope | $5.00 / $15.00 / 3 / 1 / **10** / −$6.00, 24h expiry — every field asserted equal |
| 4 | Sizing and k | `target_notional_usd 0.10`, `max_leg_notional_usd 0.125`, k `0.00619` |
| 5 | Freshness gate active | `authenticated_book_usable` present, wired at the submission path, `stale:11.2` → refused, fresh → permitted, `AUTH_TRIM_MAX_AGE_S 10.0` |
| 6 | Zero open orders, no known exposure | 01:16:23Z: `SHUTDOWN_VERIFIED`, `held_preserved []`, `still_pending []`, `errors []`, `refusals 0`, `position_net 0`, `realized_today 0` |
| 7 | Full regression | **5008 passed**, 27 skipped, 1 xfailed |
| 8 | Lineage / authorization invariants | ACCEPTANCE true, `undisclosed []`, `forbidden []`, `drift []`, `FORBIDDEN_CLASSES` all four intact, HALT present at verification time |

## Breadth is a min() of three numbers

`_rung_markets_of` returns `min(PMUS_SPRING_RUNG_MARKETS, markets_per_campaign)`
and the mandate's `max_total_markets` binds again at the wire. Moving fewer
than all three would have produced a probe that quietly stopped early while
every record claimed ten. All three now read **10**, and a test asserts they
agree.

The rung was widened **in place** rather than added as a new id: durable state
stores the rung ID, so a new pinned id meeting the stored `CP1` would have gone
through B3's `tightest()` — `markets_per_campaign` is a risk dimension — and
the stored 3 would have clamped the authorization back to 3. B3 is untouched.

## What binds the campaign's dollars, and what changed

`worst_case` derives `per_campaign = cap × markets`, so the **rung's** own
worst case is now `$5.00 × 10 = $50.00` where it read `$15.00` at three
markets. The owner held `max_total_spend` at **$15.00** while widening breadth,
so the **mandate** is now strictly tighter than the rung and is the
campaign-spend ceiling, enforced at the wire by GuardedTransport.

At three markets the two coincided at $15.00 and nothing distinguished them. At
ten they do not. **Breadth buys observations, not dollars.**

## What is unchanged

target $0.10 · leg $0.125 · k 0.00619 · maker/post-only · ~30 s rest · fee-safe
pair ceiling 0.98 · residual 3 · concurrency 1 · ACT/WAIT and model logic ·
market selection · the freshness gate · GuardedTransport and every existing
fail-closed control.

## Standing instructions for this run

* Do not force a trade.
* Do not alter price aggressiveness, rest duration, sizing or controller
  behaviour in response to early non-fills.
* Record every attempted action as
  `MODEL_ACT → EXECUTION_SKIPPED / SUBMITTED → REJECTED / RESTED → FILLED / NOT_FILLED`.
* Freshness skips are **not** model WAIT — they carry `attempted`, a
  per-condition refusal, and a venue/execution-adaptation class.
* On a fill, the existing controller handles inventory within the unchanged
  mandate. **Do not manufacture a complement by hand.**
* HALT immediately on any material discrepancy capable of increasing exposure
  beyond this authorization.
* Report `ARMED → SUBMITTED → FILLED → SETTLED → REALIZED`, and call nothing
  realized before the venue confirms it.

Removing HALT does not authorize trading; it un-halts the process. Orders
additionally require the mandate above, which
`test_the_halt_and_the_authorization_are_consistent` enforces in both
directions.
