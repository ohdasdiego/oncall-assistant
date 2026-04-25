from flask import Blueprint, jsonify, request
from ..models.database import list_incidents, get_incident, get_timeline, get_context

api_bp = Blueprint("api", __name__)


@api_bp.route("/incidents")
def api_incidents():
    status = request.args.get("status")
    incidents = list_incidents(100)
    if status:
        incidents = [i for i in incidents if i["status"] == status.upper()]
    return jsonify(incidents)


@api_bp.route("/incidents/<int:incident_id>")
def api_incident(incident_id):
    incident = get_incident(incident_id)
    if not incident:
        return jsonify({"error": "Not found"}), 404
    timeline = get_timeline(incident_id)
    context = get_context(incident_id)
    return jsonify({
        "incident": incident,
        "timeline": timeline,
        "context": context
    })


@api_bp.route("/health")
def health():
    return jsonify({"status": "ok", "service": "oncall-assistant"})
