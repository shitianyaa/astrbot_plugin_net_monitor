from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_net_monitor.monitor import NetMonitor
from astrbot_plugin_net_monitor.provider import NetProvider, NetProviderError, NetSnapshot


def snap(timestamp: float, interfaces: dict[str, tuple[int, int]]) -> NetSnapshot:
    return NetSnapshot(timestamp=timestamp, interfaces=interfaces)


class SequenceProvider(NetProvider):
    name = "sequence"

    def __init__(self, items: list[NetSnapshot | NetProviderError]) -> None:
        self._items = deque(items)

    def snapshot(self) -> NetSnapshot:
        if not self._items:
            raise NetProviderError("no more samples")
        item = self._items.popleft()
        if isinstance(item, NetProviderError):
            raise item
        return item


class SwitchingProvider(NetProvider):
    name = "switching"

    def __init__(self) -> None:
        self.generation = 0
        self._items = deque(
            [
                snap(1.0, {"eth0": (100, 200)}),
                snap(3.0, {"eth0": (150, 260)}),
                snap(5.0, {"wlan0": (10_000, 10_000)}),
            ]
        )

    def snapshot(self) -> NetSnapshot:
        item = self._items.popleft()
        if item.timestamp == 5.0:
            self.generation += 1
        return item


class NetMonitorTest(unittest.TestCase):
    def test_first_sample_sets_baseline(self) -> None:
        monitor = NetMonitor(
            SequenceProvider([snap(1.0, {"eth0": (100, 200)})]),
            window_seconds=5,
        )

        stats = monitor.sample()

        self.assertEqual(stats.up_speed_bps, 0)
        self.assertEqual(stats.down_speed_bps, 0)
        self.assertEqual(stats.session_up_bytes, 0)
        self.assertEqual(stats.session_down_bytes, 0)
        self.assertEqual(stats.system_up_bytes, 200)
        self.assertEqual(stats.system_down_bytes, 100)

    def test_delta_and_window_average(self) -> None:
        monitor = NetMonitor(
            SequenceProvider(
                [
                    snap(1.0, {"eth0": (100, 200)}),
                    snap(3.0, {"eth0": (150, 260)}),
                ]
            ),
            window_seconds=5,
        )

        monitor.sample()
        stats = monitor.sample()

        self.assertEqual(stats.up_speed_bps, 30)
        self.assertEqual(stats.down_speed_bps, 25)
        self.assertEqual(stats.up_delta_bytes, 60)
        self.assertEqual(stats.down_delta_bytes, 50)
        self.assertEqual(stats.up_speed_avg_bps, 30)
        self.assertEqual(stats.down_speed_avg_bps, 25)
        self.assertEqual(stats.window_span_seconds, 2)
        self.assertEqual(stats.session_up_bytes, 60)
        self.assertEqual(stats.session_down_bytes, 50)

    def test_zero_delta_and_window_average(self) -> None:
        monitor = NetMonitor(
            SequenceProvider(
                [
                    snap(1.0, {"eth0": (100, 200)}),
                    snap(3.0, {"eth0": (100, 200)}),
                ]
            ),
            window_seconds=5,
        )

        monitor.sample()
        stats = monitor.sample()

        self.assertEqual(stats.up_speed_bps, 0)
        self.assertEqual(stats.down_speed_bps, 0)
        self.assertEqual(stats.up_delta_bytes, 0)
        self.assertEqual(stats.down_delta_bytes, 0)
        self.assertEqual(stats.up_speed_avg_bps, 0)
        self.assertEqual(stats.down_speed_avg_bps, 0)
        self.assertEqual(stats.window_span_seconds, 2)
        self.assertEqual(stats.session_up_bytes, 0)
        self.assertEqual(stats.session_down_bytes, 0)

    def test_new_interface_does_not_create_delta_jump(self) -> None:
        monitor = NetMonitor(
            SequenceProvider(
                [
                    snap(1.0, {"eth0": (100, 100)}),
                    snap(3.0, {"eth0": (110, 130), "wlan0": (5_000, 6_000)}),
                ]
            ),
            window_seconds=5,
        )

        monitor.sample()
        stats = monitor.sample()

        self.assertEqual(stats.session_up_bytes, 30)
        self.assertEqual(stats.session_down_bytes, 10)
        self.assertEqual(stats.system_up_bytes, 6_130)
        self.assertEqual(stats.system_down_bytes, 5_110)

    def test_provider_generation_change_resets_baseline_and_preserves_session(self) -> None:
        monitor = NetMonitor(SwitchingProvider(), window_seconds=5)

        monitor.sample()
        before_switch = monitor.sample()
        after_switch = monitor.sample()

        self.assertEqual(before_switch.session_up_bytes, 60)
        self.assertEqual(before_switch.session_down_bytes, 50)
        self.assertEqual(after_switch.up_speed_bps, 0)
        self.assertEqual(after_switch.down_speed_bps, 0)
        self.assertEqual(after_switch.session_up_bytes, 60)
        self.assertEqual(after_switch.session_down_bytes, 50)
        self.assertEqual(after_switch.system_up_bytes, 10_000)
        self.assertEqual(after_switch.system_down_bytes, 10_000)

    def test_provider_error_keeps_previous_totals(self) -> None:
        monitor = NetMonitor(
            SequenceProvider(
                [
                    snap(1.0, {"eth0": (100, 100)}),
                    snap(3.0, {"eth0": (150, 160)}),
                    NetProviderError("boom"),
                ]
            ),
            window_seconds=5,
        )

        monitor.sample()
        ok_stats = monitor.sample()
        error_stats = monitor.sample()

        self.assertEqual(error_stats.session_up_bytes, ok_stats.session_up_bytes)
        self.assertEqual(error_stats.session_down_bytes, ok_stats.session_down_bytes)
        self.assertEqual(error_stats.system_up_bytes, ok_stats.system_up_bytes)
        self.assertEqual(error_stats.system_down_bytes, ok_stats.system_down_bytes)
        self.assertEqual(error_stats.error, "boom")


if __name__ == "__main__":
    unittest.main()
