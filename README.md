# Self-hosted service health dashboard

**Question.** A small, read-only dashboard for container status, resource use and HTTP uptime.

Status: **backend v0.1.0**. The HTMX frontend is the next task. Nothing here has been connected to a real homelab.

## What exists
- FastAPI backend (`src/shd/`)
  - `GET /health` — liveness, version, mode
  - `GET /api/services` — containers with state, CPU %, memory
  - `GET /api/uptime?hours=24&limit=200` — per-target last result, uptime %, response-time stats, history
  - `GET /metrics` — Prometheus text format (`shd_target_up`, `shd_target_response_ms`, `shd_container_*`)
- Checker: configurable HTTP targets, periodic async checks, results in SQLite (WAL; `(target, checked_at)` is the primary key, so duplicates are ignored).
- Docker access only through a **read-only docker-socket-proxy** over TCP. The `ContainerSource` interface only offers `list_containers()`; `unix://` hosts are refused by config validation.
- **Demo mode** (`--demo` / `SHD_DEMO=1`): synthetic containers and 24 h of synthetic uptime history; no Docker, no sockets, no outbound HTTP.
- `docker-compose.yml`: app + `tecnativa/docker-socket-proxy` (only `CONTAINERS=1`, `POST=0`), app bound to 127.0.0.1.

## Run (demo, nothing real is touched)
```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/shd --demo --port 8000
curl localhost:8000/health
curl localhost:8000/api/services
curl 'localhost:8000/api/uptime?limit=3'
curl localhost:8000/metrics
```
A captured run is in [demo_verification.txt](demo_verification.txt).

## Configuration (env)
| Variable | Default | Meaning |
|---|---|---|
| `SHD_DEMO` | `0` | synthetic data only |
| `SHD_TARGETS` | empty | `name=url,name2=url2` or JSON list `[{"name","url","expected_status"}]` |
| `SHD_DOCKER_HOST` | unset | `tcp://docker-socket-proxy:2375` (must be tcp/http) |
| `SHD_DB` | `data/shd.sqlite` | SQLite path |
| `SHD_INTERVAL` / `SHD_TIMEOUT` | `30` / `5` | seconds |

## Tests
```bash
.venv/bin/pytest -q
```
Offline: a fake Docker client, `httpx.MockTransport`, and a fixture that fails on any real socket connect.

## Limitations
- CPU % uses the `docker stats` formula from a single sample; stats are best-effort and absent for stopped containers.
- No auth on the API; keep it on localhost or a trusted network.
- `docker-compose.yml` is written but **not run** (no Docker used in development); the Dockerfile is likewise unbuilt.
- History is not pruned yet.

## Next steps
1. HTMX frontend (service cards, uptime lines), screenshots.
2. **Connecting to the real homelab is a separate decision by the owner and has not been done.** It means pointing `SHD_DOCKER_HOST` at a proxy on the actual Docker host and listing real `SHD_TARGETS`.
3. Retention/pruning, v0.1.0 release.

Part of a 30-project computational biology and scientific software portfolio.
