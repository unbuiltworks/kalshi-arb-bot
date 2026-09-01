"""Public-only best-of Gemini crypto 5m candidate selector.

Discovery and book reads in this module require no credentials and never mutate an
account. The selector looks across a bounded asset allowlist and returns at most
one same-contract YES/NO q1 candidate. Execution remains the responsibility of
the separately armed dynamic owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
import re
import time
from typing import Iterable, Mapping

import httpx

from src import gemini_l5_ladder_q1 as ladder
from src import gemini_l5_pair_q1 as pair
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base

DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP")
EVENT_RE = re.compile(r"^(BTC|ETH|SOL|XRP)05M(\d{10})$")


@dataclass(frozen=True)
class PublicCandidate:
    asset: str
    event: base.Event
    bid: D
    bid_size: D
    ask: D
    ask_size: D
    model: dict[str, D | str | int]

    @property
    def edge(self) -> D:
        return D(str(self.model["edge"]))

    @property
    def max_queue(self) -> D:
        return max(self.bid_size, self.ask_size)


def normalize_assets(values: Iterable[str] = DEFAULT_ASSETS) -> tuple[str, ...]:
    requested = []
    for value in values:
        asset = str(value).strip().upper()
        if asset in DEFAULT_ASSETS and asset not in requested:
            requested.append(asset)
    return tuple(requested)


def asset_from_ticker(ticker: str) -> str | None:
    match = EVENT_RE.fullmatch(str(ticker).strip().upper())
    return match.group(1) if match else None


async def discover_nearest_events(
    *,
    assets: Iterable[str] = DEFAULT_ASSETS,
    min_tte: float = 105.0,
) -> dict[str, base.Event]:
    """Return the nearest eligible open 5m event for each requested asset."""
    allowed = set(normalize_assets(assets))
    if not allowed:
        return {}
    found: dict[str, list[base.Event]] = {asset: [] for asset in allowed}
    now_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-l5-crypto5m-selector/1.0"},
    ) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{base.REST}/v1/prediction-markets/events",
                params=[
                    ("status", "active"),
                    ("category", "Crypto"),
                    ("limit", "500"),
                    ("offset", str(offset)),
                ],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, Mapping) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                asset = asset_from_ticker(ticker)
                if asset not in allowed:
                    continue
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = base._iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None or expiry <= now_ms:
                        continue
                    event = base.Event(
                        ticker=ticker,
                        symbol=symbol,
                        expiry_ms=expiry,
                        quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                        quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                        price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                    )
                    if event.seconds_to_expiry >= float(min_tte):
                        found[asset].append(event)
            pagination = payload.get("pagination") if isinstance(payload, Mapping) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    return {
        asset: min(events, key=lambda event: event.expiry_ms)
        for asset, events in found.items()
        if events
    }


async def select_best_candidate(
    *,
    assets: Iterable[str] = DEFAULT_ASSETS,
    min_edge: D = D("0.01"),
    min_tte: float = 105.0,
    tie_seed: str = "",
) -> PublicCandidate | None:
    """Rank safe same-contract pairs by edge, then queue depth and expiry."""
    events = await discover_nearest_events(assets=assets, min_tte=min_tte)
    candidates: list[PublicCandidate] = []
    for asset, event in events.items():
        try:
            bid, bid_size, ask, ask_size = await ladder.fresh_top_retry(
                event.symbol, attempts=2, delay_s=0.10
            )
        except RuntimeError:
            continue
        model = pair.choose_pair(
            bid,
            ask,
            min_edge=D(str(min_edge)),
            yes_queue=bid_size,
            no_queue=ask_size,
            tie_seed=f"{tie_seed}-{asset}",
        )
        if model is None:
            continue
        candidates.append(
            PublicCandidate(
                asset=asset,
                event=event,
                bid=bid,
                bid_size=bid_size,
                ask=ask,
                ask_size=ask_size,
                model=dict(model),
            )
        )
    if not candidates:
        return None
    candidates.sort(
        key=lambda candidate: (
            -candidate.edge,
            candidate.max_queue,
            candidate.event.expiry_ms,
            candidate.asset,
        )
    )
    return candidates[0]
