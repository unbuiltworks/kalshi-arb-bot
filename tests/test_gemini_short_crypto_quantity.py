from decimal import Decimal as D

from src.gemini_short_crypto_quantity import quantity_supported


def test_q1_supported_on_cent_grid():
    assert quantity_supported(quantity=D("1"), minimum=D("0.01"), increment=D("0.01"))


def test_below_minimum_rejected():
    assert not quantity_supported(quantity=D("0.01"), minimum=D("0.02"), increment=D("0.01"))


def test_off_grid_quantity_rejected():
    assert not quantity_supported(quantity=D("1"), minimum=D("0.03"), increment=D("0.02"))
