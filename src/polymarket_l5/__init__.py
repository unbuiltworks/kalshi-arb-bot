"""Read-only Polymarket historical exporter/analyzer for the L5 window trader.

This package NEVER authenticates, never signs, and never mutates. It issues
public HTTP GET requests only, against an explicit host allowlist, and it is
completely independent of the Kalshi arb bot and the SOL executor.
"""

__all__ = ["config"]
