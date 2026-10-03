"""Local mock of the two n8n webhooks, built on the standard library only.

POST /webhook/vapi-calendar         Vapi tool calls (check_availability, book_appointment)
POST /webhook/vapi-inbound-report   Vapi end-of-call-report of the front desk assistant
GET  /health

Same paths as the n8n workflows, so a Vapi tool can point at this server
through a tunnel for prompt testing without touching a real calendar.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import calendar_logic as cal
from . import report_logic as rep
from .fake_calendar import FakeCalendar

CALENDAR_PATH = "/webhook/vapi-calendar"
REPORT_PATH = "/webhook/vapi-inbound-report"


@dataclass
class WebhookApp:
    secret: str
    calendar: FakeCalendar
    clock: Callable[[], datetime]
    so_ids: dict[str, str] = field(default_factory=dict)
    notify_email: str = "you@example.com"
    inbound_rows: list[dict[str, str]] = field(default_factory=list)
    outbox: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError("an empty webhook secret would accept any caller")

    def handle_tool_call(self, headers: dict[str, str], body: Any) -> tuple[int, dict[str, Any]]:
        ctx = cal.parse_tool_call(headers, body, self.secret, self.clock())
        if ctx["action"] == "error":
            # n8n answers 200 with the error text; here a bad secret gets a real 401.
            status = 401 if ctx["toolCallId"] == "unauthorized" else 200
            return status, cal.tool_response(ctx)
        fb = self.calendar.free_busy(cal.freebusy_request(ctx, self.calendar.calendar_id))
        evaluated = cal.evaluate_slot(ctx, fb, self.calendar.calendar_id)
        if cal.should_book(evaluated):
            event = self.calendar.insert_event(cal.build_event_body(evaluated), send_updates="all")
            return 200, cal.tool_response(cal.confirmation(evaluated, event))
        return 200, cal.tool_response(evaluated)

    def handle_report(self, headers: dict[str, str], body: Any) -> tuple[int, dict[str, Any]]:
        if not rep.is_end_of_call_report(body):
            return 200, {"status": "ignored"}
        facts = rep.extract_call_facts(body, self.so_ids)
        row = rep.inbound_row(facts, self.clock())
        mail = rep.notification_email(facts, self.notify_email)
        self.inbound_rows.append(row)
        self.outbox.append(mail)
        return 200, {"status": "logged", "row": row, "email": mail}

    def dispatch(self, method: str, path: str, headers: dict[str, str], raw: bytes) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == "/health":
            return 200, {"ok": True}
        if method != "POST" or path not in (CALENDAR_PATH, REPORT_PATH):
            return 404, {"error": "not found"}
        try:
            body = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            return 400, {"error": "invalid JSON body"}
        if path == CALENDAR_PATH:
            return self.handle_tool_call(headers, body)
        return self.handle_report(headers, body)


def _handler_for(app: WebhookApp, quiet: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload = app.dispatch(method, self.path.split("?", 1)[0], headers, raw)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            if not quiet:
                super().log_message(fmt, *args)

    return Handler


def make_server(app: WebhookApp, host: str = "127.0.0.1", port: int = 0, quiet: bool = True) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler_for(app, quiet))


def start_in_thread(app: WebhookApp, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the mock on a free port; returns (server, base_url). Call server.shutdown() when done."""
    server = make_server(app, host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    h, p = server.server_address[:2]
    return server, f"http://{h}:{p}"
