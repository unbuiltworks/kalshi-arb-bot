"""Compatibility entrypoint for the current Gemini extreme combo validation.

Railway's isolated one-off service historically invoked this module name. Keep the
name stable while routing execution to the newer bounded zero-capital validation
matrix. No order entry or capital action occurs here.
"""
from __future__ import annotations
import asyncio
from src.gemini_extreme_combo_create_validation import run_once

if __name__ == "__main__":
    asyncio.run(run_once())
