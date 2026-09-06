"""Shared fixtures. The ACTIVE ramp rung is process state set by the
campaign (OWNER 5560851070); every test starts and ends on the default
(env) rung so a rung set in one test can never leak into another."""
from __future__ import annotations

import pytest

from src import pmus_l5_ramp_ladder as ladder


@pytest.fixture(autouse=True)
def _reset_active_rung():
    ladder.set_active(None)
    yield
    ladder.set_active(None)
