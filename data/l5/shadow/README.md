# Shadow tape directory

Tapes written here by `src.polymarket_l5.owned_order_shadow` during a
**real collection run in CI**.

**Local `--preflight-only` tapes are deliberately NOT committed** (see
`.gitignore`). A preflight writes a single `shadow_preregistration` row
and no observations, so an empty tape sitting in an evidence directory
could later be misread as *a collection run that found zero evidenced
fills*. That distinction is load-bearing: Shadow S1's first gate is
precisely "are there evidenced fills at anything like L5's frequency", and
a near-zero count is an informative result rather than a bug. An artifact
that cannot be told apart from that result must not exist here.

The pre-registration itself is durably recorded in
`analysis/l5/shadow/SHADOW_S1_PREREGISTRATION.md` with candidate
fingerprint `441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`,
so nothing is lost by excluding the preflight tapes.
