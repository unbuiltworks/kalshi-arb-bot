from itertools import product
from decimal import Decimal


def coverage(xs):
    p = Decimal("1")
    for x in xs:
        p *= x
    return p + sum((Decimal("1") - x for x in xs), Decimal("0"))


def test_binary_4_and_5_leg_floor_is_one():
    for n in (4, 5):
        vals = []
        for bits in product((Decimal("0"), Decimal("1")), repeat=n):
            vals.append(coverage(bits))
        assert min(vals) == Decimal("1")


def test_scalar_samples_4_and_5_leg_floor_is_at_least_one():
    grid = (Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))
    for n in (4, 5):
        for xs in product(grid, repeat=n):
            assert coverage(xs) >= Decimal("1")
