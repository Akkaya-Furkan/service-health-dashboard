from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from . import __version__
from .checker import Checker
from .config import Settings
from .demo import DEMO_TARGETS, DemoSource, SyntheticProbe, backfill
from .docker_client import ContainerSource, DockerProxySource, NullSource
from .metrics import Metrics
from .store import Store


def create_app(settings: Settings, source: ContainerSource | None = None,
               probe=None, transport=None, start_checker: bool = True) -> FastAPI:
    """Build the app. Injection points (source/probe/transport) keep tests offline."""
    if settings.demo:
        db = ":memory:" if settings.db_path == "data/shd.sqlite" else settings.db_path
        targets = settings.targets or DEMO_TARGETS
        source = source or DemoSource()
        probe = probe or SyntheticProbe()
    else:
        db = settings.db_path
        targets = settings.targets
        if source is None:
            source = DockerProxySource(settings.docker_host, settings.timeout_s) if settings.docker_host else NullSource()
    if db != ":memory:":
        import os
        os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)

    store = Store(db)
    metrics = Metrics(source)
    checker = Checker(targets, store, settings.interval_s, settings.timeout_s,
                      probe=probe, transport=transport, on_result=metrics.record)

    @asynccontextmanager
    async def lifespan(app):
        if settings.demo:
            backfill(store, targets)
        await checker.run_once()
        if start_checker:
            checker.start()
        yield
        await checker.stop()
        store.close()

    app = FastAPI(title="Service health dashboard", version=__version__, lifespan=lifespan)
    app.state.store, app.state.checker, app.state.metrics = store, checker, metrics

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "demo": settings.demo,
                "targets": len(targets), "stored_checks": store.count()}

    @app.get("/api/services")
    def services():
        try:
            cs = source.list_containers()
        except Exception as e:
            raise HTTPException(502, f"container source unavailable: {type(e).__name__}")
        return {"demo": settings.demo, "count": len(cs), "containers": [c.to_dict() for c in cs]}

    @app.get("/api/uptime")
    def uptime(hours: int = 24, limit: int = 200):
        since = int(time.time()) - hours * 3600
        out = []
        for t in targets:
            last = store.latest(t.name)
            out.append({
                "target": t.name, "url": t.url,
                "last": last.to_dict() if last else None,
                "summary": store.summary(t.name, since),
                "history": [r.to_dict() for r in store.history(t.name, since, min(limit, 1000))],
            })
        return {"demo": settings.demo, "window_hours": hours, "targets": out}

    @app.get("/metrics")
    def prom():
        body, ctype = metrics.render()
        return Response(body, media_type=ctype)

    return app
