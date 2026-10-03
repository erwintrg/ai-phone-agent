"""Minimal Vapi REST client (standard library only).

Only what `sync` needs: list, create and update assistants, tools and
structured outputs. Error messages never include request headers, so the API
key cannot end up in a log.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

# Vapi sits behind Cloudflare, which rejects the default "Python-urllib/x.y"
# User-Agent with a 403 (error 1010). Any ordinary browser-style UA passes.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ai-phone-agent/1.0"

RESOURCES = {"assistant": "/assistant", "tool": "/tool", "structured-output": "/structured-output"}


class VapiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str):
        super().__init__(f"Vapi {method} {path} failed with HTTP {status}: {body[:500]}")
        self.status = status


class VapiClient:
    def __init__(self, api_key: str, base_url: str = "https://api.vapi.ai", timeout: float = 60):
        if not api_key:
            raise ValueError("VAPI_API_KEY is empty")
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            raise VapiError(method, path, err.code, err.read().decode("utf-8", "replace")) from None
        return json.loads(raw) if raw else None

    def list(self, kind: str) -> list[dict[str, Any]]:
        data = self.request("GET", f"{RESOURCES[kind]}?limit=100")
        # /assistant and /tool return a list, /structured-output a paginated {results: [...]}
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    def create(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", RESOURCES[kind], body)

    def update(self, kind: str, ident: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"{RESOURCES[kind]}/{ident}", body)
