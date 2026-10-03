"""Python port of the calendar webhook (n8n workflow `n8n/calendar-check-and-book.json`).

The n8n workflow is the production path. This port mirrors its three code nodes
one to one so the logic can be unit-tested and demoed offline:

    parse_tool_call()   <- code node "Parse + Pruefen"
    evaluate_slot()     <- code node "Bewerten"
    should_book()       <- IF node "Buchen?"
    build_event_body()  <- HTTP node "Termin eintragen" (JSON body)
    confirmation()      <- code node "Bestaetigung"
    tool_response()     <- "Antwort" (Respond to Webhook)

The result strings stay German on purpose: the model reads them, and the
prompts tell it how to react to FREI / NICHT FREI / GEBUCHT and rule errors.
`tests/test_js_parity.py` runs the original JS code nodes under Node.js and
checks this port gives identical output.
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from .jsish import js_parse_int, js_string, js_truthy
from .timeutil import BERLIN, js_iso, parse_start, spoken_de

TOOL_NAMES = ("check_availability", "book_appointment")

# Booking rules, enforced server side so the model cannot talk its way around them.
MIN_LEAD = timedelta(hours=2)
MAX_AHEAD = timedelta(days=30)
WINDOW_START_MIN = 10 * 60          # 10:00 Berlin
WINDOW_END_MIN = 16 * 60 + 45       # 16:45 Berlin, latest END of a slot
BUFFER = timedelta(minutes=15)      # free time required on both sides
DEFAULT_DURATION, MIN_DURATION, MAX_DURATION = 30, 15, 60

MSG_UNAUTHORIZED = "Interner Fehler: nicht autorisiert."
MSG_UNEXPECTED = "Interner Fehler: unerwartetes Ereignis."
MSG_UNKNOWN_FN = "Interner Fehler: unbekannte Funktion."
MSG_NO_TIME = "Es fehlt die gewuenschte Uhrzeit."
MSG_BAD_TIME = "Die Uhrzeit war nicht verstaendlich. Bitte nochmal nach Datum und Uhrzeit fragen."
MSG_TOO_SOON = ("Dieser Termin liegt zu kurzfristig oder in der Vergangenheit. "
                "Der frueheste Termin ist in etwa zwei Stunden.")
MSG_TOO_FAR = ("So weit im Voraus buchen wir noch nicht. "
               "Bitte einen Termin innerhalb der naechsten vier Wochen vorschlagen.")
MSG_WEEKEND = "Am Wochenende ist Erwin nicht erreichbar. Bitte einen Werktag vorschlagen."
MSG_WINDOW = ("Erwin ist werktags zwischen zehn Uhr und viertel vor fuenf erreichbar. "
              "Bitte eine Uhrzeit in diesem Fenster vorschlagen.")


def _error(tool_call_id: str, text: str) -> dict[str, Any]:
    return {"action": "error", "toolCallId": tool_call_id, "result": text}


def _clean(v: Any) -> str:
    return ("" if v is None else js_string(v))[:200]


def parse_tool_call(headers: Mapping[str, str], body: Any, secret: str, now: datetime) -> dict[str, Any]:
    """Authenticate, parse and rule-check one Vapi tool call.

    Returns either {'action': 'error', 'toolCallId', 'result'} or a context
    dict with action 'check' / 'book', the slot as UTC ISO strings, the
    buffered query window and the cleaned caller fields.
    """
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
    given = js_string(hdrs.get("x-vapi-secret") or "")
    if not hmac.compare_digest(given.encode(), str(secret).encode()):
        return _error("unauthorized", MSG_UNAUTHORIZED)

    msg = body.get("message") if isinstance(body, dict) else None
    msg = msg if isinstance(msg, dict) and msg else {}
    calls = msg.get("toolCallList")
    tc = calls[0] if isinstance(calls, list) and calls and isinstance(calls[0], dict) else {}
    tool_call_id = tc.get("id") if js_truthy(tc.get("id")) else ""
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    fname = fn.get("name") or tc.get("name") or ""
    args = fn.get("arguments") or tc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}

    if msg.get("type") != "tool-calls" or not tool_call_id:
        return _error(tool_call_id, MSG_UNEXPECTED)
    if fname not in TOOL_NAMES:
        return _error(tool_call_id, MSG_UNKNOWN_FN)

    raw = js_string(args.get("startzeit") or "").strip()
    if not raw:
        return _error(tool_call_id, MSG_NO_TIME)
    start = parse_start(raw)
    if start is None:
        return _error(tool_call_id, MSG_BAD_TIME)

    parsed = js_parse_int(args.get("dauer_minuten"))
    duration = min(max(parsed if js_truthy(parsed) else DEFAULT_DURATION, MIN_DURATION), MAX_DURATION)
    end = start + timedelta(minutes=duration)

    if start < now + MIN_LEAD:
        return _error(tool_call_id, MSG_TOO_SOON)
    if start > now + MAX_AHEAD:
        return _error(tool_call_id, MSG_TOO_FAR)

    b_start, b_end = start.astimezone(BERLIN), end.astimezone(BERLIN)
    if b_start.weekday() >= 5:
        return _error(tool_call_id, MSG_WEEKEND)
    start_min = b_start.hour * 60 + b_start.minute
    end_min = b_end.hour * 60 + b_end.minute
    if start_min < WINDOW_START_MIN or end_min > WINDOW_END_MIN or end_min <= start_min:
        return _error(tool_call_id, MSG_WINDOW)

    return {
        "action": "check" if fname == "check_availability" else "book",
        "toolCallId": tool_call_id,
        "startISO": js_iso(start),
        "endISO": js_iso(end),
        "bufStartISO": js_iso(start - BUFFER),
        "bufEndISO": js_iso(end + BUFFER),
        "spoken": spoken_de(start),
        "name": _clean(args.get("name")),
        "firma": _clean(args.get("firma")),
        "telefon": _clean(args.get("telefon")),
        "email": _clean(args.get("email")),
        "thema": _clean(args.get("thema")),
    }


def freebusy_request(ctx: Mapping[str, Any], calendar_id: str) -> dict[str, Any]:
    """Body of the Google Calendar freeBusy call: the slot plus 15 min on each side."""
    return {"timeMin": ctx["bufStartISO"], "timeMax": ctx["bufEndISO"],
            "timeZone": "Europe/Berlin", "items": [{"id": calendar_id}]}


def evaluate_slot(ctx: Mapping[str, Any], freebusy: Mapping[str, Any], calendar_id: str) -> dict[str, Any]:
    """freeBusy result -> frei / nicht frei. Only busy intervals ever reach this point,
    so the answer cannot leak titles, attendees or any other event detail."""
    calendars = freebusy.get("calendars") if isinstance(freebusy, dict) else None
    cal = calendars.get(calendar_id) if isinstance(calendars, dict) else None
    busy = cal.get("busy") if isinstance(cal, dict) else None
    busy = busy if js_truthy(busy) else []
    available = isinstance(busy, list) and len(busy) == 0     # JS: busy.length === 0
    spoken = ctx["spoken"]
    if ctx["action"] == "check":
        result = (f"FREI: {spoken} ist verfuegbar. Bei Zusage direkt mit book_appointment buchen."
                  if available else
                  f"NICHT FREI: {spoken} ist leider schon verplant. Bitte einen anderen Vorschlag erfragen. "
                  "Keine Details nennen.")
    else:
        result = "" if available else (f"NICHT FREI: {spoken} ist inzwischen verplant. "
                                       "Bitte einen anderen Vorschlag erfragen.")
    return {**ctx, "available": available, "result": result}


def should_book(evaluated: Mapping[str, Any]) -> bool:
    return evaluated.get("action") == "book" and bool(evaluated.get("available"))


def build_event_body(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Google Calendar event the n8n node creates (sendUpdates=all invites the caller)."""
    name, firma = ctx.get("name") or "", ctx.get("firma") or ""
    return {
        "summary": "YCAT Demo-Termin: " + (name or "Interessent") + (f" ({firma})" if firma else ""),
        "description": ("Gebucht von Elias (Vapi Demo-Assistent).\n"
                        + "Name: " + (name or "-") + "\n"
                        + "Firma: " + (firma or "-") + "\n"
                        + "Telefon: " + (ctx.get("telefon") or "-") + "\n"
                        + "E-Mail: " + (ctx.get("email") or "-") + "\n"
                        + "Thema: " + (ctx.get("thema") or "-")),
        "start": {"dateTime": ctx["startISO"], "timeZone": "Europe/Berlin"},
        "end": {"dateTime": ctx["endISO"], "timeZone": "Europe/Berlin"},
        "attendees": [{"email": ctx["email"]}] if ctx.get("email") else [],
        "reminders": {"useDefault": True},
    }


def confirmation(evaluated: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    return {"toolCallId": evaluated["toolCallId"],
            "result": f"GEBUCHT: {evaluated['spoken']} ist fest eingetragen "
                      f"(Termin-ID {event.get('id') or 'ok'}). Bitte verbindlich zusammenfassen."}


def tool_response(item: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON body Vapi expects back from a tool server."""
    return {"results": [{"toolCallId": item["toolCallId"], "result": item["result"]}]}
