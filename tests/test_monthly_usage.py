from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_net_monitor.monthly_usage import MonthlyTrafficStore


class MonthlyTrafficStoreTest(unittest.TestCase):
    def test_add_persists_current_month_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monthly_usage.json"
            store = MonthlyTrafficStore(
                path,
                today_func=lambda: date(2026, 6, 25),
                flush_interval_seconds=0,
            )

            usage = store.add(120, 240)
            store.add(-10, 30)

            self.assertEqual(usage.month, "2026-06")
            reloaded = MonthlyTrafficStore(path, today_func=lambda: date(2026, 6, 25))
            self.assertEqual(reloaded.snapshot().up_bytes, 120)
            self.assertEqual(reloaded.snapshot().down_bytes, 270)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("version", raw)

    def test_add_buffers_until_flush_interval(self) -> None:
        now = [100.0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monthly_usage.json"
            store = MonthlyTrafficStore(
                path,
                today_func=lambda: date(2026, 6, 25),
                clock_func=lambda: now[0],
                flush_interval_seconds=60,
            )

            store.add(120, 240)
            self.assertFalse(path.exists())

            now[0] = 159.0
            store.add(1, 1)
            self.assertFalse(path.exists())

            now[0] = 160.0
            store.add(2, 3)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["month"], "2026-06")
            self.assertEqual(raw["up_bytes"], 123)
            self.assertEqual(raw["down_bytes"], 244)

    def test_snapshot_rotates_when_month_changes(self) -> None:
        today = [date(2026, 6, 30)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monthly_usage.json"
            store = MonthlyTrafficStore(path, today_func=lambda: today[0])
            store.add(100, 200)

            today[0] = date(2026, 7, 1)
            usage = store.snapshot()

            self.assertEqual(usage.month, "2026-07")
            self.assertEqual(usage.up_bytes, 0)
            self.assertEqual(usage.down_bytes, 0)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["month"], "2026-07")
            self.assertEqual(raw["up_bytes"], 0)
            self.assertEqual(raw["down_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
