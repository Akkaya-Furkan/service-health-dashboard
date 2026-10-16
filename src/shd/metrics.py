from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST

from .docker_client import ContainerSource
from .models import CheckResult


class Metrics:
    def __init__(self, source: ContainerSource):
        self.source = source
        self.registry = CollectorRegistry()
        self.up = Gauge("shd_target_up", "1 if last HTTP check succeeded", ["target"], registry=self.registry)
        self.resp = Gauge("shd_target_response_ms", "Last HTTP response time in ms", ["target"], registry=self.registry)
        self.c_up = Gauge("shd_container_running", "1 if container is running", ["name"], registry=self.registry)
        self.c_cpu = Gauge("shd_container_cpu_percent", "Container CPU percent", ["name"], registry=self.registry)
        self.c_mem = Gauge("shd_container_memory_bytes", "Container memory bytes", ["name"], registry=self.registry)

    def record(self, r: CheckResult) -> None:
        self.up.labels(r.target).set(1 if r.ok else 0)
        if r.response_ms is not None:
            self.resp.labels(r.target).set(r.response_ms)

    def render(self) -> tuple[bytes, str]:
        try:
            for c in self.source.list_containers():
                self.c_up.labels(c.name).set(1 if c.state == "running" else 0)
                if c.cpu_percent is not None:
                    self.c_cpu.labels(c.name).set(c.cpu_percent)
                if c.memory_bytes is not None:
                    self.c_mem.labels(c.name).set(c.memory_bytes)
        except Exception:
            pass  # container info is best-effort; target gauges still render
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
