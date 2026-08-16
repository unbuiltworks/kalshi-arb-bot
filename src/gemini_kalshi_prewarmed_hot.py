"""Stable entrypoint for the activation-gated prewarmed read-only HOT scanner."""
from src.gemini_kalshi_prewwarmed_hot import *  # noqa: F401,F403
from src.gemini_kalshi_prewwarmed_hot import main


if __name__ == "__main__":
    main()
