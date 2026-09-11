# Gemini BTC5m Inventory-Maker v2 Live Approval — 2026-08-27

User explicitly approved exactly one live Gemini BTC 5-minute inventory-maker v2 campaign at q=1 per outcome.

Authorized feature checkpoint: `f1c70209a57764695b45fec586859a05d7640770` on `feature/gemini-short-crypto-live-q1`.

Authorized launcher: `deploy/aws/run_gemini_btc5m_inventory_maker_q1_v2_live_once.sh` on `aws-migration-foundation`.

Approval scope:
- exactly one campaign;
- Gemini BTC 5-minute only;
- maximum q=1 per outcome;
- maker-only BUY LIMIT MOC entries;
- bounded temporary one-sided inventory allowed;
- no automatic repeat / next-window loop;
- no scaling approval;
- executed inventory is not realized profit until authoritative Gemini settlement/reconciliation.

Pre-approval v2 gate:
- 15 tests passed;
- authenticated disarmed market preflight passed;
- fresh market snapshot `GEMI-BTC05M2608272015-UP` showed modeled +$0.10 matched-pair edge after conservative maker fees;
- active short-crypto orders: 0;
- active short-crypto positions: 0;
- AWS-local reservation: false;
- order entry during preflight: false.

The live launcher must still re-run its own fresh market preflight and clean-account/local-ledger gate. The +$0.10 preflight snapshot is not authorization to use stale prices. If the fresh live gate fails, no orders should be placed.
