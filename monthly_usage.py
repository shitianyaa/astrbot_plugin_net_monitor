"""Persistent monthly traffic usage accounting."""

from __future__ import annotations

import json
from time import monotonic
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

DEFAULT_FLUSH_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class MonthlyTrafficUsage:
    """Current month traffic usage stored on disk."""

    month: str
    up_bytes: int = 0
    down_bytes: int = 0


class MonthlyTrafficStore:
    """Small JSON-backed store for current-month traffic deltas."""

    def __init__(
        self,
        path: Path,
        today_func: Callable[[], date] = date.today,
        clock_func: Callable[[], float] = monotonic,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._path = path
        self._today_func = today_func
        self._clock_func = clock_func
        self._flush_interval_seconds = max(float(flush_interval_seconds), 0.0)
        self._last_flush_at = self._clock_func()
        self._dirty = False
        self._usage = self._load()
        self._ensure_current_month()

    def snapshot(self) -> MonthlyTrafficUsage:
        """Return current month usage, rotating the store if needed."""
        if self._ensure_current_month():
            self._save()
        return self._usage

    def add(self, up_delta: int, down_delta: int) -> MonthlyTrafficUsage:
        """Add one successful sample delta to the current month."""
        if self._ensure_current_month():
            self._save()
        up = max(int(up_delta), 0)
        down = max(int(down_delta), 0)
        if up or down:
            self._usage = MonthlyTrafficUsage(
                month=self._usage.month,
                up_bytes=self._usage.up_bytes + up,
                down_bytes=self._usage.down_bytes + down,
            )
            self._dirty = True
        if self._dirty and self._should_flush():
            self.flush()
        return self._usage

    def flush(self) -> None:
        """Persist pending in-memory usage changes."""
        if self._dirty:
            self._save()

    def _current_month(self) -> str:
        return self._today_func().strftime("%Y-%m")

    def _should_flush(self) -> bool:
        return (
            self._flush_interval_seconds == 0
            or self._clock_func() - self._last_flush_at >= self._flush_interval_seconds
        )

    def _ensure_current_month(self) -> bool:
        month = self._current_month()
        if self._usage.month == month:
            return False
        self._usage = MonthlyTrafficUsage(month=month)
        return True

    def _load(self) -> MonthlyTrafficUsage:
        if not self._path.exists():
            return MonthlyTrafficUsage(month=self._current_month())
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return MonthlyTrafficUsage(month=self._current_month())

        month = raw.get("month")
        if not isinstance(month, str):
            month = self._current_month()
        return MonthlyTrafficUsage(
            month=month,
            up_bytes=self._non_negative_int(raw.get("up_bytes", 0)),
            down_bytes=self._non_negative_int(raw.get("down_bytes", 0)),
        )

    @staticmethod
    def _non_negative_int(value: object) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        payload = {
            "month": self._usage.month,
            "up_bytes": self._usage.up_bytes,
            "down_bytes": self._usage.down_bytes,
        }
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)
        self._dirty = False
        self._last_flush_at = self._clock_func()
