"""Container sources. All implementations are read-only by construction:
the interface exposes only list_containers()."""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from .config import validate_docker_host
from .models import ContainerInfo


class ContainerSource(ABC):
    @abstractmethod
    def list_containers(self) -> list[ContainerInfo]:
        """Return all containers with current state and (if running) CPU/memory."""


def cpu_percent(stats: dict) -> float | None:
    """Same formula as `docker stats`."""
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
    except (KeyError, TypeError):
        return None
    if sys_delta <= 0 or cpu_delta < 0:
        return 0.0
    return round(cpu_delta / sys_delta * ncpu * 100.0, 2)


def memory_usage(stats: dict) -> tuple[int | None, int | None]:
    try:
        m = stats["memory_stats"]
        usage = m["usage"]
        cache = m.get("stats", {}).get("inactive_file", m.get("stats", {}).get("cache", 0))
        return max(usage - cache, 0), m.get("limit")
    except (KeyError, TypeError):
        return None, None


class DockerProxySource(ContainerSource):
    """Talks to a docker-socket-proxy over TCP. Refuses unix sockets."""

    def __init__(self, base_url: str, timeout: float = 5.0, client=None,
                 workers: int = 16, cache_seconds: float = 10.0):
        self._base_url = validate_docker_host(base_url)
        self._timeout = timeout
        self._client = client
        self._workers = workers
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached: tuple[float, list[ContainerInfo]] | None = None

    def _get_client(self):
        # Created lazily: docker.DockerClient contacts the daemon on construction,
        # which would crash startup when the proxy is down.
        if self._client is None:
            import docker

            self._client = docker.DockerClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    def list_containers(self) -> list[ContainerInfo]:
        # One refresh at a time, shared for a few seconds: `stats(stream=False)` takes ~1-2 s per
        # container, so concurrent UI/Prometheus polls would otherwise pile up (29 containers took
        # 58 s serially on the first real deploy).
        with self._lock:
            now = time.monotonic()
            if self._cached and now - self._cached[0] < self._cache_seconds:
                return list(self._cached[1])
            out = self._collect()
            self._cached = (time.monotonic(), out)
            return list(out)

    def _collect(self) -> list[ContainerInfo]:
        containers = self._get_client().containers.list(all=True)

        def sample(c):
            if c.status != "running":
                return None, None, None
            try:
                st = c.stats(stream=False)
                mem, limit = memory_usage(st)
                return cpu_percent(st), mem, limit
            except Exception:  # stats are best-effort
                return None, None, None

        with ThreadPoolExecutor(max_workers=max(1, self._workers)) as pool:
            samples = list(pool.map(sample, containers))
        out = []
        for c, (cpu, mem, limit) in zip(containers, samples):
            # Image name comes from the container inspect that list() already did. `c.image`
            # would call GET /images/<id>, which the read-only socket proxy (IMAGES=0) blocks.
            attrs = getattr(c, "attrs", {}) or {}
            image = (attrs.get("Config") or {}).get("Image") or attrs.get("Image") or "?"
            out.append(ContainerInfo(
                id=c.short_id, name=c.name, image=image,
                state=c.status, status=attrs.get("Status", c.status),
                cpu_percent=cpu, memory_bytes=mem, memory_limit_bytes=limit,
            ))
        return out


class StaticSource(ContainerSource):
    """Fixed list; used in tests."""

    def __init__(self, containers: list[ContainerInfo]):
        self._c = list(containers)

    def list_containers(self) -> list[ContainerInfo]:
        return list(self._c)


class NullSource(ContainerSource):
    def list_containers(self) -> list[ContainerInfo]:
        return []
