"""Compatibility entrypoint for the isolated Gemini extreme-combo service.

The Railway Railpack plan for this one-off service pins this module name. Route it
to the current read-only q=1 live preflight without changing any shared scanner.
"""
from __future__ import annotations
import asyncio
from src.gemini_extreme_combo_live_preflight import run_once

if __name__ == "__main__":
    asyncio.run(run_once())
