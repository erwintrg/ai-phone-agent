"""Time helpers shared by the webhook port, the simulator and the tests.

All business rules live in Europe/Berlin wall-clock time, the same way the
n8n code nodes use `Intl.DateTimeFormat(..., { timeZone: 'Europe/Berlin' })`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
             "September", "Oktober", "November", "Dezember"]

# ISO 8601 date + time, optional seconds/fraction, optional offset (Z, +02:00, +0200).
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_start(raw: str) -> datetime | None:
    """Parse the `startzeit` argument the model sends.

    With an offset the instant is taken as given. Without one it is read as
    Berlin wall-clock time, which is what the n8n node approximates by trying
    +02:00 and +01:00 and keeping the one that round-trips.
    Returns an aware datetime, or None when the string is not understandable.
    """
    m = _ISO_RE.match(raw.strip())
    if not m:
        return None
    year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
    second = int(m.group(6) or 0)
    micro = int((m.group(7) or "0").ljust(6, "0"))
    off = m.group(8)
    if off is None:
        tz = BERLIN
    elif off == "Z":
        tz = timezone.utc
    else:
        sign = 1 if off[0] == "+" else -1
        digits = off[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=tz)
    except ValueError:
        return None


def parse_iso_loose(raw: str) -> datetime | None:
    """Lenient ISO parser for calendar and fixture values (date-only allowed).

    Values without an offset are treated as UTC, the default time zone of an
    n8n container.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T00:00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def js_iso(dt: datetime) -> str:
    """Same string as JavaScript's Date.prototype.toISOString()."""
    u = dt.astimezone(timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def spoken_de(dt: datetime) -> str:
    """'Dienstag, 6. Oktober um 14:00 Uhr', like Intl de-DE long format + ' Uhr'."""
    b = dt.astimezone(BERLIN)
    return f"{WEEKDAYS_DE[b.weekday()]}, {b.day}. {MONTHS_DE[b.month - 1]} um {b:%H:%M} Uhr"


def berlin_stamp(dt: datetime) -> str:
    """'yyyy-MM-dd HH:mm' in Berlin time, the format n8n's $now.format(...) writes."""
    return dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")


_DAY_TOKENS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_TOKEN_RE = re.compile(r"^@(today|mon|tue|wed|thu|fri|sat|sun|\+\d+d)\s+(\d{1,2}):(\d{2})(\s+local)?$")


def resolve_token(token: str, now: datetime) -> str:
    """Turn a relative time token from a call script into an ISO string.

    '@tue 14:00'     next Tuesday after today, 14:00 Berlin, with offset
    '@today 10:30'   today 10:30 Berlin
    '@+42d 11:00'    42 days from today
    '@tue 15:00 local'  same as '@tue 15:00' but without the offset
    Strings that do not start with '@' are returned unchanged.
    """
    if not token.startswith("@"):
        return token
    m = _TOKEN_RE.match(token.strip())
    if not m:
        raise ValueError(f"bad time token: {token!r}")
    day, hh, mm, local = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    today = now.astimezone(BERLIN).date()
    if day == "today":
        target = today
    elif day.startswith("+"):
        target = today + timedelta(days=int(day[1:-1]))
    else:
        delta = (_DAY_TOKENS[day] - today.weekday()) % 7 or 7
        target = today + timedelta(days=delta)
    dt = datetime(target.year, target.month, target.day, hh, mm, tzinfo=BERLIN)
    if local:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.isoformat(timespec="seconds")
