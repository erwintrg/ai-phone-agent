"""E.164 phone normalization, ported from the "Normalize Phone" code nodes.

German form (`outbound-qualifier-de.json`): German default, so '030 ...' becomes '+4930...'.
English form (`outbound-qualifier-en.json`): international, number must start with + or 00.
Invalid input returns the literal 'incorrect format', which is what the
workflow's IF node routes to the "Log Invalid" branch.
"""
from __future__ import annotations

import re
from typing import Any

from .jsish import js_string

INVALID = "incorrect format"


def _raw(value: Any) -> str:
    return ("" if value is None else js_string(value)).strip()


def normalize_de(value: Any) -> str:
    raw = _raw(value)
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else INVALID
    if digits.startswith("00"):
        rest = digits[2:]
        return "+" + rest if 8 <= len(rest) <= 15 else INVALID
    if digits.startswith("0"):
        rest = digits[1:]
        return "+49" + rest if 7 <= len(rest) <= 13 else INVALID
    return INVALID


def normalize_international(value: Any) -> str:
    raw = _raw(value)
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if digits.startswith("00") and 8 <= len(digits) - 2 <= 15:
        return "+" + digits[2:]
    return INVALID
