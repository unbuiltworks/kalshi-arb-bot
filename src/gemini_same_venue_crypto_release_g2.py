"""One-off release of confirmed-zero-fill g2 attempt 5 reservation."""
import asyncio
from src import gemini_same_venue_crypto_budget as budget

CANDIDATE = {
    "event_ticker": "XRP2608312100",
    "maker_symbol": "GEMI-XRP2608312100-HI0D7",
    "hedge_symbol": "GEMI-XRP2608312100-HI0D8",
}
TRADE_ID = "crypto-gem-ladder-g2-005"

async def main():
    await budget.release(candidate=CANDIDATE, trade_id=TRADE_ID, reason="AUTHORITATIVE_GEMINI_ZERO_FILL_RECONCILED")
    print("GEMINI_SAME_VENUE_G2_RELEASED confirmed_zero_fill=true trade_id=" + TRADE_ID, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
