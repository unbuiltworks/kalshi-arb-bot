# ARB V5.3 Live Canary Incident — 2026-08-30

## Classification

**REALIZED LOSS / RELEASE INSTALLATION BUG**

This was not a harmless workflow failure and not a shadow result. The approved canary submitted a real order, received a real one-sided fill, failed to flatten inside the intended rescue invariant, and subsequently settled against the account.

## Authorization boundary

- User authorization: exactly one frozen V5.3 BTC5 adaptive campaign.
- Approval ID: `v53_20260830_canary_1`
- Frozen branch: `release/gemini-adaptive-maker-v5-3-final`
- Frozen SHA: `1f876470e9e4de43235914379636c4b16d02c8ab`
- Continuous mode: OFF
- Maximum campaigns: 1
- Intended rescue-loss bound: $0.30
- Campaign-loss ceiling: $1.00
- Daily-loss ceiling: $3.00

The authorization is consumed. No second canary is authorized by it.

## Live workflow evidence

GitHub Actions live run:

- Run ID: `33335104759`
- Job ID: `99320498715`
- Workflow: `.github/workflows/aws-gemini-adaptive-v5-3-live.yml`
- Exact release tests before mutation: **90 passed**
- Fresh preflight before mutation:
  - account orders: 0
  - account positions: 0
  - available USD: **52.3155**

## What actually traded

Market:

- Ticker: `BTC05M2608302105`
- Symbol: `GEMI-BTC05M2608302105-UP`

Durable campaign state showed:

- classification: `LIVE_Q10_V5_2_OPEN_NOT_REALIZED`
- quantity: 10
- YES quote: 0.4400
- NO quote: 0.4500
- YES fill: **10**
- NO fill: **0**
- residual: **10 YES**

This reached the **submitted** and **filled** states. It never reached matched/locked.

## Rescue failure

At entry, the standing rescue model was inside the intended bound (about $0.23 worst current loss under the then-current book).

After the YES fill, the book moved before flattening. The live rescue decision observed approximately:

- filled outcome: YES
- filled price: 0.44
- current unwind execution price: 0.38
- quantity: 10
- current modeled unwind P&L: **-$0.82**
- current modeled loss: **$0.82**
- configured rescue-loss maximum: **$0.30**
- result: `RESCUE_BOUND_NO_LONGER_AVAILABLE`
- flattened: false
- hard halt: true

No open orders remained, but one directional position remained at the initial post-run reconciliation.

## Root cause: V5.3 was overwritten by V5.2 during installation

The V5.3 runtime installation order was incorrect.

`gemini_adaptive_maker_v5_3_runtime.py` installed V5.3, then installed the V5.3 recovery layer.

The V5.3 recovery installer invoked V5.2 recovery installation. V5.2 recovery installation invokes `v52.install()`, which resets the shared V5 campaign hook back to the V5.2 campaign function.

Therefore the process labeled/launched as V5.3 actually executed the V5.2 campaign path. The evidence is the live durable classification `LIVE_Q10_V5_2_OPEN_NOT_REALIZED` and V5.2-style trade/client IDs.

The V5.3 dead-man then correctly refused to auto-recover that state because its stricter recovery boundary considered the V5.2 classification foreign to V5.3.

A second release defect also exists: the quantity-grid compatibility patch had been installed in the read-only readiness/diagnostic process but was not guaranteed to be installed by the live runtime process itself. Runtime behavior must not depend on a preflight container's monkeypatches.

## Settlement and realized P&L

A committed exact-state read-only audit was run after the market closed:

- Workflow: `.github/workflows/aws-v53-canary1-recovery-audit.yml`
- Successful audit run: `33335550373`
- Account orders: **0**
- Account positions: **0**
- Available USD after settlement: **48.0255**
- Available USD before canary: **52.3155**

Account delta attributable to this isolated canary:

**48.0255 - 52.3155 = -4.2900 USD**

Classification:

- submitted: YES
- filled: YES (one leg only)
- matched/locked: NO
- settled: YES (venue account has zero position after market close)
- realized P&L: **-$4.29**

Do not count any modeled reward or future rebate not already reflected in the reconciled account balance.

The durable local state remains stale/open (`RESCUE_BOUND_NO_LONGER_AVAILABLE`) even though the venue is flat. That local-state mismatch must be reconciled before any future live release uses this state directory.

## Required remediation before any new live canary

1. Keep frozen V5.3 final SHA immutable as incident evidence.
2. Build a new release; do not patch the frozen SHA.
3. Fix install ordering so the final installed campaign hook is provably V5.3 after recovery/dead-man installation.
4. Make quantity-grid support native to the live runtime path, not dependent on an earlier diagnostic/preflight process.
5. Add an integration regression that calls the same runtime installation sequence as production and asserts:
   - final campaign hook is V5.3;
   - new durable classification is `LIVE_ADAPTIVE_MAKER_V5_3_OPEN_NOT_REALIZED`;
   - V5.2 classification/client-ID prefixes cannot be produced by a V5.3 runtime campaign.
6. Add a regression for the first-fill timing gap: a rescue that was safe at pre/post admission can become unsafe before the fill is observed. The architecture must decide how to prevent a $0.30 rescue invariant from turning into an unbounded settlement gamble.
7. Reconcile stale local state against the now-flat venue and prove startup cannot interpret it as live exposure.
8. Re-run public market simulation and authenticated disarmed readiness on the new exact SHA.
9. Require fresh explicit user authorization for any subsequent real-money canary.

## Process lesson

A pre-entry rescue check is not itself a guaranteed loss cap. If the only permitted exit can become unsafe between quote admission and fill detection, the system can still inherit a full directional contract loss. The next release must either make that gap acceptably bounded by construction or explicitly refuse sizes/prices for which the worst plausible fill-to-action move can exceed the campaign loss ceiling.
