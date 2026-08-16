"""Temporary Railpack-safe 1-cent read-only prewarmed HOT study."""
import sys

from src.gemini_kalshi_predictive_prewarmed_safe import main


if __name__ == "__main__":
    sys.argv = [
        sys.argv[0],
        "--assets", "BTC", "ETH", "SOL", "XRP",
        "--seconds", "1800",
        "--max-quote-skew-ms", "100",
        "--capital-limit", "5",
        "--min-profit", "0.01",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-hot-1c",
    ]
    main()
