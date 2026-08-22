# Unarmed hardened v2 validation

Fresh source deployment checkpoint after canonical ledger connectivity succeeded.

Railway is configured to start `src.gemini_kalshi_basis_risk_q1_campaign_v2`, with the basis-risk arm explicitly set to NO and the old manual reconciliation flag explicitly set to NO. The v2 supervisor must independently verify the canonical ledger first; only then should it fail closed because the live basis-risk arm is disabled.

No order path is authorized by this validation.
