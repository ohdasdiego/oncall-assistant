"""
Telegram notification service for On-Call Assistant.
Sends alerts at incident open, status changes, and resolution.
Uses HTML parse_mode — safe against special characters in incident titles.
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


def _esc(text: str) -> str:
    """Escape HTML special chars in user-provided text."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def _send_with_buttons(text: str, buttons: list) -> bool:
    """Send a message with inline keyboard buttons."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": buttons}
            },
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram button send failed: {e}")
        return False


def answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a Telegram callback query (removes loading spinner)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5
        )
        return True
    except Exception as e:
        logger.error(f"answerCallbackQuery failed: {e}")
        return False


def notify_incident_opened(incident_id: int, title: str, severity: str, source: str, base_url: str = "") -> bool:
    """Send incident open alert. High/Critical get action buttons; Low/Medium get plain alert."""
    emoji = SEVERITY_EMOJI.get(severity.lower(), "⚪")
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""

    if severity.lower() in ("high", "critical"):
        text = (
            f"{emoji} <b>INCIDENT [{_esc(severity.upper())}] — ACTION REQUIRED</b>\n"
            f"<b>#{incident_id}</b> — {_esc(title)}\n"
            f"Source: <code>{_esc(source)}</code>\n"
            f"Response plan is ready. How do you want to proceed?"
            f"{url_line}"
        )
        buttons = [[
            {"text": "✅ Auto-remediate", "callback_data": f"auto:{incident_id}"},
            {"text": "🔴 I'll handle it", "callback_data": f"manual:{incident_id}"}
        ]]
        return _send_with_buttons(text, buttons)
    else:
        # Low/medium — plain alert, agent handles autonomously
        text = (
            f"{emoji} <b>INCIDENT OPENED [{_esc(severity.upper())}]</b>\n"
            f"<b>#{incident_id}</b> — {_esc(title)}\n"
            f"Source: <code>{_esc(source)}</code>\n"
            f"Severity is low/medium — response plan generated, monitoring."
            f"{url_line}"
        )
        return _send(text)


def notify_auto_handled(incident_id: int, title: str, summary: str, base_url: str = "") -> bool:
    """Notify that a low/medium incident was auto-handled."""
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""
    text = (
        f"⚙️ <b>AUTO-HANDLED — #{incident_id}</b>\n"
        f"{_esc(title)}\n\n"
        f"{_esc(summary)}"
        f"{url_line}"
    )
    return _send(text)


def notify_critical_page(incident_id: int, title: str, base_url: str = "") -> bool:
    """Loud page for critical incidents — no autonomous action taken."""
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""
    text = (
        f"🚨🚨🚨 <b>CRITICAL INCIDENT — IMMEDIATE RESPONSE REQUIRED</b> 🚨🚨🚨\n"
        f"<b>#{incident_id}</b> — {_esc(title)}\n"
        f"No autonomous action taken. You must respond."
        f"{url_line}"
    )
    return _send(text)


def notify_status_change(incident_id: int, title: str, old_status: str, new_status: str, base_url: str = "") -> bool:
    emoji = STATUS_EMOJI.get(new_status, "🔄")
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""
    text = (
        f"{emoji} <b>INCIDENT UPDATE</b>\n"
        f"<b>#{incident_id}</b> — {_esc(title)}\n"
        f"Status: <code>{_esc(old_status)}</code> → <code>{_esc(new_status)}</code>"
        f"{url_line}"
    )
    return _send(text)


def notify_resolved(incident_id: int, title: str, duration_minutes: float, base_url: str = "", note: str = "") -> bool:
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""
    note_line = f"\n💡 {_esc(note)}" if note else ""
    text = (
        f"✅ <b>INCIDENT RESOLVED</b>\n"
        f"<b>#{incident_id}</b> — {_esc(title)}\n"
        f"Duration: <code>{duration_minutes:.0f} min</code>"
        f"{note_line}"
        f"{url_line}"
    )
    return _send(text)


def notify_escalation(incident_id: int, title: str, note: str, base_url: str = "") -> bool:
    url_line = f'\n🔗 <a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""
    text = (
        f"🚨 <b>ESCALATION TRIGGERED</b>\n"
        f"<b>#{incident_id}</b> — {_esc(title)}\n"
        f"Note: {_esc(note)}"
        f"{url_line}"
    )
    return _send(text)
