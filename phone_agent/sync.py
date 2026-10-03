"""Create or update the Vapi resources from the versioned configs in agents/.

Order matters: tools and structured outputs first, because assistants reference
their ids (model.toolIds, artifactPlan.structuredOutputIds).

Each resource is found by the id stored in .state/vapi_ids.json, then by name,
otherwise created. Running sync twice updates in place instead of duplicating.
The state file is written after every created resource, so a failed run never
leaves untracked duplicates behind.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import STATE_PATH, Settings
from .templates import AGENT_KEYS, load_assistant, load_structured_outputs, load_tools, render, resolve_refs
from .vapi_client import VapiClient

REQUIRED_FOR_SYNC = ("CALENDAR_WEBHOOK_URL", "VAPI_WEBHOOK_SECRET", "REPORT_WEBHOOK_URL")


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"tools": {}, "structured_outputs": {}, "assistants": {}}


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def tool_payloads(settings: Settings, preview: bool) -> dict[str, dict[str, Any]]:
    """POST /tool bodies. preview=True marks missing values as <NAME> instead of failing."""
    mode = "mark" if preview else "error"
    return {name: render(tool, settings.placeholders(), mode)[0] for name, tool in load_tools().items()}


def structured_output_payloads() -> dict[str, dict[str, Any]]:
    return {name: {"name": so["name"], "schema": so["schema"]} for name, so in load_structured_outputs().items()}


def assistant_payloads(settings: Settings, state: dict[str, Any], preview: bool) -> dict[str, dict[str, Any]]:
    """POST /assistant bodies with prompts inlined and tool / structured output ids resolved."""
    mode = "mark" if preview else "error"
    out = {}
    for key in AGENT_KEYS:
        body = render(load_assistant(key), settings.placeholders(), mode)[0]
        out[key] = resolve_refs(body, state.get("tools", {}), state.get("structured_outputs", {}),
                                strict=not preview)
    return out


def _find(existing: list[dict[str, Any]], state_id: str | None, match: Callable[[dict[str, Any]], bool]
          ) -> tuple[str | None, str]:
    ids = {x.get("id") for x in existing}
    if state_id and state_id in ids:
        return state_id, "update"
    hit = next((x for x in existing if match(x)), None)
    if hit:
        return hit["id"], "update (found by name)"
    return None, "create"


def sync(client: VapiClient, settings: Settings, state: dict[str, Any] | None = None, *,
         dry_run: bool = False, log: Callable[[str], None] = print,
         state_path: Path = STATE_PATH) -> dict[str, Any]:
    missing = [k for k in REQUIRED_FOR_SYNC if k not in settings.placeholders()]
    if missing:
        raise SystemExit("Missing settings for sync: " + ", ".join(missing) + " (see .env.example)")
    state = state if state is not None else load_state(state_path)
    for section in ("tools", "structured_outputs", "assistants"):
        state.setdefault(section, {})
    existing = {kind: client.list(kind) for kind in ("tool", "structured-output", "assistant")}

    def upsert(kind: str, section: str, key: str, label: str, body: dict[str, Any],
               match: Callable[[dict[str, Any]], bool], update_body: dict[str, Any]) -> None:
        ident, action = _find(existing[kind], state[section].get(key), match)
        if dry_run:
            log(f"  would {action:<22} {kind:<17} {label}")
            return
        if ident:
            client.update(kind, ident, update_body)
        else:
            ident = client.create(kind, body)["id"]
        state[section][key] = ident
        save_state(state, state_path)
        log(f"  {action:<28} {kind:<17} {label}  ({ident})")

    for name, body in tool_payloads(settings, preview=dry_run).items():
        update = {k: v for k, v in body.items() if k != "type"}
        upsert("tool", "tools", name, name, body,
               lambda x, n=name: (x.get("function") or {}).get("name") == n, update)
    for name, body in structured_output_payloads().items():
        upsert("structured-output", "structured_outputs", name, name, body,
               lambda x, n=name: x.get("name") == n, body)
    # Assistants last: they reference the tool and structured output ids that now exist.
    for key, body in assistant_payloads(settings, state, preview=dry_run).items():
        upsert("assistant", "assistants", key, body["name"], body,
               lambda x, n=body["name"]: x.get("name") == n, body)
    return state
