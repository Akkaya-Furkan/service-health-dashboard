import httpx
import pytest
from fastapi.testclient import TestClient

from shd.app import create_app
from shd.checker import Checker
from shd.config import Settings, load_settings, parse_targets, validate_docker_host
from shd.demo import DEMO_TARGETS, DemoSource, backfill, synthetic_result
from shd.docker_client import DockerProxySource, StaticSource, cpu_percent, memory_usage
from shd.models import CheckResult, ContainerInfo, Target
from shd.store import Store


def res(ts, ok=True, ms=10.0, target="a"):
    return CheckResult(target, ts, ok, 200 if ok else 500, ms, None)


# --- store
def test_store_dedup_and_summary(tmp_path):
    s = Store(str(tmp_path / "x.sqlite"))
    assert s.journal_mode() == "wal"
    assert s.add(res(100)) is True
    assert s.add(res(100, ok=False)) is False  # same (target, ts): ignored
    s.add(res(200, ok=False, ms=None))
    assert s.count() == 2
    assert s.summary("a")["uptime_percent"] == 50.0
    assert s.latest("a").checked_at == 200
    assert [r.checked_at for r in s.history("a")] == [100, 200]


def test_backfill_idempotent():
    s = Store(":memory:")
    n1 = backfill(s, DEMO_TARGETS, hours=1, now=1_000_000)
    n2 = backfill(s, DEMO_TARGETS, hours=1, now=1_000_000)
    assert n1 > 0 and n2 == 0


# --- checker (httpx MockTransport, no sockets)


def test_checker_http_mock():
    import asyncio

    def handler(req):
        return httpx.Response(200 if "good" in str(req.url) else 500)

    store = Store(":memory:")
    ts = [Target("good", "http://svc.test/good"), Target("bad", "http://svc.test/bad")]
    c = Checker(ts, store, transport=httpx.MockTransport(handler))
    out = asyncio.run(c.run_once())
    d = {r.target: r for r in out}
    assert d["good"].ok and d["good"].response_ms is not None
    assert not d["bad"].ok and d["bad"].error == "HTTP 500"
    assert store.count() == 2


def test_checker_connect_error():
    import asyncio

    def handler(req):
        raise httpx.ConnectError("boom")

    store = Store(":memory:")
    c = Checker([Target("x", "http://svc.test")], store, transport=httpx.MockTransport(handler))
    r = asyncio.run(c.run_once())[0]
    assert not r.ok and r.error == "ConnectError" and r.response_ms is None


# --- config
def test_parse_targets():
    assert parse_targets("a=http://x, b=http://y")[1] == Target("b", "http://y")
    assert parse_targets('[{"name":"a","url":"http://x","expected_status":204}]')[0].expected_status == 204
    with pytest.raises(ValueError):
        parse_targets("nourl")


def test_docker_host_must_be_tcp():
    assert validate_docker_host("tcp://proxy:2375")
    for bad in ("unix:///var/run/docker.sock", "/var/run/docker.sock"):
        with pytest.raises(ValueError):
            validate_docker_host(bad)
    with pytest.raises(ValueError):
        load_settings({"SHD_DOCKER_HOST": "unix:///var/run/docker.sock"})


def test_demo_ignores_docker_host():
    assert load_settings({"SHD_DEMO": "1", "SHD_DOCKER_HOST": "tcp://x:1"}).docker_host is None


# --- docker client (fake SDK client)
def test_stats_math():
    st = {"cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 2},
          "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
          "memory_stats": {"usage": 1000, "limit": 5000, "stats": {"inactive_file": 200}}}
    assert cpu_percent(st) == 40.0
    assert memory_usage(st) == (800, 5000)
    assert cpu_percent({}) is None


class FakeContainer:
    def __init__(self, name, status):
        self.short_id, self.name, self.status = name[:4], name, status
        self.attrs = {"Status": "Up 1 hour", "Config": {"Image": f"{name}:1"}}

    @property
    def image(self):
        # The real SDK would call GET /images/<id>; the read-only proxy blocks that (403).
        raise AssertionError("DockerProxySource must not touch Container.image")

    def stats(self, stream=False):
        assert stream is False
        return {"cpu_stats": {"cpu_usage": {"total_usage": 2}, "system_cpu_usage": 20, "online_cpus": 1},
                "precpu_stats": {"cpu_usage": {"total_usage": 1}, "system_cpu_usage": 10},
                "memory_stats": {"usage": 100, "limit": 1000}}


class FakeClient:
    containers = type("C", (), {"list": staticmethod(lambda all=False: [FakeContainer("web", "running"), FakeContainer("job", "exited")])})()


