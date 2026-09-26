# Self-hosted service health dashboard

**Question.** A small, read-only dashboard for container status, resource use and HTTP uptime.

## Data
Docker Engine (through a read-only socket proxy) and HTTP health checks.

## Approach
1. FastAPI backend with the Docker SDK behind docker-socket-proxy
2. HTTP uptime checks and response times stored in SQLite
3. HTMX frontend with service cards and uptime lines
4. docker-compose install and a demo mode with synthetic data

## Deliverables
One-command install, demo mode, screenshots, v0.1.0 release.

## Status
Planned — part of a 30-project computational biology and scientific software portfolio.
