"""
Telegram notification service for On-Call Assistant.
Sends alerts at incident open, status changes, and resolution.
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

SEVERITY_EMOJI = {
    "low": "🟡",
    "medium": "🟠",
    "high": "🔴",
    "critical": "🚨",
}

STATUS_EMOJI = {
    "OPEN": "🔔",
    "INVESTIGATING": "🔍",
    "MITIGATED": "⚠️",
    "RESOLVED": "✅",
}


def _send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API_URL,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def notify_incident_opened(incident_id: int, title: str, severity: str, source: str, base_url: str = "") -> bool:
    emoji = SEVERITY_EMOJI.get(severity.lower(), "⚪")
    url_line = f"\n🔗 [View Incident]({base_url}/incidents/{incident_id})" if base_url else ""
    text = (
        f"{emoji} *INCIDENT OPENED* [{severity.upper()}]\n"
        f"*#{incident_id}* — {title}\n"
        f"Source: `{source}`"
        f"{url_line}"
    )
    return _send(text)


def notify_status_change(incident_id: int, title: str, old_status: str, new_status: str, base_url: str = "") -> bool:
    emoji = STATUS_EMOJI.get(new_status, "🔄")
    url_line = f"\n🔗 [View Incident]({base_url}/incidents/{incident_id})" if base_url else ""
    text = (
        f"{emoji} *INCIDENT UPDATE*\n"
        f"*#{incident_id}* — {title}\n"
        f"Status: `{old_status}` → `{new_status}`"
        f"{url_line}"
    )
    return _send(text)


def notify_resolved(incident_id: int, title: str, duration_minutes: float, base_url: str = "") -> bool:
    url_line = f"\n🔗 [View Incident]({base_url}/incidents/{incident_id})" if base_url else ""
    text = (
        f"✅ *INCIDENT RESOLVED*\n"
        f"*#{incident_id}* — {title}\n"
        f"Duration: `{duration_minutes:.0f} min`"
        f"{url_line}"
    )
    return _send(text)


def notify_escalation(incident_id: int, title: str, note: str, base_url: str = "") -> bool:
    url_line = f"\n🔗 [View Incident]({base_url}/incidents/{incident_id})" if base_url else ""
    text = (
        f"🚨 *ESCALATION TRIGGERED*\n"
        f"*#{incident_id}* — {title}\n"
        f"Note: {note}"
        f"{url_line}"
    )
    return _send(text)
