"""
Claude service — synthesizes multi-source context into an actionable response plan.
"""

import os
import logging
import anthropic

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """You are an expert on-call SRE assistant embedded in an operations platform called ADOStack.

Your job is to synthesize information from multiple monitoring and logging sources into a clear, actionable incident response plan for the on-call engineer.

You receive:
- Incident title and description
- Relevant runbook steps (from RAG Runbook Assistant, may be unavailable)
- Similar past incidents (from AI Incident Logger, may be unavailable)
- Current infrastructure health (from AI Infra Monitor, may be unavailable)

Your output must follow this exact structure:

## Severity Assessment
[One sentence on severity and blast radius]

## Likely Root Cause
[1-3 bullet points on probable causes based on available context]

## Immediate Actions
[Numbered steps, concrete and specific. Reference runbook steps if available.]

## Watch Points
[2-3 things to monitor during remediation]

## Escalation Trigger
[Single sentence: when to escalate beyond self-remediation]

Be direct and technical. The engineer is under time pressure. No preamble."""


def build_context_block(context: dict) -> str:
    parts = []

    runbook = context.get("rag_runbook", {})
    if runbook.get("available") and runbook.get("content"):
        parts.append(f"### Runbook Context\n{runbook['content']}")
    else:
        parts.append("### Runbook Context\n[Unavailable — RAG service offline or no matching runbook]")

    past = context.get("incident_logger", {})
    if past.get("available") and past.get("content"):
        parts.append(f"### Past Similar Incidents\n{past['content']}")
    else:
        parts.append("### Past Similar Incidents\n[Unavailable — Incident Logger offline or no matches]")

    health = context.get("infra_monitor", {})
    if health.get("available") and health.get("content"):
        parts.append(f"### Current Infrastructure Health\n{health['content']}")
    else:
        parts.append("### Current Infrastructure Health\n[Unavailable — Infra Monitor offline]")

    return "\n\n".join(parts)


def generate_response_plan(title: str, description: str, severity: str, context: dict) -> str:
    """Call Claude to synthesize a response plan from aggregated context."""
    context_block = build_context_block(context)
    sources_available = context.get("sources_available", 0)

    user_message = f"""## Incident
**Title:** {title}
**Severity:** {severity.upper()}
**Description:** {description}

---

## Context from ADOStack Sources ({sources_available}/3 available)

{context_block}

---

Generate an incident response plan for the on-call engineer."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"⚠️ AI response unavailable: {e}\n\nFall back to manual runbook review."


def generate_handoff_notes(incident: dict, timeline: list, response_plan: str) -> str:
    """Generate shift handoff notes for an incident."""
    timeline_text = "\n".join(
        f"[{e['created_at']}] {e['event_type']}: {e['content'][:200]}"
        for e in timeline
    )

    user_message = f"""Generate concise shift handoff notes for this incident.

## Incident
Title: {incident['title']}
Severity: {incident.get('severity', 'unknown').upper()}
Status: {incident['status']}
Opened: {incident['created_at']}
{'Resolved: ' + incident['resolved_at'] if incident.get('resolved_at') else 'STILL OPEN'}

## Timeline
{timeline_text or 'No timeline events recorded.'}

## AI Response Plan (summary)
{response_plan[:500]}...

Write 3-5 bullet points suitable for a shift handoff. Include: what happened, what was done, current state, and what the next engineer should watch for."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude handoff error: {e}")
        return f"Handoff notes unavailable: {e}"
