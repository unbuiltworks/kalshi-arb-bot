# Generic cross-market shadow deployment

This branch is deployed by the isolated Railway `polymarket-kalshi-generic-shadow` service.
It is read-only: fuzzy discovery cannot enter the scanner directly; only explicit registry pairs can be streamed, and order-entry calls are prohibited by tests.
