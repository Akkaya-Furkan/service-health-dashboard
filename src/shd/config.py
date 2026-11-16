from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .models import Target


@dataclass
class Settings:
    demo: bool = False
    db_path: str = "data/shd.sqlite"
    interval_s: float = 30.0
    timeout_s: float = 5.0
    docker_host: str | None = None  # tcp://docker-socket-proxy:2375
    targets: list[Target] = field(default_factory=list)
    retention_days: int = 30
    api_token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.retention_days < 0:
            raise ValueError("SHD_RETENTION_DAYS must be non-negative (0 disables pruning)")


def parse_targets(spec: str) -> list[Target]:
    """Parse 'name=url,name2=url2' or a JSON list of {name,url,expected_status}."""
    spec = spec.strip()
    if not spec:
        return []
    if spec.startswith("["):
        return [
            Target(d["name"], d["url"], int(d.get("expected_status", 200)))
            for d in json.loads(spec)
        ]
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, url = part.partition("=")
        if not sep or not name.strip() or not url.strip():
            raise ValueError(f"bad target spec {part!r}, expected name=url")
        out.append(Target(name.strip(), url.strip()))
    return out


def validate_docker_host(host: str) -> str:
    """Only a TCP socket-proxy endpoint is accepted; the raw docker socket is refused."""
    scheme = urlparse(host).scheme
    if scheme not in ("tcp", "http"):
        raise ValueError(
            "SHD_DOCKER_HOST must be a tcp:// or http:// docker-socket-proxy "
            f"endpoint, got {host!r} (the raw docker socket is never used)"
        )
    return host


def load_settings(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    s = Settings(
        demo=env.get("SHD_DEMO", "").lower() in ("1", "true", "yes"),
        db_path=env.get("SHD_DB", "data/shd.sqlite"),
        interval_s=float(env.get("SHD_INTERVAL", "30")),
        timeout_s=float(env.get("SHD_TIMEOUT", "5")),
        retention_days=int(env.get("SHD_RETENTION_DAYS", "30")),
        api_token=env.get("SHD_API_TOKEN") or None,
    )
    s.targets = parse_targets(env.get("SHD_TARGETS", ""))
    host = env.get("SHD_DOCKER_HOST")
    if host and not s.demo:
        s.docker_host = validate_docker_host(host)
    return s
