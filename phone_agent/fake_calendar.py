"""In-memory stand-in for the two Google Calendar endpoints the workflow calls.

POST /calendar/v3/freeBusy                       -> free_busy()
POST /calendar/v3/calendars/{id}/events          -> insert_event()

Like the real freeBusy API it only ever returns busy intervals (clipped to the
query window), never titles or attendees.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .timeutil import js_iso, parse_iso_loose, resolve_token


@dataclass
class FakeCalendar:
    calendar_id: str = "you@example.com"
    busy: list[tuple[datetime, datetime]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_fixture(cls, path: str | Path, now: datetime, calendar_id: str = "you@example.com") -> "FakeCalendar":
        """Load busy blocks; times may be ISO strings or relative tokens like '@tue 10:40'."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        busy = []
        for block in data.get("busy", []):
            start = parse_iso_loose(resolve_token(block["start"], now))
            end = parse_iso_loose(resolve_token(block["end"], now))
            if start is None or end is None or end <= start:
                raise ValueError(f"bad busy block in {path}: {block}")
            busy.append((start, end))
        return cls(calendar_id=calendar_id, busy=busy)

    def free_busy(self, request: dict[str, Any]) -> dict[str, Any]:
        t_min = parse_iso_loose(request["timeMin"])
        t_max = parse_iso_loose(request["timeMax"])
        out: dict[str, Any] = {"kind": "calendar#freeBusy", "timeMin": request["timeMin"],
                               "timeMax": request["timeMax"], "calendars": {}}
        for item in request.get("items", []):
            cal_id = item.get("id")
            if cal_id != self.calendar_id:
                out["calendars"][cal_id] = {"errors": [{"domain": "global", "reason": "notFound"}], "busy": []}
                continue
            hits = [(max(s, t_min), min(e, t_max)) for s, e in sorted(self.busy) if s < t_max and e > t_min]
            out["calendars"][cal_id] = {"busy": [{"start": js_iso(s), "end": js_iso(e)} for s, e in hits]}
        return out

    def insert_event(self, body: dict[str, Any], send_updates: str = "all") -> dict[str, Any]:
        start = parse_iso_loose(body["start"]["dateTime"])
        end = parse_iso_loose(body["end"]["dateTime"])
        event = {"id": f"evt{len(self.events) + 1:04d}", "status": "confirmed",
                 "sendUpdates": send_updates, **body}
        self.events.append(event)
        self.busy.append((start, end))
        return event
