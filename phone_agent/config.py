"""Settings from environment variables, with an optional .env file.

No third-party dependency: a minimal KEY=VALUE reader, enough for .env.example.
Values already set in the environment win over the file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".state" / "vapi_ids.json"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    vapi_api_key: str = ""
    vapi_base_url: str = "https://api.vapi.ai"
    webhook_secret: str = ""
    n8n_base_url: str = ""
    calendar_webhook_url: str = ""
    report_webhook_url: str = ""
    calendar_id: str = ""
    sheet_id: str = ""
    notify_email: str = ""
    phone_number_id: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get
        n8n = env("N8N_BASE_URL", "").rstrip("/")
        return cls(
            vapi_api_key=env("VAPI_API_KEY", ""),
            vapi_base_url=env("VAPI_BASE_URL", "https://api.vapi.ai").rstrip("/"),
            webhook_secret=env("VAPI_WEBHOOK_SECRET", ""),
            n8n_base_url=n8n,
            calendar_webhook_url=env("CALENDAR_WEBHOOK_URL", "") or (f"{n8n}/webhook/vapi-calendar" if n8n else ""),
            report_webhook_url=env("REPORT_WEBHOOK_URL", "") or (f"{n8n}/webhook/vapi-inbound-report" if n8n else ""),
            calendar_id=env("GOOGLE_CALENDAR_ID", ""),
            sheet_id=env("GOOGLE_SHEET_ID", ""),
            notify_email=env("NOTIFY_EMAIL", ""),
            phone_number_id=env("VAPI_PHONE_NUMBER_ID", ""),
        )

    def placeholders(self) -> dict[str, str]:
        """Values for the __NAME__ placeholders in agents/ and n8n/. Empty values are left out."""
        values = {
            "CALENDAR_WEBHOOK_URL": self.calendar_webhook_url,
            "REPORT_WEBHOOK_URL": self.report_webhook_url,
            "VAPI_WEBHOOK_SECRET": self.webhook_secret,
            "GOOGLE_CALENDAR_ID": self.calendar_id,
            "GOOGLE_CALENDAR_ID_URLENCODED": quote(self.calendar_id, safe="") if self.calendar_id else "",
            "GOOGLE_SHEET_ID": self.sheet_id,
            "NOTIFY_EMAIL": self.notify_email,
            "VAPI_PHONE_NUMBER_ID": self.phone_number_id,
        }
        return {k: v for k, v in values.items() if v}
