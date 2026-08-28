"""Railway-safe entrypoint for the read-only 15-minute crypto velocity shadow.

This branch entrypoint intentionally exposes no order-entry path. It delegates to
the existing BTC/ETH/SOL/XRP Gemini/Kalshi WebSocket shadow collector, whose
defaults are read-only, 15-minute duration, and 100 ms maximum cross-venue quote
skew. Railway service-level arguments may extend the observation duration.
"""

from src.gemini_kalshi_multi_ws_shadow import main


if __name__ == "__main__":
    main()
