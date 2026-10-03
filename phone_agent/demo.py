"""Offline demo: no account, no key, no network beyond 127.0.0.1.

1. A lead form submission becomes a Vapi call request (phone normalization).
2. A scripted German call: every tool call goes over HTTP to the local
   calendar webhook, which runs the same rules as the n8n workflow.
3. The rule and error branches of that webhook.
4. What lands in the sheet and the inbox after a call.

The clock is frozen so the output is the same on every run.
"""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import datetime
from typing import Any

from . import phone
from . import report_logic as rep
from .config import ROOT
from .fake_calendar import FakeCalendar
from .jsish import js_string
from .server import CALENDAR_PATH, REPORT_PATH, WebhookApp, start_in_thread
from .simulator import WIDTH, load_script, post_json, run_script
from .timeutil import BERLIN, parse_iso_loose

DEMO_NOW = datetime(2026, 10, 5, 9, 0, tzinfo=BERLIN)
DEMO_SECRET = "local-demo-secret"
FIXTURES = ROOT / "fixtures"


def _fixture(name: str) -> Any:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("_comment", None)
    return data


def _table(rows: dict[str, Any], indent: int = 5) -> list[str]:
    width = max(len(k) for k in rows)
    out = []
    for key, value in rows.items():
        text = value if isinstance(value, str) else js_string(value)
        lines = textwrap.wrap(text, WIDTH - indent - width - 2) or [""]
        out.append(f"{' ' * indent}{key:<{width}}  {lines[0]}")
        out.extend(f"{' ' * (indent + width + 2)}{line}" for line in lines[1:])
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    out = print
    so_ids = _fixture("so_ids.json")
    calendar = FakeCalendar.from_fixture(FIXTURES / "calendar_busy.json", DEMO_NOW)
    app = WebhookApp(secret=DEMO_SECRET, calendar=calendar, clock=lambda: DEMO_NOW, so_ids=so_ids,
                     notify_email="you@example.com")
    server, base = start_in_thread(app)
    try:
        out("AI phone agent, offline demo")
        out(f"Clock frozen at {DEMO_NOW:%A %Y-%m-%d %H:%M} Europe/Berlin. Fake calendar with "
            f"{len(calendar.busy)} busy blocks. Webhooks on {base} (local only).")

        # 1. form -> call request
        out("\n1) Lead form -> outbound call request   (n8n: outbound-qualifier-de.json)")
        for raw in ["030 0123 4567", "0049 30 01234567", "+44 7700 900123", "12345"]:
            norm = phone.normalize_de(raw)
            tail = '  -> logged as "Ungültige Nummer", no call' if norm == phone.INVALID else ""
            out(f"     phone {raw!r:<22} -> {norm}{tail}")
        form = _fixture("demo_form_de.json")
        request = rep.build_call_request(form, "de", phone.normalize_de(form["Telefonnummer"]),
                                         "<qualifier-de assistant id>", "<your Vapi phone number id>")
        out("     POST https://api.vapi.ai/call   (built, not sent)")
        out("\n".join("     " + line for line in json.dumps(request, indent=2, ensure_ascii=False).splitlines()))

        # 2. the call
        out("\n2) The call: scripted transcript, tool calls go to POST " + CALENDAR_PATH)
        call = run_script(load_script(FIXTURES / "call_de_booking.json"), base + CALENDAR_PATH,
                          DEMO_SECRET, DEMO_NOW, out=out)

        # 3. edge cases
        out("\n3) Rule and error branches of the same webhook")
        edges = run_script(load_script(FIXTURES / "edge_cases.json"), base + CALENDAR_PATH,
                           DEMO_SECRET, DEMO_NOW, out=out, compact=True)

        # 4. after the call
        out('\n4) After the call')
        ended = _fixture("qualifier_call_ended.json")
        route = rep.outbound_route(ended)
        out(f'   Outbound: GET /call returns status "ended" -> branch "{route}" -> row in "Demo Submissions":')
        for line in _table(rep.outbound_row(form, "de", route, DEMO_NOW, call=ended, so_ids=so_ids)):
            out(line)
        out(f"\n   Inbound: Vapi posts the front desk end-of-call-report to {REPORT_PATH}")
        status, body = post_json(base + REPORT_PATH, _fixture("front_desk_report.json"), {})
        out(f'   HTTP {status}, row appended to "Inbound Calls":')
        for line in _table(body["row"]):
            out(line)
        mail = body["email"]
        out(f"   Notification to {mail['to']}")
        out(f"     Subject: {mail['subject']}")
        for line in mail["message"].splitlines():
            wrapped = textwrap.wrap(line, WIDTH - 5, subsequent_indent="  ") or [""]
            out("\n".join(f"     {w}" if w else "" for w in wrapped))

        # summary
        out(f"\nFake calendar now holds {len(calendar.events)} new event(s):")
        for ev in calendar.events:
            start = parse_iso_loose(ev["start"]["dateTime"]).astimezone(BERLIN)
            end = parse_iso_loose(ev["end"]["dateTime"]).astimezone(BERLIN)
            invite = ", ".join(a["email"] for a in ev["attendees"]) or "none"
            out(f"  {ev['id']}  {start:%a %d.%m. %H:%M}-{end:%H:%M}  \"{ev['summary']}\"  invite: {invite}")
        total = call.exchanges + edges.exchanges
        failed = [x for x in total if not x.ok]
        out(f"\n{len(total) - len(failed)}/{len(total)} tool calls answered as expected.")
        for x in failed:
            out(f"  FAILED: {x.label}: got {x.result!r}")
        return 0 if not failed else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
