# Gemini BTC5m Inventory-Maker v3 Live Approval — 2026-08-27

User explicitly approved exactly one live Gemini BTC 5-minute inventory-maker v3 campaign at q=1 per outcome.

Authorized feature checkpoint: `5a131b5d755d8b59155d6b3845d9fba161656f0c` on `feature/gemini-short-crypto-live-q1`.

Authorized launcher: `deploy/aws/run_gemini_btc5m_inventory_maker_q1_v3_live_once.sh` on `aws-migration-foundation`.

Approval scope:
- exactly one campaign;
- Gemini BTC 5-minute only;
- maximum q=1 per outcome;
- maker-only BUY LIMIT MOC entries;
- bounded temporary one-sided inventory allowed;
- Gemini REST book for public market data;
- private Gemini WebSocket for order/fill transport;
- no automatic second campaign;
- no overnight supervisor approval yet;
- no scaling approval;
- executed inventory is not realized profit until authoritative Gemini settlement/reconciliation.

Pre-approval v3 gate:
- 18 tests passed;
- authenticated disarmed REST-book market preflight passed;
- fresh market snapshot `GEMI-BTC05M2608272035-UP` showed modeled +$0.07 matched-pair edge after conservative maker fees;
- active short-crypto orders: 0;
- active short-crypto positions: 0;
- AWS-local reservation: false;
- order entry during preflight: false.

The live launcher must rerun its own fresh market preflight and clean-account/local-ledger gate. The +$0.07 preflight snapshot is not authorization to use stale prices. If the fresh live gate fails, no orders should be placed.
