"""Tax-grade factual ledger schema for high-frequency event-contract trading.

This module deliberately does NOT decide tax characterization. Prediction-market tax
classification is unsettled; the ledger records economic facts so a tax professional
can map them to the appropriate treatment later without reconstructing thousands of
fills from screenshots or venue UIs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal as D
import csv
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class TradeLedgerRow:
    timestamp_utc: str
    venue: str
    event_id: str
    market_id: str
    category: str
    action: str
    outcome: str
    maker_taker: str
    quantity: D
    price: D
    gross_cashflow_usd: D
    fee_usd: D
    settlement_cashflow_usd: D = D('0')
    realized_trade_pnl_usd: D = D('0')
    reward_type: str = ''
    reward_status: str = ''
    reward_amount_usd: D = D('0')
    currency: str = 'USD'
    external_id: str = ''
    source: str = ''
    tax_characterization: str = 'UNCLASSIFIED_PENDING_TAX_ADVISOR'


FIELDNAMES = list(TradeLedgerRow.__dataclass_fields__)


def write_csv(path: Path, rows: Iterable[TradeLedgerRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            writer.writerow({k: str(v) if isinstance(v, D) else v for k, v in data.items()})
