"""Post-call logic, ported from the report and qualifier workflows.

Inbound (`n8n/front-desk-report.json`), runs on every end-of-call-report:
    is_end_of_call_report()  <- IF "Is End-of-Call-Report?"
    extract_call_facts()     <- code node "Extract Call Facts"
    inbound_row()            <- Sheets node "Log Inbound Call"
    notification_email()     <- Gmail node "Notify Erwin"

Outbound (`n8n/outbound-qualifier-{de,en}.json`), runs after a form submission:
    build_call_request()     <- HTTP node "Call Lead" (POST https://api.vapi.ai/call)
    outbound_route()         <- IF nodes "Ended?" and "Voicemail?"
    outbound_row()           <- Sheets nodes "Log Invalid" / "Log Voicemail" / "Log Complete"
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .jsish import js_string, js_truthy
from .timeutil import berlin_stamp

INBOUND_SO = ["caller_name", "caller_company", "caller_category", "reason_summary",
              "action_requested", "wants_erwin_callback"]
QUALIFIER_SO = ["service_interest", "motivation", "urgency", "past_experience", "budget",
                "appointment_interest", "call_summary", "callback_window"]

INBOUND_COLUMNS = ["Date", "Caller Number", "Caller Name", "Company", "Category", "Reason",
                   "Action Requested", "Wants Callback", "Summary", "Recording"]

# Form field names per workflow language.
FORM_FIELDS = {
    "de": {"name": "Name", "phone": "Telefonnummer", "email": "E-Mail", "company": "Firma",
           "request": "Anliegen", "size": "Firmengroesse"},
    "en": {"name": "Name", "phone": "Phone", "email": "Email", "company": "Company",
           "request": "Request", "size": "Company Size"},
}
STATUS = {
    "de": {"invalid": "Ungültige Nummer", "voicemail": "Voicemail - erneut anrufen", "complete": "Abgeschlossen"},
    "en": {"invalid": "Invalid number", "voicemail": "Voicemail - call again", "complete": "Complete"},
}
SO_COLUMNS = [("Service Interest", "service_interest"), ("Motivation", "motivation"),
              ("Urgency", "urgency"), ("Past Experience", "past_experience"), ("Budget", "budget"),
              ("Appointment Interest", "appointment_interest"), ("Summary", "call_summary")]


def _message(payload: Any) -> dict[str, Any]:
    """n8n wraps the HTTP body as {headers, body}; accept both shapes like the JS does."""
    if not isinstance(payload, dict):
        return {}
    outer = payload["body"] if js_truthy(payload.get("body")) else payload     # JS: w.body || w
    msg = outer.get("message") if isinstance(outer, dict) else None
    return msg if isinstance(msg, dict) else {}


def is_end_of_call_report(payload: Any) -> bool:
    return _message(payload).get("type") == "end-of-call-report"


def extract_call_facts(payload: Any, so_ids: Mapping[str, str]) -> dict[str, Any]:
    m = _message(payload)
    artifact = m.get("artifact") if isinstance(m.get("artifact"), dict) else {}
    so = artifact.get("structuredOutputs") or {}

    def g(key: str) -> Any:
        v = so.get(so_ids.get(key, ""))
        if v is None:
            return ""
        if isinstance(v, dict) and v.get("result") is not None:
            return v["result"]
        return v

    call = m.get("call") if isinstance(m.get("call"), dict) else {}
    customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
    analysis = m.get("analysis") if isinstance(m.get("analysis"), dict) else {}
    reason = g("reason_summary")
    return {
        "startedAt": m.get("startedAt") or "",
        "callerNumber": customer.get("number") or "",
        "callerName": g("caller_name"),
        "callerCompany": g("caller_company"),
        "category": g("caller_category"),
        "reason": reason,
        "action": g("action_requested"),
        "wantsCallback": js_string(g("wants_erwin_callback")),
        "summary": analysis.get("summary") or reason,
        "recording": artifact.get("recordingUrl") or "",
    }


def inbound_row(facts: Mapping[str, Any], now: datetime) -> dict[str, str]:
    return {
        "Date": berlin_stamp(now),
        "Caller Number": js_string(facts.get("callerNumber", "")),
        "Caller Name": js_string(facts.get("callerName", "")),
        "Company": js_string(facts.get("callerCompany", "")),
        "Category": js_string(facts.get("category", "")),
        "Reason": js_string(facts.get("reason", "")),
        "Action Requested": js_string(facts.get("action", "")),
        "Wants Callback": js_string(facts.get("wantsCallback", "")),
        "Summary": js_string(facts.get("summary", "")),
        "Recording": js_string(facts.get("recording", "")),
    }


def notification_email(facts: Mapping[str, Any], to: str) -> dict[str, str]:
    company = facts.get("callerCompany") or ""
    subject = (f"\U0001F4DE {facts.get('category') or 'Anruf'}: {facts.get('callerName') or 'Unbekannt'}"
               + (f" ({company})" if company and company != "none" else ""))
    body = (f"Kategorie: {facts.get('category', '')}\n"
            f"Anrufer: {facts.get('callerName', '')} / {company}\n"
            f"Nummer: {facts.get('callerNumber', '')}\n\n"
            f"Grund: {facts.get('reason', '')}\n"
            f"Gewünschte Aktion: {facts.get('action', '')}\n"
            f"Rückruf gewünscht: {facts.get('wantsCallback', '')}\n\n"
            f"Zusammenfassung: {facts.get('summary', '')}\n"
            f"Aufnahme: {facts.get('recording', '')}")
    return {"to": to, "subject": subject, "message": body}


def build_call_request(form: Mapping[str, Any], lang: str, normalized_number: str,
                       assistant_id: str, phone_number_id: str) -> dict[str, Any]:
    """Body of POST https://api.vapi.ai/call. Form values go in as assistant variables,
    which the prompt reads as {{lead_name}}, {{lead_company_name}}, {{lead_request}}."""
    f = FORM_FIELDS[lang]
    return {
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customers": [{"number": normalized_number}],
        "assistantOverrides": {"variableValues": {
            "lead_name": form.get(f["name"]),
            "lead_company_name": form.get(f["company"]),
            "lead_request": form.get(f["request"]),
        }},
    }


def outbound_route(call: Mapping[str, Any]) -> str:
    """'poll' until Vapi reports status 'ended', then 'voicemail' or 'complete'."""
    if call.get("status") != "ended":
        return "poll"
    return "voicemail" if call.get("endedReason") == "voicemail" else "complete"


def outbound_row(form: Mapping[str, Any], lang: str, outcome: str, now: datetime,
                 call: Mapping[str, Any] | None = None, so_ids: Mapping[str, str] | None = None) -> dict[str, Any]:
    f = FORM_FIELDS[lang]
    row: dict[str, Any] = {
        "Date": berlin_stamp(now),
        "Name": form.get(f["name"]),
        "Phone": form.get(f["phone"]),
        "Email": form.get(f["email"]),
        "Company": form.get(f["company"]),
        "Request": form.get(f["request"]),
        "Company Size": form.get(f["size"]),
    }
    if outcome == "invalid":
        row["Status"] = STATUS[lang]["invalid"]
        return row
    if outcome == "voicemail":
        row.update({col: "N/A" for col, _ in SO_COLUMNS})
        row["Status"] = STATUS[lang]["voicemail"]
        return row
    so = ((call or {}).get("artifact") or {}).get("structuredOutputs") or {}
    ids = so_ids or {}

    def result(key: str) -> Any:
        entry = so.get(ids.get(key, ""))
        value = entry.get("result") if isinstance(entry, dict) else None
        return "" if value is None else value

    row.update({col: result(key) for col, key in SO_COLUMNS})
    row["Status"] = STATUS[lang]["complete"]
    row["Callback Window"] = result("callback_window")
    return row
