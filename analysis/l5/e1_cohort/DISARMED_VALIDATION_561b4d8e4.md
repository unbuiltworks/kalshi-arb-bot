# E1 disarmed validation — `561b4d8e49bdea77d67d3a1babd82540d41b92f9`

Commit `561b4d8e49bdea77d67d3a1babd82540d41b92f9`
Fingerprint `481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486`
Service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`)
Project `arb-ev-wallet-shadow` (`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`)
Environment `production` (`f3642d27-26dc-4e21-9170-bc76290c076f`)

**NOT ARMED. No order was placed. Realized P&L $0.00.**

## 1. Isolated comparison suites — zero regressions

Both run with `-p no:randomly`, same ignores, in isolated trees (the
baseline in a detached `git worktree` at `14e7d7477`, so neither run could
see the other's files):

| | baseline `14e7d7477` | fixed `561b4d8e4` |
|---|---|---|
| passed | 11096 | **11115** |
| failed | 110 | 110 |
| errors | 23 | 23 |
| skipped / xfailed | 27 / 1 | 27 / 1 |

The `FAILED`/`ERROR` name sets are **identical** — 133 lines each, and
`comm` is empty in both directions. The +19 passes are exactly the new
tests. The 110 pre-existing failures are unrelated (`tests/test_gemini_*`,
deploy-manifest guards) and fail the same way at `14e7d7477`.

Three further modules (`test_gemini_collector_windows`,
`test_gemini_position_semantics_probe`, `test_gemini_settlement_probe`)
fail *collection* at both SHAs for a missing `websockets` dependency in
this container, so they were excluded from both runs equally.

E1 package alone: **193/193 green**. Freeze: `assert_frozen()` passes,
computed == literal, 139 invariants.

## 2–3. In-image verification — deployment `4e2d2ba0-c926-44e0-9c8f-dc0b97ec9133`

Read-only, armed=no, no venue POST, no secret value printed. Verbatim:

    IMGVERIFY_GIT_SHA 561b4d8e49bdea77d67d3a1babd82540d41b92f9
    IMGVERIFY_PYTHON 3.11.17
    IMGVERIFY_CRYPTOGRAPHY version=49.0.0
    IMGVERIFY_SIGN_VERIFY ok sig_bytes=64
    IMGVERIFY_FINGERPRINT literal=481d1d3f...e2486 computed=481d1d3f...e2486
                          match=True invariants=139
    IMGVERIFY_SIGNING_CONTRACT {"header_names": ["Content-Type",
      "X-PM-Access-Key", "X-PM-Signature", "X-PM-Timestamp"],
      "header_values_recorded": false, "latch_touched": false,
      "network_performed": "none", "ok": true, "venue_post": "none"}
    IMGVERIFY_CRED_CONTRACT {"VALUES_NEVER_READ_OR_RECORDED": true,
      "names_not_read_by_any_code": ["PMUS_API_SECRET"],
      "names_read": ["PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY"],
      "present": {"PMUS_API_KEY_ID": true, "PMUS_API_SECRET_KEY": true}}
    IMGVERIFY_ENTRYPOINT module=scripts.l5_e1_cohort_run
                         file=/app/scripts/l5_e1_cohort_run.py
    IMGVERIFY_THIRD_PARTY_AT_ENTRYPOINT []
    IMGVERIFY_ARMED_FN False

The signing contract is satisfied by the **production** credential names
with a real Ed25519 signature, and only the four header NAMES are
recorded. No value, no length, no prefix.

`IMGVERIFY_THIRD_PARTY_AT_ENTRYPOINT []` is the mechanism of the original
defect, still true and now harmless: importing the entrypoint loads no
third-party module, which is why the import assertion could not catch the
missing dependency — hence the separate build-time signing proof.

Build steps, from the build log of this image:

    [ 3/24] RUN pip install --no-cache-dir cryptography==49.0.0
            Successfully installed cffi-2.1.1 cryptography-49.0.0 pycparser-3.0
            (wheel: cryptography-49.0.0-cp311-abi3-manylinux_2_34_x86_64.whl)
    [23/24] BUILD OK fingerprint   (assert_frozen)
    [24/24] BUILD OK signing       (throwaway Ed25519 generate/sign/verify)

## Final deployment — `b10e8192-3f78-4fb4-805f-e9628be2494d`, **SUCCESS**

Normal E1 entrypoint restored: `startCommand` is
`python3 -m scripts.l5_e1_cohort_run`. `restartPolicyType: NEVER`.

Disarmed boot output at 2026-10-07T18:39:11Z:

    "LABEL": "DISARMED. NO ORDER MAY BE PLACED."
    "AUTHORIZATION": "NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE SET."
    "armed": false
    "fingerprint_pin_present": false
    "disarmed_behaviour": "boots, asserts the frozen spec, prints the
      preflight and the DISARMED refusal, and returns 0 WITHOUT requesting
      a snapshot, claiming a latch slot or reaching an order endpoint"

### Zero selector / feed / order network activity

`dns` and `network-flow` streams, 18:38:30Z–18:41:00Z: **both empty.**

With a POSITIVE CONTROL, because absence of evidence is not evidence of
absence and I made exactly that mistake earlier in this incident. The same
streams during the armed run do report for this service:

    16:48:58.250  gamma-api.polymarket.com              A/AAAA  NOERROR
    16:48:58.275  ws-subscriptions-clob.polymarket.com  A/AAAA  NOERROR

So the empty result is real: the disarmed boot resolved no selector host,
no feed host and no venue host.

(That control also re-confirms the root cause: `api.polymarket.us`, the
order endpoint, appears in neither window. Signing failed before any
socket.)

### The old cohort tape is untouched

Established from the code path rather than by re-reading it, which is the
stronger claim: `main()` returns `0` at the arming gate **before**
`progress_factory`, `auth_factory` or `recorder_factory` are constructed.
No tape or latch file is opened, created or written on the disarmed path.
Combined with zero network activity, this boot touched nothing.

`windows_used` therefore remains **1 of 12** and the failed cohort's
records keep the hashes recorded in the incident diagnosis.

## 4. Production-path regression reaches the submission boundary

`test_the_whole_chain_under_the_production_env_contract` — PASSED. Drives
the real `runner.main()` with the production credential contract (real
variable names, throwaway key):

    env contract -> signing proven locally -> WINDOW_OPENED -> live-book
    snapshot -> eligibility -> placement decision -> submission boundary
    -> FAKE transport

with `quantity == 0.13` and `participateDontInitiate == true` on every
post, `venue_errors == 0`, `local_execution_faults == 0`.

## 5. Missing signing capability produces exactly one local fault

`test_a_broken_credential_contract_is_one_local_failure_not_a_storm` —
PASSED in all four parametrisations (`dependency_missing`, `secret_unset`,
`secret_unusable`, `key_id_unset`):

    stopped_because        == LOCAL_EXECUTION_FAULT
    local_execution_faults == 1
    venue_errors           == 0
    consecutive_venue_errors == 0
    truncation_cause       == LOCAL_EXECUTION_FAULT  (never VENUE_ERROR_STORM)
    transport_constructed  == False
    latch_slots_consumed   == 0
    placements             == 0

`test_it_stops_on_the_FIRST_fault_not_the_fifth` — PASSED, and asserts
zero snapshots were even requested.

`test_a_genuine_venue_error_still_gets_the_bounded_storm_protection` —
PASSED: with signing healthy, a rejecting venue still produces exactly 5
venue errors and `VENUE_ERROR_STORM`. The protection that works is intact.

## Residuals, reported rather than changed

1. The service config still carries `source.commitSha: "14e7d7477"` while
   the deployed image is `561b4d8e4` (proven from inside via
   `RAILWAY_GIT_COMMIT_SHA`). Variable-triggered deploys resolve the
   BRANCH HEAD and ignore that pin — established earlier in this incident
   — and branch head is `561b4d8e4`. The pin is stale, not wrong in
   effect. Re-pinning was not in the authorization, so it was not done.
2. `PMUS_E1_SESSION_ID` is still set (`sid_set=True`); `PMUS_E1_LIVE=NO`
   and arm/gate/pin are blank, so `arming.armed()` is `False`. A session
   id alone cannot arm anything.
3. Three leftover marker variables from this validation
   (`CRYPTO_PROBE_RUN`, `IMAGE_VERIFY_RUN`, `E1_DISARMED_BOOT_561b4d8e4`).
   Inert; removing them would trigger another deployment.

## Unchanged

0.13 shares/order, $0.60 first-leg ceiling, $0.96 pair ceiling, 20
attempts/window, 12 windows / 240 maximum, $1.56/window, $5.00 cohort risk
ceiling, 13 complement tranches, maker-only, no chase/requote/taker
rescue. Websocket readiness and the immediate per-snapshot freshness
verification untouched.
