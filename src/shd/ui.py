"""Pure helpers for the server-rendered views."""
from __future__ import annotations

W, H = 240, 40


def fmt_bytes(n) -> str:
    if n is None:
        return "–"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def spark_points(history) -> str:
    """SVG polyline points for response time (ok checks only), scaled to W x H."""
    pts = [(r.checked_at, r.response_ms) for r in history if r.ok and r.response_ms is not None]
    if len(pts) < 2:
        return ""
    t0, t1 = pts[0][0], pts[-1][0]
    ms = [p[1] for p in pts]
    lo, hi = min(ms), max(ms)
    span_t = (t1 - t0) or 1
    span_m = (hi - lo) or 1
    return " ".join(
        f"{(t - t0) / span_t * W:.1f},{H - 4 - (m - lo) / span_m * (H - 8):.1f}" for t, m in pts
    )


def uptime_bar(history, buckets: int = 48) -> list[str]:
    """Collapse history into `buckets` cells: 'up', 'down', 'partial' or 'none'."""
    if not history:
        return ["none"] * buckets
    t0, t1 = history[0].checked_at, history[-1].checked_at
    span = (t1 - t0) or 1
    cells: list[list[bool]] = [[] for _ in range(buckets)]
    for r in history:
        i = min(int((r.checked_at - t0) / span * buckets), buckets - 1)
        cells[i].append(r.ok)
    return ["none" if not c else "up" if all(c) else "down" if not any(c) else "partial" for c in cells]
