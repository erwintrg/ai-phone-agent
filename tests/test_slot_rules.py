"""Booking rules enforced server side: lead time, horizon, weekdays, hours, duration."""
from __future__ import annotations

import pytest

from conftest import HEADERS, NOW, SECRET, tool_call
from phone_agent import calendar_logic as cal


def check(startzeit, **extra):
    return cal.parse_tool_call(HEADERS, tool_call(args={"startzeit": startzeit, **extra}), SECRET, NOW)


@pytest.mark.parametrize("startzeit, ok", [
    ("2026-10-05T10:59:00+02:00", False),   # 1 h 59 min ahead
    ("2026-10-05T11:00:00+02:00", True),    # exactly 2 h ahead
    ("2026-10-05T08:00:00+02:00", False),   # in the past
])
def test_minimum_lead_time_of_two_hours(startzeit, ok):
    assert (check(startzeit)["action"] == "check") is ok
    if not ok:
        assert check(startzeit)["result"] == cal.MSG_TOO_SOON


def test_horizon_is_thirty_days():
    # NOW is 07:00 UTC; 30 days later it is 08:00 Berlin because summer time ended in between.
    assert check("2026-11-03T10:00:00+01:00")["action"] == "check"
    assert check("2026-11-04T10:00:00+01:00")["result"] == cal.MSG_TOO_FAR


@pytest.mark.parametrize("startzeit", ["2026-10-10T11:00:00+02:00", "2026-10-11T11:00:00+02:00"])
def test_weekends_are_closed(startzeit):
    assert check(startzeit)["result"] == cal.MSG_WEEKEND


@pytest.mark.parametrize("startzeit, minutes, ok", [
    ("2026-10-06T09:45:00+02:00", 30, False),   # starts before 10:00
    ("2026-10-06T10:00:00+02:00", 30, True),
    ("2026-10-06T16:15:00+02:00", 30, True),    # ends exactly 16:45
    ("2026-10-06T16:16:00+02:00", 30, False),   # ends 16:46
    ("2026-10-06T16:30:00+02:00", 15, True),
    ("2026-10-06T16:00:00+02:00", 60, False),
])
def test_business_window_10_to_16_45(startzeit, minutes, ok):
    ctx = check(startzeit, dauer_minuten=minutes)
    assert (ctx["action"] == "check") is ok
    if not ok:
        assert ctx["result"] == cal.MSG_WINDOW


@pytest.mark.parametrize("value, minutes", [
    (None, 30), (0, 30), ("abc", 30), (True, 30),       # JS: parseInt(...) || 30
    (5, 15), (-10, 15),                                 # clamped up to 15
    (90, 60), (45, 45), ("45", 45), ("45 Minuten", 45), (45.7, 45),
])
def test_duration_parsing_and_clamping(value, minutes):
    ctx = check("2026-10-06T11:00:00+02:00", dauer_minuten=value)
    start_min = int(ctx["startISO"][11:13]) * 60 + int(ctx["startISO"][14:16])
    end_min = int(ctx["endISO"][11:13]) * 60 + int(ctx["endISO"][14:16])
    assert end_min - start_min == minutes


def test_rule_order_matches_the_workflow():
    # A Saturday 40 days out reports the horizon first, like the n8n node.
    assert check("2026-11-14T11:00:00+01:00")["result"] == cal.MSG_TOO_FAR
