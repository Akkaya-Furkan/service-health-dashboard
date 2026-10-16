from __future__ import annotations

import sqlite3
import threading

from .models import CheckResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    target TEXT NOT NULL,
    checked_at INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    status_code INTEGER,
    response_ms REAL,
    error TEXT,
    PRIMARY KEY (target, checked_at)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_checks_time ON checks (checked_at);
"""


class Store:
    """SQLite store. WAL mode; (target, checked_at) is unique so re-inserting is a no-op."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def journal_mode(self) -> str:
        with self._lock:
            return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    def add(self, r: CheckResult) -> bool:
        """Insert a result; returns False if (target, checked_at) already existed."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO checks VALUES (?,?,?,?,?,?)",
                (r.target, r.checked_at, int(r.ok), r.status_code, r.response_ms, r.error),
            )
            return cur.rowcount == 1

    def add_many(self, rs) -> int:
        return sum(self.add(r) for r in rs)

    def latest(self, target: str) -> CheckResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checks WHERE target=? ORDER BY checked_at DESC LIMIT 1",
                (target,),
            ).fetchone()
        return _row(row) if row else None

    def history(self, target: str, since: int = 0, limit: int = 200) -> list[CheckResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM checks WHERE target=? AND checked_at>=? "
                "ORDER BY checked_at DESC LIMIT ?",
                (target, since, limit),
            ).fetchall()
        return [_row(r) for r in rows][::-1]

    def summary(self, target: str, since: int = 0) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n, SUM(ok) up, AVG(CASE WHEN ok=1 THEN response_ms END) avg_ms, "
                "MAX(response_ms) max_ms FROM checks WHERE target=? AND checked_at>=?",
                (target, since),
            ).fetchone()
        n = row["n"] or 0
        up = row["up"] or 0
        return {
            "checks": n,
            "uptime_percent": round(100.0 * up / n, 2) if n else None,
            "avg_response_ms": round(row["avg_ms"], 1) if row["avg_ms"] is not None else None,
            "max_response_ms": round(row["max_ms"], 1) if row["max_ms"] is not None else None,
        }

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


def _row(row) -> CheckResult:
    return CheckResult(
        row["target"], row["checked_at"], bool(row["ok"]),
        row["status_code"], row["response_ms"], row["error"],
    )
