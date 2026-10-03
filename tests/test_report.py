"""Post-call logic: front desk report and outbound qualifier logging."""
from __future__ import annotations

import copy

import pytest

from conftest import NOW, fixture_json
from phone_agent import report_logic as rep

SO_IDS = fixture_json("so_ids.json")
REPORT = fixture_json("front_desk_report.json")


def facts(report=REPORT):
    return rep.extract_call_facts(report, SO_IDS)


def test_only_end_of_call_reports_are_processed():
    assert rep.is_end_of_call_report(REPORT)
    assert rep.is_end_of_call_report({"body": REPORT})        # n8n wraps the body
    assert not rep.is_end_of_call_report({"message": {"type": "status-update"}})
    assert not rep.is_end_of_call_report(None)


def test_extract_reads_structured_outputs_by_id():
    f = facts()
    assert f["callerName"] == "Max Mustermann"
    assert f["category"] == "new_inquiry"
    assert f["wantsCallback"] == "true"                       # JS String(true)
    assert f["callerNumber"] == "+493001234599"
    assert f["summary"].startswith("Max Mustermann von Beispiel Solar GmbH")


def test_summary_falls_back_to_reason_and_missing_outputs_are_empty():
    report = copy.deepcopy(REPORT)
    del report["message"]["analysis"]
    report["message"]["artifact"]["structuredOutputs"] = {
        SO_IDS["reason_summary"]: {"name": "reason_summary", "result": "Kurzer Grund."}}
    f = facts(report)
    assert f["summary"] == "Kurzer Grund."
    assert f["callerName"] == "" and f["wantsCallback"] == ""


def test_inbound_row_and_notification():
    row = rep.inbound_row(facts(), NOW)
    assert list(row) == rep.INBOUND_COLUMNS
    assert row["Date"] == "2026-10-05 09:00" and row["Category"] == "new_inquiry"
    assert row["Wants Callback"] == "true" and row["Action Requested"] == "Rückruf Dienstagvormittag"
    mail = rep.notification_email(facts(), "you@example.com")
    assert mail["to"] == "you@example.com"
    assert mail["subject"] == "\U0001F4DE new_inquiry: Max Mustermann (Beispiel Solar GmbH)"
    assert mail["message"].splitlines()[:3] == ["Kategorie: new_inquiry", "Anrufer: Max Mustermann / Beispiel Solar GmbH",
                                                "Nummer: +493001234599"]
    assert "Rückruf gewünscht: true\n\nZusammenfassung: Max Mustermann" in mail["message"]


def test_notification_subject_without_company_and_for_unknown_callers():
    assert rep.notification_email({"category": "other", "callerName": "Max",
                                   "callerCompany": "none"}, "x")["subject"].endswith("other: Max")
    assert rep.notification_email({}, "x")["subject"].endswith("Anruf: Unbekannt")


def test_call_request_body():
    form = fixture_json("demo_form_de.json")
    body = rep.build_call_request(form, "de", "+493001234567", "asst-1", "phone-1")
    assert body["customers"] == [{"number": "+493001234567"}]
    assert body["assistantOverrides"]["variableValues"] == {
        "lead_name": "Jane Example", "lead_company_name": "Acme Example GmbH",
        "lead_request": "Anfragen aus dem Kontaktformular sofort zurückrufen lassen"}


@pytest.mark.parametrize("call, route", [
    ({"status": "in-progress"}, "poll"),
    ({"status": "ended", "endedReason": "voicemail"}, "voicemail"),
    ({"status": "ended", "endedReason": "customer-ended-call"}, "complete"),
])
def test_outbound_route(call, route):
    assert rep.outbound_route(call) == route


def test_outbound_rows_per_outcome():
    form = fixture_json("demo_form_de.json")
    ended = fixture_json("qualifier_call_ended.json")
    done = rep.outbound_row(form, "de", "complete", NOW, call=ended, so_ids=SO_IDS)
    assert done["Status"] == "Abgeschlossen" and done["Appointment Interest"] is True
    assert done["Callback Window"] == "Dienstag 14:00, gleiche Nummer"
    vm = rep.outbound_row(form, "de", "voicemail", NOW)
    assert vm["Status"] == "Voicemail - erneut anrufen" and vm["Budget"] == "N/A"
    bad = rep.outbound_row(form, "de", "invalid", NOW)
    assert bad["Status"] == "Ungültige Nummer" and "Budget" not in bad


def test_english_workflow_uses_english_fields_and_labels():
    form = {"Name": "Jane Example", "Phone": "+44 7700 900123", "Email": "jane@acme-example.com",
            "Company": "Acme Example Ltd", "Request": "Call back web leads", "Company Size": "2-10"}
    row = rep.outbound_row(form, "en", "complete", NOW, call={"artifact": {}}, so_ids=SO_IDS)
    assert row["Company"] == "Acme Example Ltd" and row["Status"] == "Complete" and row["Budget"] == ""
