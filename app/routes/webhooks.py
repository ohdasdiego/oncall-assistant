"""
Webhook endpoints:
  POST /webhooks/infra-monitor  - receives alerts from AI Infra Monitor
  POST /webhooks/telegram       - receives Telegram bot commands and button callbacks
"""

import os
import hmac
import hashlib
import logging
import json
import requests
from flask import Blueprint, request, jsonify

from ..models.database import create_incident, get_incident, add_timeline_event, update_incident_status
from ..routes.incidents import _process_incident
from ..services import telegram

logger = logging.getLogger(__name__)
webhooks_bp = Blueprint("webhooks", __name__)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me-in-prod")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# -- Infra Monitor webhook -----------------------------------------------------

@webhooks_bp.route("/infra-monitor", methods=["POST"])
def infra_monitor_webhook():
    """
    Expected payload from AI Infra Monitor:
    {
      "title": "High CPU on prod-web-01",
      "description": "CPU usage at 94% for 5 minutes",
      "severity": "high",
      "host": "prod-web-01",
      "metric": "cpu_percent",
      "value": 94.2
    }
    """
    sig_header = request.headers.get("X-Webhook-Signature", "")
    if WEBHOOK_SECRET and WEBHOOK_SECRET != "change-me-in-prod":
        expected = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(),
            request.data,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            logger.warning("Infra Monitor webhook: invalid signature")
            return jsonify({"error": "Invalid signature"}), 401

    data = request.get_json(silent=True) or {}
    title = data.get("title", "Alert from Infra Monitor")
    description = data.get("description", "")
    severity = data.get("severity", "medium")
    host = data.get("host", "unknown")

    if host != "unknown":
        description = f"Host: {host}\n{description}"

    incident_id = create_incident(title, description, severity, source="infra_monitor")
    add_timeline_event(
        incident_id, "WEBHOOK",
        f"Alert received from AI Infra Monitor. Host: {host}, Severity: {severity.upper()}"
    )

    # Low/medium: auto-advance to INVESTIGATING immediately
    # High/critical: stay OPEN until engineer responds to Telegram button
    if severity.lower() in ("low", "medium"):
        update_incident_status(incident_id, "INVESTIGATING")
        add_timeline_event(incident_id, "STATUS_CHANGE",
            "OPEN -> INVESTIGATING (auto - low/medium severity)")

    _process_incident(incident_id, title, description, severity)

    base_url = os.getenv("BASE_URL", "")
    telegram.notify_incident_opened(incident_id, title, severity, "infra_monitor", base_url)

    # Extra loud page for critical
    if severity.lower() == "critical":
        telegram.notify_critical_page(incident_id, title, base_url)

    logger.info(f"Infra Monitor webhook created incident #{incident_id}: {title}")
    return jsonify({"incident_id": incident_id, "status": "created"}), 201


# -- Telegram bot webhook ------------------------------------------------------

@webhooks_bp.route("/telegram", methods=["POST"])
def telegram_webhook():
    """
    Handles Telegram bot commands and inline button callbacks.
      /oncall <description>  - open a new incident
      /status                - list open incidents
      callback_query         - inline button presses (auto/manual)
    """
    data = request.get_json(silent=True) or {}

    # Handle inline button callback_query
    if "callback_query" in data:
        cq = data["callback_query"]
        cq_id = cq.get("id", "")
        cq_data = cq.get("data", "")

        telegram.answer_callback(cq_id)  # clear spinner immediately

        try:
            action, incident_id_str = cq_data.split(":", 1)
            incident_id = int(incident_id_str)
        except (ValueError, AttributeError):
            return jsonify({"ok": True})

        incident = get_incident(incident_id)
        if not incident:
            return jsonify({"ok": True})

        base_url = os.getenv("BASE_URL", "")
        url_line = f'\n<a href="{base_url}/incidents/{incident_id}">View Incident</a>' if base_url else ""

        if action == "auto":
            update_incident_status(incident_id, "INVESTIGATING")
            add_timeline_event(incident_id, "STATUS_CHANGE",
                "OPEN -> INVESTIGATING (auto-remediation approved via Telegram)")
            add_timeline_event(incident_id, "WEBHOOK",
                "Auto-remediation approved by on-call engineer via Telegram.")
            telegram._send(
                f"<b>Auto-remediation approved - #{incident_id}</b>\n"
                f"Following response plan. Check dashboard for updates.{url_line}"
            )
            logger.info(f"Incident #{incident_id}: auto-remediation approved")

        elif action == "manual":
            update_incident_status(incident_id, "INVESTIGATING")
            add_timeline_event(incident_id, "STATUS_CHANGE",
                "OPEN -> INVESTIGATING (manual - engineer taking over)")
            add_timeline_event(incident_id, "WEBHOOK",
                "On-call engineer confirmed manual handling via Telegram.")
            telegram._send(
                f"<b>Manual handling confirmed - #{incident_id}</b>\n"
                f"You have the incident. Response plan is ready.{url_line}"
            )
            logger.info(f"Incident #{incident_id}: manual handling confirmed")

        return jsonify({"ok": True})

    # Handle bot command message
    message = data.get("message", {})
    text = message.get("text", "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    if not text:
        return jsonify({"ok": True})

    def reply(msg):
        if TELEGRAM_BOT_TOKEN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=5
                )
            except Exception as e:
                logger.warning(f"Telegram reply failed: {e}")

    if text.startswith("/oncall"):
        alert_text = text[len("/oncall"):].strip()
        if not alert_text:
            reply("Usage: <code>/oncall &lt;alert description&gt;</code>")
            return jsonify({"ok": True})

        severity = "high"  # default to high - Telegram alerts are usually urgent
        for sev in ("critical", "high", "medium", "low"):
            if alert_text.lower().startswith(f"[{sev}]"):
                severity = sev
                alert_text = alert_text[len(f"[{sev}]"):].strip()
                break

        incident_id = create_incident(
            title=alert_text[:120],
            description=f"Triggered via Telegram by chat {chat_id}",
            severity=severity,
            source="telegram"
        )
        add_timeline_event(incident_id, "OPENED", f"Incident opened via Telegram. Severity: {severity.upper()}")
        _process_incident(incident_id, alert_text, "", severity)

        base_url = os.getenv("BASE_URL", "")
        telegram.notify_incident_opened(incident_id, alert_text, severity, "telegram", base_url)

        if severity.lower() == "critical":
            telegram.notify_critical_page(incident_id, alert_text, base_url)

    elif text.startswith("/status"):
        from ..models.database import list_incidents
        open_incidents = [i for i in list_incidents(20) if i["status"] != "RESOLVED"]
        if not open_incidents:
            reply("No open incidents.")
        else:
            lines = [f"<b>Open Incidents ({len(open_incidents)})</b>"]
            for i in open_incidents:
                lines.append(f"- #{i['id']} [{i['severity'].upper()}] {i['title']} - <code>{i['status']}</code>")
            reply("\n".join(lines))

    else:
        reply("Commands:\n<code>/oncall &lt;description&gt;</code> - open incident\n<code>/status</code> - list open incidents")

    return jsonify({"ok": True})
