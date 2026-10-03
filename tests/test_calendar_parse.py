"""Parse and validate: the 'Parse + Pruefen' step of the calendar webhook."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from conftest import HEADERS, NOW, SECRET, tool_call
from phone_agent import calendar_logic as cal
from phone_agent.timeutil import BERLIN


def parse(body, headers=HEADERS, now=NOW):
    return cal.parse_tool_call(headers, body, SECRET, now)


def test_check_call_returns_slot_and_buffered_window():
    ctx = parse(tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00"}))
    assert ctx["action"] == "check"
    assert ctx["toolCallId"] == "tc-1"
    assert ctx["startISO"] == "2026-10-06T12:00:00.000Z"
    assert ctx["endISO"] == "2026-10-06T12:30:00.000Z"          # default 30 minutes
    assert ctx["bufStartISO"] == "2026-10-06T11:45:00.000Z"     # 15 min before
    assert ctx["bufEndISO"] == "2026-10-06T12:45:00.000Z"       # 15 min after
    assert ctx["spoken"] == "Dienstag, 6. Oktober um 14:00 Uhr"


def test_book_call_keeps_caller_fields_and_truncates_to_200_chars():
    ctx = parse(tool_call("book_appointment", {
        "startzeit": "2026-10-06T14:00:00+02:00", "name": "Jane Example", "firma": "Acme Example GmbH",
        "telefon": "+49 30 01234567", "email": "jane@acme-example.com", "thema": "x" * 500}))
    assert ctx["action"] == "book"
    assert (ctx["name"], ctx["firma"], ctx["email"]) == ("Jane Example", "Acme Example GmbH", "jane@acme-example.com")
    assert len(ctx["thema"]) == 200


def test_arguments_may_arrive_as_json_string():
    body = tool_call(args=json.dumps({"startzeit": "2026-10-06T14:00:00+02:00"}))
    assert parse(body)["action"] == "check"


def test_unparseable_argument_string_counts_as_missing_time():
    assert parse(tool_call(args="{not json"))["result"] == cal.MSG_NO_TIME


@pytest.mark.parametrize("headers", [{}, {"x-vapi-secret": "wrong"}, {"x-vapi-secret": ""}])
def test_bad_or_missing_secret_is_rejected(headers):
    ctx = parse(tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00"}), headers=headers)
    assert ctx == {"action": "error", "toolCallId": "unauthorized", "result": cal.MSG_UNAUTHORIZED}


def test_secret_header_name_is_case_insensitive():
    ctx = parse(tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00"}), headers={"X-Vapi-Secret": SECRET})
    assert ctx["action"] == "check"


@pytest.mark.parametrize("body", [
    tool_call(msg_type="status-update", args={"startzeit": "2026-10-06T14:00:00+02:00"}),
    tool_call(tc_id="", args={"startzeit": "2026-10-06T14:00:00+02:00"}),
    {"message": {"type": "tool-calls", "toolCallList": []}},
    {"message": "tool-calls"},
    {},
    None,
])
def test_unexpected_events_are_refused(body):
    assert parse(body)["result"] == cal.MSG_UNEXPECTED


def test_unknown_function_is_refused():
    assert parse(tool_call("cancel_appointment", {"startzeit": "x"}))["result"] == cal.MSG_UNKNOWN_FN


@pytest.mark.parametrize("value, message", [
    (None, cal.MSG_NO_TIME),
    ("", cal.MSG_NO_TIME),
    ("   ", cal.MSG_NO_TIME),
    ("naechsten Dienstag", cal.MSG_BAD_TIME),
    ("2026-10-06", cal.MSG_BAD_TIME),               # a date alone is not a slot
    ("2026-10-06T14", cal.MSG_BAD_TIME),
    ("2026-13-06T14:00:00+02:00", cal.MSG_BAD_TIME),
])
def test_missing_or_garbled_time(value, message):
    assert parse(tool_call(args={"startzeit": value}))["result"] == message


@pytest.mark.parametrize("value", [
    "2026-10-06T14:00:00+02:00", "2026-10-06T14:00+02:00", "2026-10-06T14:00:00+0200",
    "2026-10-06T12:00:00Z", "2026-10-06T12:00:00.000Z", "2026-10-06T14:00:00",  # last: no offset
])
def test_equivalent_spellings_of_the_same_instant(value):
    assert parse(tool_call(args={"startzeit": value}))["startISO"] == "2026-10-06T12:00:00.000Z"


def test_time_without_offset_uses_berlin_winter_time_too():
    winter_now = datetime(2026, 11, 23, 9, 0, tzinfo=BERLIN)
    ctx = parse(tool_call(args={"startzeit": "2026-12-01T14:00:00"}), now=winter_now)
    assert ctx["startISO"] == "2026-12-01T13:00:00.000Z"        # CET is UTC+1
    assert ctx["spoken"] == "Dienstag, 1. Dezember um 14:00 Uhr"
