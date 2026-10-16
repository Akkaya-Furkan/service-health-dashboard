from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    expected_status: int = 200


@dataclass(frozen=True)
class CheckResult:
    target: str
    checked_at: int  # unix seconds
    ok: bool
    status_code: int | None
    response_ms: float | None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ContainerInfo:
    id: str
    name: str
    image: str
    state: str  # running, exited, ...
    status: str  # human readable ("Up 3 hours")
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)
