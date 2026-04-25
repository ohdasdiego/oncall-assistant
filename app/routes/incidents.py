import json
import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, jsonify

from ..models.database import (
    create_incident, get_incident, list_incidents,
    update_incident_status, add_timeline_event, get_timeline, get_context
)
from ..services.aggregator import aggregate_context
from ..services.claude_service import generate_response_plan, generate_handoff_notes
from ..services import telegram

incidents_bp = Blueprint("incidents", __name__)

BASE_URL = os.getenv("BASE_URL", "")


@incidents_bp.route("/")
def index():
    incidents = list_incidents()
    return render_template("index.html", incidents=incidents)


@incidents_bp.route("/incidents/new", methods=["GET", "POST"])
def new_incident():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        severity = request.form.get("severity", "medium")

        if not title:
            return render_template("new_incident.html", error="Title is required.")

        incident_id = create_incident(title, description, severity, source="manual")
        add_timeline_event(incident_id, "OPENED", f"Incident created manually. Severity: {severity.upper()}")

        # Fire-and-forget context aggregation + AI plan
        _process_incident(incident_id, title, description, severity)

        telegram.notify_incident_opened(incident_id, title, severity, "manual", BASE_URL)

        return redirect(url_for("incidents.view_incident", incident_id=incident_id))

    return render_template("new_incident.html")


@incidents_bp.route("/incidents/<int:incident_id>")
def view_incident(incident_id):
    incident = get_incident(incident_id)
    if not incident:
        return "Incident not found", 404
    timeline = get_timeline(incident_id)
    context_entries = get_context(incident_id)
    return render_template(
        "incident_detail.html",
        incident=incident,
        timeline=timeline,
        context_entries=context_entries
    )


@incidents_bp.route("/incidents/<int:incident_id>/status", methods=["POST"])
def update_status(incident_id):
    incident = get_incident(incident_id)
    if not incident:
        return jsonify({"error": "Not found"}), 404

    new_status = request.form.get("status") or request.json.get("status")
    old_status = incident["status"]

    if new_status not in ("OPEN", "INVESTIGATING", "MITIGATED", "RESOLVED"):
        return jsonify({"error": "Invalid status"}), 400

    update_incident_status(incident_id, new_status)
    add_timeline_event(incident_id, "STATUS_CHANGE", f"{old_status} → {new_status}")

    # Calculate duration if resolving
    if new_status == "RESOLVED":
        try:
            opened = datetime.fromisoformat(incident["created_at"])
            duration = (datetime.utcnow() - opened).total_seconds() / 60
            telegram.notify_resolved(incident_id, incident["title"], duration, BASE_URL)
        except Exception:
            telegram.notify_status_change(incident_id, incident["title"], old_status, new_status, BASE_URL)
    else:
        telegram.notify_status_change(incident_id, incident["title"], old_status, new_status, BASE_URL)

    if request.is_json:
        return jsonify({"status": new_status})
    return redirect(url_for("incidents.view_incident", incident_id=incident_id))


@incidents_bp.route("/incidents/<int:incident_id>/handoff")
def handoff_notes(incident_id):
    incident = get_incident(incident_id)
    if not incident:
        return "Incident not found", 404

    timeline = get_timeline(incident_id)
    context_entries = get_context(incident_id)

    # Get most recent AI plan if available
    plan_text = ""
    for entry in timeline:
        if entry["event_type"] == "AI_PLAN":
            plan_text = entry["content"]

    notes = generate_handoff_notes(incident, timeline, plan_text)
    add_timeline_event(incident_id, "HANDOFF", f"Handoff notes generated:\n{notes}")

    return render_template(
        "handoff.html",
        incident=incident,
        notes=notes,
        timeline=timeline
    )


def _process_incident(incident_id, title, description, severity):
    """Aggregate context and generate AI plan. Called synchronously for simplicity."""
    try:
        context = aggregate_context(title, description)
        add_timeline_event(
            incident_id, "CONTEXT_AGGREGATED",
            f"Sources available: {context['sources_available']}/3 — "
            f"Runbook: {'✓' if context['rag_runbook']['available'] else '✗'} | "
            f"Past incidents: {'✓' if context['incident_logger']['available'] else '✗'} | "
            f"Infra health: {'✓' if context['infra_monitor']['available'] else '✗'}"
        )

        plan = generate_response_plan(title, description, severity, context)
        add_timeline_event(incident_id, "AI_PLAN", plan)

    except Exception as e:
        add_timeline_event(incident_id, "ERROR", f"Context aggregation failed: {e}")
