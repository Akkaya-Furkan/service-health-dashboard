from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import httpx

from .models import CheckResult, Target

log = logging.getLogger("shd.checker")


async def http_probe(target: Target, client: httpx.AsyncClient) -> CheckResult:
    start = time.perf_counter()
    ts = int(time.time())
    try:
        resp = await client.get(target.url)
        ms = round((time.perf_counter() - start) * 1000, 1)
        ok = resp.status_code == target.expected_status
        return CheckResult(target.name, ts, ok, resp.status_code, ms, None if ok else f"HTTP {resp.status_code}")
    except httpx.HTTPError as e:
        return CheckResult(target.name, ts, False, None, None, type(e).__name__)


class Checker:
    """Periodically probes targets and stores results. `probe` may be a sync callable
    (demo) or None for real HTTP checks via httpx (a transport can be injected for tests)."""

    def __init__(self, targets, store, interval_s=30.0, timeout_s=5.0,
                 probe: Callable[[Target], CheckResult] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, on_result=None,
                 retention_days: int = 30):
        self.targets = list(targets)
        self.store = store
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.probe = probe
        self.transport = transport
        self.on_result = on_result
        self.retention_days = retention_days
        self._last_prune: float | None = None
        self._task: asyncio.Task | None = None

    def prune_if_due(self) -> int:
        """Prune at startup, then attempt at most once per hour, even after failure."""
        if self.retention_days == 0:
            return 0
        now = time.monotonic()
        if self._last_prune is not None and now - self._last_prune < 3600:
            return 0
        self._last_prune = now
        cutoff = int(time.time()) - self.retention_days * 86400
        return self.store.prune_before(cutoff)

    async def run_once(self) -> list[CheckResult]:
        if self.probe is not None:
            results = [self.probe(t) for t in self.targets]
        else:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport,
                                         follow_redirects=True) as c:
                results = await asyncio.gather(*(http_probe(t, c) for t in self.targets))
        for r in results:
            self.store.add(r)
            if self.on_result:
                self.on_result(r)
        return list(results)

    async def _loop(self):
        while True:
            try:
                self.prune_if_due()
            except Exception:
                log.exception("retention pruning failed")
            try:
                await self.run_once()
            except Exception:
                log.exception("check round failed")
            await asyncio.sleep(self.interval_s)

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
