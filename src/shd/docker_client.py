"""Container sources. All implementations are read-only by construction:
the interface exposes only list_containers()."""
from __future__ import annotations

from abc import ABC, abstractmethod

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

    def __init__(self, base_url: str, timeout: float = 5.0, client=None):
        self._base_url = validate_docker_host(base_url)
        self._timeout = timeout
        self._client = client

    def _get_client(self):
        # Created lazily: docker.DockerClient contacts the daemon on construction,
        # which would crash startup when the proxy is down.
        if self._client is None:
            import docker

            self._client = docker.DockerClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    def list_containers(self) -> list[ContainerInfo]:
        out = []
        for c in self._get_client().containers.list(all=True):
            cpu = mem = limit = None
            if c.status == "running":
                try:
                    st = c.stats(stream=False)
                    cpu = cpu_percent(st)
                    mem, limit = memory_usage(st)
                except Exception:  # stats are best-effort
                    pass
            tags = c.image.tags if c.image is not None else []
            out.append(ContainerInfo(
                id=c.short_id, name=c.name, image=tags[0] if tags else "?",
                state=c.status, status=c.attrs.get("Status", c.status) if hasattr(c, "attrs") else c.status,
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
