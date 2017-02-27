import pytest

from decimal import Decimal

from jtune.jtune import median
from jtune.jtune import mean
from jtune.jtune import stdev


def test_median_empty():
    """An empty median() is 0"""

    assert median([]) == 0


def test_median_modulo():
    """median [2048, 4096, 49152, 64738] is 26624.0"""

    assert median([2048, 4096, 49152, 64738]) == 26624.0


def test_median_real():
    """median [2048, 4096, 49152] is 4096"""

    assert median([2048, 4096, 49152]) == 4096


def test_mean_invalid_length():
    """median [4096, 49152, 64738] with a length of -1 is 0"""

    assert mean([4096, 49152, 64738], -1) == 0


def test_mean_valid():
    """mean [4096, 49152, 64738] is Decimal('39328.66666666666666666666667')"""

    assert mean([4096, 49152, 64738]) == Decimal('39328.66666666666666666666667')


def test_std_dev():
    """stdev [2064, 4096, 8192, 49152] is 22329.918465293747"""

    assert stdev([2064, 4096, 8192, 49152]) == 22329.918465293747
