"""Gemini prediction-market EXECUTION. The only Gemini package that may mutate.

Every other Gemini module in this repository is read-only and a test proves it
cannot reach the mutation path. This package exists so that capability lives in
exactly one place, inert by default.
"""
