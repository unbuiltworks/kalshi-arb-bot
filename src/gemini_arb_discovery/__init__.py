"""Platform-wide executable-arbitrage discovery for Gemini prediction markets.

READ-ONLY BY CONSTRUCTION. Nothing in this package imports an order transport,
holds a credential, or reaches a mutating endpoint. Every call is an
unauthenticated public GET.

This package is a separate research lane. It does not import from, write to, or
depend on the BTC5m/L5 information-edge experiment.
"""
