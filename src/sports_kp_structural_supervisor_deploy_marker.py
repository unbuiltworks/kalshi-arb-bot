"""Watched deployment marker for sports_poly_structural_live_supervisor.

No order-entry logic lives here. The Railway sports-kp-live-executor start command
runs the bounded q1 structural totals supervisor; this file only forces a fresh
source snapshot after the supervisor and restart-policy changes.
"""
