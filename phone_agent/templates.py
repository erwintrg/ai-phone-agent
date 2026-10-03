"""Load the versioned agent configs and n8n exports, and fill their placeholders.

Two kinds of markers:

* `__NAME__` placeholders (UPPER_SNAKE between double underscores) for values from
  .env or from the ids `sync` stored. This syntax collides neither with Vapi's
  `{{lead_name}}` prompt variables nor with n8n's `{{ $json... }}` expressions
  or JS template literals inside code nodes.
* `@file:prompt.md`, `@tool:<name>`, `@so:<name>` references inside assistant
  templates, resolved when the payload is built.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import ROOT, Settings

AGENTS_DIR = ROOT / "agents"
N8N_DIR = ROOT / "n8n"
AGENT_KEYS = ["qualifier-de", "qualifier-en", "front-desk"]
TOOL_NAMES = ["check_availability", "book_appointment"]
PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]*[A-Z0-9])__")
# Node types that carry a webhookId in an n8n export (trigger URL or resume URL).
WEBHOOK_NODE_TYPES = {"n8n-nodes-base.webhook", "n8n-nodes-base.formTrigger",
                      "n8n-nodes-base.wait", "n8n-nodes-base.gmail"}


def map_strings(obj: Any, fn: Callable[[str], Any]) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [map_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    return obj


def find_placeholders(obj: Any) -> set[str]:
    found: set[str] = set()
    map_strings(obj, lambda s: found.update(PLACEHOLDER_RE.findall(s)) or s)
    return found


MARK_RE = re.compile(r"<(?:[A-Z][A-Z0-9_]*|tool id: [a-z_]+|so id: [a-z_]+)>")


def find_marked(obj: Any) -> set[str]:
    """Preview markers left by render(missing='mark') and resolve_refs(strict=False)."""
    found: set[str] = set()
    map_strings(obj, lambda s: found.update(MARK_RE.findall(s)) or s)
    return found


def render(obj: Any, values: dict[str, str], missing: str = "keep") -> tuple[Any, set[str]]:
    """Replace __NAME__ placeholders. missing='keep' leaves unknown ones in place,
    'mark' turns them into <NAME> (for previews), 'error' raises KeyError."""
    unknown: set[str] = set()

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        unknown.add(name)
        if missing == "error":
            raise KeyError(name)
        return f"<{name}>" if missing == "mark" else match.group(0)

    return map_strings(obj, lambda s: PLACEHOLDER_RE.sub(sub, s)), unknown


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_tools() -> dict[str, dict[str, Any]]:
    return {name: load_json(AGENTS_DIR / "tools" / f"{name}.json") for name in TOOL_NAMES}


def load_structured_outputs() -> dict[str, dict[str, Any]]:
    return {so["name"]: so for so in load_json(AGENTS_DIR / "structured-outputs.json")}


def load_assistant(key: str) -> dict[str, Any]:
    """Assistant template with @file: references inlined."""
    folder = AGENTS_DIR / key

    def inline(s: str) -> str:
        if s.startswith("@file:"):
            return (folder / s[len("@file:"):]).read_text(encoding="utf-8").rstrip("\n")
        return s

    return map_strings(load_json(folder / "assistant.json"), inline)


def resolve_refs(obj: Any, tool_ids: dict[str, str], so_ids: dict[str, str], strict: bool) -> Any:
    def resolve(s: str) -> str:
        for prefix, table in (("@tool:", tool_ids), ("@so:", so_ids)):
            if s.startswith(prefix):
                name = s[len(prefix):]
                if name in table:
                    return table[name]
                if strict:
                    raise KeyError(f"no id for {s}")
                return f"<{prefix[1:-1]} id: {name}>"
        return s

    return map_strings(obj, resolve)


def state_placeholders(state: dict[str, Any]) -> dict[str, str]:
    """Placeholder values that come from ids created by `sync`."""
    values: dict[str, str] = {}
    for key, ident in (state.get("assistants") or {}).items():
        values["VAPI_ASSISTANT_ID_" + key.upper().replace("-", "_")] = ident
    for name, ident in (state.get("structured_outputs") or {}).items():
        values[f"SO_{name.upper()}_ID"] = ident
    return values


def all_placeholders(settings: Settings, state: dict[str, Any]) -> dict[str, str]:
    return {**settings.placeholders(), **state_placeholders(state)}


def load_workflows() -> dict[str, dict[str, Any]]:
    return {p.name: load_json(p) for p in sorted(N8N_DIR.glob("*.json"))}


def render_workflow(wf: dict[str, Any], values: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    """Fill placeholders and give every node a fresh id (and webhookId where the
    node type uses one), so the file imports cleanly into any n8n instance."""
    rendered, missing = render(wf, values, missing="keep")
    for node in rendered["nodes"]:
        node["id"] = str(uuid.uuid4())
        if node["type"] in WEBHOOK_NODE_TYPES:
            node["webhookId"] = str(uuid.uuid4())
    return rendered, missing
