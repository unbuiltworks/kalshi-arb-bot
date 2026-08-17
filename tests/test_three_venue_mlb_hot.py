import asyncio
from decimal import Decimal

from src.mlb_game_side_normalization import NormalizedSide, _identity
from src.three_venue_mlb_hot import GeminiHotStore, HotContract, HotUniverse
from src.universal_sports_identity import SportsEventIdentity


def _gemini_side() -> NormalizedSide:
    event = SportsEventIdentity(
        sport="baseball", league="mlb", participants=("BAL", "TB"), scheduled_start_ms=1787004300000
    )
    return NormalizedSide(
        _identity(
            venue="gemini", market_id="GEMI-X", event=event, family="moneyline",
            subject_type="team", subject="BAL", stat="", comparator="YES", line=None,
        ),
        "GEMI-X", "YES", "Baltimore",
    )


def _universe() -> HotUniverse:
    side = _gemini_side()
    contract = HotContract(side.contract_key, {"gemini": side}, None)
    return HotUniverse(
        contracts={side.contract_key: contract},
        market_to_keys={("gemini", "GEMI-X"): (side.contract_key,)},
        initial_gemini_events=[], diagnostics={},
    )


def _events(yes: str, no: str):
    return [{
        "ticker": "MLB-2608172205-BAL-TB-M",
        "contracts": [{
            "instrumentSymbol": "GEMI-X",
            "label": "Baltimore",
            "prices": {"buy": {"yes": yes, "no": no}},
        }],
    }]


def test_gemini_hot_store_only_wakes_on_price_change():
    store = GeminiHotStore(_universe())
    assert store.ingest(_events("0.45", "0.56"), notify=True) == 1
    first = store.updates.get_nowait()
    assert first.venue == "gemini"
    assert first.market_id == "GEMI-X"

    assert store.ingest(_events("0.45", "0.56"), notify=True) == 0
    assert store.updates.empty()

    assert store.ingest(_events("0.44", "0.57"), notify=True) == 1
    assert store.updates.get_nowait().market_id == "GEMI-X"


def test_gemini_hot_store_builds_fee_inclusive_q1_quote():
    universe = _universe()
    key = next(iter(universe.contracts))
    store = GeminiHotStore(universe)
    store.ingest(_events("0.45", "0.56"), notify=False)
    row = store.quote(key, max_age_ms=10_000)
    assert row is not None
    assert row.quote.venue == "gemini"
    assert row.quote.yes_depth == Decimal("1")
    assert row.quote.no_depth == Decimal("1")
    assert row.quote.yes_cost >= Decimal("0.45")
    assert row.quote.no_cost >= Decimal("0.56")


def test_hot_store_queue_is_asyncio_queue():
    store = GeminiHotStore(_universe())
    assert isinstance(store.updates, asyncio.Queue)
