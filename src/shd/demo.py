"""Synthetic data: no network, no docker socket. Deterministic per (seed, time)."""
from __future__ import annotations

import math
import random
import time

from .docker_client import ContainerSource
from .models import CheckResult, ContainerInfo, Target

DEMO_TARGETS = [
    Target("nextcloud", "http://demo.invalid/nextcloud"),
    Target("jellyfin", "http://demo.invalid/jellyfin"),
    Target("grafana", "http://demo.invalid/grafana"),
    Target("pihole", "http://demo.invalid/pihole"),
]

_BASE_MS = {"nextcloud": 180, "jellyfin": 90, "grafana": 60, "pihole": 25}
_DOWN = {"pihole": (0.0, 0.06), "nextcloud": (0.5, 0.02)}  # (phase, probability)


def synthetic_result(target: Target, ts: int, seed: int = 42) -> CheckResult:
    rng = random.Random(f"{seed}:{target.name}:{ts}")
    p = _DOWN.get(target.name, (0, 0.01))[1]
    if rng.random() < p:
        return CheckResult(target.name, ts, False, 503, None, "synthetic outage")
    base = _BASE_MS.get(target.name, 100)
    wave = 1 + 0.25 * math.sin(ts / 600.0)
    ms = round(base * wave * rng.uniform(0.8, 1.3), 1)
    return CheckResult(target.name, ts, True, 200, ms, None)


def backfill(store, targets, hours: int = 24, step_s: int = 300, now: int | None = None) -> int:
    now = int(now if now is not None else time.time())
    now -= now % step_s
    n = 0
    for t in targets:
        for ts in range(now - hours * 3600, now + 1, step_s):
            n += store.add(synthetic_result(t, ts))
    return n


class SyntheticProbe:
    def __call__(self, target: Target) -> CheckResult:
        return synthetic_result(target, int(time.time()))


_CONTAINERS = [
    ("nextcloud", "nextcloud:29", "running", 2.4, 310),
    ("nextcloud-db", "postgres:16", "running", 1.1, 180),
    ("jellyfin", "jellyfin/jellyfin:10", "running", 6.0, 620),
    ("grafana", "grafana/grafana:11", "running", 0.8, 140),
    ("pihole", "pihole/pihole:2024", "running", 0.3, 70),
    ("old-backup-job", "restic/restic:0.17", "exited", None, None),
]


class DemoSource(ContainerSource):
    def list_containers(self) -> list[ContainerInfo]:
        t = time.time()
        out = []
        for i, (name, image, state, cpu, mem) in enumerate(_CONTAINERS):
            if state == "running":
                cpu_v = round(max(cpu * (1 + 0.3 * math.sin(t / 30 + i)), 0), 2)
                mem_v = int(mem * 1024 * 1024 * (1 + 0.05 * math.sin(t / 90 + i)))
                status = "Up 3 days"
            else:
                cpu_v = mem_v = None
                status = "Exited (0) 2 days ago"
            out.append(ContainerInfo(
                id=f"demo{i:08x}", name=name, image=image, state=state, status=status,
                cpu_percent=cpu_v, memory_bytes=mem_v,
                memory_limit_bytes=2 * 1024**3 if state == "running" else None,
            ))
        return out
