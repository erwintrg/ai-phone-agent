"""Versioned agent configs and the sanitized n8n exports."""
from __future__ import annotations

import json
import re

import pytest

from conftest import ROOT
from phone_agent.config import Settings
from phone_agent.sync import assistant_payloads, structured_output_payloads, tool_payloads
from phone_agent.templates import (AGENT_KEYS, PLACEHOLDER_RE, all_placeholders, find_placeholders, load_assistant,
                                   load_structured_outputs, load_workflows, render, render_workflow)

SETTINGS = Settings(vapi_api_key="k", webhook_secret="s3cret-value", calendar_webhook_url="https://n8n.example.com/webhook/vapi-calendar",
                    report_webhook_url="https://n8n.example.com/webhook/vapi-inbound-report",
                    calendar_id="owner@example.com", sheet_id="sheet-123", notify_email="you@example.com",
                    phone_number_id="phone-1")
STATE = {"tools": {"check_availability": "tool-a", "book_appointment": "tool-b"},
         "structured_outputs": {so: f"so-{so}" for so in load_structured_outputs()},
         "assistants": {"qualifier-de": "asst-de", "qualifier-en": "asst-en", "front-desk": "asst-fd"}}


def test_prompts_are_inlined_and_keep_vapi_variables():
    for key in AGENT_KEYS:
        prompt = load_assistant(key)["model"]["messages"][0]["content"]
        assert not prompt.startswith("@file:") and len(prompt) > 2000
    for key in ("qualifier-de", "qualifier-en"):
        prompt = load_assistant(key)["model"]["messages"][0]["content"]
        assert {"{{lead_name}}", "{{lead_company_name}}", "{{lead_request}}"} <= set(re.findall(r"\{\{\w+\}\}", prompt))


def test_every_structured_output_reference_exists_and_is_used():
    defined = set(load_structured_outputs())
    used = set()
    for key in AGENT_KEYS:
        refs = load_assistant(key).get("artifactPlan", {}).get("structuredOutputIds", [])
        used |= {r.removeprefix("@so:") for r in refs}
    assert used == defined


def test_payloads_resolve_ids_and_settings():
    tools = tool_payloads(SETTINGS, preview=False)
    assert tools["check_availability"]["server"] == {"url": SETTINGS.calendar_webhook_url,
                                                    "secret": "s3cret-value", "timeoutSeconds": 20}
    assistants = assistant_payloads(SETTINGS, STATE, preview=False)
    assert assistants["qualifier-de"]["model"]["toolIds"] == ["tool-a", "tool-b"]
    assert "so-callback_window" in assistants["qualifier-en"]["artifactPlan"]["structuredOutputIds"]
    assert assistants["front-desk"]["server"]["url"] == SETTINGS.report_webhook_url
    assert not find_placeholders([tools, assistants])
    assert len(structured_output_payloads()) == 14


def test_strict_mode_refuses_missing_values():
    with pytest.raises(KeyError):
        tool_payloads(Settings(), preview=False)
    with pytest.raises(KeyError):
        assistant_payloads(SETTINGS, {}, preview=False)
    preview = assistant_payloads(Settings(), {}, preview=True)
    assert preview["front-desk"]["server"]["url"] == "<REPORT_WEBHOOK_URL>"
    assert preview["qualifier-de"]["model"]["toolIds"][0] == "<tool id: check_availability>"


def test_render_leaves_unknown_placeholders_and_vapi_syntax_alone():
    text = {"a": "__KNOWN__ and __UNKNOWN__ and {{lead_name}} and ${ctx.spoken} and __init__"}
    out, missing = render(text, {"KNOWN": "x"})
    assert out["a"] == "x and __UNKNOWN__ and {{lead_name}} and ${ctx.spoken} and __init__"
    assert missing == {"UNKNOWN"}


WORKFLOWS = load_workflows()


def test_four_workflows_exported():
    assert sorted(WORKFLOWS) == ["calendar-check-and-book.json", "front-desk-report.json",
                                 "outbound-qualifier-de.json", "outbound-qualifier-en.json"]


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_exports_carry_no_instance_identifiers(name):
    wf = WORKFLOWS[name]
    assert set(wf) == {"name", "nodes", "connections", "settings"}
    for node in wf["nodes"]:
        assert not {"id", "webhookId", "credentials"} & set(node), node["name"]
    text = json.dumps(wf)
    assert "@gmail.com" not in text and "youcanautomatethis.com" not in text
    assert not re.search(r'"value": "1[A-Za-z0-9_-]{30,}"', text)        # no Google Sheet ids


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_exports_are_wired_consistently(name):
    wf = WORKFLOWS[name]
    names = {n["name"] for n in wf["nodes"]}
    for source, outputs in wf["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for edge in branch:
                assert edge["node"] in names
    referenced = set(re.findall(r"\$\('([^']+)'\)", json.dumps(wf, ensure_ascii=False)))
    assert referenced <= names


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_every_placeholder_can_be_filled(name):
    values = all_placeholders(SETTINGS, STATE)
    placeholders = find_placeholders(WORKFLOWS[name])
    assert placeholders <= set(values), placeholders - set(values)
    rendered, missing = render_workflow(WORKFLOWS[name], values)
    assert not missing and not PLACEHOLDER_RE.search(json.dumps(rendered))
    assert all(n.get("id") for n in rendered["nodes"])
    hooks = [n for n in rendered["nodes"] if n["type"] in ("n8n-nodes-base.webhook", "n8n-nodes-base.formTrigger")]
    assert hooks and all(n.get("webhookId") for n in hooks)


def test_calendar_workflow_keeps_the_secret_check_and_the_rules():
    code = {n["name"]: n["parameters"].get("jsCode", "") for n in WORKFLOWS["calendar-check-and-book.json"]["nodes"]}
    assert "const SECRET = '__VAPI_WEBHOOK_SECRET__';" in code["Parse + Pruefen"]
    assert "16 * 60 + 45" in code["Parse + Pruefen"] and "15 * 60000" in code["Parse + Pruefen"]
    rendered, _ = render_workflow(WORKFLOWS["calendar-check-and-book.json"], all_placeholders(SETTINGS, STATE))
    url = next(n for n in rendered["nodes"] if n["name"] == "Termin eintragen")["parameters"]["url"]
    assert url == "https://www.googleapis.com/calendar/v3/calendars/owner%40example.com/events"
