import datetime
import pytest

from decimal import Decimal

from jtune.jtune import Display


def test_record_add():
    """Ensure the length of our display object is 2"""

    display = Display()
    display.add('first')
    display.add('second')

    assert len(display.display_output) == 2
