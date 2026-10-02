"""Lossless interactive measurement input (#268), not spreadsheet import policy."""

import math
import re
from decimal import Decimal

from PyQt6.QtGui import QValidator


_NUMBER = re.compile(r"[+]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)")
_PARTIAL = re.compile(r"[+]?(?:[0-9]*(?:[.,][0-9]*)?)")


def parse_measurement(text, *, default=None):
    """Accept plain non-negative decimals; never guess grouping or drop bad input."""
    value = str(text).strip()
    if not value and default is not None:
        return float(default)
    if not _NUMBER.fullmatch(value):
        raise ValueError(value)
    number = float(value.replace(',', '.'))
    if not math.isfinite(number) or number < 0:
        raise ValueError(value)
    return number


def format_measurement(value):
    """Round-trip the stored number without integer conversion or display rounding."""
    if value is None or value == '':
        return ''
    return format(Decimal(str(value)), 'f')


class MeasurementValidator(QValidator):
    """Same plain-decimal grammar during typing and saving, independent of OS locale."""

    def __init__(self, maximum=None, parent=None):
        super().__init__(parent)
        self.maximum = maximum

    def validate(self, text, position):
        try:
            number = parse_measurement(text)
        except ValueError:
            state = (self.State.Intermediate if _PARTIAL.fullmatch(text)
                     else self.State.Invalid)
        else:
            state = (self.State.Acceptable if self.maximum is None or number <= self.maximum
                     else self.State.Invalid)
        return state, text, position
