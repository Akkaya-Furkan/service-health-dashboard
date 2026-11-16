import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from shd.app import create_app
from shd.checker import Checker
from shd.config import Settings, load_settings
from shd.models import CheckResult
from shd.store import Store


def result(target, timestamp):
    return CheckResult(target, timestamp, True, 200, 10.0)


def test_prune_boundary_all_targets_and_persistence(tmp_path):
    path = str(tmp_path / "checks.sqlite")
    store = Store(path)
    store.add_many([
        result("a", 99), result("b", 99), result("a", 100), result("b", 101),
    ])
    assert store.prune_before(100) == 2
    assert store.prune_before(100) == 0
    store.close()

    store = Store(path)
    assert store.count() == 2
    assert store.latest("a").checked_at == 100
    assert store.latest("b").checked_at == 101
    store.close()


@pytest.mark.parametrize("value, expected", [(None, 30), ("0", 0), ("7", 7)])
def test_retention_config(value, expected):
    env = {} if value is None else {"SHD_RETENTION_DAYS": value}
    assert load_settings(env).retention_days == expected
    assert Settings().retention_days == 30


@pytest.mark.parametrize("value", ["-1", "abc", "1.5", ""])
def test_retention_config_invalid(value):
    with pytest.raises(ValueError):
        load_settings({"SHD_RETENTION_DAYS": value})


def test_retention_settings_negative():
    with pytest.raises(ValueError, match="SHD_RETENTION_DAYS"):
        Settings(retention_days=-1)


@pytest.mark.parametrize("days, expected_count", [(1, 2), (0, 3)])
def test_startup_prunes_existing_database(tmp_path, monkeypatch, days, expected_count):
    now = 2_000_000_000
    monkeypatch.setattr("shd.checker.time.time", lambda: now)
    path = str(tmp_path / "checks.sqlite")
    store = Store(path)
    store.add_many([
        result("old", now - 86401), result("boundary", now - 86400),
        result("recent", now),
    ])
    store.close()

    app = create_app(Settings(db_path=path, retention_days=days), start_checker=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["stored_checks"] == expected_count
        assert app.state.checker.retention_days == days

    store = Store(path)
    assert store.count() == expected_count
    store.close()


def test_pruning_uses_monotonic_hourly_limit(monkeypatch):
    clock = {"wall": 2_000_000_000, "monotonic": 0.0}
    monkeypatch.setattr("shd.checker.time.time", lambda: clock["wall"])
    monkeypatch.setattr("shd.checker.time.monotonic", lambda: clock["monotonic"])
    store = Mock()
    store.prune_before.return_value = 4
    checker = Checker([], store, retention_days=7)
    assert checker.prune_if_due() == 4
    store.prune_before.assert_called_once_with(2_000_000_000 - 7 * 86400)

    clock.update(wall=2_000_100_000, monotonic=3599.9)
    assert checker.prune_if_due() == 0
    assert store.prune_before.call_count == 1

    clock.update(wall=1_999_999_000, monotonic=3600.0)
    assert checker.prune_if_due() == 4
    assert store.prune_before.call_count == 2
    store.prune_before.assert_called_with(1_999_999_000 - 7 * 86400)


@pytest.mark.parametrize("prune_fails", [False, True])
def test_loop_prunes_hourly_and_keeps_checking(monkeypatch, prune_fails):
    # A frozen monotonic clock also works with asyncio; no real sleeps are needed.
    clock = {"now": 0.0}
    monkeypatch.setattr("shd.checker.time.monotonic", lambda: clock["now"])
    store = Mock()
    if prune_fails:
        store.prune_before.side_effect = RuntimeError("database busy")
    checker = Checker([], store, interval_s=30)
    checker.run_once = AsyncMock(return_value=[])
    ticks = iter([3599.0, 3600.0, 3601.0])

    async def sleep(interval):
        assert interval == 30
        tick = next(ticks, None)
        if tick is None:
            raise asyncio.CancelledError
        clock["now"] = tick

    monkeypatch.setattr("shd.checker.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(checker._loop())
    assert store.prune_before.call_count == 2
    assert checker.run_once.await_count == 4


def test_disabled_pruning_never_touches_store():
    store = Mock()
    checker = Checker([], store, retention_days=0)
    assert checker.prune_if_due() == 0
    assert checker.prune_if_due() == 0
    store.prune_before.assert_not_called()
