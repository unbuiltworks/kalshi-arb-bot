# E1 superseded fingerprints — must never be armed

Each entry records WHY, because a superseded fingerprint with no reason is
just a hash someone will eventually re-pin.

| # | fingerprint | why it must never be armed |
|---|---|---|
| 1 | `110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c` | the risk bound was realized-loss-only; it did not count unresolved residual exposure or the prospective attempt, so `$5.00` was not a true ceiling |
| 2 | `bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a` | **partial-fill accounting lost quantity.** A 0.13 first leg filling 0.05 then 0.08 complemented only the first partial and silently left 0.08 unhedged. Its own fix then lost 0.005 to sub-tick rounding — the same bug one layer down |
| 3 | `f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4` | WIP; superseded by the namespace and state-isolation work (E1 was reading XR2W's `PMUS_EXEC1_*` and writing into `/state/pmus-ws/exec1x`) |
| 4 | `e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310` | **the ARMED production path terminates before `run_cohort`.** `main()`'s armed branch is a bare `raise SystemExit`; there is no `snapshot_provider`. Arming it would have produced **twelve empty windows and zero placements.** Separately, frozen eligibility rule E1 requires authorization "for this exact market slug", but `eligible()` calls `armed_env_summary()` and never calls `may_submit(slug)` — so the per-slug gate was stated and not enforced, leaving market selection unconstrained |

## The pattern worth naming

All four were invisible to a fully green suite. Every one of the 110 tests
entered at `run_cohort` or below with a provider injected; **none entered at
`main()` armed**, which is the only path that runs live. The declared
artifact and the executed artifact diverged four times, and the tests could
not tell because they never started where production starts.

That is why the next fingerprint is only issued after the **actual
production `main()`** has been driven end-to-end, armed, through two
distinct windows, with fake transport — and after the same SHA has booted
DISARMED in Railway.

## What was never at risk

No order was ever placed by any E1 build. No E1 build was ever armed or
deployed. Each defect was caught before arming, three of them by driving the
artifact rather than reading it, and the dollar ceilings would have held in
every case. XR2W stayed byte-identical throughout
(`dda1e2c5…`, `800078a8…`, `add23515…`, `50b9ff13…`), and A6's spent latch
was never touched.
