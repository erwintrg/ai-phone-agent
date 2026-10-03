"""Tiny helpers that reproduce JavaScript semantics the n8n code nodes rely on.

The Python port must give the same answers as the JS in n8n, including the
odd corners (parseInt('45 Minuten') is 45, String(true) is 'true', {} is truthy).
"""
from __future__ import annotations

import re
from typing import Any


def js_string(v: Any) -> str:
    """String(v) for the value types that show up in webhook payloads."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, dict):
        return "[object Object]"
    if isinstance(v, list):
        return ",".join("" if x is None else js_string(x) for x in v)
    return str(v)


def js_parse_int(v: Any) -> int | None:
    """parseInt(v): leading integer of String(v), None where JS gives NaN."""
    if v is None or isinstance(v, bool):
        return None
    m = re.match(r"\s*([+-]?\d+)", js_string(v))
    return int(m.group(1)) if m else None


def js_truthy(v: Any) -> bool:
    return not (v is None or v is False or v == "" or (isinstance(v, (int, float)) and v == 0))
