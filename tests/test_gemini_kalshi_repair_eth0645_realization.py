from decimal import Decimal

from src import gemini_kalshi_crypto_live_campaign as base
from src.gemini_kalshi_repair_eth0645_realization import execution_record

D = Decimal


def test_repair_reconstructs_existing_fill_economics_exactly():
    record = execution_record()
    costs = base._costs(record)
    assert costs["raw_cost"] == D("0.93")
    assert costs["fees"] == D("0.020400")
    assert costs["capital"] == D("0.950400")

    settlement = {
        "gemini_outcome": "yes",
        "kalshi_outcome": "yes",
    }
    g_side = record["gemini_payload"]["eventOutcome"].lower()
    k_side = base._purchased_kalshi_side(record["kalshi_payload"])
    wins = int(g_side == settlement["gemini_outcome"]) + int(k_side == settlement["kalshi_outcome"])
    payout = D(wins)
    assert payout == D("1")
    assert payout - costs["capital"] == D("0.049600")
