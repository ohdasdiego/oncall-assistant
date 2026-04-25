"""
Webhook endpoints:
  POST /webhooks/infra-monitor  — receives alerts from AI Infra Monitor
  POST /webhooks/telegram        — receives Telegram bot commands
"""

import os
import hmac
import hashlib
import logging
import json
import requests
from flask import Blueprint, request, jsonify

from ..models.database import create_incident, add_timeline_event
from ..routes.incidents import _process_incident
from ..services import telegram

logger = logging.getLogger(__name__)
webhooks_bp = Blueprint("webhooks", __name__)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me-in-prod")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Infra Monitor webhook ──────────────────────────────────────────────────────

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
    # Optional HMAC signature verification
    sig_header = request.headers.get("X-Webhook-Signature", "")
    if WEBHOOK_SECRET and WEBHOOK_SECRET != "change-me-in-prod":
        expected = hmac.new(
            WEBHOOK_SECRET.encode(),
            request.data,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, f"sha256={expected}"):
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
    _process_incident(incident_id, title, description, severity)

    base_url = os.getenv("BASE_URL", "")
    telegram.notify_incident_opened(incident_id, title, severity, "infra_monitor", base_url)

    logger.info(f"Infra Monitor webhook created incident #{incident_id}: {title}")
    return jsonify({"incident_id": incident_id, "status": "created"}), 201


# ── Telegram bot webhook ───────────────────────────────────────────────────────

@webhooks_bp.route("/telegram", methods=["POST"])
def telegram_webhook():
    """
    Telegram bot commands:
      /oncall <alert description>   — open a new incident
      /status                       — list open incidents
    """
    data = request.get_json(silent=True) or {}
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
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                    timeout=5
                )
            except Exception as e:
                logger.warning(f"Telegram reply failed: {e}")

    if text.startswith("/oncall"):
        alert_text = text[len("/oncall"):].strip()
        if not alert_text:
            reply("Usage: `/oncall <alert description>`")
            return jsonify({"ok": True})

        # Parse optional severity prefix: /oncall [HIGH] disk full on prod-db-01
        severity = "medium"
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
        url_line = f"\n🔗 {base_url}/incidents/{incident_id}" if base_url else ""
        reply(f"✅ *Incident #{incident_id} opened*\n{alert_text}{url_line}")

    elif text.startswith("/status"):
        from ..models.database import list_incidents
        open_incidents = [i for i in list_incidents(20) if i["status"] != "RESOLVED"]
        if not open_incidents:
            reply("✅ No open incidents.")
        else:
            lines = [f"*Open Incidents ({len(open_incidents)})*"]
            for i in open_incidents:
                lines.append(f"• #{i['id']} [{i['severity'].upper()}] {i['title']} — `{i['status']}`")
            reply("\n".join(lines))

    else:
        reply("Available commands:\n`/oncall <description>` — open incident\n`/status` — list open incidents")

    return jsonify({"ok": True})
