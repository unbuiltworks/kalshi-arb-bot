# Grok independent Cell C fee/cashflow audit packet

Purpose: independent forensic review of the PMUS BTC15m Cell C economics. Do not assume the existing reports are correct. Reconstruct from the lowest-level evidence available and report missing evidence rather than inferring it.

## Primary evidence
- `analysis/l5/favorite_dca/CELL_C_CAPACITY_REPORT.md`
- `analysis/l5/favorite_dca/CELL_C_CAPACITY.json`
- `scripts/pmus_cellC_capacity.py`
- `analysis/l5/favorite_dca/HYBRID_MAKER_TAKER_STUDY.md`
- `scripts/pmus_cellC_hybrid_execution.py`
- `src/pmus_fees.py`
- `tests/test_pmus_fees.py`
- `analysis/pmus/pmus_published_fee_schedule.json`

## Underlying replay evidence
- `analysis/l5/favorite_dca/replay_summary.json` (~8 MB)
- `analysis/l5/favorite_dca/trajectories.jsonl.gz` (~19 MB compressed)
- `scripts/pmus_favorite_dca.py`
- `analysis/l5/favorite_dca/PREREGISTRATION_C.md`
- `analysis/l5/favorite_dca/FAVORITE_DCA_RESULT.md`

## Accounting/correction context
- `analysis/l5/favorite_dca/RESEARCH_RECORD_CORRECTION.md`
- `analysis/l5/favorite_dca/CF_MATCH_EXTEND_ATTRIBUTION.md`
- `scripts/pmus_cfmatch_extend_attribution.py`
- `scripts/pmus_matched_decomposition.py`

## Additional capacity/liquidity evidence
- `analysis/l5/favorite_dca/CAPITAL_ENVELOPES.json`
- `analysis/l5/favorite_dca/CAPITAL_ENVELOPES.md`
- `scripts/pmus_dca_capital_envelopes.py`
- `scripts/pmus_entry_depth_study.py`

## Real PMUS reconciliation helpers/evidence locations
- `scripts/pmus_q1_settlement_audit.py`
- `scripts/pmus_window_ledger.py`
- `scripts/pmus_auth_preflight_readonly.py`

The auditor should locate any referenced probe manifests/results from these scripts/reports. If actual venue fill/fee/balance evidence is not committed on this branch, mark real-trade calibration as missing rather than inventing it.

## Required independent checks
1. Recompute fill-level fees with the effective-dated PMUS formula and venue rounding.
2. Reconstruct total cashflow as sales + settlement/redemption - purchases - fees + verified credits.
3. Reproduce or reject the ~$100 row: gross +$4.2812, slippage -$0.0153, taker fees -$5.0024, net -$0.7365 per window.
4. Trace the exact code definition of `turnover=30.24`; do not infer its units.
5. Break gross, fees, turnover and net down by ENTRY / EXTEND / EXIT.
6. Check economic outcome price versus wire representation.
7. Check for duplicate fees, fees on cancelled/unfilled size, quantity/notional mistakes, and incorrect rounding grain.
8. Audit maker-fill evidence discipline in the hybrid study (no touch=fill).
9. Distinguish defensible top-of-book capacity from extrapolated capacity.
10. Do not use FIFO/pro-rata matched/residual attribution as proof of total portfolio profitability.

Status labels must remain replay/shadow/live probe/submitted/filled/settled/realized. Nothing in this packet is automatically realized profit.
