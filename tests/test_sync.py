"""Real-mode `sync` against an in-process fake of the Vapi REST API."""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from phone_agent.config import Settings
from phone_agent.sync import load_state, sync
from phone_agent.vapi_client import VapiClient, VapiError

API_KEY = "test-key-not-real"


class FakeVapi:
    """Stores resources per kind and records every request."""

    def __init__(self):
        self.store = {"assistant": {}, "tool": {}, "structured-output": {}}
        self.requests: list[dict] = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = self.path.split("?")[0].strip("/").split("/")
                fake.requests.append({"method": method, "path": "/" + "/".join(path), "body": body,
                                      "auth": self.headers.get("Authorization"),
                                      "ua": self.headers.get("User-Agent")})
                if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                    return self._reply(401, {"message": "Invalid Key"})
                kind = path[0]
                items = fake.store[kind]
                if method == "GET":
                    rows = list(items.values())
                    return self._reply(200, {"results": rows} if kind == "structured-output" else rows)
                if method == "POST":
                    ident = str(uuid.uuid4())
                    items[ident] = {"id": ident, **body}
                    return self._reply(201, items[ident])
                if method == "PATCH" and len(path) == 2 and path[1] in items:
                    items[path[1]].update(body)
                    return self._reply(200, items[path[1]])
                return self._reply(404, {"message": "not found"})

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

            def do_PATCH(self):  # noqa: N802
                self._handle("PATCH")

            def log_message(self, *args):
                pass

        return Handler

    def writes(self):
        return [r for r in self.requests if r["method"] != "GET"]


@pytest.fixture
def vapi():
    fake = FakeVapi()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def settings():
    return Settings(vapi_api_key=API_KEY, webhook_secret="hook-secret",
                    calendar_webhook_url="https://n8n.example.com/webhook/vapi-calendar",
                    report_webhook_url="https://n8n.example.com/webhook/vapi-inbound-report")


def run(vapi, settings, tmp_path, **kw):
    client = VapiClient(API_KEY, vapi.url)
    return sync(client, settings, load_state(tmp_path / "ids.json"), log=lambda _: None,
                state_path=tmp_path / "ids.json", **kw)


def test_first_sync_creates_everything_with_linked_ids(vapi, settings, tmp_path):
    state = run(vapi, settings, tmp_path)
    posts = [r for r in vapi.writes() if r["method"] == "POST"]
    assert [r["path"] for r in posts].count("/tool") == 2
    assert [r["path"] for r in posts].count("/structured-output") == 14
    assert [r["path"] for r in posts].count("/assistant") == 3
    # assistants come last and point at the ids created before them
    assert [r["path"] for r in posts][-3:] == ["/assistant"] * 3
    de = vapi.store["assistant"][state["assistants"]["qualifier-de"]]
    assert de["model"]["toolIds"] == [state["tools"]["check_availability"], state["tools"]["book_appointment"]]
    assert set(de["artifactPlan"]["structuredOutputIds"]) <= set(vapi.store["structured-output"])
    tool = vapi.store["tool"][state["tools"]["book_appointment"]]
    assert tool["server"]["secret"] == "hook-secret"
    assert json.loads((tmp_path / "ids.json").read_text()) == state


def test_second_sync_updates_in_place(vapi, settings, tmp_path):
    first = run(vapi, settings, tmp_path)
    before = len(vapi.requests)
    second = run(vapi, settings, tmp_path)
    new = vapi.requests[before:]
    assert {r["method"] for r in new} == {"GET", "PATCH"}
    assert first == second
    tool_patch = next(r for r in new if r["method"] == "PATCH" and r["path"].startswith("/tool/"))
    assert "type" not in tool_patch["body"]


def test_existing_resources_are_found_by_name_without_state(vapi, settings, tmp_path):
    run(vapi, settings, tmp_path)
    (tmp_path / "ids.json").unlink()
    before = len(vapi.requests)
    run(vapi, settings, tmp_path)
    assert not [r for r in vapi.requests[before:] if r["method"] == "POST"]


def test_dry_run_only_reads(vapi, settings, tmp_path):
    lines = []
    sync(VapiClient(API_KEY, vapi.url), settings, load_state(tmp_path / "x.json"), dry_run=True,
         log=lines.append, state_path=tmp_path / "x.json")
    assert vapi.writes() == []
    assert len(lines) == 2 + 14 + 3 and all(line.strip().startswith("would create") for line in lines)
    assert not (tmp_path / "x.json").exists()


def test_missing_settings_stop_before_any_request(vapi, tmp_path):
    with pytest.raises(SystemExit):
        run(vapi, Settings(vapi_api_key=API_KEY), tmp_path)
    assert vapi.requests == []


def test_requests_carry_auth_and_a_browser_user_agent(vapi, settings, tmp_path):
    run(vapi, settings, tmp_path)
    assert all(r["auth"] == f"Bearer {API_KEY}" for r in vapi.requests)
    assert all(r["ua"] and "Python-urllib" not in r["ua"] for r in vapi.requests)


def test_errors_never_echo_the_key(vapi):
    client = VapiClient("wrong-key-value", vapi.url)
    with pytest.raises(VapiError) as err:
        client.list("assistant")
    assert err.value.status == 401 and "wrong-key-value" not in str(err.value)
