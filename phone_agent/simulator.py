"""Scripted call simulator.

Plays a call script (fixtures/*.json): spoken turns are printed, tool turns are
sent to a webhook as Vapi-style `tool-calls` server messages, exactly the
fields the webhook reads (message.type, toolCallList[0].id,
function.name, function.arguments), with the x-vapi-secret header Vapi adds
when a tool has a server secret. Every tool turn can carry an expected result
prefix, so a script doubles as an end-to-end check.

Works against the local mock (default) or any deployed webhook URL.
"""
from __future__ import annotations

import json
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .timeutil import parse_start, resolve_token, spoken_de

WIDTH = 100


@dataclass
class ToolExchange:
    label: str
    function: str
    arguments: dict[str, Any]
    status: int
    result: str
    expected: str | None
    ok: bool


@dataclass
class SimulationResult:
    exchanges: list[ToolExchange] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(x.ok for x in self.exchanges)

    @property
    def failures(self) -> list[ToolExchange]:
        return [x for x in self.exchanges if not x.ok]


def load_script(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tool_call_payload(name: str, arguments: dict[str, Any], *, call_id: str, tool_call_id: str,
                      now: datetime, as_string: bool = False, message_type: str = "tool-calls") -> dict[str, Any]:
    args: Any = json.dumps(arguments, ensure_ascii=False) if as_string else arguments
    return {"message": {
        "type": message_type,
        "timestamp": int(now.timestamp() * 1000),
        "call": {"id": call_id, "type": "outboundPhoneCall"},
        "toolCallList": [{"id": tool_call_id, "type": "function",
                          "function": {"name": name, "arguments": args}}],
    }}


def post_json(url: str, payload: Any, headers: dict[str, str], timeout: float = 20) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(body or "{}")
        except ValueError:
            return err.code, {"raw": body[:300]}


def _resolve_args(args: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {k: resolve_token(v, now) if isinstance(v, str) else v for k, v in args.items()}


def _say(out: Callable[[str], None], who: str, text: str, pad: int) -> None:
    lines = textwrap.wrap(text, WIDTH - pad - 4) or [""]
    out(f"  {who:<{pad}}  {lines[0]}")
    for line in lines[1:]:
        out(f"  {'':<{pad}}  {line}")


def run_script(script: dict[str, Any], url: str, secret: str, now: datetime,
               out: Callable[[str], None] = print, allow_booking: bool = True,
               compact: bool = False) -> SimulationResult:
    """Play a script. compact=True prints one 'label  result' block per tool turn."""
    agent = script.get("agent_name", "Agent")
    caller = script.get("caller_name", "Caller")
    labels = [t.get("label", t.get("tool", "")) for t in script["turns"] if "tool" in t]
    if compact:
        pad = max((len(label) for label in labels), default=0)
    else:
        pad = max(len(agent), len(caller), len("tool >"))
    result = SimulationResult()
    slot = ""
    for i, turn in enumerate(script["turns"], start=1):
        if "agent" in turn:
            _say(out, agent, turn["agent"].replace("{slot}", slot), pad)
        elif "caller" in turn:
            _say(out, caller, turn["caller"], pad)
        elif "note" in turn:
            _say(out, "", f"({turn['note']})", pad)
        elif "tool" in turn:
            name = turn["tool"]
            args = _resolve_args(turn.get("arguments", {}), now)
            if name == "book_appointment" and not allow_booking:
                out(f"  {'tool >':<{pad}}  {name} skipped (--no-book)")
                continue
            if "startzeit" in args and parse_start(str(args["startzeit"])) is not None:
                slot = spoken_de(parse_start(str(args["startzeit"])))
            payload = tool_call_payload(name, args, call_id=script.get("call_id", "call-sim"),
                                        tool_call_id=f"toolcall-{i:03d}", now=now,
                                        as_string=turn.get("arguments_as_string", False),
                                        message_type=turn.get("message_type", "tool-calls"))
            status, body = post_json(url, payload, {"x-vapi-secret": turn.get("secret", secret)})
            results = body.get("results") or [{}]
            text = str(results[0].get("result", body))
            expected = turn.get("expect")
            ok = status == turn.get("expect_status", 200) and (expected is None or text.startswith(expected))
            label = turn.get("label", name)
            result.exchanges.append(ToolExchange(label, name, args, status, text, expected, ok))
            mark = "ok" if ok else f"MISMATCH, expected {expected!r} / HTTP {turn.get('expect_status', 200)}"
            status_note = "" if status == 200 else f"HTTP {status}: "
            if compact:
                _say(out, label, f"{status_note}{text}  [{mark}]", pad)
                continue
            shown_args = json.dumps(args, ensure_ascii=False)
            if turn.get("arguments_as_string"):
                shown_args += "  (arguments sent as a JSON string)"
            _say(out, "tool >", f"{name} {shown_args}", pad)
            _say(out, "tool <", f"{status_note}{text}  [{mark}]", pad)
    return result
