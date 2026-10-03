"""E.164 normalization of the form's phone field ('Normalize Phone' nodes)."""
from __future__ import annotations

import pytest

from phone_agent.phone import INVALID, normalize_de, normalize_international


@pytest.mark.parametrize("raw, expected", [
    ("030 0123 4567", "+493001234567"),        # national format, German default
    ("030/0123-4567", "+493001234567"),
    ("0049 30 01234567", "+493001234567"),     # 00 international prefix
    ("+49 (0)30 0123 4567", "+4903001234567"),  # '(0)' is kept, as in the workflow
    ("+44 7700 900123", "+447700900123"),
    ("030 0000 0000", "+493000000000"),
    ("12345", INVALID),                        # no prefix at all
    ("030 12", INVALID),                       # too short after the 0
    ("+49 123", INVALID),                      # fewer than 8 digits
    ("", INVALID),
    (None, INVALID),
])
def test_german_form(raw, expected):
    assert normalize_de(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("+1 415 555 0100", "+14155550100"),
    ("0044 20 7946 0000", "+442079460000"),
    ("030 0123 4567", INVALID),                # no country code, no default country
    ("+1 23", INVALID),
    ("", INVALID),
])
def test_international_form(raw, expected):
    assert normalize_international(raw) == expected
