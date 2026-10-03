"""The local webhook server over real HTTP, and the scripted calls against it."""
from __future__ import annotations

import json
import urllib.request

import pytest

from conftest import NOW, ROOT, SECRET, fixture_json, tool_call
from phone_agent.fake_calendar import FakeCalendar
from phone_agent.server import CALENDAR_PATH, REPORT_PATH, WebhookApp, start_in_thread
from phone_agent.simulator import load_script, post_json, run_script


@pytest.fixture
def served():
    calendar = FakeCalendar.from_fixture(ROOT / "fixtures" / "calendar_busy.json", NOW)
    app = WebhookApp(secret=SECRET, calendar=calendar, clock=lambda: NOW, so_ids=fixture_json("so_ids.json"))
    server, base = start_in_thread(app)
    yield app, base
    server.shutdown()
    server.server_close()


def test_tool_call_round_trip(served):
    app, base = served
    status, body = post_json(base + CALENDAR_PATH,
                             tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00"}, tc_id="call_abc"),
                             {"x-vapi-secret": SECRET})
    assert status == 200
    assert body["results"][0]["toolCallId"] == "call_abc"
    assert body["results"][0]["result"].startswith("FREI:")


def test_bad_secret_gets_401(served):
    _, base = served
    status, body = post_json(base + CALENDAR_PATH, tool_call(args={"startzeit": "x"}), {"x-vapi-secret": "nope"})
    assert status == 401
    assert body["results"][0]["result"] == "Interner Fehler: nicht autorisiert."


def test_invalid_json_unknown_path_and_health(served):
    _, base = served
    req = urllib.request.Request(base + CALENDAR_PATH, data=b"{oops", method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 400
    assert post_json(base + "/webhook/other", {}, {})[0] == 404
    with urllib.request.urlopen(base + "/health") as resp:
        assert json.loads(resp.read()) == {"ok": True}


def test_report_endpoint_logs_and_ignores(served):
    app, base = served
    assert post_json(base + REPORT_PATH, {"message": {"type": "status-update"}}, {})[1] == {"status": "ignored"}
    status, body = post_json(base + REPORT_PATH, fixture_json("front_desk_report.json"), {})
    assert status == 200 and body["status"] == "logged"
    assert app.inbound_rows[0]["Caller Name"] == "Max Mustermann"
    assert app.inbound_rows[0]["Category"] == "new_inquiry"
    assert app.outbox[0]["to"] == "you@example.com"


def test_booking_script_passes_and_books_one_event(served):
    app, base = served
    lines: list[str] = []
    result = run_script(load_script(ROOT / "fixtures" / "call_de_booking.json"), base + CALENDAR_PATH,
                        SECRET, NOW, out=lines.append)
    assert result.ok, result.failures
    assert [x.function for x in result.exchanges] == ["check_availability"] * 3 + ["book_appointment"]
    assert len(app.calendar.events) == 1
    event = app.calendar.events[0]
    assert event["start"]["dateTime"] == "2026-10-06T12:00:00.000Z"
    assert event["attendees"] == [{"email": "jane@acme-example.com"}]
    assert any("Dienstag, 6. Oktober um 14:00 Uhr" in line for line in lines)


def test_edge_case_script_after_booking(served):
    app, base = served
    run_script(load_script(ROOT / "fixtures" / "call_de_booking.json"), base + CALENDAR_PATH, SECRET, NOW,
               out=lambda _: None)
    result = run_script(load_script(ROOT / "fixtures" / "edge_cases.json"), base + CALENDAR_PATH, SECRET, NOW,
                        out=lambda _: None, compact=True)
    assert result.ok, result.failures
    assert len(app.calendar.events) == 1          # the double booking was refused


def test_no_book_mode_never_writes(served):
    app, base = served
    result = run_script(load_script(ROOT / "fixtures" / "call_de_booking.json"), base + CALENDAR_PATH,
                        SECRET, NOW, out=lambda _: None, allow_booking=False)
    assert result.ok and len(result.exchanges) == 3
    assert app.calendar.events == []


def test_demo_runs_clean(capsys):
    from phone_agent.demo import main
    assert main() == 0
    out = capsys.readouterr().out
    assert "14/14 tool calls answered as expected." in out
    assert "GEBUCHT" in out and "Category          new_inquiry" in out