def test_docker_source_with_fake_client():
    cs = DockerProxySource("tcp://x:1", client=FakeClient()).list_containers()
    assert [c.name for c in cs] == ["web", "job"]
    assert cs[0].cpu_percent == 10.0 and cs[0].memory_bytes == 100
    assert cs[1].cpu_percent is None
    assert [c.image for c in cs] == ["web:1", "job:1"]


# --- API
def client_for(settings, **kw):
    return TestClient(create_app(settings, start_checker=False, **kw))


def test_api_with_fake_clients():
    s = Settings(db_path=":memory:", targets=[Target("web", "http://svc.test")])
    src = StaticSource([ContainerInfo("1", "web", "img", "running", "Up", 1.5, 100, 1000)])
    probe = lambda t: CheckResult(t.name, 1_700_000_000, True, 200, 12.5)
    with client_for(s, source=src, probe=probe) as c:
        assert c.get("/health").json()["status"] == "ok"
        assert c.get("/api/services").json()["containers"][0]["name"] == "web"
        # probe timestamp is far in the past, so use a wide window
        u = c.get("/api/uptime?hours=100000").json()["targets"][0]
        assert u["last"]["response_ms"] == 12.5 and u["summary"]["uptime_percent"] == 100.0
        m = c.get("/metrics")
        assert m.headers["content-type"].startswith("text/plain")
        assert 'shd_target_up{target="web"} 1.0' in m.text
        assert 'shd_container_cpu_percent{name="web"} 1.5' in m.text


def test_services_502_when_source_fails():
    class Boom(StaticSource):
        def list_containers(self):
            raise RuntimeError("proxy down")
    with client_for(Settings(db_path=":memory:"), source=Boom([])) as c:
        assert c.get("/api/services").status_code == 502
        assert c.get("/metrics").status_code == 200


def test_demo_mode_endpoints():
    with client_for(Settings(demo=True)) as c:
        h = c.get("/health").json()
        assert h["demo"] and h["stored_checks"] > 100
        svc = c.get("/api/services").json()
        assert svc["count"] == 6 and any(x["state"] == "exited" for x in svc["containers"])
        up = c.get("/api/uptime").json()
        assert len(up["targets"]) == len(DEMO_TARGETS)
        assert all(t["history"] for t in up["targets"])
        assert "shd_target_up" in c.get("/metrics").text


def test_synthetic_deterministic():
    t = DEMO_TARGETS[0]
    assert synthetic_result(t, 12345) == synthetic_result(t, 12345)
    assert DemoSource().list_containers()


# --- frontend
from shd.ui import fmt_bytes, spark_points, uptime_bar


def test_ui_helpers():
    assert fmt_bytes(None) == "–" and fmt_bytes(1536) == "1.5 KiB" and fmt_bytes(5) == "5 B"
    h = [res(0, ms=10), res(10, ms=30), res(20, ok=False, ms=None)]
    assert len(spark_points(h).split()) == 2  # failed check excluded
    assert spark_points(h[:1]) == ""
    bar = uptime_bar(h, buckets=3)
    assert bar == ["up", "up", "down"] and uptime_bar([], 4) == ["none"] * 4


def test_ui_pages_demo():
    with client_for(Settings(demo=True)) as c:
        page = c.get("/")
        assert page.status_code == 200 and "DEMO" in page.text and "htmx.min.js" in page.text
        assert c.get("/static/htmx.min.js").status_code == 200
        sv = c.get("/ui/services").text
        assert "nextcloud" in sv and "Exited" in sv and "<meter" in sv
        up = c.get("/ui/uptime").text
        assert "pihole" in up and "polyline" in up and "% up" in up


def test_ui_empty_and_error_states():
    class Boom(StaticSource):
        def list_containers(self):
            raise RuntimeError("proxy down")
    with client_for(Settings(db_path=":memory:"), source=Boom([])) as c:
        assert "container source unavailable" in c.get("/ui/services").text
        assert "No HTTP targets configured" in c.get("/ui/uptime").text
    with client_for(Settings(db_path=":memory:"), source=StaticSource([])) as c:
        assert "No containers reported" in c.get("/ui/services").text


def test_docker_source_lazy_no_connect_at_startup():
    # constructing must not touch the network (conftest fails any connect)
    src = DockerProxySource("tcp://127.0.0.1:1")
    with pytest.raises(Exception):
        src.list_containers()
    with pytest.raises(ValueError):
        DockerProxySource("unix:///var/run/docker.sock")
