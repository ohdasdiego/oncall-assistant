"""
Context Aggregator — pulls from all ADOStack sources:
  1. RAG Runbook Assistant (runbooks.ado-runner.com)
  2. AI Incident Logger (past similar incidents)
  3. AI Infra Monitor (current system health)
"""

import os
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# --- Source endpoints (configure via env) ---
RAG_RUNBOOK_URL = os.getenv("RAG_RUNBOOK_URL", "https://runbooks.ado-runner.com/api/query")
INCIDENT_LOGGER_URL = os.getenv("INCIDENT_LOGGER_URL", "http://localhost:5001/api/incidents")
INFRA_MONITOR_URL = os.getenv("INFRA_MONITOR_URL", "http://localhost:5000/api/metrics")

REQUEST_TIMEOUT = int(os.getenv("CONTEXT_TIMEOUT_SECONDS", "8"))


def fetch_runbook_context(query: str) -> dict:
    """Query RAG Runbook Assistant for relevant runbook steps."""
    try:
        resp = requests.post(
            RAG_RUNBOOK_URL,
            json={"question": query},
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "source": "rag_runbook",
            "available": True,
            "content": data.get("answer") or data.get("response") or str(data),
            "fetched_at": datetime.utcnow().isoformat()
        }
    except requests.exceptions.ConnectionError:
        logger.warning("RAG Runbook: connection refused (service may be down)")
    except requests.exceptions.Timeout:
        logger.warning("RAG Runbook: request timed out")
    except Exception as e:
        logger.warning(f"RAG Runbook: {e}")
    return {"source": "rag_runbook", "available": False, "content": None}


def fetch_past_incidents(query: str, limit: int = 5) -> dict:
    """Query AI Incident Logger for similar past incidents."""
    try:
        resp = requests.get(
            INCIDENT_LOGGER_URL,
            params={"search": query, "limit": limit},
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        # Logger returns {"incidents": [...], "total": N} or a plain list
        incidents = data.get("incidents", data) if isinstance(data, dict) else data
        if not incidents:
            return {"source": "incident_logger", "available": True, "content": "No similar past incidents found."}
        summary = "\n".join(
            f"- [{i.get('severity','?').upper()}] {i.get('title','?')} — {i.get('resolution') or i.get('status','No resolution logged.')}"
            for i in incidents[:limit]
        )
        return {
            "source": "incident_logger",
            "available": True,
            "content": summary,
            "fetched_at": datetime.utcnow().isoformat()
        }
    except requests.exceptions.ConnectionError:
        logger.warning("Incident Logger: connection refused")
    except requests.exceptions.Timeout:
        logger.warning("Incident Logger: request timed out")
    except Exception as e:
        logger.warning(f"Incident Logger: {e}")
    return {"source": "incident_logger", "available": False, "content": None}


def fetch_infra_health() -> dict:
    """Get current system health snapshot from AI Infra Monitor."""
    try:
        resp = requests.get(INFRA_MONITOR_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # Extract key health signals from Infra Monitor /api/metrics response
        ai = data.get("ai_analysis", {})
        metrics = data.get("metrics", {})
        lines = []
        if ai.get("headline"):
            lines.append(f"Status: {ai.get('status','?').upper()} — {ai['headline']}")
        # API returns {latest: {cpu_percent, memory: {percent}, disks: [{percent}]}}
        latest = data.get("latest") or metrics
        if latest:
            cpu = latest.get("cpu_percent")
            mem = latest.get("memory", {}).get("percent") if isinstance(latest.get("memory"), dict) else latest.get("memory_percent")
            disks = latest.get("disks", [])
            disk = disks[0].get("percent") if disks else latest.get("disk_percent")
            if cpu is not None: lines.append(f"CPU: {cpu}%")
            if mem is not None: lines.append(f"Memory: {mem}%")
            if disk is not None: lines.append(f"Disk: {disk}%")
        anomalies = ai.get("anomalies", [])
        if anomalies:
            lines.append(f"Anomalies: {', '.join(anomalies)}")
        content = "\n".join(lines) if lines else json.dumps(data, indent=2)
        return {
            "source": "infra_monitor",
            "available": True,
            "content": content,
            "fetched_at": datetime.utcnow().isoformat()
        }
    except requests.exceptions.ConnectionError:
        logger.warning("Infra Monitor: connection refused")
    except requests.exceptions.Timeout:
        logger.warning("Infra Monitor: request timed out")
    except Exception as e:
        logger.warning(f"Infra Monitor: {e}")
    return {"source": "infra_monitor", "available": False, "content": None}


def aggregate_context(title: str, description: str) -> dict:
    """
    Aggregate context from all sources for a given incident.
    Returns dict with results per source + availability flags.
    """
    query = f"{title}. {description}".strip()

    runbook = fetch_runbook_context(query)
    past = fetch_past_incidents(query)
    health = fetch_infra_health()

    sources_available = sum([
        runbook.get("available", False),
        past.get("available", False),
        health.get("available", False)
    ])

    return {
        "query": query,
        "sources_available": sources_available,
        "rag_runbook": runbook,
        "incident_logger": past,
        "infra_monitor": health,
    }
