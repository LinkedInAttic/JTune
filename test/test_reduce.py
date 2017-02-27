import datetime
import pytest

from decimal import Decimal

from jtune.jtune import reduce_seconds
from jtune.jtune import reduce_k
from jtune.jtune import ord_num
from jtune.jtune import sec_diff


def test_reduce_seconds_return_seconds():
    """Output as 1s"""

    assert reduce_seconds(secs=1) == '1s'


def test_reduce_seconds_return_minutes():
    """2064 seconds is 34m24s"""

    assert reduce_seconds(secs=2064) == '34m24s'


def test_reduce_seconds_return_hours():
    """64738 seconds is 17h58m"""

    assert reduce_seconds(secs=64738) == '17h58m'


def test_reduce_seconds_return_days():
    """129476 seconds is 1d11h"""

    assert reduce_seconds(secs=129476) == '1d11h'


def test_reduce_k_short_form():
    """4096, shortform, is 4M"""

    assert reduce_k(size=4096, short_form=True) == '4M'


def test_reduce_k_long_form():
    """4096 is 4 MiB"""

    assert reduce_k(size=4096, short_form=False) == '4 MiB'


def test_reduce_k_precision():
    """64738 with precision of 1 is 63.2M"""

    assert reduce_k(size=64738, precision=1) == '63.2M'


def test_ord_num_st():
    """1 is 1st"""

    assert ord_num(1) == '1st'


def test_ord_num_nd():
    """2 is 2nd"""
    assert ord_num(2) == '2nd'


def test_ord_num_rd():
    """3 is 3rd"""

    assert ord_num(3) == '3rd'


def test_ord_num_th():
    """64738 is 64738th"""

    assert ord_num(64738) == '64738th'


def test_sec_diff_first():
    """(2017, 2, 1, 14, 41, 20, 922749) - (2017, 1, 21, 14, 41, 18, 457651) is Decimal('-950402.465098')"""

    first = datetime.datetime(2017, 2, 1, 14, 41, 20, 922749)
    second = datetime.datetime(2017, 1, 21, 14, 41, 18, 457651)
    assert sec_diff(first_time=first, second_time=second) == Decimal('-950402.465098')


def test_sec_diff_second():
    """(2017, 1, 21, 14, 41, 18, 457651) - (2017, 2, 1, 14, 41, 20, 922749) is Decimal('950402.465098')"""

    first = datetime.datetime(2017, 1, 21, 14, 41, 18, 457651)
    second = datetime.datetime(2017, 2, 1, 14, 41, 20, 922749)
    assert sec_diff(first_time=first, second_time=second) == Decimal('950402.465098')
