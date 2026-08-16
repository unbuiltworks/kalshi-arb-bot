"""Railpack-safe entrypoint for the isolated read-only Gemini/Kalshi HOT scanner."""
from src.gemini_kalshi_multi_ws_hot_catalog import hot


if __name__ == "__main__":
    hot.main()
