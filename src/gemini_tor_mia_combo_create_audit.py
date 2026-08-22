"""Compatibility entrypoint for the current Gemini extreme-combo live preflight.

Railway's isolated one-off service historically invokes this module name. Keep it
stable while routing to the read-only q=1 preflight. No order entry occurs here.
"""
from __future__ import annotations
import asyncio
from src.gemini_extreme_combo_live_preflight import run_once

if __name__ == "__main__":
    asyncio.run(run_once())
