# On-Call Assistant

An AI-powered incident response tool that aggregates context from across ADOStack and generates actionable response plans the moment an alert fires.

> Alert fires → context aggregated from 3 sources → Claude synthesizes a response plan → incident tracked through resolution.

---

## Live Demo

 **[oncall.ado-runner.com](https://oncall.ado-runner.com)**

---

## What It Does

When an incident is opened — via the web UI, a webhook, or a Telegram bot command — the On-Call Assistant immediately pulls context from three ADOStack sources in parallel:

- **RAG Runbook Assistant** — retrieves relevant runbook steps for the incident type
- **AI Incident Logger** — surfaces similar past incidents and their resolutions
- **AI Infra Monitor** — snapshots current system health

That context is fed to Claude, which synthesizes a structured response plan: severity assessment, likely root cause, immediate action steps, watch points, and an escalation trigger. The incident is then tracked through a full lifecycle with a timestamped timeline of every event.

**Incident intake paths:**
- **Web UI** — manual incident creation at the dashboard
- **Webhook** — `POST /webhooks/infra-monitor` receives alerts from AI Infra Monitor automatically
- **Telegram bot** — `/oncall [high] disk full on prod-db-01`

**Lifecycle tracking:**
- Status flow: `OPEN → INVESTIGATING → MITIGATED → RESOLVED`
- Full timeline: every context fetch, AI plan, status change, and note logged
- Telegram notifications at open, every status change, and resolution (with duration)
- Shift handoff notes — AI-generated summary for the next on-call engineer

**Graceful degradation:**
- Works with partial ADOStack availability
- Clearly marks which sources were reachable vs. offline per incident

---

## Sample Output

### AI Response Plan

```
## Severity Assessment
HIGH — elevated CPU sustained above threshold on primary host; risk of cascading
service degradation if not addressed within 15 minutes.

## Likely Root Cause
- Runaway process or unbounded cron job consuming CPU cycles
- Sudden traffic spike without load shedding
- Memory pressure forcing excessive swapping

## Immediate Actions
1. Run `top` or `htop` to identify the highest CPU consumer
2. Check cron logs: `journalctl -u cron --since "10 min ago"`
3. If a known process: restart via `sudo systemctl restart <service>`
4. Verify infra-monitor metrics to confirm trend direction

## Watch Points
- CPU trend over next 3 readings (rising vs. stabilizing)
- Memory pressure — OOM risk if swap is active
- Network I/O — rule out a traffic-driven cause

## Escalation Trigger
Escalate if CPU remains above 85% after two remediation attempts or if a second
service shows degradation within the same window.
```

### Telegram Alert

```
 INCIDENT OPENED [HIGH]
#4 — High CPU on claw-gateway1
Source: `infra_monitor`
 View Incident
```

### API Response — `/api/incidents`

```json
[
 {
 "id": 4,
 "title": "High CPU on claw-gateway1",
 "severity": "high",
 "status": "INVESTIGATING",
 "source": "infra_monitor",
 "created_at": "2026-04-25T17:00:00",
 "updated_at": "2026-04-25T17:04:22"
 }
]
```

---

## Architecture

```
Alert Intake
├── Flask UI → manual incident creation
├── POST /webhooks/infra-monitor → AI Infra Monitor alerts (HMAC-signed)
└── Telegram /oncall <text> → bot command

 ↓

Context Aggregator (parallel fetch, 8s timeout per source)
├── runbooks.ado-runner.com → relevant runbook steps (RAG)
├── localhost:5001/api/incidents → similar past incidents
└── localhost:5000/api/health → current system health

 ↓

Claude (claude-haiku) → structured response plan

 ↓

SQLite — incidents + timeline_events + context_cache

 ↓

Telegram → notification at each lifecycle event
```

```
Browser ──► Cloudflare (SSL/DDoS) ──► Nginx (CF IPs only) ──► Gunicorn:5005
 │
 Claude API (Anthropic)
 ADOStack services
```

**Key design decisions:**
- Gunicorn binds to `127.0.0.1:5005` only — never exposed directly
- Nginx restricts inbound to Cloudflare IP ranges — direct IP access blocked
- Context aggregation is parallel with per-source timeouts — one offline service never blocks the others
- SQLite for incident storage — simple, zero-dependency, auditable
- Webhook HMAC verification — Infra Monitor alerts validated with shared secret

---

## Tech Stack

| Layer | Technology |
|---|---|
| AI response planning | Anthropic Claude API (`claude-haiku-4-5`) |
| Context aggregation | Python 3, requests (parallel fetch) |
| API / Web UI | Flask 3, Gunicorn |
| Database | SQLite (incidents, timeline, context cache) |
| Telegram integration | Bot API (webhook + notifications) |
| Reverse proxy | Nginx (Cloudflare IP allowlist) |
| CDN / SSL | Cloudflare |
| Process management | systemd |
| OS / Hosting | Ubuntu 24.04 VPS |

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Incident dashboard UI |
| `GET /incidents/new` | GET | New incident form |
| `POST /incidents/new` | POST | Create incident manually |
| `GET /incidents/<id>` | GET | Incident detail + timeline |
| `POST /incidents/<id>/status` | POST | Update incident status |
| `GET /incidents/<id>/handoff` | GET | Generate shift handoff notes |
| `GET /api/incidents` | GET | List all incidents (filter: `?status=OPEN`) |
| `GET /api/incidents/<id>` | GET | Incident detail + timeline + context |
| `GET /api/health` | GET | Service liveness probe |
| `POST /webhooks/infra-monitor` | POST | Receive Infra Monitor alerts (HMAC) |
| `POST /webhooks/telegram` | POST | Telegram bot webhook |

---

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/ohdasdiego/oncall-assistant.git
cd oncall-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

### 3. Run locally

```bash
python wsgi.py
# → Dashboard at http://localhost:5005
```

### 4. Deploy as a systemd service

```bash
sudo cp oncall-assistant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable oncall-assistant
sudo systemctl start oncall-assistant
```

### 5. Nginx reverse proxy

```bash
sudo cp nginx.conf /etc/nginx/sites-available/oncall-assistant
sudo ln -sf /etc/nginx/sites-available/oncall-assistant /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 6. Telegram bot setup

Create a bot via [@BotFather](https://t.me/BotFather), get your token, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then register the webhook:

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
 -d "url=https://oncall.ado-runner.com/webhooks/telegram"
```

**Bot commands:**
```
/oncall <description> Open a new incident (medium severity)
/oncall [high] disk full Open with explicit severity
/status List all open incidents
```

---

## Cost Analysis

All AI calls use `claude-haiku-4-5` pricing ($1.00/M input, $5.00/M output):

| Scenario | Calls/month | Input tokens/mo | Output tokens/mo | Est. cost/mo |
|---|---|---|---|---|
| 5 incidents/day | 150 | ~225K | ~150K | ~$0.98 |
| 20 incidents/day | 600 | ~900K | ~600K | ~$3.90 |
| 50 incidents/day | 1,500 | ~2.25M | ~1.5M | ~$9.75 |

> **Per-call breakdown:** ~1,500 input tokens (system prompt + incident + 3-source context) + ~1,024 output tokens (response plan) ≈ $0.0065/incident. Handoff notes add ~$0.001/generation.

**Cost levers:**
- Reduce `CONTEXT_TIMEOUT_SECONDS` to limit fetch time per source
- Switch to a lighter model for handoff notes vs. response plans
- Cache context per incident — already implemented via `context_cache` table

---

## Skills Demonstrated

This project is intentionally production-aligned — not a local toy:

- **Agentic AI design** — multi-source context aggregation feeding structured AI reasoning with graceful degradation
- **Incident management** — full lifecycle tracking, timeline events, status workflows, shift handoffs
- **API integration** — parallel HTTP fetches with per-source timeouts, HMAC webhook verification, Telegram Bot API
- **Systems ops** — systemd service management, Nginx reverse proxy, Cloudflare IP allowlisting
- **Security hygiene** — secrets in `.env` (gitignored), loopback-only binding, CF-only nginx, HMAC-signed webhooks
- **Database design** — normalized SQLite schema (incidents, timeline, context cache) with proper foreign keys
- **Service architecture** — part of a 5-service ADOStack platform; designed for inter-service communication and graceful partial availability

---

## ADOStack

| Tool | Role |
|------|------|
| [AI Infra Monitor](https://monitor.ado-runner.com) | Metric collection + AI health analysis |
| [AI Incident Logger](https://incidents.ado-runner.com) | Threshold alerting + incident records |
| [RAG Runbook Assistant](https://runbooks.ado-runner.com) | Vector search over IT runbooks |
| # | Project | Live | Role |
|---|---------|------|------|
| 1 | [AI Infra Monitor](https://github.com/ohdasdiego/ai-infra-monitor) | [monitor.ado-runner.com](https://monitor.ado-runner.com) | Metric collection + AI health analysis |
| 2 | [AI Incident Logger](https://github.com/ohdasdiego/ai-incident-logger) | [incidents.ado-runner.com](https://incidents.ado-runner.com) | Threshold alerting + incident records |
| 3 | [Code Auditor](https://github.com/ohdasdiego/code-auditor) | CLI | AI-powered code review |
| 4 | [RAG Runbook Assistant](https://github.com/ohdasdiego/rag-runbook-assistant) | [runbooks.ado-runner.com](https://runbooks.ado-runner.com) | Vector search over IT runbooks |
| 5 | [K8s Event Summarizer](https://github.com/ohdasdiego/k8s-event-summarizer) | [k8s.ado-runner.com](https://k8s.ado-runner.com) | Kubernetes cluster health digests |
| 6 | [AI Incident Orchestrator](https://github.com/ohdasdiego/ai-incident-orchestrator) | [orchestrator.ado-runner.com](https://orchestrator.ado-runner.com) | Multi-agent triage pipeline |
| **7** | **On-Call Assistant** | **[oncall.ado-runner.com](https://oncall.ado-runner.com)** | **← You are here** |

---

## Roadmap

- [x] Webhook integration — AI Infra Monitor auto-posts to `/webhooks/infra-monitor` on threshold breach, auto-advancing status to INVESTIGATING
- [x] Escalation routing — High/Critical get Telegram action buttons; Low/Medium auto-handled
- [ ] Auto-resolve confirmation — Telegram prompt when Infra Monitor reports anomaly cleared
- [ ] Context refresh — re-pull ADOStack sources mid-incident as conditions change
- [ ] Escalation tracking — log and notify when escalation trigger is met
- [ ] Incident search — filter and search past incidents by keyword, severity, or source
- [ ] Multi-user support — basic auth or token-based access for write endpoints

---

## Author

**Diego Perez** · [github.com/ohdasdiego](https://github.com/ohdasdiego/oncall-assistant)
