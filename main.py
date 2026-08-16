"""Railpack-safe entrypoint for the read-only 21:00 settlement reconciler."""
from src.gemini_kalshi_target_reconcile import main

if __name__ == "__main__":
    main()
