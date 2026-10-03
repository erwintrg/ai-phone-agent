"""Command line entry point: python -m phone_agent <command>

Offline:  demo, serve, simulate, plan, render-n8n
Real:     sync (needs VAPI_API_KEY and the webhook settings in .env)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT, STATE_PATH, Settings
from .timeutil import BERLIN, parse_start

BUILD = ROOT / "build"
FALLBACK_SECRET = "local-demo-secret"


def _clock(now_arg: str | None):
    if not now_arg:
        return lambda: datetime.now(BERLIN)
    fixed = parse_start(now_arg)
    if fixed is None:
        raise SystemExit(f"--now must look like 2026-10-05T09:00 (got {now_arg!r})")
    return lambda: fixed


def _secret(arg: str | None) -> str:
    secret = arg or os.environ.get("VAPI_WEBHOOK_SECRET") or ""
    if not secret:
        print(f"note: VAPI_WEBHOOK_SECRET not set, using {FALLBACK_SECRET!r}", file=sys.stderr)
        secret = FALLBACK_SECRET
    return secret


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import main as demo_main
    return demo_main()


def cmd_serve(args: argparse.Namespace) -> int:
    from .fake_calendar import FakeCalendar
    from .server import CALENDAR_PATH, REPORT_PATH, WebhookApp, make_server

    Settings.from_env()  # loads .env into the environment
    clock = _clock(args.now)
    calendar = FakeCalendar.from_fixture(args.busy, clock(), calendar_id=args.calendar_id)
    from .sync import load_state

    # Real structured output ids after `sync`, the demo stand-ins otherwise.
    so_ids = load_state().get("structured_outputs") or {
        k: v for k, v in json.loads((ROOT / "fixtures" / "so_ids.json").read_text(encoding="utf-8")).items()
        if not k.startswith("_")}
    app = WebhookApp(secret=_secret(args.secret), calendar=calendar, clock=clock, so_ids=so_ids)
    server = make_server(app, args.host, args.port, quiet=False)
    host, port = server.server_address[:2]
    print(f"Mock webhooks on http://{host}:{port}{CALENDAR_PATH} and {REPORT_PATH} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from .simulator import load_script, run_script

    Settings.from_env()
    result = run_script(load_script(args.script), args.url, _secret(args.secret), _clock(args.now)(),
                        allow_booking=not args.no_book)
    print(f"\n{len(result.exchanges) - len(result.failures)}/{len(result.exchanges)} tool calls as expected")
    return 0 if result.ok else 1


def cmd_plan(args: argparse.Namespace) -> int:
    from .sync import assistant_payloads, load_state, structured_output_payloads, tool_payloads
    from .templates import find_marked

    settings = Settings.from_env()
    state = load_state()
    tools = tool_payloads(settings, preview=True)
    sos = structured_output_payloads()
    assistants = assistant_payloads(settings, state, preview=True)
    out_dir = BUILD / "vapi"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Vapi resources (offline preview, nothing is sent):")
    for name, body in tools.items():
        (out_dir / f"tool.{name}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  tool               {name:<24} -> {body['server']['url']}")
    for name, body in sos.items():
        print(f"  structured output  {name:<24} ({body['schema']['type']})")
    (out_dir / "structured-outputs.json").write_text(json.dumps(list(sos.values()), indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
    for key, body in assistants.items():
        (out_dir / f"assistant.{key}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
        model = body["model"]
        prompt = model["messages"][0]["content"]
        print(f"  assistant          {body['name']:<34} {model['model']}, {body['transcriber']['model']}/"
              f"{body['transcriber']['language']}, tools {len(model.get('toolIds', []))}, "
              f"structured outputs {len(body.get('artifactPlan', {}).get('structuredOutputIds', []))}, "
              f"prompt {len(prompt)} chars")
    marked = sorted(find_marked([tools, assistants]))
    if marked:
        print(f"{len(marked)} values still open (fill .env, ids come from sync): " + ", ".join(marked))
    print(f"Payloads written to {out_dir.relative_to(ROOT)}/")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .sync import sync
    from .vapi_client import VapiClient

    settings = Settings.from_env()
    if not settings.vapi_api_key:
        raise SystemExit("VAPI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    client = VapiClient(settings.vapi_api_key, settings.vapi_base_url)
    print("Dry run, only GET requests:" if args.dry_run else "Syncing Vapi resources:")
    sync(client, settings, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"Ids saved to {STATE_PATH.relative_to(ROOT)}. Next: python -m phone_agent render-n8n")
    return 0


def cmd_render_n8n(args: argparse.Namespace) -> int:
    from .sync import load_state
    from .templates import all_placeholders, load_workflows, render_workflow

    settings = Settings.from_env()
    values = all_placeholders(settings, load_state())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing_all: set[str] = set()
    for name, wf in load_workflows().items():
        rendered, missing = render_workflow(wf, values)
        missing_all |= missing
        (out_dir / name).write_text(json.dumps(rendered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        note = f"  (unfilled: {', '.join(sorted(missing))})" if missing else ""
        print(f"  {name}{note}")
    shown = out_dir.resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    print(f"Written to {shown}/. Import each file in n8n (Workflows > Import from File), "
          "then pick your Google and Vapi credentials on the marked nodes.")
    return 1 if (missing_all and args.strict) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m phone_agent", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="offline demo (same as python demo.py)").set_defaults(func=cmd_demo)

    s = sub.add_parser("serve", help="run the mock webhooks with a fake calendar")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--now", help="freeze the clock, e.g. 2026-10-05T09:00 (Berlin)")
    s.add_argument("--busy", default=str(ROOT / "fixtures" / "calendar_busy.json"))
    s.add_argument("--calendar-id", default="you@example.com")
    s.add_argument("--secret", help="expected x-vapi-secret (default: VAPI_WEBHOOK_SECRET)")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("simulate", help="play a call script against a calendar webhook")
    s.add_argument("--url", default="http://127.0.0.1:8787/webhook/vapi-calendar")
    s.add_argument("--script", default=str(ROOT / "fixtures" / "call_de_booking.json"))
    s.add_argument("--secret", help="x-vapi-secret to send (default: VAPI_WEBHOOK_SECRET)")
    s.add_argument("--now", help="clock used to resolve '@tue 14:00' style times")
    s.add_argument("--no-book", action="store_true", help="skip book_appointment turns")
    s.set_defaults(func=cmd_simulate)

    sub.add_parser("plan", help="render the Vapi payloads offline into build/vapi").set_defaults(func=cmd_plan)

    s = sub.add_parser("sync", help="create or update tools, structured outputs and assistants on Vapi")
    s.add_argument("--dry-run", action="store_true", help="only GET, print what would change")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("render-n8n", help="fill the n8n exports with your values into build/n8n")
    s.add_argument("--out", default=str(BUILD / "n8n"))
    s.add_argument("--strict", action="store_true", help="exit 1 if any placeholder stays unfilled")
    s.set_defaults(func=cmd_render_n8n)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
