"""The Python port must answer exactly like the JavaScript running in n8n.

Pulls the code nodes and expressions straight out of n8n/*.json, runs them
under Node.js with a frozen clock (tests/js_harness.cjs), and compares the
output with phone_agent. Skipped when `node` is not installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from conftest import NOW, ROOT, SECRET, fixture_json, tool_call
from phone_agent import calendar_logic as cal
from phone_agent import phone
from phone_agent import report_logic as rep
from phone_agent.templates import load_workflows, render
from phone_agent.timeutil import BERLIN

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

CAL_ID = "owner@example.com"
SO_IDS = fixture_json("so_ids.json")
VALUES = {"VAPI_WEBHOOK_SECRET": SECRET, "GOOGLE_CALENDAR_ID": CAL_ID,
          **{f"SO_{name.upper()}_ID": ident for name, ident in SO_IDS.items()}}
WF = {name: render(wf, VALUES)[0] for name, wf in load_workflows().items()}


def node(workflow: str, name: str) -> dict:
    return next(n for n in WF[workflow]["nodes"] if n["name"] == name)


def code(workflow: str, name: str) -> str:
    return node(workflow, name)["parameters"]["jsCode"]


def expression(workflow: str, name: str, param: str) -> str:
    text = node(workflow, name)["parameters"][param]
    assert text.startswith("={{") and text.endswith("}}")
    return text[3:-2]


def run_js(jobs: list[dict]) -> list:
    proc = subprocess.run(["node", str(Path(__file__).with_name("js_harness.cjs"))], input=json.dumps(jobs),
                          capture_output=True, text=True, timeout=60, check=True)
    results = json.loads(proc.stdout)
    for job, res in zip(jobs, results):
        assert res["ok"], (job.get("label"), res.get("error"))
    return [r["out"] for r in results]


def iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------- calendar webhook

PARSE_CASES = [
    ("check, offset", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00"})),
    ("check, no seconds", tool_call(args={"startzeit": "2026-10-06T14:00+02:00"})),
    ("check, Z", tool_call(args={"startzeit": "2026-10-06T12:00:00Z"})),
    ("check, +0200", tool_call(args={"startzeit": "2026-10-06T14:00:00+0200"})),
    ("check, no offset", tool_call(args={"startzeit": "2026-10-06T14:00:00"})),
    ("check, no offset no seconds", tool_call(args={"startzeit": "2026-10-06T15:30"})),
    ("check, 45 min", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": 45})),
    ("duration as text", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": "45 Minuten"})),
    ("duration 0", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": 0})),
    ("duration 5", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": 5})),
    ("duration 90", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": 90})),
    ("duration float", tool_call(args={"startzeit": "2026-10-06T14:00:00+02:00", "dauer_minuten": 44.9})),
    ("book, all fields", tool_call("book_appointment", {
        "startzeit": "2026-10-06T14:00:00+02:00", "name": "Jane Example", "firma": "Acme Example GmbH",
        "telefon": "+49 30 01234567", "email": "jane@acme-example.com", "thema": "x" * 300})),
    ("book, numeric phone", tool_call("book_appointment", {"startzeit": "2026-10-06T14:00:00+02:00",
                                                           "telefon": 4930012345})),
    ("arguments as string", tool_call(args=json.dumps({"startzeit": "2026-10-06T14:00:00+02:00"}))),
    ("arguments broken", tool_call(args="{oops")),
    ("too soon", tool_call(args={"startzeit": "2026-10-05T10:59:00+02:00"})),
    ("exactly 2h", tool_call(args={"startzeit": "2026-10-05T11:00:00+02:00"})),
    ("past", tool_call(args={"startzeit": "2026-10-01T11:00:00+02:00"})),
    ("too far", tool_call(args={"startzeit": "2026-11-04T10:00:00+01:00"})),
    ("saturday", tool_call(args={"startzeit": "2026-10-10T11:00:00+02:00"})),
    ("sunday", tool_call(args={"startzeit": "2026-10-11T11:00:00+02:00"})),
    ("before 10", tool_call(args={"startzeit": "2026-10-06T09:45:00+02:00"})),
    ("ends 16:45", tool_call(args={"startzeit": "2026-10-06T16:15:00+02:00"})),
    ("ends 16:46", tool_call(args={"startzeit": "2026-10-06T16:16:00+02:00"})),
    ("garbled", tool_call(args={"startzeit": "naechsten Dienstag"})),
    ("date only", tool_call(args={"startzeit": "2026-10-06"})),
    ("missing time", tool_call(args={})),
    ("unknown function", tool_call("cancel_appointment", {"startzeit": "2026-10-06T14:00:00+02:00"})),
    ("wrong type", tool_call(msg_type="status-update")),
    ("no tool call id", tool_call(tc_id="")),
    ("empty body", {}),
]


@pytest.mark.parametrize("secret", [SECRET, "wrong"])
def test_parse_node_matches(secret):
    headers = {"x-vapi-secret": secret}
    jobs = [{"kind": "code", "label": label, "code": code("calendar-check-and-book.json", "Parse + Pruefen"),
             "items": [{"headers": headers, "params": {}, "query": {}, "body": body}], "refs": {}, "now": iso(NOW)}
            for label, body in PARSE_CASES]
    for (label, body), js_out in zip(PARSE_CASES, run_js(jobs)):
        assert js_out == [cal.parse_tool_call(headers, body, SECRET, NOW)], label


def test_parse_node_matches_in_winter_time():
    winter = datetime(2026, 11, 23, 9, 0, tzinfo=BERLIN)
    cases = ["2026-12-01T14:00:00", "2026-12-01T14:00:00+01:00", "2026-12-01T13:00:00Z"]
    jobs = [{"kind": "code", "code": code("calendar-check-and-book.json", "Parse + Pruefen"),
             "items": [{"headers": {"x-vapi-secret": SECRET}, "body": tool_call(args={"startzeit": c})}],
             "refs": {}, "now": iso(winter)} for c in cases]
    for c, js_out in zip(cases, run_js(jobs)):
        assert js_out == [cal.parse_tool_call({"x-vapi-secret": SECRET}, tool_call(args={"startzeit": c}),
                                              SECRET, winter)], c


def test_known_difference_space_separator_in_winter():
    """'2026-12-01 14:00' (space, no offset): the n8n node falls back to +02:00 and books
    13:00 Berlin; the port reads it as 14:00 Berlin. Pinned here so the difference stays visible."""
    winter = datetime(2026, 11, 23, 9, 0, tzinfo=BERLIN)
    body = tool_call(args={"startzeit": "2026-12-01 14:00"})
    [js_out] = run_js([{"kind": "code", "code": code("calendar-check-and-book.json", "Parse + Pruefen"),
                        "items": [{"headers": {"x-vapi-secret": SECRET}, "body": body}], "refs": {},
                        "now": iso(winter)}])
    assert js_out[0]["startISO"] == "2026-12-01T12:00:00.000Z"
    assert cal.parse_tool_call({"x-vapi-secret": SECRET}, body, SECRET, winter)["startISO"] == "2026-12-01T13:00:00.000Z"


def _contexts():
    out = []
    for name, args in [("check_availability", {}), ("book_appointment", {"name": "Jane Example", "telefon": "1",
                                                                          "email": "jane@acme-example.com",
                                                                          "firma": "Acme Example GmbH"}),
                       ("book_appointment", {"telefon": "1"})]:
        out.append(cal.parse_tool_call({"x-vapi-secret": SECRET},
                                       tool_call(name, {"startzeit": "2026-10-06T14:00:00+02:00", **args}),
                                       SECRET, NOW))
    return out


FREEBUSY = [
    {"calendars": {CAL_ID: {"busy": []}}},
    {"calendars": {CAL_ID: {"busy": [{"start": "2026-10-06T12:10:00Z", "end": "2026-10-06T12:40:00Z"}]}}},
    {"calendars": {}},
    {},
]


def test_evaluate_node_matches():
    pairs = [(ctx, fb) for ctx in _contexts() for fb in FREEBUSY]
    jobs = [{"kind": "code", "code": code("calendar-check-and-book.json", "Bewerten"), "items": [fb],
             "refs": {"Parse + Pruefen": ctx}, "now": iso(NOW)} for ctx, fb in pairs]
    for (ctx, fb), js_out in zip(pairs, run_js(jobs)):
        assert js_out == [cal.evaluate_slot(ctx, fb, CAL_ID)]


def test_booking_decision_event_body_confirmation_and_response_match():
    evaluated = [cal.evaluate_slot(ctx, FREEBUSY[0], CAL_ID) for ctx in _contexts()]
    cond = node("calendar-check-and-book.json", "Buchen?")["parameters"]["conditions"]["conditions"][0]
    jobs = [{"kind": "expr", "expr": cond["leftValue"][3:-2], "json": ev, "now": iso(NOW)} for ev in evaluated]
    jobs += [{"kind": "expr", "expr": expression("calendar-check-and-book.json", "Termin eintragen", "jsonBody"),
              "json": ev, "now": iso(NOW)} for ev in evaluated]
    jobs += [{"kind": "code", "code": code("calendar-check-and-book.json", "Bestaetigung"), "items": [event],
              "refs": {"Bewerten": evaluated[1]}, "now": iso(NOW)} for event in ({"id": "evt42"}, {})]
    jobs += [{"kind": "expr", "expr": expression("calendar-check-and-book.json", "Antwort", "responseBody"),
              "json": {"toolCallId": "tc-1", "result": "FREI: ok"}, "now": iso(NOW)}]
    out = run_js(jobs)
    n = len(evaluated)
    assert out[:n] == [cal.should_book(ev) for ev in evaluated]
    assert [json.loads(x) for x in out[n:2 * n]] == [cal.build_event_body(ev) for ev in evaluated]
    assert out[2 * n] == [cal.confirmation(evaluated[1], {"id": "evt42"})]
    assert out[2 * n + 1] == [cal.confirmation(evaluated[1], {})]
    assert json.loads(out[-1]) == cal.tool_response({"toolCallId": "tc-1", "result": "FREI: ok"})


# ---------------------------------------------------------------- phone normalization

PHONES = ["030 0123 4567", "030/0123-4567", "0049 30 01234567", "+49 (0)30 0123 4567", "+44 7700 900123",
          "+1 415 555 0100", "0044 20 7946 0000", "12345", "030 12", "+49 123", "", "  +49 30 0123 4567  ",
          "0000000000000000000", 3001234567]


@pytest.mark.parametrize("workflow, field, port", [
    ("outbound-qualifier-de.json", "Telefonnummer", phone.normalize_de),
    ("outbound-qualifier-en.json", "Phone", phone.normalize_international),
])
def test_phone_nodes_match(workflow, field, port):
    jobs = [{"kind": "code", "code": code(workflow, "Normalize Phone"), "items": [{field: p}], "refs": {},
             "now": iso(NOW)} for p in PHONES]
    for p, js_out in zip(PHONES, run_js(jobs)):
        assert js_out[0]["Phone Number"] == port(p), p


# ---------------------------------------------------------------- front desk report

def _reports():
    base = fixture_json("front_desk_report.json")
    no_start = json.loads(json.dumps(base))
    del no_start["message"]["startedAt"]
    sparse = {"message": {"type": "end-of-call-report", "call": {}, "artifact": {"structuredOutputs": {
        SO_IDS["wants_erwin_callback"]: {"result": False}, SO_IDS["caller_company"]: "none"}}}}
    return [base, no_start, sparse, {"message": {"type": "end-of-call-report"}}]


def test_extract_node_matches():
    reports = _reports()
    jobs = [{"kind": "code", "code": code("front-desk-report.json", "Extract Call Facts"),
             "items": [{"headers": {}, "body": r}], "refs": {}, "now": iso(NOW)} for r in reports]
    assert [out[0] for out in run_js(jobs)] == [rep.extract_call_facts(r, SO_IDS) for r in reports]
