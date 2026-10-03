"""Slot evaluation and the booking decision ('Bewerten', 'Buchen?', 'Termin eintragen', 'Bestaetigung')."""
from __future__ import annotations

from datetime import datetime

import pytest

from conftest import HEADERS, NOW, SECRET, tool_call
from phone_agent import calendar_logic as cal
from phone_agent.fake_calendar import FakeCalendar

CAL_ID = "you@example.com"


def ctx_for(fn="check_availability", start="2026-10-06T10:00:00+02:00", **args):
    return cal.parse_tool_call(HEADERS, tool_call(fn, {"startzeit": start, **args}), SECRET, NOW)


def busy_calendar(*blocks):
    return FakeCalendar(CAL_ID, busy=[(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in blocks])


def evaluate(ctx, calendar):
    return cal.evaluate_slot(ctx, calendar.free_busy(cal.freebusy_request(ctx, CAL_ID)), CAL_ID)


def test_free_slot_is_reported_free():
    ev = evaluate(ctx_for(), busy_calendar())
    assert ev["available"] is True
    assert ev["result"].startswith("FREI: Dienstag, 6. Oktober um 10:00 Uhr ist verfuegbar")
    assert cal.should_book(ev) is False          # a check never books


def test_busy_slot_is_reported_without_any_detail():
    calendar = busy_calendar(("2026-10-06T10:00:00+02:00", "2026-10-06T11:00:00+02:00"))
    ev = evaluate(ctx_for(), calendar)
    assert ev["available"] is False
    assert ev["result"].startswith("NICHT FREI")
    assert "11:00" not in ev["result"] and "Keine Details nennen" in ev["result"]


@pytest.mark.parametrize("block, available", [
    (("2026-10-06T10:40:00+02:00", "2026-10-06T11:30:00+02:00"), False),  # starts 10 min after the slot
    (("2026-10-06T09:00:00+02:00", "2026-10-06T09:50:00+02:00"), False),  # ends 10 min before the slot
    (("2026-10-06T10:45:00+02:00", "2026-10-06T11:30:00+02:00"), True),   # exactly 15 min after
    (("2026-10-06T09:00:00+02:00", "2026-10-06T09:45:00+02:00"), True),   # exactly 15 min before
])
def test_fifteen_minute_buffer_on_both_sides(block, available):
    assert evaluate(ctx_for(), busy_calendar(block))["available"] is available


def test_booking_a_free_slot_goes_ahead():
    ev = evaluate(ctx_for("book_appointment", name="Jane Example", telefon="+49 30 01234567"), busy_calendar())
    assert ev["result"] == ""
    assert cal.should_book(ev) is True


def test_booking_a_slot_that_got_taken_meanwhile_is_refused():
    calendar = busy_calendar(("2026-10-06T10:00:00+02:00", "2026-10-06T10:30:00+02:00"))
    ev = evaluate(ctx_for("book_appointment", name="Jane Example", telefon="+49 30 01234567"), calendar)
    assert cal.should_book(ev) is False
    assert ev["result"].startswith("NICHT FREI") and "inzwischen verplant" in ev["result"]


def test_event_body_carries_the_lead_data():
    ctx = ctx_for("book_appointment", name="Jane Example", firma="Acme Example GmbH", telefon="+49 30 01234567",
                  email="jane@acme-example.com", thema="Rueckrufe")
    body = cal.build_event_body(ctx)
    assert body["summary"] == "YCAT Demo-Termin: Jane Example (Acme Example GmbH)"
    assert body["description"].splitlines() == [
        "Gebucht von Elias (Vapi Demo-Assistent).", "Name: Jane Example", "Firma: Acme Example GmbH",
        "Telefon: +49 30 01234567", "E-Mail: jane@acme-example.com", "Thema: Rueckrufe"]
    assert body["start"] == {"dateTime": "2026-10-06T08:00:00.000Z", "timeZone": "Europe/Berlin"}
    assert body["attendees"] == [{"email": "jane@acme-example.com"}]


def test_event_body_without_optional_fields():
    body = cal.build_event_body(ctx_for("book_appointment", telefon="+49 30 01234567"))
    assert body["summary"] == "YCAT Demo-Termin: Interessent"
    assert body["attendees"] == []
    assert "Firma: -" in body["description"]


def test_confirmation_and_response_shape():
    ev = evaluate(ctx_for("book_appointment", name="Jane Example", telefon="1"), busy_calendar())
    done = cal.confirmation(ev, {"id": "evt42"})
    assert done["result"] == ("GEBUCHT: Dienstag, 6. Oktober um 10:00 Uhr ist fest eingetragen "
                              "(Termin-ID evt42). Bitte verbindlich zusammenfassen.")
    assert cal.confirmation(ev, {})["result"].count("Termin-ID ok") == 1
    assert cal.tool_response(done) == {"results": [{"toolCallId": "tc-1", "result": done["result"]}]}


def test_fake_calendar_makes_a_booked_slot_busy():
    calendar = busy_calendar()
    ctx = ctx_for("book_appointment", name="Jane Example", telefon="1")
    ev = evaluate(ctx, calendar)
    calendar.insert_event(cal.build_event_body(ev))
    again = evaluate(ctx, calendar)
    assert again["available"] is False and cal.should_book(again) is False


def test_fake_freebusy_clips_to_the_window_and_never_returns_titles():
    calendar = busy_calendar(("2026-10-06T08:00:00+02:00", "2026-10-06T18:00:00+02:00"))
    fb = calendar.free_busy({"timeMin": "2026-10-06T08:45:00.000Z", "timeMax": "2026-10-06T09:15:00.000Z",
                             "items": [{"id": CAL_ID}]})
    assert fb["calendars"][CAL_ID] == {"busy": [{"start": "2026-10-06T08:45:00.000Z",
                                                 "end": "2026-10-06T09:15:00.000Z"}]}


@pytest.mark.parametrize("freebusy, available", [
    ({"calendars": {CAL_ID: {"busy": []}}}, True),
    ({"calendars": {CAL_ID: {"busy": {}}}}, False),   # JS: ({}).length === 0 is false
    ({"calendars": []}, True),
    ({}, True),                                       # no entry counts as free, see README limitations
])
def test_evaluate_follows_javascript_truthiness(freebusy, available):
    assert cal.evaluate_slot(ctx_for(), freebusy, CAL_ID)["available"] is available


def test_server_refuses_an_empty_secret():
    from phone_agent.server import WebhookApp
    with pytest.raises(ValueError):
        WebhookApp(secret="", calendar=busy_calendar(), clock=lambda: NOW)
