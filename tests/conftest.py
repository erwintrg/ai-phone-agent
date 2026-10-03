from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from phone_agent.timeutil import BERLIN  # noqa: E402

SECRET = "test-secret"
HEADERS = {"x-vapi-secret": SECRET}
# Monday, 5 Oct 2026, 09:00 Berlin (CEST). Tuesday the 6th is the default test day.
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=BERLIN)


def tool_call(name: str = "check_availability", args: Any = None, *, tc_id: str = "tc-1",
              msg_type: str = "tool-calls") -> dict[str, Any]:
    """A Vapi tool-calls server message as the webhook receives it."""
    return {"message": {"type": msg_type, "toolCallList": [
        {"id": tc_id, "type": "function", "function": {"name": name, "arguments": args or {}}}]}}


def fixture_json(name: str) -> Any:
    data = json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("_comment", None)
    return data


@pytest.fixture
def now() -> datetime:
    return NOW
