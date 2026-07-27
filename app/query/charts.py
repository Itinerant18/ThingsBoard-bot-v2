"""Chart data formatting — port of Java ChartService.

Turns a ThingsBoard timeseries response into the Chart.js shape the frontend
expects: {"label": key, "points": [{"t": ts_ms, "y": value}, ...]} sorted by time.
Fail-soft like Java: bad data or a TB error yields an empty chart, not a 500.
"""

from typing import Any

DEFAULT_WINDOW_HOURS = 24  # Java TWENTY_FOUR_HOURS_MS
MAX_WINDOW_HOURS = 7 * 24


def chart_from_history(key: str, history: Any) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    series = history.get(key) if isinstance(history, dict) else None
    if isinstance(series, list):
        for raw in series:
            if not isinstance(raw, dict) or "ts" not in raw:
                continue
            try:
                ts = int(raw["ts"])
            except (TypeError, ValueError):
                continue
            points.append({"t": ts, "y": str(raw.get("value"))})
        points.sort(key=lambda p: p["t"])
    return {"label": key, "points": points}
